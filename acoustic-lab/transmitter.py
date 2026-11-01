"""transmitter.py — кодирование сообщения в BFSK-сигнал и WAV.

Задача SN-011. Сейчас доступен только интерфейс командной строки:
модуляции и воспроизведения ещё нет, скрипт не создаёт фиктивных файлов.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from snlab.cli import not_implemented
from snlab.config import DEFAULT_PROFILE, PROFILES

TOOL_NAME = "transmitter.py"
TASK_ID = "SN-011"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Кодирование текстового сообщения в BFSK-сигнал и запись в WAV.",
    )
    parser.add_argument(
        "--message",
        required=False,
        help="текст сообщения (UTF-8, ограничение задаётся профилем)",
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
        help="путь для WAV-файла",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return not_implemented(TOOL_NAME, TASK_ID)


if __name__ == "__main__":
    sys.exit(main())
