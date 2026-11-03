"""file_transfer.py — файл → пакеты → Frame v1 → BFSK → WAV и обратно.

Инструмент реализует файловый слой над Acoustic Data Link v0. Frame v1,
CRC-16/CCITT-FALSE, BFSK и профили не меняются. Контракт описан в
``docs/acoustic-file-transfer.md``.

Команда ``send`` создаёт WAV и JSON, но **не** воспроизводит звук. Команда
``receive`` читает WAV и создаёт файл только при полном наборе чанков и
совпадении SHA-256. Ни один из режимов не доказывает передачу через воздух.

Коды возврата:
    0 — успех;
    2 — неверные аргументы, профиль или файл нельзя упаковать;
    3 — ошибка чтения/записи файла или WAV;
    4 — передача не восстановлена (нет сигнала, потеря чанков, CRC, SHA-256).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from snlab.config import DATA_PROFILES, ProfileError, get_profile
from snlab.datalink import ACOUSTIC_MIN_PREAMBLE_MATCHES
from snlab.frame import PREAMBLE_BITS
from snlab.file_transfer import (
    DEFAULT_INTER_FRAME_GAP_S,
    DEFAULT_LEAD_SILENCE_S,
    DEFAULT_TAIL_SILENCE_S,
    FileTransferError,
    decode_report_ok,
    decode_wav_to_file,
    encode_file_to_wav,
)
from snlab.reporting import configure_console, environment, utc_now, write_json

TOOL_NAME = "file_transfer.py"
TASK_ID = "SN-034"
DEFAULT_PROFILE_NAME = "data-audible"
DEFAULT_TX_WAV = Path("results") / "file-transfer.wav"
DEFAULT_RX_DIR = Path("results") / "received"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_IO = 3
EXIT_DECODE = 4

LIMITATIONS = [
    "WAV — файловый канал L0; успешный send/receive не подтверждает передачу "
    "через воздух.",
    "Один файл 0..4096 байт, без ACK, повторов по запросу и докачки.",
    "SHA-256 проверяет целостность файла, но не аутентифицирует отправителя.",
    "Нет шифрования и SCNP/AEAD: секреты и личные данные передавать нельзя.",
    "Частоты профилей — экспериментальные, поддержка оборудования не "
    "гарантируется.",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Упаковка файла в последовательность кадров Frame v1 и обратная "
            "сборка из WAV. Звук не воспроизводится и не записывается."
        ),
        epilog=(
            "Коды возврата: 0 — успех, 2 — неверные аргументы или файл, "
            "3 — ошибка WAV/файла, 4 — передача не восстановлена."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    send = commands.add_parser(
        "send", help="файл → пакеты → WAV и JSON-отчёт"
    )
    send.add_argument(
        "--file",
        type=Path,
        required=True,
        metavar="PATH",
        help="передаваемый файл, 0..4096 байт",
    )
    send.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_TX_WAV,
        help="путь для WAV (по умолчанию %(default)s)",
    )
    send.add_argument(
        "--report",
        type=Path,
        help="путь JSON-отчёта (по умолчанию рядом с WAV)",
    )
    send.add_argument(
        "--profile",
        choices=sorted(DATA_PROFILES),
        default=DEFAULT_PROFILE_NAME,
        help="профиль передачи (по умолчанию %(default)s)",
    )
    send.add_argument(
        "--transfer-id",
        type=int,
        choices=range(0x100),
        metavar="0..255",
        help="идентификатор передачи (по умолчанию случайный)",
    )
    send.add_argument(
        "--gap-ms",
        type=float,
        default=DEFAULT_INTER_FRAME_GAP_S * 1000.0,
        metavar="MS",
        help="пауза между кадрами, мс (по умолчанию %(default)s)",
    )
    send.add_argument(
        "--lead-s",
        type=float,
        default=DEFAULT_LEAD_SILENCE_S,
        metavar="S",
        help="тишина перед первым кадром, с (по умолчанию %(default)s)",
    )
    send.add_argument(
        "--tail-s",
        type=float,
        default=DEFAULT_TAIL_SILENCE_S,
        metavar="S",
        help="тишина после последнего кадра, с (по умолчанию %(default)s)",
    )

    receive = commands.add_parser(
        "receive", help="WAV → пакеты → файл и JSON-отчёт"
    )
    receive.add_argument(
        "--wav",
        type=Path,
        required=True,
        metavar="PATH",
        help="входной PCM16 mono WAV",
    )
    receive.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_RX_DIR,
        help="каталог для собранного файла (по умолчанию %(default)s)",
    )
    receive.add_argument(
        "--report",
        type=Path,
        help="путь JSON-отчёта (по умолчанию рядом с WAV)",
    )
    receive.add_argument(
        "--profile",
        choices=sorted(DATA_PROFILES),
        default=DEFAULT_PROFILE_NAME,
        help="профиль передачи (по умолчанию %(default)s)",
    )
    receive.add_argument(
        "--min-preamble-matches",
        type=_preamble_matches,
        default=ACOUSTIC_MIN_PREAMBLE_MATCHES,
        metavar="N",
        help=(
            "сколько из 32 бит преамбулы должны совпасть "
            "(по умолчанию %(default)s)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "send":
            return run_send(args)
        return run_receive(args)
    except ProfileError as exc:
        _fail(f"ошибка профиля: {exc}", None)
        return EXIT_USAGE
    except FileTransferError as exc:
        _fail(f"ошибка файла: {exc}", None)
        return EXIT_USAGE
    except (FileNotFoundError, OSError) as exc:
        _fail(f"ошибка ввода-вывода: {exc}", "проверьте путь и права на запись")
        return EXIT_IO


def run_send(args: argparse.Namespace) -> int:
    if not args.file.is_file():
        _fail(f"файл не найден: {args.file}", None)
        return EXIT_IO
    profile = get_profile(args.profile)
    wav_path = Path(args.out)
    report_path = (
        Path(args.report)
        if args.report is not None
        else wav_path.with_suffix(".tx.json")
    )
    report = encode_file_to_wav(
        args.file,
        profile,
        wav_path,
        transfer_id=args.transfer_id,
        inter_frame_gap_s=args.gap_ms / 1000.0,
        lead_silence_s=args.lead_s,
        tail_silence_s=args.tail_s,
    )
    report.update(_report_meta(report_path))
    report["limits"] = LIMITATIONS
    write_json(report_path, report)

    transfer = report["transfer"]
    timing = report["timing"]
    print(
        f"Профиль: {report['profile']['name']}, "
        f"файл {report['file']['filename']} "
        f"({report['file']['size']} байт)"
    )
    print(
        f"Пакетов: {transfer['packet_count']} "
        f"(DATA {transfer['total_chunks']}), "
        f"звук {timing['total_duration_s']:.2f} с"
    )
    print(f"Файл SHA-256: {report['file']['sha256']}")
    print("WAV создан; это не подтверждение передачи через воздух.")
    print(f"WAV: {wav_path}")
    print(f"JSON: {report_path}")
    return EXIT_OK


def run_receive(args: argparse.Namespace) -> int:
    profile = get_profile(args.profile)
    wav_path = Path(args.wav)
    report_path = (
        Path(args.report)
        if args.report is not None
        else wav_path.with_suffix(".rx.json")
    )
    report = decode_wav_to_file(
        wav_path,
        profile,
        args.out_dir,
        min_preamble_matches=args.min_preamble_matches,
    )
    report.update(_report_meta(report_path))
    report["limits"] = LIMITATIONS
    write_json(report_path, report)

    if decode_report_ok(report):
        print(
            f"Файл принят: {report['file']['filename']} "
            f"({report['file']['size']} байт), SHA-256 совпал"
        )
        print(f"Файл: {report['output_path']}")
        print(f"JSON: {report_path}")
        print("Приём из WAV не доказывает передачу через воздух.")
        return EXIT_OK

    _fail(
        f"файл не восстановлен: [{report['error_type']}] {report['message']}",
        "JSON-отчёт сохранён рядом",
    )
    print(f"JSON: {report_path}")
    if report["error_type"] in {
        "wav_not_found",
        "wav_format_error",
        "output_error",
    }:
        return EXIT_IO
    return EXIT_DECODE


def _report_meta(report_path: Path) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": utc_now(),
        "environment": environment(),
        "report_path": str(report_path),
    }


def _preamble_matches(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "min_preamble_matches должен быть целым числом"
        ) from None
    if not 1 <= number <= PREAMBLE_BITS:
        raise argparse.ArgumentTypeError(
            f"min_preamble_matches должен быть в диапазоне 1..{PREAMBLE_BITS}"
        )
    return number


def _fail(message: str, hint: str | None) -> None:
    print(f"ОШИБКА: {message}", file=sys.stderr)
    if hint:
        print(f"Подсказка: {hint}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover - точка входа CLI
    raise SystemExit(main())
