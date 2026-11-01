"""Общие элементы интерфейса командной строки Acoustic Lab."""

from __future__ import annotations

import sys

EXIT_NOT_IMPLEMENTED = 2


def not_implemented(tool: str, task_id: str) -> int:
    """Сообщить, что интерфейс определён, а реализация ещё не начата.

    Заглушка обязана быть явной: она не имитирует измерения и не возвращает
    «успех», чтобы результат нельзя было принять за реальный эксперимент.
    """
    print(
        f"{tool}: интерфейс определён, реализация запланирована в задаче {task_id}.",
        file=sys.stderr,
    )
    return EXIT_NOT_IMPLEMENTED
