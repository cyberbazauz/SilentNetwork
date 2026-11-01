"""Smoke-тесты интерфейса командной строки Acoustic Lab.

Все четыре инструмента реализованы; здесь проверяется только общий контракт
`--help` и то, что запуск без обязательных аргументов не выдаёт себя за
успешный эксперимент. Функциональные тесты находятся в профильных файлах.
"""

from __future__ import annotations

import pytest

import benchmark
import receiver
import scanner
import transmitter

TOOLS = [scanner, transmitter, receiver, benchmark]


@pytest.mark.parametrize("module", TOOLS, ids=lambda module: module.TOOL_NAME)
def test_help_exits_zero(module, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        module.main(["--help"])
    assert excinfo.value.code == 0
    assert "usage:" in capsys.readouterr().out.lower()


@pytest.mark.parametrize(
    "module",
    [transmitter, receiver],
    ids=lambda module: module.TOOL_NAME,
)
def test_required_arguments_are_enforced(
    module, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        module.main([])
    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err.lower()
