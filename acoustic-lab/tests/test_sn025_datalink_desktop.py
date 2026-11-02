"""SN-025: Windows Desktop Lab — передача и приём текста через звук.

Физический звук не используется. Воспроизведение подменяется
`FakePlaybackEngine`, запись — детерминированной функцией, которая отдаёт
ранее сгенерированный WAV или шум. Успешный файловый round-trip здесь не
объявляется передачей через воздух.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import desktop_app
from snlab import audio, datalink_desktop, playback
from snlab.config import PROFILE_BFSK_18K, PROFILE_DATA_AUDIBLE
from snlab.datalink import decode_wav_to_text, encode_text_to_wav
from snlab.wavio import read_wav, write_pcm16


def make_output_device(index: int = 3, *, default: bool = True) -> audio.DeviceInfo:
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
        is_default_output=default,
    )


def make_input_device(index: int = 7, *, default: bool = True) -> audio.DeviceInfo:
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
        is_default_input=default,
    )


def make_recording(samples: np.ndarray, sample_rate: int = 48_000) -> audio.Recording:
    return audio.Recording(
        samples=samples,
        sample_rate=sample_rate,
        device_index=7,
        device_name="Microphone (USB Audio)",
        frames_requested=int(samples.size),
        frames_captured=int(samples.size),
        overflows=0,
        wall_time_s=float(samples.size / sample_rate),
    )


def samples_for_text(tmp_path: Path, text: str = "PING") -> np.ndarray:
    path = tmp_path / "source.wav"
    encode_text_to_wav(text, PROFILE_DATA_AUDIBLE, path, lead_silence_s=0.25)
    data, sample_rate, channels = read_wav(path)
    assert sample_rate == PROFILE_DATA_AUDIBLE.sample_rate
    assert channels == 1
    return np.asarray(data[:, 0], dtype=np.float64)


# --- backend: генерация ----------------------------------------------------


def test_generate_data_message_writes_wav_and_json(tmp_path: Path) -> None:
    generated = datalink_desktop.generate_data_message(
        "Проверка связи", "data-audible", tmp_path
    )

    assert generated.wav_path.is_file()
    assert generated.metadata_path.is_file()
    assert generated.profile.name == "data-audible"
    assert generated.text == "Проверка связи"
    assert generated.report["result"] == "ok"
    assert generated.report["payload_bytes"] == len("Проверка связи".encode("utf-8"))
    assert generated.report["crc_is_authentication"] is False

    metadata = json.loads(generated.metadata_path.read_text(encoding="utf-8"))
    assert metadata["task"] == "SN-025"
    assert metadata["direction"] == "tx"
    assert metadata["file"]["sha256"] == generated.sha256

    decoded = decode_wav_to_text(generated.wav_path, PROFILE_DATA_AUDIBLE)
    assert decoded["result"] == "ok"
    assert decoded["text"] == "Проверка связи"


def test_generate_data_message_rejects_bad_input(tmp_path: Path) -> None:
    with pytest.raises(datalink_desktop.datalink.DataLinkPayloadError):
        datalink_desktop.generate_data_message("", "data-audible", tmp_path)
    with pytest.raises(datalink_desktop.datalink.DataLinkPayloadError):
        datalink_desktop.generate_data_message("A" * 65, "data-audible", tmp_path)
    with pytest.raises(datalink_desktop.DataDesktopError):
        datalink_desktop.generate_data_message("A", "bfsk-18k", tmp_path)
    with pytest.raises(datalink_desktop.DataDesktopError):
        datalink_desktop.get_data_profile("bfsk-18k")


def test_generate_data_message_is_deterministic_in_frame(tmp_path: Path) -> None:
    first = datalink_desktop.generate_data_message(
        "SILENTNETWORK", "data-audible", tmp_path, timestamp="fixed-1"
    )
    second = datalink_desktop.generate_data_message(
        "SILENTNETWORK", "data-audible", tmp_path, timestamp="fixed-2"
    )
    assert first.report["payload_hex"] == second.report["payload_hex"]
    assert first.report["frame"]["crc16"] == second.report["frame"]["crc16"]
    assert first.report["signal"]["pcm_sha256"] == second.report["signal"]["pcm_sha256"]
    assert first.wav_path != second.wav_path


# --- backend: PLAY ---------------------------------------------------------


def test_play_data_message_requires_hardware_test(tmp_path: Path) -> None:
    generated = datalink_desktop.generate_data_message("PING", "data-audible", tmp_path)
    engine = playback.FakePlaybackEngine()
    with pytest.raises(datalink_desktop.DataPlaybackDisabled):
        datalink_desktop.play_data_message(
            generated, make_output_device(), engine=engine, hardware_test=False
        )
    assert engine.calls == []


def test_play_data_message_records_playback(tmp_path: Path) -> None:
    generated = datalink_desktop.generate_data_message("PING", "data-audible", tmp_path)
    engine = playback.FakePlaybackEngine()
    record = datalink_desktop.play_data_message(
        generated,
        make_output_device(),
        engine=engine,
        hardware_test=True,
        timestamp="fixed",
    )

    assert record.performed is True
    assert record.status == "completed"
    assert record.frames_total == generated.frames
    assert record.json_path.is_file()
    payload = json.loads(record.json_path.read_text(encoding="utf-8"))
    assert payload["schema"] == datalink_desktop.PLAYBACK_SCHEMA
    assert payload["direction"] == "tx"
    assert payload["message"]["text"] == "PING"
    assert payload["crc_is_authentication"] is False
    assert payload["playback"]["engine"] == "FakePlaybackEngine"


def test_play_data_message_rejects_output_and_changed_file(tmp_path: Path) -> None:
    generated = datalink_desktop.generate_data_message("PING", "data-audible", tmp_path)
    engine = playback.FakePlaybackEngine()
    with pytest.raises(datalink_desktop.DataDesktopError):
        datalink_desktop.play_data_message(
            generated, make_input_device(), engine=engine, hardware_test=True
        )

    changed = Path(generated.wav_path).read_bytes() + b"\x00"
    Path(generated.wav_path).write_bytes(changed)
    with pytest.raises(datalink_desktop.DataDesktopError):
        datalink_desktop.play_data_message(
            generated, make_output_device(), engine=engine, hardware_test=True
        )
    assert engine.calls == []


# --- backend: приём --------------------------------------------------------


def test_receive_data_decodes_recorded_signal(tmp_path: Path) -> None:
    samples = samples_for_text(tmp_path, "Привет")
    received = datalink_desktop.receive_data(
        "data-audible",
        make_input_device(),
        duration_s=5.0,
        results_dir=tmp_path,
        recorder=lambda **_: make_recording(samples),
        timestamp="fixed",
    )

    assert received.ok is True
    assert received.text == "Привет"
    assert received.error_type is None
    assert received.wav_path.is_file()
    payload = json.loads(received.json_path.read_text(encoding="utf-8"))
    assert payload["schema"] == datalink_desktop.RECEIVE_SCHEMA
    assert payload["direction"] == "rx"
    assert payload["crc_is_authentication"] is False
    assert payload["capture"]["sample_rate_hz"] == 48_000


def test_receive_data_reports_signal_not_found(tmp_path: Path) -> None:
    rng = np.random.default_rng(20261102)
    noise = rng.normal(0.0, 0.01, 48_000).astype(np.float64)
    received = datalink_desktop.receive_data(
        "data-audible",
        make_input_device(),
        duration_s=3.0,
        results_dir=tmp_path,
        recorder=lambda **_: make_recording(noise),
    )
    assert received.ok is False
    assert received.error_type == "signal_not_found"
    assert received.text is None
    assert received.json_path.is_file()


def test_receive_data_validates_inputs(tmp_path: Path) -> None:
    with pytest.raises(datalink_desktop.DataDesktopError):
        datalink_desktop.receive_data(
            "data-audible", make_output_device(), results_dir=tmp_path
        )
    with pytest.raises(datalink_desktop.DataDesktopError):
        datalink_desktop.receive_data(
            "data-audible", make_input_device(), 0.1, tmp_path
        )
    with pytest.raises(datalink_desktop.DataDesktopError):
        datalink_desktop.receive_data(
            "bfsk-18k", make_input_device(), 3.0, tmp_path
        )


def test_receive_data_rejects_wrong_sample_rate(tmp_path: Path) -> None:
    samples = np.zeros(48_000, dtype=np.float64)
    with pytest.raises(datalink_desktop.DataDesktopError):
        datalink_desktop.receive_data(
            "data-audible",
            make_input_device(),
            duration_s=1.0,
            results_dir=tmp_path,
            recorder=lambda **_: make_recording(samples, sample_rate=44_100),
        )


# --- GUI -------------------------------------------------------------------


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


def build_app(
    tmp_path: Path,
    *,
    engine: Any | None = None,
    recorder: Any | None = None,
    hardware_test: bool = True,
) -> Any:
    root = make_root()
    app = desktop_app.DesktopApp(
        root,
        hardware_test=hardware_test,
        results_dir=tmp_path / "results",
        engine=engine if engine is not None else playback.FakePlaybackEngine(),
        device_lister=lambda: [make_output_device()],
        input_device_lister=lambda: [make_input_device()],
        recorder=recorder,
        confirm_playback=lambda tone, device: True,
        confirm_data_playback=lambda generated, device: True,
    )
    return root, app


def wait_for(root: Any, condition: Any, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("условие не выполнилось за отведённое время")


def test_gui_data_sections_are_wired(tmp_path: Path) -> None:
    root, app = build_app(tmp_path)
    try:
        assert str(app.data_play_button.cget("state")) == "disabled"
        assert app.data_profile_var.get() == "data-audible"
        assert app.data_selected_input is not None
        assert app.data_selected_input.index == 7
        assert app.selected_device is not None
        assert app.selected_device.index == 3
        assert app.data_input_box.get().startswith("[7]")
    finally:
        root.destroy()


def test_gui_generate_and_play_data_message(tmp_path: Path) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        app.data_text_var.set("SILENTNETWORK")
        app.generate_data()
        assert app.data_generated is not None
        assert str(app.data_play_button.cget("state")) == "normal"

        assert app.request_data_playback() is True
        wait_for(root, lambda: app.data_play_status.cget("text").startswith("PLAY"))
        assert app.data_playing is False
        assert app.data_play_status.cget("text").startswith("PLAY завершён")
        assert "JSON:" in app.data_play_json.cget("text")
        assert engine.calls

        json_path = Path(app.data_play_json.cget("text").split("JSON: ", 1)[1])
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["message"]["text"] == "SILENTNETWORK"
    finally:
        root.destroy()


def test_gui_generate_shows_error_for_empty_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        desktop_app.messagebox, "showerror", lambda *args, **kwargs: None
    )
    root, app = build_app(tmp_path)
    try:
        app.data_text_var.set("")
        app.generate_data()
        assert app.data_generated is None
        assert "не создано" in app.data_generated_info.cget("text").lower()
    finally:
        root.destroy()


def test_gui_record_and_decode_shows_text(tmp_path: Path) -> None:
    samples = samples_for_text(tmp_path, "PING")
    root, app = build_app(
        tmp_path, recorder=lambda **_: make_recording(samples)
    )
    try:
        app.data_duration_var.set("5")
        app.record_and_decode()
        wait_for(root, lambda: not app.data_recording and app.data_received is not None)
        assert app.data_received.ok is True
        assert app.data_verdict_label.cget("text") == "Сообщение принято"
        assert app.data_received_text.cget("text") == "PING"
        assert "JSON:" in app.data_record_json.cget("text")
    finally:
        root.destroy()


def test_gui_record_and_decode_shows_human_error(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    noise = rng.normal(0.0, 0.01, 48_000).astype(np.float64)
    root, app = build_app(
        tmp_path, recorder=lambda **_: make_recording(noise)
    )
    try:
        app.data_duration_var.set("3")
        app.record_and_decode()
        wait_for(root, lambda: not app.data_recording and app.data_received is not None)
        assert app.data_received.ok is False
        assert app.data_verdict_label.cget("text") == "Сигнал не найден"
        assert "(преамбула" in app.data_verdict_hint.cget("text").lower()
    finally:
        root.destroy()


def test_gui_record_rejects_bad_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        desktop_app.messagebox, "showerror", lambda *args, **kwargs: None
    )
    root, app = build_app(tmp_path, recorder=lambda **_: None)
    try:
        app.data_duration_var.set("abc")
        app.record_and_decode()
        assert app.data_received is None
        assert app.data_recording is False
    finally:
        root.destroy()


def test_describe_data_error_covers_known_types() -> None:
    for error_type in datalink_desktop.datalink.DATA_LINK_ERROR_TYPES:
        title, hint = desktop_app.DesktopApp._describe_data_error(
            {"error_type": error_type, "message": "детали"}
        )
        assert title
        assert "детали" in hint
    title, _ = desktop_app.DesktopApp._describe_data_error({"error_type": "unknown"})
    assert title == "Сообщение не принято"
