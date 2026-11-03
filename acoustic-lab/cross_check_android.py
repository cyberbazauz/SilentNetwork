#!/usr/bin/env python3
"""SN-027: автоматическая кросс-проверка Python ↔ Kotlin.

Скрипт создаёт WAV-файлы Python-кодеком, запускает Kotlin-тест
``CrossLanguageTest`` (Android unit test на JVM), затем декодирует
Kotlin-файлы обратно Python-кодеком и сверяет текст, преамбулу и CRC.

Дополнительно проверяется файловый слой File Transfer v0: Python-файл →
WAV → Kotlin-декодер → собранный файл и SHA-256, затем Kotlin-файл → WAV →
Python-декодер. SHA-256 проверяет целостность, но не аутентифицирует
отправителя.

Проверяется только файловый канал L0 (WAV → декодер). Это не доказывает
передачу через воздух: для неё нужен отдельный физический тест SN-028.

Запуск из корня репозитория::

    python acoustic-lab/cross_check_android.py

Exit code 0 — все кейсы совпали в обе стороны, 1 — есть расхождения или
сборка/тест Kotlin завершились ошибкой.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from snlab.config import DATA_PROFILES
from snlab.datalink import decode_wav_to_text, encode_text_to_wav
from snlab.file_transfer import decode_wav_to_file, encode_file_to_wav

CASES: tuple[tuple[str, str, str], ...] = (
    ("ascii", "SilentNetwork", "data-audible"),
    ("cyrillic", "Привет, мир", "data-audible"),
    ("mixed", "SN-027: тест", "data-robust"),
    ("max64", "A" * 64, "data-robust"),
)

# id, имя файла, содержимое, профиль, transfer_id
FILE_CASES: tuple[tuple[str, str, bytes, str, int], ...] = (
    ("text", "py-note.txt", b"SilentNetwork file transfer cross-check\n", "data-audible", 42),
    ("cyrillic", "привет.txt", "Привет, файл!".encode("utf-8"), "data-robust", 43),
    ("binary", "bytes.bin", bytes(range(120)), "data-audible", 44),
    ("empty", "empty.txt", b"", "data-audible", 45),
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


def build_python_file_wavs(work_dir: Path) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    source_dir = work_dir / "python-src"
    source_dir.mkdir(parents=True, exist_ok=True)
    for case_id, filename, content, profile_name, transfer_id in FILE_CASES:
        profile = DATA_PROFILES[profile_name]
        # encode_file_to_wav берёт имя файла из имени исходника, поэтому
        # исходник должен называться ровно так, как нужно в MANIFEST.
        source = source_dir / filename
        source.write_bytes(content)
        wav_name = f"py_ft_{case_id}.wav"
        report = encode_file_to_wav(
            source,
            profile,
            work_dir / wav_name,
            transfer_id=transfer_id,
        )
        if report.get("result") != "ok":
            raise SystemExit(f"Python не смог закодировать файл {case_id}: {report}")
        cases.append(
            {
                "id": case_id,
                "profile": profile_name,
                "filename": filename,
                "content_hex": content.hex(),
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "transfer_id": transfer_id,
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
    file_cases: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    result_path = work_dir / "crosscheck-result.json"
    if not result_path.is_file():
        raise SystemExit(f"Kotlin-тест не создал {result_path}")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if payload.get("schema") != REPORT_SCHEMA:
        raise SystemExit(f"неожиданная схема отчёта: {payload.get('schema')!r}")

    rows: list[dict[str, object]] = []
    file_rows: list[dict[str, object]] = []
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

    specs = {str(spec["id"]): spec for spec in file_cases}
    py_decoded_dir = work_dir / "python-decoded"
    for case in payload.get("files", []):
        case_id = case["id"]
        spec = specs.get(case_id)
        if spec is None:
            failures.append(f"{case_id}: файловый кейс отсутствует в манифесте Python")
            continue
        profile_name = str(spec["profile"])
        profile = DATA_PROFILES[profile_name]
        expected_content = bytes.fromhex(str(spec["content_hex"]))
        expected_sha = str(spec["sha256"])
        expected_name = str(spec["filename"])

        kotlin_ok = bool(case.get("kotlin_ok"))
        py_to_kt_ok = (
            kotlin_ok
            and case.get("kotlin_filename") == expected_name
            and case.get("kotlin_sha256") == expected_sha
            and case.get("kotlin_size") == len(expected_content)
        )
        if not py_to_kt_ok:
            failures.append(
                f"file/{case_id}: Python → Kotlin не сошёлся "
                f"(ok={kotlin_ok}, filename={case.get('kotlin_filename')!r}, "
                f"sha256={case.get('kotlin_sha256')!r}, "
                f"size={case.get('kotlin_size')!r}, "
                f"error={case.get('kotlin_error')!r})"
            )

        kotlin_wav = work_dir / str(case.get("kt_wav", ""))
        if not kotlin_wav.is_file():
            failures.append(f"file/{case_id}: Kotlin не создал {kotlin_wav.name}")
            py_report: dict[str, object] = {}
        else:
            py_report = decode_wav_to_file(kotlin_wav, profile, py_decoded_dir)
        py_file = py_report.get("file")
        py_file_dict = py_file if isinstance(py_file, dict) else {}
        kt_to_py_ok = (
            py_report.get("result") == "ok"
            and py_file_dict.get("filename") == expected_name
            and py_file_dict.get("sha256") == expected_sha
            and py_file_dict.get("size") == len(expected_content)
        )
        if not kt_to_py_ok:
            failures.append(
                f"file/{case_id}: Kotlin → Python не сошёлся "
                f"(result={py_report.get('result')!r}, "
                f"filename={py_file_dict.get('filename')!r}, "
                f"sha256={py_file_dict.get('sha256')!r}, "
                f"size={py_file_dict.get('size')!r}, "
                f"error={py_report.get('error_type')!r})"
            )

        file_rows.append(
            {
                "id": case_id,
                "profile": profile_name,
                "filename": expected_name,
                "size": len(expected_content),
                "sha256": expected_sha,
                "chunks": case.get("kotlin_total_chunks"),
                "py_to_kt": py_to_kt_ok,
                "kt_to_py": kt_to_py_ok,
            }
        )

    return rows, file_rows, failures


def write_markdown(
    report_path: Path,
    work_dir: Path,
    command: list[str],
    rows: list[dict[str, object]],
    file_rows: list[dict[str, object]],
    failures: list[str],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# SN-027: кросс-проверка Python ↔ Kotlin",
        "",
        f"- Дата: {stamp}",
        "- Канал: файловый L0 (WAV), не воздух.",
        "- Проверка Data Link: Python WAV → Kotlin-декодер и Kotlin WAV → Python-декодер.",
        "- Проверка File Transfer v0: Python-файл → Kotlin-декодер → файл и SHA-256, "
        "затем Kotlin-файл → Python-декодер.",
        f"- Рабочий каталог: `{work_dir}`",
        f"- Команда Kotlin: `{' '.join(command)}`",
        "",
        "## Data Link",
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
    lines.append("## File Transfer v0")
    lines.append("")
    lines.append(
        "| Кейс | Профиль | Имя | Размер | Чанки | Python → Kotlin | Kotlin → Python | SHA-256 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in file_rows:
        sha = str(row["sha256"])
        lines.append(
            "| {id} | {profile} | {name} | {size} Б | {chunks} | {py_to_kt} | "
            "{kt_to_py} | `{sha}…` |".format(
                id=row["id"],
                profile=row["profile"],
                name=row["filename"],
                size=row["size"],
                chunks=row["chunks"],
                py_to_kt="OK" if row["py_to_kt"] else "FAIL",
                kt_to_py="OK" if row["kt_to_py"] else "FAIL",
                sha=sha[:12],
            )
        )
    lines.append("")
    total = len(rows) + len(file_rows)
    if failures:
        lines.append(f"**Итог: FAIL ({len(failures)} расхождений).**")
        lines.append("")
        for failure in failures:
            lines.append(f"- {failure}")
    else:
        lines.append(
            f"**Итог: PASS ({total}/{total} кейсов в обе стороны, "
            f"из них файловых {len(file_rows)}).**"
        )
    lines.append("")
    lines.append(
        "CRC-16 обнаруживает ошибки, но не является аутентификацией. "
        "SHA-256 проверяет целостность файла, но не аутентифицирует отправителя. "
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
    for stale_dir in ("kotlin-decoded", "python-decoded", "python-src"):
        shutil.rmtree(work_dir / stale_dir, ignore_errors=True)

    cases = build_python_wavs(work_dir)
    file_cases = build_python_file_wavs(work_dir)
    (work_dir / "crosscheck-input.json").write_text(
        json.dumps(
            {"schema": REPORT_SCHEMA, "cases": cases, "files": file_cases},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    command = run_kotlin(repo_root, work_dir)
    rows, file_rows, failures = verify(work_dir, file_cases)

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
    for row in file_rows:
        print(
            "[{py}] file/{id:<8} {profile:<12} {size:>4} B  "
            "Python->Kotlin={py_to_kt} Kotlin->Python={kt_to_py} "
            "SHA={sha}".format(
                py="OK" if row["py_to_kt"] and row["kt_to_py"] else "FAIL",
                id=row["id"],
                profile=row["profile"],
                size=row["size"],
                py_to_kt="OK" if row["py_to_kt"] else "FAIL",
                kt_to_py="OK" if row["kt_to_py"] else "FAIL",
                sha=str(row["sha256"])[:12],
            )
        )

    if not args.no_report:
        write_markdown(report_path, work_dir, command, rows, file_rows, failures)
        print(f"Отчёт: {report_path}")

    if failures:
        print("РАСХОЖДЕНИЯ:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    total = len(rows) + len(file_rows)
    print(f"PASS: {total}/{total} кейсов в обе стороны (файловых {len(file_rows)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
