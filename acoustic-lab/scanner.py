"""scanner.py — проверка аудиоустройств, форматов и фонового шума.

Задача SN-010. Сейчас доступен только интерфейс командной строки: скрипт
явно сообщает, что реализация не начата, и не имитирует измерения.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from snlab.cli import not_implemented

TOOL_NAME = "scanner.py"
TASK_ID = "SN-010"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Перечисление аудиоустройств и поддерживаемых форматов, "
            "запись фонового шума и спектральный анализ."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="перечислить устройства и поддерживаемые параметры",
    )
    parser.add_argument(
        "--capture",
        type=float,
        metavar="SECONDS",
        help="записать фоновый шум заданной длительности",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="путь для JSON-отчёта или WAV-записи",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return not_implemented(TOOL_NAME, TASK_ID)


if __name__ == "__main__":
    sys.exit(main())
