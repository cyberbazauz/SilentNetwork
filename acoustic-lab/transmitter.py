"""transmitter.py — сообщение → кадр → BFSK → PCM16 WAV (SN-011).

Инструмент создаёт **файл**. Он не воспроизводит звук, не открывает динамик и
не доказывает передачу через воздух. Успешный запуск означает только то, что
из заданных байт детерминированно построен корректный BFSK-сигнал.

Коды возврата:
    0 — WAV и JSON созданы;
    2 — неверные аргументы, пустой/слишком большой payload, ошибка профиля;
    4 — не удалось записать WAV, JSON или прочитать входной файл.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

from snlab.bfsk import DEFAULT_RAMP_MS, Modulation, ModulationError, modulate
from snlab.config import (
    DEFAULT_PROFILE,
    MAX_PAYLOAD_BYTES,
    PROFILES,
    BfskProfile,
    ProfileError,
    get_profile,
)
from snlab.frame import Frame, FrameError, build_frame
from snlab.reporting import configure_console, environment, utc_now, write_json
from snlab.wavio import write_pcm16

TOOL_NAME = "transmitter.py"
TASK_ID = "SN-011"
SCHEMA = "silentnetwork.acoustic-lab.transmitter/1"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_IO = 4

DEFAULT_OUT = Path("results") / "tx.wav"

LIMITATIONS = [
    "Созданный WAV — файловый сигнал, а не подтверждение передачи через воздух.",
    "Модулятор не выполняет FEC и не восстанавливает ошибки; CRC-16 только "
    "обнаруживает повреждение кадра и не является аутентификацией.",
    "Побитовая идентичность SHA-256 гарантируется на одной платформе и версиях "
    "библиотек; между ОС возможно расхождение в последнем разряде из-за libm.",
    "Частоты 16–22 кГц — экспериментальные: поддержка конкретного оборудования "
    "не проверена этим инструментом.",
    "Линейные рампы меняют спектр на краях сигнала; для измерений параметры "
    "рампа должны фиксироваться вместе с результатом.",
]


class TransmitError(ValueError):
    """Входные данные нельзя превратить в передаваемый кадр."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Кодирование сообщения или файла в BFSK-сигнал и запись PCM16 WAV. "
            "Звук не воспроизводится."
        ),
        epilog=(
            "Коды возврата: 0 — успех, 2 — неверные аргументы или payload, "
            "4 — ошибка чтения/записи файла."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--message",
        help="текст сообщения в UTF-8; пустая строка недопустима",
    )
    source.add_argument(
        "--file",
        type=Path,
        metavar="PATH",
        help=f"файл с произвольными байтами, не более {MAX_PAYLOAD_BYTES} байт",
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
        default=DEFAULT_OUT,
        help="путь для WAV-файла (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="путь JSON-отчёта (по умолчанию рядом с WAV)",
    )
    parser.add_argument(
        "--ramp-ms",
        type=float,
        default=DEFAULT_RAMP_MS,
        metavar="MS",
        help=(
            "длительность линейного нарастания/спада, мс; "
            f"0 отключает (по умолчанию %(default)s)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except TransmitError as exc:
        _fail(f"ошибка payload: {exc}", None)
        return EXIT_USAGE
    except (ProfileError, FrameError, ModulationError) as exc:
        _fail(f"ошибка параметров: {exc}", "проверьте --profile и --ramp-ms")
        return EXIT_USAGE
    except OSError as exc:
        _fail(f"ошибка файла: {exc}", "проверьте путь и права на запись")
        return EXIT_IO


def run(args: argparse.Namespace) -> int:
    profile = get_profile(args.profile)
    payload, payload_source = load_payload(args)
    frame = build_frame(payload, max_payload_bytes=MAX_PAYLOAD_BYTES)
    modulation = modulate(frame.bits, profile, ramp_ms=args.ramp_ms)

    wav_path = Path(args.out)
    write_pcm16(wav_path, modulation.samples, profile.sample_rate)
    wav_bytes = wav_path.stat().st_size
    wav_sha256 = sha256_file(wav_path)

    report_path = Path(args.report) if args.report is not None else wav_path.with_suffix(".json")
    report = build_report(
        args=args,
        payload_source=payload_source,
        profile=profile,
        payload=payload,
        frame=frame,
        modulation=modulation,
        wav_path=wav_path,
        wav_bytes=wav_bytes,
        wav_sha256=wav_sha256,
        report_path=report_path,
    )
    write_json(report_path, report)

    print_transmission_summary(report)
    print(f"\nWAV: {wav_path}")
    print(f"JSON: {report_path}")
    return EXIT_OK


def load_payload(args: argparse.Namespace) -> tuple[bytes, str]:
    """Прочитать payload из аргумента или файла без загрузки лишних байт."""
    if args.message is not None:
        if args.message == "":
            raise TransmitError("пустое сообщение: передавать нечего")
        payload = args.message.encode("utf-8")
        source = "message"
    else:
        path = Path(args.file)
        if not path.is_file():
            raise TransmitError(f"файл не найден: {path}")
        with path.open("rb") as handle:
            payload = handle.read(MAX_PAYLOAD_BYTES + 1)
        source = "file"

    if not payload:
        raise TransmitError("пустой payload: передавать нечего")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise TransmitError(
            f"payload {len(payload)} байт превышает лимит {MAX_PAYLOAD_BYTES}"
        )
    return payload, source


def build_report(
    *,
    args: argparse.Namespace,
    payload_source: str,
    profile: BfskProfile,
    payload: bytes,
    frame: Frame,
    modulation: Modulation,
    wav_path: Path,
    wav_bytes: int,
    wav_sha256: str,
    report_path: Path,
) -> dict[str, Any]:
    """Собрать JSON-отчёт с параметрами, размерами и контрольными суммами."""
    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": utc_now(),
        "environment": environment(),
        "input": {
            "kind": payload_source,
            "path": str(args.file) if args.file is not None else None,
            "payload_bytes": len(payload),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "payload_text": _payload_text(payload),
        },
        "profile": profile.as_dict(),
        "frame": frame.as_dict(),
        "modulation": modulation.as_dict(),
        "output": {
            "wav_path": str(wav_path),
            "wav_bytes": wav_bytes,
            "wav_sha256": wav_sha256,
            "sample_rate_hz": profile.sample_rate,
            "channels": 1,
            "sample_format": "PCM16 little-endian",
            "report_path": str(report_path),
        },
        "determinism": {
            "pcm_sha256": modulation.pcm_sha256(),
            "wav_sha256": wav_sha256,
            "note": (
                "Одинаковые payload, профиль и ramp_ms дают одинаковые PCM и WAV "
                "на одной платформе и версиях библиотек. Время создания отчёта "
                "на содержимое WAV не влияет."
            ),
        },
        "limitations": LIMITATIONS,
    }


def print_transmission_summary(report: dict[str, Any]) -> None:
    profile = report["profile"]
    frame = report["frame"]
    modulation = report["modulation"]
    output = report["output"]

    print(f"Профиль: {profile['name']} (Fs={profile['sample_rate']} Гц, "
          f"mark={profile['mark_hz']:.0f} Гц, space={profile['space_hz']:.0f} Гц, "
          f"{profile['symbol_rate']:.0f} симв/с)")
    print(
        f"Payload: {report['input']['payload_bytes']} байт, "
        f"SHA-256 {report['input']['payload_sha256'][:16]}…"
    )
    print(
        f"Кадр: преамбула {frame['preamble_bits']} бит + sync {frame['sync_word']}, "
        f"payload {frame['payload_bits']} бит, CRC {frame['crc16']}, "
        f"всего {frame['total_bits']} бит"
    )
    print(
        f"Сигнал: {modulation['samples']} отсчётов, "
        f"{modulation['duration_s']:.3f} с, рампа {modulation['ramp_ms']:g} мс, "
        f"пик {modulation['peak_amplitude']:.3f}"
    )
    print(f"PCM SHA-256: {modulation['pcm_sha256']}")
    print(f"WAV SHA-256: {output['wav_sha256']} ({output['wav_bytes']} байт)")


def sha256_file(path: Path) -> str:
    """SHA-256 файла без загрузки его целиком в память."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_text(payload: bytes) -> str | None:
    """Текст payload для отчёта, если это читаемый UTF-8, а не двоичные данные.

    Двоичный файл может случайно оказаться допустимым UTF-8 (например,
    байты 0x00…0x1F), поэтому дополнительно отбрасываются управляющие
    символы. Отчёт не должен превращать произвольные байты в «текст».
    """
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(not (character.isprintable() or character in "\r\n\t") for character in text):
        return None
    return text


def _fail(message: str, hint: str | None) -> None:
    print(message, file=sys.stderr)
    if hint:
        print(f"Подсказка: {hint}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
