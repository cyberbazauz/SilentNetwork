"""receiver.py — декодирование BFSK-сигнала из WAV или записи.

Задача SN-021 (файловая часть). Сейчас доступен только интерфейс командной
строки: демодуляции ещё нет, скрипт не выдаёт «успешный» результат.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from snlab.cli import not_implemented
from snlab.config import DEFAULT_PROFILE, PROFILES

TOOL_NAME = "receiver.py"
TASK_ID = "SN-021"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Декодирование BFSK-сигнала из WAV с проверкой целостности.",
    )
    parser.add_argument(
        "--in",
        dest="input_path",
        type=Path,
        required=False,
        help="входной WAV-файл",
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
        help="путь для декодированного текста",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return not_implemented(TOOL_NAME, TASK_ID)


if __name__ == "__main__":
    sys.exit(main())
