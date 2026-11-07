"""SN-035: вкладка чата в Windows Desktop Lab.

Физический звук не используется. Захват подменяется `FakeCapture`, который
не открывает микрофон, а воспроизведение — `FakePlaybackEngine`. Успешный
разбор синтетического PCM здесь не объявляется передачей через воздух.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import desktop_app
from snlab import audio, chat, playback
from snlab.config import PROFILE_DATA_AUDIBLE


def make_output_device(index: int = 3) -> audio.DeviceInfo:
    return audio.DeviceInfo(
        index=index,
        name="Speaker (Realtek(R) Audio)",
        hostapi=0,
        hostapi_name="MME",
        max_input_channels=0,
        max_output_channels=2,
        default_samplerate=48_000.0,
        default_low_input_latency=0.01,
        default_low_output_latency=0.01,
        is_default_output=True,
    )


def make_input_device(index: int = 7) -> audio.DeviceInfo:
    return audio.DeviceInfo(
        index=index,
        name="Microphone (USB Audio)",
        hostapi=0,
        hostapi_name="MME",
        max_input_channels=2,
        max_output_channels=0,
        default_samplerate=48_000.0,
        default_low_input_latency=0.01,
        default_low_output_latency=0.01,
        is_default_input=True,
    )


class FakeCapture:
    """Замена `audio.ContinuousCapture`: никакого микрофона и потоков."""

    instances: list["FakeCapture"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.paused = False
        FakeCapture.instances.append(self)

    def start(self) -> None:
        self.started = True

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def clear_instances() -> None:
    FakeCapture.instances.clear()


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


def build_app(tmp_path: Path, *, engine: Any | None = None) -> tuple[Any, Any]:
    root = make_root()
    app = desktop_app.DesktopApp(
        root,
        hardware_test=True,
        results_dir=tmp_path / "results",
        engine=engine if engine is not None else playback.FakePlaybackEngine(),
        device_lister=lambda: [make_output_device()],
        input_device_lister=lambda: [make_input_device()],
        confirm_playback=lambda tone, device: True,
        confirm_data_playback=lambda generated, device: True,
        confirm_file_playback=lambda generated, device: True,
    )
    return root, app


def wait_for(root: Any, condition: Any, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("условие не выполнилось за отведённое время")


def start_fake_listening(app: Any, monkeypatch: pytest.MonkeyPatch) -> FakeCapture:
    monkeypatch.setattr(desktop_app.audio, "ContinuousCapture", FakeCapture)
    app.start_chat_listening()
    assert FakeCapture.instances, "захват не был создан"
    capture = FakeCapture.instances[-1]
    assert capture.started
    return capture


# --- вкладка и элементы -----------------------------------------------------


def test_gui_chat_section_is_wired(tmp_path: Path) -> None:
    root, app = build_app(tmp_path)
    try:
        assert app._chat_tab is not None
        labels = [
            app.tabview.tab(tab, "text")
            for tab in app.tabview.tabs()
        ]
        assert any("Чат" in label for label in labels)
        assert app.chat_mode_var.get() == "Нормальный"
        assert app.chat_mode_box.cget("values")[0] == "Нормальный"
        assert app.chat_selected_input is not None
        assert app.chat_selected_input.index == 7
        assert app.chat_input_box.get().startswith("[7]")
        assert str(app.chat_listen_button.cget("text")) == "Слушать микрофон"
        assert str(app.chat_capture) == "None"
        assert "Микрофон" in app.chat_status_label.cget("text")
    finally:
        root.destroy()


def test_gui_auto_starts_when_chat_tab_is_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop_app.audio, "ContinuousCapture", FakeCapture)
    root, app = build_app(tmp_path)
    try:
        app.tabview.select(app._chat_tab)
        app._on_tab_changed()
        assert app.chat_capture is not None
        capture = FakeCapture.instances[-1]
        assert capture.started
        assert str(app.chat_input_box.cget("state")) == "disabled"
        assert str(app.chat_listen_button.cget("text")).startswith("Остановить")

        app.toggle_chat_listening()
        assert capture.stopped
        assert app.chat_capture is None
        assert str(app.chat_input_box.cget("state")) == "readonly"

        # После ручной остановки возврат на вкладку снова разрешает автозапуск.
        app._on_tab_changed()
        assert app.chat_capture is None
        app.tabview.select(app.tabview.tabs()[0])
        app._on_tab_changed()
        app.tabview.select(app._chat_tab)
        app._on_tab_changed()
        assert app.chat_capture is not None
        app.stop_chat_listening()
    finally:
        root.destroy()


# --- отправка текста --------------------------------------------------------


def test_gui_send_text_plays_immediately(tmp_path: Path) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        app.chat_text_var.set("привет")
        app.send_chat_text()

        wait_for(root, lambda: not app.chat_playing)
        assert engine.calls, "звук не был передан в движок"
        assert engine.calls[0]["sample_rate"] == PROFILE_DATA_AUDIBLE.sample_rate
        assert app.chat_text_var.get() == ""
        log = app.chat_log.get("1.0", "end")
        assert "Вы: привет" in log
        assert "прозвучал полностью" in app.chat_status_label.cget("text")
    finally:
        root.destroy()


def test_gui_send_text_rejects_too_long_message(tmp_path: Path) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        app.chat_text_var.set("я" * 40)  # 80 байт UTF-8 > 64
        app.send_chat_text()

        assert engine.calls == []
        assert app.chat_playing is False
        assert "не отправлено" in app.chat_status_label.cget("text")
    finally:
        root.destroy()


def test_gui_send_text_rejects_reserved_prefix(tmp_path: Path) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        app.chat_text_var.set("SF-text")
        app.send_chat_text()

        assert engine.calls == []
        assert "не отправлено" in app.chat_status_label.cget("text")
    finally:
        root.destroy()


# --- отправка файла ---------------------------------------------------------


def test_gui_send_file_encodes_and_plays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        source = tmp_path / "note.txt"
        source.write_bytes(b"file-over-sound")
        monkeypatch.setattr(
            desktop_app.filedialog,
            "askopenfilename",
            lambda **_: str(source),
        )

        app.choose_chat_file()

        wait_for(root, lambda: not app.chat_playing and not app.chat_encoding)
        assert engine.calls, "файл не был передан в движок"
        log = app.chat_log.get("1.0", "end")
        assert "файл note.txt" in log
        assert "прозвучал полностью" in app.chat_status_label.cget("text")
        samples = engine.calls[0]["samples"]
        assert samples.size > PROFILE_DATA_AUDIBLE.sample_rate  # больше секунды
    finally:
        root.destroy()


def test_gui_send_file_rejects_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        source = tmp_path / "huge.bin"
        source.write_bytes(b"X" * (desktop_app.file_transfer.MAX_FILE_BYTES + 1))
        monkeypatch.setattr(
            desktop_app.filedialog,
            "askopenfilename",
            lambda **_: str(source),
        )

        app.choose_chat_file()

        assert engine.calls == []
        assert "больше лимита" in app.chat_status_label.cget("text")
    finally:
        root.destroy()


# --- приём и полудуплекс ----------------------------------------------------


def test_gui_receives_text_from_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, app = build_app(tmp_path)
    try:
        capture = start_fake_listening(app, monkeypatch)
        message = chat.encode_text_message("привет", PROFILE_DATA_AUDIBLE)
        capture.kwargs["on_samples"](message.samples)
        app._consume_chat_queue()

        log = app.chat_log.get("1.0", "end")
        assert "Принято: привет" in log
        assert "CRC-16" in app.chat_status_label.cget("text")
    finally:
        root.destroy()


def test_gui_receive_gap_resets_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, app = build_app(tmp_path)
    try:
        capture = start_fake_listening(app, monkeypatch)
        message = chat.encode_text_message("до разрыва", PROFILE_DATA_AUDIBLE)
        capture.kwargs["on_samples"](message.samples[: message.samples.size // 2])
        capture.kwargs["on_gap"]("pause")
        capture.kwargs["on_samples"](message.samples)
        app._consume_chat_queue()

        log = app.chat_log.get("1.0", "end")
        assert "Разрыв потока (pause)" in log
        assert "Принято: до разрыва" in log
    finally:
        root.destroy()


def test_gui_playback_pauses_capture_until_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = playback.FakePlaybackEngine(wait_for_stop=True)
    root, app = build_app(tmp_path, engine=engine)
    try:
        capture = start_fake_listening(app, monkeypatch)
        app.chat_text_var.set("полудуплекс")
        app.send_chat_text()

        wait_for(root, lambda: engine.is_playing)
        assert capture.paused is True
        assert "Приём приостановлен" in app.chat_status_label.cget("text")

        app.request_chat_stop()
        wait_for(root, lambda: not app.chat_playing)
        assert capture.paused is False
        assert "остановлен" in app.chat_status_label.cget("text")
    finally:
        root.destroy()


def test_gui_close_stops_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, app = build_app(tmp_path)
    capture = start_fake_listening(app, monkeypatch)
    try:
        app._on_close()
        assert capture.stopped
        assert app.chat_capture is None
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001 - окно уже закрыто
            pass
