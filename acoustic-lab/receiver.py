"""receiver.py — WAV → BFSK → проверенный кадр → payload (SN-021).

Приёмник читает только WAV и профиль. Он не получает биты, кадр или позиции
символов от передатчика: начало кадра и значения бит восстанавливаются из
аудиосигнала.

Это файловый канал L0. Инструмент не открывает микрофон и не доказывает
передачу через воздух.

Коды возврата:
    0 — кадр найден, проверен и payload восстановлен;
    2 — неверные аргументы или профиль;
    3 — WAV не найден, повреждён, не PCM16 mono или не совпадает с профилем;
    4 — сигнал/кадр не найден, повреждён или не прошёл CRC.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

from snlab.config import DEFAULT_PROFILE, PROFILES, ProfileError, get_profile
from snlab.receiver import (
    CrcError,
    DecodedFrame,
    ReceiverError,
    SignalNotFound,
    SyncWordError,
    decode_wav,
)
from snlab.reporting import configure_console, environment, utc_now, write_json
from snlab.wavio import WavFormatError

TOOL_NAME = "receiver.py"
TASK_ID = "SN-021"
SCHEMA = "silentnetwork.acoustic-lab.receiver/1"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_DECODE = 4

LIMITATIONS = [
    "Восстановление выполнено из WAV-файла, а не из реального акустического "
    "сигнала через микрофон.",
    "Нет FEC, эквалайзера, оценки доплера и подавления реверберации: кадр "
    "либо проходит CRC, либо отклоняется.",
    "CRC-16 обнаруживает повреждение, но не аутентифицирует payload и не "
    "заменяет AEAD.",
    "Поиск преамбулы требует точного совпадения 32/32 бит и рассчитан на "
    "чистый файловый канал; для шумного канала порог придётся пересмотреть.",
    "Частота дискретизации WAV должна точно совпадать с профилем; "
    "ресемплирование не выполняется.",
    "Успешное декодирование одного файла не подтверждает дальность, "
    "устойчивость к помехам или работу другого оборудования.",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Поиск BFSK-кадра в PCM16 WAV, демодуляция, проверка sync word, "
            "версии, длины и CRC-16, восстановление payload."
        ),
        epilog=(
            "Коды возврата: 0 — успех, 2 — неверные аргументы, "
            "3 — ошибка WAV/файла, 4 — кадр не найден или не прошёл проверку."
        ),
    )
    parser.add_argument(
        "--in",
        dest="input_path",
        type=Path,
        required=True,
        help="входной PCM16 mono WAV",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default=DEFAULT_PROFILE.name,
        help="профиль передачи (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="путь для восстановленного payload (байты как есть)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="путь JSON-отчёта (по умолчанию рядом с --out)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ProfileError as exc:
        _fail(f"ошибка профиля: {exc}", None)
        return EXIT_USAGE
    except (FileNotFoundError, WavFormatError, OSError) as exc:
        _fail(f"ошибка входа: {exc}", "ожидается PCM16 mono WAV нужной частоты")
        return EXIT_INPUT
    except ReceiverError as exc:
        _fail(f"кадр не восстановлен: {exc}", _hint(exc))
        return EXIT_DECODE


def run(args: argparse.Namespace) -> int:
    profile = get_profile(args.profile)
    source = Path(args.input_path)
    decoded = decode_wav(source, profile)

    text = _payload_text(decoded.payload)
    saved_to: Path | None = None
    if args.out is not None:
        saved_to = Path(args.out)
        saved_to.parent.mkdir(parents=True, exist_ok=True)
        saved_to.write_bytes(decoded.payload)

    report_path = args.report
    if report_path is None and saved_to is not None:
        report_path = saved_to.with_suffix(".json")

    report = build_report(
        source=source,
        profile=profile.as_dict(),
        decoded=decoded,
        text=text,
        saved_to=saved_to,
        report_path=report_path,
    )
    if report_path is not None:
        write_json(report_path, report)

    print_decoded_summary(report)
    if saved_to is not None:
        print(f"Payload: {saved_to}")
    if report_path is not None:
        print(f"JSON: {report_path}")
    return EXIT_OK


def build_report(
    *,
    source: Path,
    profile: dict[str, Any],
    decoded: DecodedFrame,
    text: str | None,
    saved_to: Path | None,
    report_path: Path | None,
) -> dict[str, Any]:
    """Собрать JSON-отчёт: вход, профиль, синхронизация, кадр, payload."""
    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": utc_now(),
        "environment": environment(),
        "input": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "sha256": sha256_file(source),
            "sample_rate_hz": decoded.sample_rate,
            "channels": 1,
            "sample_format": "PCM16 little-endian",
            "samples": decoded.total_samples,
            "duration_s": decoded.duration_s,
        },
        "profile": profile,
        "sync": {
            "method": "preamble correlation + energy refinement",
            "preamble_bits": 32,
            "preamble_matches": decoded.preamble_matches,
            "start_sample": decoded.start_sample,
            "start_time_s": decoded.start_time_s,
        },
        "frame": decoded.as_dict(),
        "payload": {
            "bytes": len(decoded.payload),
            "sha256": hashlib.sha256(decoded.payload).hexdigest(),
            "text": text,
            "saved_to": str(saved_to) if saved_to is not None else None,
        },
        "output": {
            "report_path": str(report_path) if report_path is not None else None,
        },
        "limitations": LIMITATIONS,
    }


def print_decoded_summary(report: dict[str, Any]) -> None:
    sync = report["sync"]
    frame = report["frame"]
    payload = report["payload"]

    print(
        f"Кадр найден: sample={sync['start_sample']} "
        f"({sync['start_time_s']:.4f} с), "
        f"преамбула {sync['preamble_matches']}/{sync['preamble_bits']}"
    )
    print(
        f"Профиль: {report['profile']['name']}, версия {frame['version']}, "
        f"payload {payload['bytes']} байт, CRC {frame['crc16']}: OK"
    )
    if payload["text"] is not None:
        print(f"Текст: {payload['text']}")
    else:
        print("Payload: двоичные данные (текстовое представление недоступно)")
    print(f"Payload SHA-256: {payload['sha256']}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_text(payload: bytes) -> str | None:
    """Читаемый UTF-8 текст или None для двоичных данных."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(not (character.isprintable() or character in "\r\n\t") for character in text):
        return None
    return text


def _hint(exc: ReceiverError) -> str | None:
    if isinstance(exc, SignalNotFound):
        return "проверьте --profile и что WAV создан этим профилем"
    if isinstance(exc, SyncWordError):
        return "sync word повреждён или выбран неверный профиль"
    if isinstance(exc, CrcError):
        return "payload или CRC повреждены; это не аутентификация"
    return None


def _fail(message: str, hint: str | None) -> None:
    print(message, file=sys.stderr)
    if hint:
        print(f"Подсказка: {hint}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
