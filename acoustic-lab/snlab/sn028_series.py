"""SN-028: пакетный разбор записей серии Laptop → Phone.

Телефон сохраняет каждую принятую запись как WAV и JSON. Этот модуль
принимает каталог экспортированных WAV, декодирует каждый файл тем же
Python-приёмником и строит таблицу серии с проверкой текста и CRC-16.

Модуль не доказывает, что WAV записан через воздух: он проверяет только
содержимое предоставленных файлов. Происхождение записи фиксируется в
протоколе SN-028 отдельно.

Имя файла по умолчанию: ``sn028-w2p-<дистанция>-<M1..M4>-<номер>.wav``,
например ``sn028-w2p-30cm-M1-01.wav``. Если имя не соответствует шаблону,
запись всё равно декодируется, но ожидаемый текст считается неизвестным.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from snlab.config import DATA_PROFILES, BfskProfile
from snlab.datalink import ACOUSTIC_MIN_PREAMBLE_MATCHES, MAX_DATA_PAYLOAD_BYTES
from snlab.receiver import (
    CrcError,
    FrameLengthError,
    FrameVersionError,
    IncompleteFrame,
    ReceiverError,
    SignalNotFound,
    SyncWordError,
    decode_samples,
)
from snlab.wavio import WavFormatError, read_pcm16

REPORT_SCHEMA = "silentnetwork.sn028.series/1"

#: Фиксированные тексты из `docs/sn028-physical-test-protocol.md` §3.
FIXED_TEXTS: dict[str, str] = {
    "M1": "SN-028 link check",
    "M2": "Привет, SilentNetwork",
    "M3": "0123456789abcdefghijABCDEFGHIJ!?",
    "M4": "SN-028 payload 0123456789 abcdefghijklmnopqrstuvwxyz ABCDEFGHIJK",
}

#: Пороги PASS, утверждённые Product Owner 2026-11-03.
THRESHOLDS: dict[str, tuple[int, int]] = {
    "30cm": (10, 10),
    "1m": (10, 8),
}

DISTANCE_ALIASES = {
    "30cm": "30cm",
    "0.3m": "30cm",
    "0,3m": "30cm",
    "1m": "1m",
    "3m": "3m",
    "noise": "noise",
    "шум": "noise",
}

_FILENAME_RE = re.compile(
    r"^sn028-w2p-(?P<distance>[^-]+)-(?P<text_id>M[1-4])-(?P<index>\d+)\.wav$",
    re.IGNORECASE,
)


class SeriesError(ValueError):
    """Некорректные параметры разбора серии."""


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    """Один WAV: метрики записи и результат декодирования."""

    path: Path
    distance: str
    text_id: str | None
    expected_text: str | None
    decoded_text: str | None
    passed: bool
    error_type: str | None
    message: str
    preamble_matches: int | None
    start_time_s: float | None
    rms_dbfs: float
    peak_dbfs: float
    clipped_samples: int
    duration_s: float
    sample_rate: int
    sha256: str

    @property
    def crc_ok(self) -> bool:
        return self.passed or self.error_type == "text_mismatch"

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": self.path.name,
            "distance": self.distance,
            "text_id": self.text_id,
            "expected_text": self.expected_text,
            "decoded_text": self.decoded_text,
            "passed": self.passed,
            "crc_ok": self.crc_ok,
            "error_type": self.error_type,
            "message": self.message,
            "preamble_matches": self.preamble_matches,
            "start_time_s": self.start_time_s,
            "rms_dbfs": self.rms_dbfs,
            "peak_dbfs": self.peak_dbfs,
            "clipped_samples": self.clipped_samples,
            "duration_s": self.duration_s,
            "sample_rate_hz": self.sample_rate,
            "wav_sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class SeriesReport:
    """Итог серии: попытки, счётчики и вердикт по утверждённому порогу."""

    distance: str
    profile_name: str
    attempts: tuple[AttemptOutcome, ...]
    passed: int
    failed: int
    false_frames: int
    verdict: str
    threshold: str
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": REPORT_SCHEMA,
            "generated_at_utc": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "distance": self.distance,
            "profile": self.profile_name,
            "threshold": self.threshold,
            "verdict": self.verdict,
            "attempts_total": len(self.attempts),
            "passed": self.passed,
            "failed": self.failed,
            "false_frames": self.false_frames,
            "notes": list(self.notes),
            "attempts": [attempt.as_dict() for attempt in self.attempts],
            "crc_is_authentication": False,
        }


def normalize_distance(value: str) -> str:
    """Привести подпись дистанции к `30cm` / `1m` / `3m` / `noise`."""
    key = value.strip().lower()
    if key in DISTANCE_ALIASES:
        return DISTANCE_ALIASES[key]
    raise SeriesError(
        "дистанция должна быть одной из: 30cm, 1m, 3m, noise"
    )


def parse_filename(path: str | Path) -> tuple[str | None, str | None]:
    """Вернуть (дистанция, text_id) из имени файла, если оно по шаблону."""
    match = _FILENAME_RE.match(Path(path).name)
    if match is None:
        return None, None
    distance = normalize_distance(match.group("distance"))
    return distance, match.group("text_id").upper()


def expected_text(text_id: str | None) -> str | None:
    if text_id is None:
        return None
    return FIXED_TEXTS.get(text_id)


def _dbfs(value: float) -> float:
    if value <= 0.0:
        return -240.0
    return float(20.0 * math.log10(value))


def _level(samples: np.ndarray) -> tuple[float, float, int]:
    data = np.asarray(samples, dtype=np.float64)
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
    peak = float(np.max(np.abs(data)))
    clipped = int(np.count_nonzero(np.abs(data) >= 0.999))
    return _dbfs(rms), _dbfs(peak), clipped


def _error_type(exc: ReceiverError | WavFormatError) -> str:
    if isinstance(exc, WavFormatError):
        return "wav_format_error"
    if isinstance(exc, SignalNotFound):
        return "signal_not_found"
    if isinstance(exc, SyncWordError):
        return "sync_mismatch"
    if isinstance(exc, FrameVersionError):
        return "version_unsupported"
    if isinstance(exc, FrameLengthError):
        return "length_invalid"
    if isinstance(exc, IncompleteFrame):
        return "incomplete_frame"
    if isinstance(exc, CrcError):
        return "crc_mismatch"
    return "receiver_error"


def analyze_attempt(
    path: str | Path,
    *,
    distance: str,
    profile: BfskProfile,
    text_id: str | None = None,
    expected: str | None = None,
    min_preamble_matches: int = ACOUSTIC_MIN_PREAMBLE_MATCHES,
    max_payload_bytes: int = MAX_DATA_PAYLOAD_BYTES,
) -> AttemptOutcome:
    """Декодировать один WAV и сравнить текст с ожидаемым.

    Ошибки декодирования не выбрасываются наружу: они становятся строкой
    таблицы с `passed=False`, чтобы серия из 10 попыток не прерывалась.
    """
    source = Path(path)
    sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    samples, sample_rate = read_pcm16(
        source, expected_sample_rate=profile.sample_rate
    )
    rms_dbfs, peak_dbfs, clipped = _level(samples)
    duration_s = float(samples.size / sample_rate)
    common = {
        "path": source,
        "distance": distance,
        "text_id": text_id,
        "expected_text": expected,
        "rms_dbfs": rms_dbfs,
        "peak_dbfs": peak_dbfs,
        "clipped_samples": clipped,
        "duration_s": duration_s,
        "sample_rate": sample_rate,
        "sha256": sha256,
    }

    try:
        frame = decode_samples(
            samples,
            profile,
            max_payload_bytes=max_payload_bytes,
            min_preamble_matches=min_preamble_matches,
        )
    except (ReceiverError, WavFormatError) as exc:
        return AttemptOutcome(
            decoded_text=None,
            passed=False,
            error_type=_error_type(exc),
            message=str(exc),
            preamble_matches=None,
            start_time_s=None,
            **common,
        )

    try:
        decoded = frame.payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        return AttemptOutcome(
            decoded_text=None,
            passed=False,
            error_type="payload_utf8_error",
            message=str(exc),
            preamble_matches=frame.preamble_matches,
            start_time_s=frame.start_time_s,
            **common,
        )

    text_ok = expected is None or decoded == expected
    return AttemptOutcome(
        decoded_text=decoded,
        passed=text_ok,
        error_type=None if text_ok else "text_mismatch",
        message=(
            "кадр принят, CRC совпал, текст совпал"
            if text_ok
            else "кадр принят, CRC совпал, но текст не совпал с ожидаемым"
        ),
        preamble_matches=frame.preamble_matches,
        start_time_s=frame.start_time_s,
        **common,
    )


def build_report(
    attempts: Sequence[AttemptOutcome],
    *,
    distance: str,
    profile: BfskProfile,
) -> SeriesReport:
    """Посчитать PASS/FAIL по утверждённым порогам SN-028."""
    if not attempts:
        raise SeriesError("нет ни одной записи для отчёта")
    normalized = normalize_distance(distance)
    passed = sum(1 for attempt in attempts if attempt.passed)
    failed = len(attempts) - passed
    false_frames = sum(1 for attempt in attempts if attempt.crc_ok)
    notes: list[str] = []

    if normalized == "noise":
        threshold = "0 ложных кадров среди 10 записей шума"
        if len(attempts) < 10:
            verdict = "INCOMPLETE"
            notes.append(f"записей {len(attempts)} из 10: серия не завершена")
        elif false_frames == 0:
            verdict = "PASS"
        else:
            verdict = "FAIL"
            notes.append(f"ложных кадров с верным CRC: {false_frames}")
        return SeriesReport(
            distance=normalized,
            profile_name=profile.name,
            attempts=tuple(attempts),
            passed=passed,
            failed=failed,
            false_frames=false_frames,
            verdict=verdict,
            threshold=threshold,
            notes=tuple(notes),
        )

    if normalized == "3m":
        threshold = "исследовательское измерение, обязательного PASS нет"
        verdict = "RESEARCH ONLY"
        return SeriesReport(
            distance=normalized,
            profile_name=profile.name,
            attempts=tuple(attempts),
            passed=passed,
            failed=failed,
            false_frames=false_frames,
            verdict=verdict,
            threshold=threshold,
            notes=tuple(notes),
        )

    required_attempts, required_passes = THRESHOLDS.get(normalized, (10, 10))
    threshold = f"{required_passes} из {required_attempts} точных текстов при CRC OK"
    if len(attempts) < required_attempts:
        verdict = "INCOMPLETE"
        notes.append(
            f"попыток {len(attempts)} из {required_attempts}: серия не завершена"
        )
    elif passed >= required_passes:
        verdict = "PASS"
    else:
        verdict = "FAIL"
        notes.append(f"успешных попыток {passed}, требуется {required_passes}")
    unknown_text = sum(1 for attempt in attempts if attempt.expected_text is None)
    if unknown_text:
        notes.append(
            f"у {unknown_text} записей ожидаемый текст неизвестен: "
            "проверялся только CRC, а не совпадение текста"
        )
    return SeriesReport(
        distance=normalized,
        profile_name=profile.name,
        attempts=tuple(attempts),
        passed=passed,
        failed=failed,
        false_frames=false_frames,
        verdict=verdict,
        threshold=threshold,
        notes=tuple(notes),
    )


def collect_wavs(directory: str | Path) -> list[Path]:
    source = Path(directory)
    if not source.is_dir():
        raise SeriesError(f"каталог не найден: {source}")
    return sorted(source.glob("*.wav"), key=lambda item: item.name.lower())


def analyze_directory(
    directory: str | Path,
    *,
    distance: str,
    profile: BfskProfile,
    min_preamble_matches: int = ACOUSTIC_MIN_PREAMBLE_MATCHES,
) -> SeriesReport:
    """Разобрать все WAV каталога и построить отчёт серии."""
    if not isinstance(profile, BfskProfile):
        raise SeriesError("профиль должен быть BfskProfile")
    normalized = normalize_distance(distance)
    attempts: list[AttemptOutcome] = []
    for path in collect_wavs(directory):
        file_distance, text_id = parse_filename(path)
        if file_distance is not None and file_distance != normalized:
            continue
        attempts.append(
            analyze_attempt(
                path,
                distance=normalized,
                profile=profile,
                text_id=text_id,
                expected=expected_text(text_id),
                min_preamble_matches=min_preamble_matches,
            )
        )
    return build_report(attempts, distance=normalized, profile=profile)


def write_report(report: SeriesReport, out_dir: str | Path) -> dict[str, Path]:
    """Сохранить CSV, JSON и Markdown. Возвращает пути созданных файлов."""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"sn028-series-{report.distance}-{stamp}"

    csv_path = target / f"{stem}.csv"
    json_path = target / f"{stem}.json"
    md_path = target / f"{stem}.md"

    rows = [attempt.as_dict() for attempt in report.attempts]
    fieldnames = [
        "file",
        "distance",
        "text_id",
        "expected_text",
        "decoded_text",
        "passed",
        "crc_ok",
        "error_type",
        "preamble_matches",
        "start_time_s",
        "rms_dbfs",
        "peak_dbfs",
        "clipped_samples",
        "duration_s",
        "sample_rate_hz",
        "wav_sha256",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    json_path.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(_markdown(report), encoding="utf-8")
    return {"csv": csv_path, "json": json_path, "md": md_path}


def _markdown(report: SeriesReport) -> str:
    lines = [
        f"# SN-028 — серия {report.distance}",
        "",
        f"- Профиль: `{report.profile_name}`",
        f"- Попыток: {len(report.attempts)}",
        f"- Успешно (CRC + текст): {report.passed}",
        f"- Неуспешно: {report.failed}",
        f"- Вердикт: **{report.verdict}**",
        f"- Порог: {report.threshold}",
        "",
        "| # | Файл | Текст | Ожидаемый | Принят | CRC | Преамбула | RMS, dBFS | Пик, dBFS | Клиппинг | Ошибка |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for index, attempt in enumerate(report.attempts, start=1):
        lines.append(
            "| {index} | {file} | {text_id} | {expected} | {passed} | {crc} | "
            "{preamble} | {rms:.1f} | {peak:.1f} | {clipped} | {error} |".format(
                index=index,
                file=attempt.path.name,
                text_id=attempt.text_id or "—",
                expected=_shorten(attempt.expected_text),
                passed=_yes_no(attempt.passed),
                crc=_yes_no(attempt.crc_ok),
                preamble=(
                    "—"
                    if attempt.preamble_matches is None
                    else f"{attempt.preamble_matches}/32"
                ),
                rms=attempt.rms_dbfs,
                peak=attempt.peak_dbfs,
                clipped=attempt.clipped_samples,
                error=attempt.error_type or "—",
            )
        )
    if report.notes:
        lines.extend(["", "## Примечания", ""])
        lines.extend(f"- {note}" for note in report.notes)
    lines.extend(
        [
            "",
            "## Ограничения",
            "",
            "Отчёт проверяет только содержимое предоставленных WAV. Он не "
            "подтверждает, что запись сделана через воздух, и не заменяет "
            "протокол SN-028. CRC-16 не является аутентификацией.",
            "",
        ]
    )
    return "\n".join(lines)


def _yes_no(value: bool) -> str:
    return "да" if value else "нет"


def _shorten(value: str | None, limit: int = 32) -> str:
    if value is None:
        return "—"
    return value if len(value) <= limit else value[: limit - 1] + "…"
