"""SN-028: каталог результатов в собранном EXE.

PyInstaller onefile распаковывает код во временный `_MEIPASS`; если писать
результаты рядом с `__file__`, они теряются после закрытия приложения. Тест
фиксирует, что в режиме `sys.frozen` оба модуля выбирают каталог рядом с `.exe`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from snlab import datalink_desktop, desktop


def test_results_dir_next_to_frozen_executable(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "SilentNetwork-Desktop.exe"))
    expected = tmp_path / "results" / "hardware_calibration"
    assert desktop.default_results_dir() == expected
    assert datalink_desktop.default_results_dir() == expected


def test_results_dir_uses_repository_layout_when_not_frozen(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    project = Path(__file__).resolve().parent.parent
    expected = project / "results" / "hardware_calibration"
    assert desktop.default_results_dir() == expected
    assert datalink_desktop.default_results_dir() == expected
