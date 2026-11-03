"""SN-028: автовыбор аудиоустройств не должен подставлять Bluetooth/виртуальные.

Проверяется только политика выбора и текст предупреждений. Реальный звук
не воспроизводится и не записывается.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

import desktop_app
from snlab import audio


def make_device(
    index: int,
    name: str,
    *,
    direction: str,
    default: bool = False,
    hostapi_name: str = "MME",
) -> audio.DeviceInfo:
    return audio.DeviceInfo(
        index=index,
        name=name,
        hostapi=0,
        hostapi_name=hostapi_name,
        max_input_channels=2 if direction == "input" else 0,
        max_output_channels=2 if direction == "output" else 0,
        default_samplerate=48_000.0,
        default_low_input_latency=0.01,
        default_low_output_latency=0.01,
        is_default_input=default and direction == "input",
        is_default_output=default and direction == "output",
    )


BT_HEADSET = "Головной телефон (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0;(inkax-T02))"
BT_HEADPHONES = "Наушники (inkax-T02)"
BUILTIN_SPEAKER = "Speaker (Realtek(R) Audio)"
BUILTIN_MIC = "Microphone Array (AMD Audio Device)"
VIRTUAL_MIXER = "Стерео микшер (Realtek HD Audio Stereo input)"
USB_MIC = "Microphone (USB Audio)"


def test_name_classification() -> None:
    assert audio.is_bluetooth_device(BT_HEADSET)
    assert audio.is_bluetooth_device(BT_HEADPHONES)
    assert not audio.is_bluetooth_device(BUILTIN_SPEAKER)
    assert not audio.is_bluetooth_device("Headphones 1 (Realtek HD Audio 2nd output with HAP)")
    assert audio.is_virtual_device(VIRTUAL_MIXER)
    assert not audio.is_virtual_device(BUILTIN_MIC)
    assert audio.is_builtin_device(BUILTIN_SPEAKER)
    assert audio.is_builtin_device(BUILTIN_MIC)


def test_bluetooth_default_output_is_not_auto_selected() -> None:
    devices = [
        make_device(4, BT_HEADPHONES, direction="output", default=True),
        make_device(3, BUILTIN_SPEAKER, direction="output"),
    ]
    assert audio.select_preferred_device(devices, "output").index == 3


def test_virtual_mixer_is_not_auto_selected_as_input() -> None:
    devices = [
        make_device(13, VIRTUAL_MIXER, direction="input", default=True),
        make_device(1, BUILTIN_MIC, direction="input"),
    ]
    assert audio.select_preferred_device(devices, "input").index == 1


def test_only_bluetooth_returns_none() -> None:
    devices = [make_device(25, BT_HEADSET, direction="input", default=True)]
    assert audio.select_preferred_device(devices, "input") is None
    devices = [
        make_device(24, BT_HEADSET, direction="output", default=True),
        make_device(13, VIRTUAL_MIXER, direction="output"),
    ]
    assert audio.select_preferred_device(devices, "output") is None


def test_usb_microphone_is_used_when_no_builtin() -> None:
    devices = [
        make_device(25, BT_HEADSET, direction="input", default=True),
        make_device(7, USB_MIC, direction="input"),
    ]
    assert audio.select_preferred_device(devices, "input").index == 7


def test_device_warning_text() -> None:
    bt = make_device(25, BT_HEADSET, direction="input")
    virtual = make_device(13, VIRTUAL_MIXER, direction="input")
    builtin = make_device(1, BUILTIN_MIC, direction="input")
    assert "Bluetooth" in (audio.device_warning(bt) or "")
    assert "Виртуальное" in (audio.device_warning(virtual) or "")
    assert audio.device_warning(builtin) is None


def test_select_preferred_device_validates_direction() -> None:
    with pytest.raises(ValueError):
        audio.select_preferred_device([], "both")


def make_root() -> Any:
    import tkinter as tk

    last_error: Exception | None = None
    for _ in range(3):
        desktop_app.prepare_tcl_environment()
        try:
            root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - headless Windows
            last_error = exc
            time.sleep(0.1)
            continue
        root.withdraw()
        return root
    pytest.skip(f"Tkinter недоступен: {last_error}")


def build_app(tmp_path: Any, *, outputs: list[Any], inputs: list[Any]) -> tuple[Any, Any]:
    root = make_root()
    app = desktop_app.DesktopApp(
        root,
        hardware_test=True,
        results_dir=tmp_path / "results",
        device_lister=lambda: outputs,
        input_device_lister=lambda: inputs,
    )
    return root, app


def test_gui_skips_bluetooth_output_and_warns(tmp_path: Any) -> None:
    outputs = [
        make_device(4, BT_HEADPHONES, direction="output", default=True),
        make_device(3, BUILTIN_SPEAKER, direction="output"),
    ]
    root, app = build_app(tmp_path, outputs=outputs, inputs=[make_device(1, BUILTIN_MIC, direction="input")])
    try:
        assert app.selected_device is not None
        assert app.selected_device.index == 3
        assert "Автовыбор: Speaker" in app.device_status.cget("text")
    finally:
        root.destroy()


def test_gui_leaves_output_empty_when_only_bluetooth(tmp_path: Any) -> None:
    outputs = [make_device(4, BT_HEADPHONES, direction="output", default=True)]
    root, app = build_app(tmp_path, outputs=outputs, inputs=[make_device(1, BUILTIN_MIC, direction="input")])
    try:
        assert app.selected_device is None
        assert "Автовыбор отключён" in app.device_status.cget("text")
        assert str(app.data_play_button.cget("state")) == "disabled"
    finally:
        root.destroy()


def test_gui_skips_bluetooth_input(tmp_path: Any) -> None:
    inputs = [
        make_device(25, BT_HEADSET, direction="input", default=True),
        make_device(1, BUILTIN_MIC, direction="input"),
    ]
    root, app = build_app(
        tmp_path,
        outputs=[make_device(3, BUILTIN_SPEAKER, direction="output")],
        inputs=inputs,
    )
    try:
        assert app.data_selected_input is not None
        assert app.data_selected_input.index == 1
    finally:
        root.destroy()
