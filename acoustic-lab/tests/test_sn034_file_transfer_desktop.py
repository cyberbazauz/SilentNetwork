"""SN-034: Windows Desktop Lab — передача файла через звук.

Физический звук не используется. Воспроизведение подменяется
`FakePlaybackEngine`, запись — детерминированной функцией, которая отдаёт
ранее сгенерированный WAV или шум. Успешный файловый round-trip здесь не
объявляется передачей через воздух.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import desktop_app
from snlab import audio, file_transfer, file_transfer_desktop, playback
from snlab.config import PROFILE_DATA_AUDIBLE
from snlab.wavio import read_wav


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


def samples_for_file(
    tmp_path: Path, content: bytes = b"PING", *, filename: str = "note.txt"
) -> np.ndarray:
    source = tmp_path / filename
    source.write_bytes(content)
    wav_path = tmp_path / "source.wav"
    file_transfer.encode_file_to_wav(
        source, PROFILE_DATA_AUDIBLE, wav_path, transfer_id=7, lead_silence_s=0.25
    )
    data, sample_rate, channels = read_wav(wav_path)
    assert sample_rate == PROFILE_DATA_AUDIBLE.sample_rate
    assert channels == 1
    return np.asarray(data[:, 0], dtype=np.float64)


def write_source(tmp_path: Path, content: bytes = b"PING") -> Path:
    source = tmp_path / "note.txt"
    source.write_bytes(content)
    return source


# --- backend: генерация ----------------------------------------------------


def test_generate_file_message_writes_wav_and_json(tmp_path: Path) -> None:
    generated = file_transfer_desktop.generate_file_message(
        write_source(tmp_path, b"hello"), "data-audible", tmp_path, transfer_id=7
    )

    assert generated.wav_path.is_file()
    assert generated.metadata_path.is_file()
    assert generated.profile.name == "data-audible"
    assert generated.filename == "note.txt"
    assert generated.file_size == 5
    assert generated.sha256 == hashlib.sha256(b"hello").hexdigest()
    assert generated.wav_sha256 != generated.sha256
    assert generated.report["result"] == "ok"
    assert generated.report["crc_is_authentication"] is False
    assert generated.packet_count == 4
    assert generated.total_chunks == 1

    metadata = json.loads(generated.metadata_path.read_text(encoding="utf-8"))
    assert metadata["schema"] == file_transfer_desktop.TX_SCHEMA
    assert metadata["contract_schema"] == file_transfer.REPORT_SCHEMA
    assert metadata["task"] == "SN-034"
    assert metadata["direction"] == "tx"
    assert metadata["source"]["sha256"] == generated.sha256
    assert metadata["file"]["sha256"] == generated.wav_sha256

    received = file_transfer.decode_wav_to_file(
        generated.wav_path, PROFILE_DATA_AUDIBLE, tmp_path / "received"
    )
    assert received["result"] == "ok"
    assert (tmp_path / "received" / "note.txt").read_bytes() == b"hello"


def test_generate_file_message_rejects_missing_and_bad_files(
    tmp_path: Path,
) -> None:
    with pytest.raises(file_transfer_desktop.FileDesktopError):
        file_transfer_desktop.generate_file_message(
            tmp_path / "missing.bin", "data-audible", tmp_path
        )
    with pytest.raises(file_transfer_desktop.FileDesktopError):
        file_transfer_desktop.generate_file_message(
            write_source(tmp_path), "bfsk-18k", tmp_path
        )
    huge = tmp_path / "huge.bin"
    huge.write_bytes(b"X" * (file_transfer.MAX_FILE_BYTES + 1))
    with pytest.raises(file_transfer.FileTooLargeError):
        file_transfer_desktop.generate_file_message(
            huge, "data-audible", tmp_path
        )
    with pytest.raises(file_transfer_desktop.FileDesktopError):
        file_transfer_desktop.get_data_profile("bfsk-18k")


def test_generate_file_message_is_deterministic_with_fixed_transfer_id(
    tmp_path: Path,
) -> None:
    source = write_source(tmp_path, b"deterministic")
    first = file_transfer_desktop.generate_file_message(
        source, "data-audible", tmp_path, transfer_id=11, timestamp="fixed-1"
    )
    second = file_transfer_desktop.generate_file_message(
        source, "data-audible", tmp_path, transfer_id=11, timestamp="fixed-2"
    )
    assert first.wav_sha256 == second.wav_sha256
    assert first.wav_path != second.wav_path


# --- backend: PLAY ---------------------------------------------------------


def test_play_file_message_requires_hardware_test(tmp_path: Path) -> None:
    generated = file_transfer_desktop.generate_file_message(
        write_source(tmp_path), "data-audible", tmp_path
    )
    engine = playback.FakePlaybackEngine()
    with pytest.raises(file_transfer_desktop.FilePlaybackDisabled):
        file_transfer_desktop.play_file_message(
            generated, make_output_device(), engine=engine, hardware_test=False
        )
    assert engine.calls == []


def test_play_file_message_records_playback(tmp_path: Path) -> None:
    generated = file_transfer_desktop.generate_file_message(
        write_source(tmp_path, b"hello"), "data-audible", tmp_path
    )
    engine = playback.FakePlaybackEngine()
    record = file_transfer_desktop.play_file_message(
        generated,
        make_output_device(),
        engine=engine,
        hardware_test=True,
        timestamp="fixed",
    )

    assert record.performed is True
    assert record.status == "completed"
    assert record.frames_total == int(round(generated.file_duration_s * 48_000))
    assert record.json_path.is_file()
    payload = json.loads(record.json_path.read_text(encoding="utf-8"))
    assert payload["schema"] == file_transfer_desktop.PLAYBACK_SCHEMA
    assert payload["direction"] == "tx"
    assert payload["file"]["filename"] == "note.txt"
    assert payload["file"]["sha256"] == generated.sha256
    assert payload["file"]["wav_sha256"] == generated.wav_sha256
    assert payload["sha256_is_authentication"] is False
    assert payload["playback"]["engine"] == "FakePlaybackEngine"


def test_play_file_message_rejects_input_and_changed_wav(tmp_path: Path) -> None:
    generated = file_transfer_desktop.generate_file_message(
        write_source(tmp_path), "data-audible", tmp_path
    )
    engine = playback.FakePlaybackEngine()
    with pytest.raises(file_transfer_desktop.FileDesktopError):
        file_transfer_desktop.play_file_message(
            generated, make_input_device(), engine=engine, hardware_test=True
        )

    changed = Path(generated.wav_path).read_bytes() + b"\x00"
    Path(generated.wav_path).write_bytes(changed)
    with pytest.raises(file_transfer_desktop.FileDesktopError):
        file_transfer_desktop.play_file_message(
            generated, make_output_device(), engine=engine, hardware_test=True
        )
    assert engine.calls == []


# --- backend: приём --------------------------------------------------------


def test_receive_file_decodes_recorded_signal(tmp_path: Path) -> None:
    samples = samples_for_file(tmp_path, b"payload-123")
    received = file_transfer_desktop.receive_file(
        "data-audible",
        make_input_device(),
        duration_s=15.0,
        results_dir=tmp_path,
        recorder=lambda **_: make_recording(samples),
        timestamp="fixed",
    )

    assert received.ok is True
    assert received.filename == "note.txt"
    assert received.error_type is None
    assert received.wav_path.is_file()
    assert received.output_path is not None
    assert Path(received.output_path).read_bytes() == b"payload-123"
    payload = json.loads(received.json_path.read_text(encoding="utf-8"))
    assert payload["schema"] == file_transfer_desktop.RECEIVE_SCHEMA
    assert payload["contract_schema"] == file_transfer.REPORT_SCHEMA
    assert payload["direction"] == "rx"
    assert payload["sha256_is_authentication"] is False
    assert payload["capture"]["sample_rate_hz"] == 48_000
    assert payload["file"]["size"] == 11


def test_receive_file_uses_unique_subdirectory(tmp_path: Path) -> None:
    samples = samples_for_file(tmp_path, b"same-name")
    for stamp in ("first", "second"):
        received = file_transfer_desktop.receive_file(
            "data-audible",
            make_input_device(),
            duration_s=15.0,
            results_dir=tmp_path,
            recorder=lambda **_: make_recording(samples),
            timestamp=stamp,
        )
        assert received.ok is True
    assert Path(received.output_path).parent.name.startswith("file-rx-")


def test_receive_file_reports_signal_not_found(tmp_path: Path) -> None:
    rng = np.random.default_rng(20261104)
    noise = rng.normal(0.0, 0.01, 48_000).astype(np.float64)
    received = file_transfer_desktop.receive_file(
        "data-audible",
        make_input_device(),
        duration_s=3.0,
        results_dir=tmp_path,
        recorder=lambda **_: make_recording(noise),
    )
    assert received.ok is False
    assert received.error_type == "signal_not_found"
    assert received.filename is None
    assert received.json_path.is_file()


def test_receive_file_validates_inputs(tmp_path: Path) -> None:
    with pytest.raises(file_transfer_desktop.FileDesktopError):
        file_transfer_desktop.receive_file(
            "data-audible", make_output_device(), results_dir=tmp_path
        )
    with pytest.raises(file_transfer_desktop.FileDesktopError):
        file_transfer_desktop.receive_file(
            "data-audible", make_input_device(), 0.1, tmp_path
        )
    with pytest.raises(file_transfer_desktop.FileDesktopError):
        file_transfer_desktop.receive_file(
            "data-audible", make_input_device(), 601.0, tmp_path
        )
    with pytest.raises(file_transfer_desktop.FileDesktopError):
        file_transfer_desktop.receive_file(
            "bfsk-18k", make_input_device(), 3.0, tmp_path
        )


def test_receive_file_rejects_wrong_sample_rate(tmp_path: Path) -> None:
    samples = np.zeros(48_000, dtype=np.float64)
    with pytest.raises(file_transfer_desktop.FileDesktopError):
        file_transfer_desktop.receive_file(
            "data-audible",
            make_input_device(),
            duration_s=1.0,
            results_dir=tmp_path,
            recorder=lambda **_: make_recording(samples, sample_rate=44_100),
        )


def test_recommended_record_seconds_has_bounds() -> None:
    assert file_transfer_desktop.recommended_record_seconds(10.0) == 13.0
    assert file_transfer_desktop.recommended_record_seconds(0.0) == 3.0
    assert (
        file_transfer_desktop.recommended_record_seconds(599.0)
        == file_transfer_desktop.MAX_RECORD_SECONDS
    )
    with pytest.raises(file_transfer_desktop.FileDesktopError):
        file_transfer_desktop.recommended_record_seconds(-1.0)


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


def test_gui_file_sections_are_wired(tmp_path: Path) -> None:
    root, app = build_app(tmp_path)
    try:
        assert str(app.file_play_button.cget("state")) == "disabled"
        assert app.file_profile_var.get() == "data-audible"
        assert app.file_selected_input is not None
        assert app.file_selected_input.index == 7
        assert app.file_input_box.get().startswith("[7]")
        assert app.file_generated is None
    finally:
        root.destroy()


def test_gui_generate_and_play_file(tmp_path: Path) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, engine=engine)
    try:
        source = write_source(tmp_path, b"UI-file")
        app.file_source_var.set(str(source))
        app.generate_file_message()
        assert app.file_generated is not None
        assert str(app.file_play_button.cget("state")) == "normal"
        assert "note.txt" in app.file_generated_info.cget("text")

        assert app.request_file_playback() is True
        wait_for(
            root,
            lambda: app.file_play_status.cget("text").startswith("PLAY"),
        )
        assert app.file_playing is False
        assert app.file_play_status.cget("text").startswith("PLAY завершён")
        assert "JSON:" in app.file_play_json.cget("text")
        assert engine.calls

        json_path = Path(app.file_play_json.cget("text").split("JSON: ", 1)[1])
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["file"]["filename"] == "note.txt"
        assert payload["file"]["sha256"] == hashlib.sha256(b"UI-file").hexdigest()
    finally:
        root.destroy()


def test_gui_generate_file_shows_error_for_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        desktop_app.messagebox, "showerror", lambda *args, **kwargs: None
    )
    root, app = build_app(tmp_path)
    try:
        app.file_source_var.set(str(tmp_path / "missing.bin"))
        app.generate_file_message()
        assert app.file_generated is None
        assert "не создан" in app.file_generated_info.cget("text").lower()
    finally:
        root.destroy()


def test_gui_record_and_build_file(tmp_path: Path) -> None:
    samples = samples_for_file(tmp_path, b"from-air")
    root, app = build_app(
        tmp_path, recorder=lambda **_: make_recording(samples)
    )
    try:
        app.file_duration_var.set("15")
        app.record_and_decode_file()
        wait_for(
            root,
            lambda: not app.file_recording and app.file_received is not None,
        )
        assert app.file_received.ok is True
        assert app.file_verdict_label.cget("text") == "Файл принят"
        info = app.file_received_info.cget("text")
        assert "note.txt" in info
        assert hashlib.sha256(b"from-air").hexdigest() in info
        assert "JSON:" in app.file_record_json.cget("text")
    finally:
        root.destroy()


def test_gui_record_file_shows_human_error(tmp_path: Path) -> None:
    rng = np.random.default_rng(17)
    noise = rng.normal(0.0, 0.01, 48_000).astype(np.float64)
    root, app = build_app(
        tmp_path, recorder=lambda **_: make_recording(noise)
    )
    try:
        app.file_duration_var.set("3")
        app.record_and_decode_file()
        wait_for(
            root,
            lambda: not app.file_recording and app.file_received is not None,
        )
        assert app.file_received.ok is False
        assert app.file_verdict_label.cget("text") == "Сигнал не найден"
        assert "преамбул" in app.file_verdict_hint.cget("text").lower()
    finally:
        root.destroy()


def test_gui_file_record_rejects_bad_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        desktop_app.messagebox, "showerror", lambda *args, **kwargs: None
    )
    root, app = build_app(tmp_path, recorder=lambda **_: None)
    try:
        app.file_duration_var.set("abc")
        app.record_and_decode_file()
        assert app.file_received is None
        assert app.file_recording is False
    finally:
        root.destroy()


def test_describe_file_error_covers_known_types() -> None:
    known = [
        "signal_not_found",
        "not_file_transfer",
        "incomplete_transfer",
        "transfer_conflict",
        "hash_mismatch",
        "manifest_invalid",
        "packet_invalid",
        "filename_invalid",
        "file_too_large",
        "output_error",
        "wav_format_error",
        "wav_not_found",
        "sync_mismatch",
        "crc_mismatch",
        "incomplete_frame",
    ]
    for error_type in known:
        title, hint = desktop_app.DesktopApp._describe_file_error(
            {"error_type": error_type, "message": "детали"}
        )
        assert title
        assert "детали" in hint
    title, _ = desktop_app.DesktopApp._describe_file_error(
        {"error_type": "unknown"}
    )
    assert title == "Файл не принят"

