#!/usr/bin/env python3
"""SN-027: автоматическая кросс-проверка Python ↔ Kotlin.

Скрипт создаёт WAV-файлы Python-кодеком, запускает Kotlin-тест
``CrossLanguageTest`` (Android unit test на JVM), затем декодирует
Kotlin-файлы обратно Python-кодеком и сверяет текст, преамбулу и CRC.

Проверяется только файловый канал L0 (WAV → декодер). Это не доказывает
передачу через воздух: для неё нужен отдельный физический тест SN-028.

Запуск из корня репозитория::

    python acoustic-lab/cross_check_android.py

Exit code 0 — все кейсы совпали в обе стороны, 1 — есть расхождения или
сборка/тест Kotlin завершились ошибкой.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from snlab.config import DATA_PROFILES
from snlab.datalink import decode_wav_to_text, encode_text_to_wav

CASES: tuple[tuple[str, str, str], ...] = (
    ("ascii", "SilentNetwork", "data-audible"),
    ("cyrillic", "Привет, мир", "data-audible"),
    ("mixed", "SN-027: тест", "data-robust"),
    ("max64", "A" * 64, "data-robust"),
)

GRADLE_TEST = "org.silentnetwork.app.audiolab.datalink.CrossLanguageTest"
REPORT_SCHEMA = "silentnetwork.cross-check/1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="каталог для WAV и JSON (по умолчанию acoustic-lab/results/cross-check)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="путь к Markdown-отчёту (по умолчанию results/sn027-cross-check.md)",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="не писать Markdown-отчёт",
    )
    return parser.parse_args()


def build_python_wavs(work_dir: Path) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for case_id, text, profile_name in CASES:
        profile = DATA_PROFILES[profile_name]
        wav_name = f"py_tx_{case_id}.wav"
        report = encode_text_to_wav(text, profile, work_dir / wav_name)
        if report.get("result") != "ok":
            raise SystemExit(f"Python не смог закодировать {case_id}: {report}")
        cases.append(
            {
                "id": case_id,
                "text": text,
                "profile": profile_name,
                "wav": wav_name,
            }
        )
    return cases


def run_kotlin(repo_root: Path, work_dir: Path) -> list[str]:
    android_dir = repo_root / "apps" / "android"
    gradlew = android_dir / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not gradlew.is_file():
        raise SystemExit(f"не найден Gradle wrapper: {gradlew}")
    command = [
        str(gradlew),
        ":app:testDebugUnitTest",
        "--tests",
        GRADLE_TEST,
        f"-PsnCrosscheckDir={work_dir}",
        "--rerun-tasks",
        "--console=plain",
    ]
    print("Запуск Kotlin-теста:")
    print("  " + " ".join(command))
    completed = subprocess.run(command, cwd=android_dir, check=False)
    if completed.returncode != 0:
        raise SystemExit(
            "Kotlin-тест завершился ошибкой "
            f"(exit {completed.returncode}); см. отчёт Gradle выше"
        )
    return command


def verify(
    work_dir: Path,
) -> tuple[list[dict[str, object]], list[str]]:
    result_path = work_dir / "crosscheck-result.json"
    if not result_path.is_file():
        raise SystemExit(f"Kotlin-тест не создал {result_path}")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if payload.get("schema") != REPORT_SCHEMA:
        raise SystemExit(f"неожиданная схема отчёта: {payload.get('schema')!r}")

    rows: list[dict[str, object]] = []
    failures: list[str] = []

    for case in payload.get("cases", []):
        case_id = case["id"]
        profile_name = case["profile"]
        profile = DATA_PROFILES[profile_name]

        kotlin_ok = bool(case.get("kotlin_ok"))
        kotlin_text = case.get("kotlin_text")
        kotlin_crc = bool(case.get("kotlin_crc_ok"))
        kotlin_preamble = case.get("kotlin_preamble_matches")
        py_to_kt_ok = (
            kotlin_ok
            and kotlin_text == case.get("py_text")
            and kotlin_crc
            and kotlin_preamble == 32
        )
        if not py_to_kt_ok:
            failures.append(
                f"{case_id}: Python → Kotlin не сошёлся "
                f"(ok={kotlin_ok}, text={kotlin_text!r}, crc_ok={kotlin_crc}, "
                f"preamble={kotlin_preamble}, error={case.get('kotlin_error')!r})"
            )

        kotlin_wav = work_dir / case["kt_wav"]
        if not kotlin_wav.is_file():
            failures.append(f"{case_id}: Kotlin не создал {kotlin_wav.name}")
            py_report: dict[str, object] = {}
        else:
            py_report = decode_wav_to_text(kotlin_wav, profile)
        kt_to_py_ok = (
            py_report.get("result") == "ok"
            and py_report.get("text") == case.get("kt_text")
            and py_report.get("crc_ok") is True
            and py_report.get("preamble_matches") == 32
        )
        if not kt_to_py_ok:
            failures.append(
                f"{case_id}: Kotlin → Python не сошёлся "
                f"(result={py_report.get('result')!r}, "
                f"text={py_report.get('text')!r}, "
                f"crc_ok={py_report.get('crc_ok')!r}, "
                f"preamble={py_report.get('preamble_matches')!r}, "
                f"error={py_report.get('error_type')!r})"
            )

        rows.append(
            {
                "id": case_id,
                "profile": profile_name,
                "py_text": case.get("py_text"),
                "kt_text": case.get("kt_text"),
                "py_to_kt": py_to_kt_ok,
                "kt_to_py": kt_to_py_ok,
                "preamble": kotlin_preamble,
                "crc_hex": case.get("kt_crc_hex"),
                "total_bits": case.get("kt_total_bits"),
            }
        )

    return rows, failures


def write_markdown(
    report_path: Path,
    work_dir: Path,
    command: list[str],
    rows: list[dict[str, object]],
    failures: list[str],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# SN-027: кросс-проверка Python ↔ Kotlin",
        "",
        f"- Дата: {stamp}",
        "- Канал: файловый L0 (WAV), не воздух.",
        "- Проверка: Python WAV → Kotlin-декодер и Kotlin WAV → Python-декодер.",
        f"- Рабочий каталог: `{work_dir}`",
        f"- Команда Kotlin: `{' '.join(command)}`",
        "",
        "| Кейс | Профиль | Python → Kotlin | Kotlin → Python | Преамбула | CRC-16 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {id} | {profile} | {py_to_kt} | {kt_to_py} | {preamble}/32 | {crc} |".format(
                id=row["id"],
                profile=row["profile"],
                py_to_kt="OK" if row["py_to_kt"] else "FAIL",
                kt_to_py="OK" if row["kt_to_py"] else "FAIL",
                preamble=row["preamble"],
                crc=row["crc_hex"],
            )
        )
    lines.append("")
    if failures:
        lines.append(f"**Итог: FAIL ({len(failures)} расхождений).**")
        lines.append("")
        for failure in failures:
            lines.append(f"- {failure}")
    else:
        lines.append(f"**Итог: PASS ({len(rows)}/{len(rows)} кейсов в обе стороны).**")
    lines.append("")
    lines.append(
        "CRC-16 обнаруживает ошибки, но не является аутентификацией. "
        "Результат не подтверждает передачу через воздух."
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    # Консоль Windows может быть в cp1251: не падаем на символах, которых в
    # ней нет. Markdown-отчёт всегда пишется в UTF-8.
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except AttributeError:  # pragma: no cover - старые интерпретаторы
        pass
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    work_dir = (args.work_dir or (repo_root / "acoustic-lab" / "results" / "cross-check")).resolve()
    report_path = (
        args.report or (repo_root / "acoustic-lab" / "results" / "sn027-cross-check.md")
    ).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    for stale in ("crosscheck-input.json", "crosscheck-result.json"):
        (work_dir / stale).unlink(missing_ok=True)

    cases = build_python_wavs(work_dir)
    (work_dir / "crosscheck-input.json").write_text(
        json.dumps({"schema": REPORT_SCHEMA, "cases": cases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    command = run_kotlin(repo_root, work_dir)
    rows, failures = verify(work_dir)

    for row in rows:
        print(
            "[{py}] {id:<8} {profile:<12} Python->Kotlin={py_to_kt} "
            "Kotlin->Python={kt_to_py} preamble={preamble}/32 CRC={crc}".format(
                py="OK" if row["py_to_kt"] and row["kt_to_py"] else "FAIL",
                id=row["id"],
                profile=row["profile"],
                py_to_kt="OK" if row["py_to_kt"] else "FAIL",
                kt_to_py="OK" if row["kt_to_py"] else "FAIL",
                preamble=row["preamble"],
                crc=row["crc_hex"],
            )
        )

    if not args.no_report:
        write_markdown(report_path, work_dir, command, rows, failures)
        print(f"Отчёт: {report_path}")

    if failures:
        print("РАСХОЖДЕНИЯ:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"PASS: {len(rows)}/{len(rows)} кейсов в обе стороны")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
