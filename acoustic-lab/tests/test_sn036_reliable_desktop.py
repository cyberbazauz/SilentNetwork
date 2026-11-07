"""SN-036: ART/1 в Windows Desktop Lab.

Физический звук не используется: захват подменяется `FakeCapture`, а
воспроизведение — `FakePlaybackEngine`. Синтетический PCM доказывает только
работу интеграции, а не передачу через воздух. CRC-16 и SHA-256 не
аутентифицируют отправителя.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import desktop_app
from snlab import audio, playback, reliable, reliable_chat
from snlab.chat import StreamingFrameDecoder
from snlab.config import PROFILE_DATA_AUDIBLE
from snlab.reliable import PACKET_STATUS, ReliableReceiver, TransportConfig
from snlab.reliable_chat import config_for_profile, encode_transport_reply

PROFILE = PROFILE_DATA_AUDIBLE


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


def decode_round(samples: np.ndarray) -> list[Any]:
    """Разобрать PCM раунда потоковым декодером чата."""
    decoder = StreamingFrameDecoder(PROFILE)
    packets = []
    for index in range(0, samples.size, 4_800):
        for frame in decoder.feed(samples[index : index + 4_800]):
            packets.append(reliable.TransportPacket.parse(frame.payload))
    for frame in decoder.flush():
        packets.append(reliable.TransportPacket.parse(frame.payload))
    return packets


# --- элементы вкладки -------------------------------------------------------


def test_gui_reliable_checkbox_defaults_off(tmp_path: Path) -> None:
    root, app = build_app(tmp_path)
    try:
        assert app.chat_reliable_var.get() is False
        assert str(app.chat_reliable_check.cget("text")).startswith(
            "Подтверждать доставку"
        )
        assert str(app.chat_reliable_check.cget("state")) == "normal"
    finally:
        root.destroy()


def test_gui_reliable_requires_listening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        app.chat_reliable_var.set(True)
        app.chat_text_var.set("без микрофона")
        app.send_chat_text()

        assert engine.calls == []
        assert app.chat_reliable_controller is None
        assert "Включите прослушивание микрофона" in app.chat_status_label.cget(
            "text"
        )
    finally:
        root.destroy()


def test_gui_listening_starts_transport_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, app = build_app(tmp_path)
    try:
        app.chat_reliable_var.set(True)
        start_fake_listening(app, monkeypatch)
        assert app.chat_session is not None
        assert app.chat_session.transport_enabled is True

        app.chat_reliable_var.set(False)
        app._on_chat_reliable_toggled()
        assert app.chat_capture is not None
        assert app.chat_session is not None
        assert app.chat_session.transport_enabled is False
        app.stop_chat_listening()
    finally:
        root.destroy()


# --- отправка с подтверждением ---------------------------------------------


def test_gui_reliable_text_is_confirmed_by_peer_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        app.chat_reliable_var.set(True)
        capture = start_fake_listening(app, monkeypatch)
        app.chat_text_var.set("art text")
        app.send_chat_text()

        wait_for(root, lambda: not app.chat_playing)
        assert engine.calls, "раунд ART/1 не был передан в движок"
        assert app.chat_reliable_controller is not None
        assert app.chat_reliable_controller.transfer_id >= 1

        # Приёмник на другой стороне: разбираем реальный PCM раунда.
        packets = decode_round(engine.calls[0]["samples"])
        receiver = ReliableReceiver(config_for_profile(PROFILE))
        for packet in packets:
            receiver.handle_packet(packet, 0)
        assert receiver.complete is True
        assert receiver.delivered == b"art text"
        status = receiver.poll(30_000)
        assert status is not None
        assert status.packet_type == PACKET_STATUS

        reply = encode_transport_reply(status, PROFILE)
        capture.kwargs["on_samples"](reply.samples)
        app._consume_chat_queue()

        assert app.chat_reliable_controller is None
        assert app.chat_playing is False
        log = app.chat_log.get("1.0", "end")
        assert "ART/1: доставка подтверждена" in log
        assert "Подлинность отправителя не проверялась" in (
            app.chat_status_label.cget("text")
        )
    finally:
        root.destroy()


def test_gui_reliable_round_is_not_reported_done_without_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        app.chat_reliable_var.set(True)
        start_fake_listening(app, monkeypatch)
        app.chat_text_var.set("no status")
        app.send_chat_text()

        wait_for(root, lambda: not app.chat_playing)
        assert app.chat_reliable_controller is not None
        app._poll_chat_transport()
        log = app.chat_log.get("1.0", "end")
        assert "доставка подтверждена" not in log
        assert "ожидаю STATUS" in app.chat_status_label.cget("text")
    finally:
        root.destroy()


# --- приём и автоматический ответ STATUS -----------------------------------


def test_gui_receiving_round_plays_status_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        # Ускоряем окно ответа, чтобы тест не ждал секунды реального времени.
        fast = TransportConfig(
            window=12,
            symbol_rate=8_000.0,
            inter_frame_gap_ms=0,
            ack_guard_ms=10,
            ack_margin_ms=10,
            turnaround_ms=0,
        )
        monkeypatch.setattr(
            desktop_app.reliable_chat,
            "config_for_profile",
            lambda profile: fast,
        )
        app.chat_reliable_var.set(True)
        capture = start_fake_listening(app, monkeypatch)

        peer = reliable.ReliableSender(b"hi", 21, session_id=0x1234)
        action = peer.advance(0)
        assert action.round is not None
        audio_round = reliable_chat.encode_transport_round(
            action.round, PROFILE
        )
        capture.kwargs["on_samples"](audio_round.samples)
        app._consume_chat_queue()

        wait_for(root, lambda: bool(engine.calls), timeout_s=5.0)
        log = app.chat_log.get("1.0", "end")
        assert "ART/1: ответ status" in log
        assert "ART/1: приём" in log
        app.stop_chat_listening()
    finally:
        root.destroy()


def test_gui_reliable_cancel_between_rounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        app.chat_reliable_var.set(True)
        start_fake_listening(app, monkeypatch)
        app.chat_text_var.set("cancel me")
        app.send_chat_text()

        wait_for(root, lambda: not app.chat_playing)
        assert app.chat_reliable_controller is not None
        app.request_chat_stop()

        assert app.chat_reliable_controller is None
        log = app.chat_log.get("1.0", "end")
        assert "ART/1: отменено пользователем" in log
    finally:
        root.destroy()
