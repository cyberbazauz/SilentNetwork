"""benchmark.py — измерение BER/PER по SNR и сохранение результатов.

Задача SN-013. Сейчас доступен только интерфейс командной строки:
измерительного цикла ещё нет, скрипт не создаёт фиктивных CSV/PNG.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from snlab.cli import not_implemented

TOOL_NAME = "benchmark.py"
TASK_ID = "SN-013"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Измерение BER/PER на смоделированном канале AWGN. "
            "Результаты AWGN не являются подтверждением передачи через воздух."
        ),
    )
    parser.add_argument(
        "--snr",
        default="0,5,10,15,20",
        help="список значений SNR в дБ через запятую (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=100,
        help="число испытаний на каждое значение SNR (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20261101,
        help="зерно генератора для воспроизводимости (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="путь для CSV с результатами",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return not_implemented(TOOL_NAME, TASK_ID)


if __name__ == "__main__":
    sys.exit(main())
