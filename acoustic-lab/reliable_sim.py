"""CLI демонстрации ART/1 на модельном канале.

Примеры:

    python reliable_sim.py --size 1000 --loss 0.1 --seed 7
    python reliable_sim.py --file note.txt --loss 0.2 --report round.json

Скрипт не воспроизводит и не записывает звук. Успешный результат означает
только то, что логика подтверждений и повторов сработала на модели канала.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from snlab.reliable import (
    DEFAULT_SYMBOL_RATE,
    DEFAULT_WINDOW,
    MAX_MESSAGE_BYTES,
    ReliableTransportError,
    TransportConfig,
)
from snlab.reliable_sim import SimulationError, simulate_transfer
from snlab.reporting import configure_console, write_json

TOOL_NAME = "reliable_sim.py"
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAILED = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Симуляция надёжной доставки ART/1: ACK/NACK-карта и повтор "
            "только потерянных чанков. Звук не используется."
        ),
        epilog=(
            "Коды возврата: 0 — доставка подтверждена моделью, "
            "2 — неверные аргументы, 4 — передача не завершена."
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--file",
        type=Path,
        metavar="PATH",
        help="файл для передачи (0..8192 байт)",
    )
    source.add_argument(
        "--size",
        type=int,
        default=200,
        metavar="BYTES",
        help="размер синтетического сообщения (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--loss",
        type=float,
        default=0.0,
        metavar="P",
        help="вероятность потери кадра отправителя 0..1",
    )
    parser.add_argument(
        "--status-loss",
        type=float,
        default=0.0,
        metavar="P",
        help="вероятность потери STATUS 0..1",
    )
    parser.add_argument(
        "--duplicate",
        type=float,
        default=0.0,
        metavar="P",
        help="вероятность дубликата кадра 0..1",
    )
    parser.add_argument("--seed", type=int, default=1, help="seed потерь")
    parser.add_argument(
        "--window",
        type=int,
        default=DEFAULT_WINDOW,
        metavar="1..15",
        help="DATA-кадров в раунде (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--symbol-rate",
        type=float,
        default=DEFAULT_SYMBOL_RATE,
        metavar="BAUD",
        help="символьная скорость для расчёта таймингов",
    )
    parser.add_argument(
        "--report",
        type=Path,
        metavar="PATH",
        help="путь JSON-отчёта (по умолчанию не сохраняется)",
    )
    parser.add_argument(
        "--show-rounds",
        action="store_true",
        help="показать номера DATA в каждом раунде",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    try:
        if args.file is not None:
            if not args.file.is_file():
                print(f"Файл не найден: {args.file}", file=sys.stderr)
                return EXIT_USAGE
            data = args.file.read_bytes()
            filename = args.file.name
        else:
            if not 0 <= args.size <= MAX_MESSAGE_BYTES:
                print(
                    f"Размер должен быть 0..{MAX_MESSAGE_BYTES} байт",
                    file=sys.stderr,
                )
                return EXIT_USAGE
            data = bytes((index * 37) & 0xFF for index in range(args.size))
            filename = None
        config = TransportConfig(
            window=args.window,
            symbol_rate=args.symbol_rate,
        )
        report = simulate_transfer(
            data,
            config=config,
            filename=filename,
            seed=args.seed,
            data_loss=args.loss,
            status_loss=args.status_loss,
            duplicate_prob=args.duplicate,
        )
    except (ReliableTransportError, SimulationError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.report is not None:
        write_json(args.report, report)

    print(
        f"Сообщение: {report['message']['bytes']} байт, "
        f"{report['message']['chunks']} чанков, "
        f"потери данных {args.loss:.0%}, потери STATUS {args.status_loss:.0%}"
    )
    print(
        f"Раундов: {report['sender']['rounds_sent']}, "
        f"DATA-кадров: {report['sender']['data_frames_sent']} "
        f"(повторов {report['sender']['data_frames_retransmitted']}), "
        f"STATUS получено: {report['sender']['statuses_received']}"
    )
    print(
        f"Модельное время: {report['elapsed_s']:.1f} с; "
        f"потеряно DATA: {report['channel_stats']['data_frames_lost']}, "
        f"потеряно STATUS: {report['channel_stats']['status_frames_lost']}"
    )
    if report["result"] == "ok":
        print("Доставка подтверждена моделью: все чанки приняты, SHA-256 совпал.")
    else:
        print(
            f"Передача не завершена: {report['error_type']}",
            file=sys.stderr,
        )
    if args.show_rounds:
        for item in report["rounds"]:
            print(
                f"  раунд {item['round']:2d}: DATA {item['data_seqs']} "
                f"(было missing {len(item['missing_before'])})"
            )
    if args.report is not None:
        print(f"JSON: {args.report}")
    print("Это симуляция логики, а не передача через воздух.")
    return EXIT_OK if report["result"] == "ok" else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
