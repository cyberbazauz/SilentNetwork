"""Общие элементы отчётов Acoustic Lab: окружение, время, запись JSON."""

from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def utc_now() -> str:
    """Текущее время в UTC в формате ISO 8601 (секундная точность)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def environment() -> dict[str, Any]:
    """Версии Python и измерительных библиотек для воспроизводимости."""
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "libraries": {
            "numpy": np.__version__,
            "scipy": module_version("scipy"),
            "sounddevice": module_version("sounddevice"),
            "matplotlib": module_version("matplotlib"),
        },
    }


def module_version(name: str) -> str | None:
    """Версия установленного модуля или None, если он не нужен/не найден."""
    try:
        module = __import__(name)
    except ImportError:
        return None
    return getattr(module, "__version__", None)


def write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    """Записать JSON в UTF-8, создав родительские каталоги."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def configure_console() -> None:
    """Не падать на символах, которые не кодируются в текущей консоли."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):  # pragma: no cover - экзотические потоки
                pass
