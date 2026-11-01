"""Smoke-тесты интерфейса командной строки.

Для инструментов, реализация которых ещё не начата, проверяется явное
поведение заглушек: `--help` работает, запуск возвращает код 2 и называет
задачу. Ни один тест не утверждает, что измерения или передача выполнены.

scanner.py реализован в SN-010 (`test_scanner.py`), transmitter.py — в SN-011
(`test_transmitter.py`).
"""

from __future__ import annotations

import pytest

import benchmark
import receiver

STUBS = [receiver, benchmark]


@pytest.mark.parametrize("module", STUBS, ids=lambda module: module.TOOL_NAME)
def test_help_exits_zero(module, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        module.main(["--help"])
    assert excinfo.value.code == 0
    assert "usage:" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("module", STUBS, ids=lambda module: module.TOOL_NAME)
def test_unimplemented_tool_reports_task(
    module, capsys: pytest.CaptureFixture[str]
) -> None:
    assert module.main([]) == 2
    assert module.TASK_ID in capsys.readouterr().err
