"""Тесты Windows Desktop Lab SN-015: файлы, анализ, playback-заглушка, GUI.

Физическое аудио не используется: все устройства и движки подменяются
детерминированными объектами, WAV создаются синтетически.
"""

from __future__ import annotations

import json
import math
import threading
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import desktop_app
from snlab import analysis, audio, desktop, playback
from snlab.reporting import write_json
from snlab.wavio import WavFormatError, read_wav, write_pcm16


def make_device(
    index: int = 3,
    *,
    name: str = "Speaker (Realtek(R) Audio)",
    output: int = 2,
    input_channels: int = 0,
) -> audio.DeviceInfo:
    return audio.DeviceInfo(
        index=index,
        name=name,
        hostapi=0,
        hostapi_name="MME",
        max_input_channels=input_channels,
        max_output_channels=output,
        default_samplerate=48_000.0,
        default_low_input_latency=0.01,
        default_low_output_latency=0.01,
    )


def make_tone(
    tmp_path: Path,
    *,
    frequency_hz: float = 1_000.0,
    sample_rate: int = 48_000,
    duration_s: float = 0.5,
    amplitude: float = 0.1,
    lead_ms: float = 50.0,
    tail_ms: float = 50.0,
    timestamp: str | None = None,
) -> desktop.GeneratedTone:
    settings = desktop.ToneSettings(
        frequency_hz=frequency_hz,
        sample_rate=sample_rate,
        duration_s=duration_s,
        amplitude=amplitude,
        ramp_ms=5.0,
        lead_ms=lead_ms,
        tail_ms=tail_ms,
    )
    return desktop.generate_tone(settings, tmp_path, timestamp=timestamp)


def write_sine(
    path: Path,
    *,
    frequency_hz: float = 1_000.0,
    sample_rate: int = 48_000,
    duration_s: float = 1.0,
    amplitude: float = 0.2,
    noise: float = 0.0,
    seed: int = 7,
) -> Path:
    time_axis = np.arange(int(round(duration_s * sample_rate))) / sample_rate
    signal = amplitude * np.sin(2.0 * math.pi * frequency_hz * time_axis)
    if noise:
        rng = np.random.default_rng(seed)
        signal = signal + noise * rng.standard_normal(signal.size)
    return write_pcm16(path, signal, sample_rate)


# --------------------------------------------------------------- generation


def test_tone_settings_defaults() -> None:
    settings = desktop.ToneSettings()

    assert settings.frequency_hz == 1_000.0
    assert settings.sample_rate == 48_000
    assert settings.duration_s == 3.0
    assert settings.amplitude == 0.10
    assert settings.ramp_ms == 5.0

    plan = settings.plan()
    assert plan.channels == 1
    assert plan.frequencies_hz == (1_000.0,)
    assert plan.tone_ms == 3_000.0
    assert plan.gap_ms == 0.0
    assert settings.total_samples == plan.total_samples
    assert settings.file_duration_s == pytest.approx(
        plan.total_samples / 48_000, rel=0, abs=1e-12
    )


def test_generate_tone_writes_pcm16_wav_and_json(tmp_path: Path) -> None:
    tone = make_tone(tmp_path, frequency_hz=1_000.0, amplitude=0.1)

    assert tone.wav_path.is_file()
    assert tone.metadata_path.is_file()
    assert tone.wav_path.parent == tmp_path
    assert tone.frames == tone.settings.total_samples

    data, sample_rate, channels = read_wav(tone.wav_path)
    assert sample_rate == 48_000
    assert channels == 1
    assert data.shape == (tone.frames, 1)
    start = tone.settings.plan().tone_start_sample(0)
    stop = tone.settings.plan().tone_end_sample(0)
    tone_data = data[start:stop, 0]
    # Амплитуда 0.1 → RMS примерно 0.1 / sqrt(2) с учётом PCM-округления.
    rms = float(np.sqrt(np.mean(np.square(tone_data, dtype=np.float64))))
    assert rms == pytest.approx(0.1 / math.sqrt(2.0), rel=0.02)
    assert float(np.max(np.abs(tone_data))) <= 0.101

    payload = json.loads(tone.metadata_path.read_text(encoding="utf-8"))
    assert payload["schema"] == desktop.GENERATION_SCHEMA
    assert payload["task"] == "SN-015"
    assert payload["mode"] == "file_only"
    assert payload["tone"]["frequency_hz"] == 1_000.0
    assert payload["tone"]["sample_rate_hz"] == 48_000
    assert payload["tone"]["duration_s"] == 0.5
    assert payload["tone"]["channels"] == 1
    assert payload["file"]["sha256"] == tone.sha256
    assert payload["file"]["frames"] == tone.frames
    assert payload["file"]["format"] == "pcm_s16le"
    assert payload["playback"]["performed"] is False
    assert payload["playback"]["status"] == "not_requested"


def test_generated_tone_is_deterministic(tmp_path: Path) -> None:
    first = make_tone(tmp_path, frequency_hz=18_000.0, timestamp="fixed")
    second = make_tone(tmp_path, frequency_hz=18_000.0, timestamp="second")

    assert first.sha256 == second.sha256
    assert first.wav_path.read_bytes() == second.wav_path.read_bytes()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"frequency_hz": 0.0},
        {"frequency_hz": 10_000.0, "sample_rate": 8_000},
        {"duration_s": 0.0},
        {"duration_s": 6.0},
        {"amplitude": 0.0},
        {"amplitude": 1.5},
        {"ramp_ms": -1.0},
        {"lead_ms": 10_000.0},
    ],
)
def test_tone_settings_reject_invalid_values(kwargs: dict[str, Any]) -> None:
    with pytest.raises(desktop.ToneSettingsError):
        desktop.ToneSettings(**kwargs)


def test_generate_tone_rejects_wrong_type(tmp_path: Path) -> None:
    with pytest.raises(desktop.ToneSettingsError):
        desktop.generate_tone("not a tone", tmp_path)  # type: ignore[arg-type]


# ----------------------------------------------------------------- playback


def test_play_requires_hardware_flag(tmp_path: Path) -> None:
    tone = make_tone(tmp_path)
    device = make_device()
    engine = playback.FakePlaybackEngine()

    with pytest.raises(desktop.PlaybackDisabled):
        desktop.play_generated(
            tone,
            device,
            engine=engine,
            hardware_test=False,
        )

    assert engine.calls == []
    assert list(tmp_path.glob("*.playback-*.json")) == []


def test_fake_playback_completed_writes_json(tmp_path: Path) -> None:
    tone = make_tone(tmp_path)
    device = make_device()
    engine = playback.FakePlaybackEngine()

    record = desktop.play_generated(
        tone,
        device,
        engine=engine,
        hardware_test=True,
        timestamp="play1",
    )

    assert record.status == "completed"
    assert record.performed is True
    assert record.frames_written == record.frames_total == tone.frames
    assert len(engine.calls) == 1
    call = engine.calls[0]
    assert call["device"] == device
    assert call["sample_rate"] == 48_000
    assert call["samples"].shape == (tone.frames,)

    payload = json.loads(record.json_path.read_text(encoding="utf-8"))
    assert payload["schema"] == desktop.PLAYBACK_SCHEMA
    assert payload["mode"] == "hardware_test"
    assert payload["device"]["index"] == device.index
    assert payload["device"]["hostapi_name"] == "MME"
    assert payload["file"]["sha256"] == tone.sha256
    assert payload["playback"]["status"] == "completed"
    assert payload["playback"]["performed"] is True
    assert payload["playback"]["engine"] == "FakePlaybackEngine"
    assert payload["playback"]["frames_written"] == tone.frames
    assert payload["playback"]["started_at_utc"]
    assert payload["playback"]["finished_at_utc"]


def test_fake_playback_stop_is_not_success(tmp_path: Path) -> None:
    tone = make_tone(tmp_path)
    device = make_device()
    engine = playback.FakePlaybackEngine(wait_for_stop=True, stop_timeout_s=5.0)
    result: dict[str, Any] = {}

    def target() -> None:
        result["record"] = desktop.play_generated(
            tone,
            device,
            engine=engine,
            hardware_test=True,
            timestamp="stopped",
        )

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not engine.is_playing and time.monotonic() < deadline:
        time.sleep(0.01)
    assert engine.is_playing
    engine.stop()
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    record = result["record"]
    assert record.status == "stopped"
    assert record.performed is False
    payload = json.loads(record.json_path.read_text(encoding="utf-8"))
    assert payload["playback"]["performed"] is False
    assert payload["playback"]["status"] == "stopped"
    assert any("остановлен" in warning for warning in payload["warnings"])


def test_play_error_is_recorded(tmp_path: Path) -> None:
    tone = make_tone(tmp_path)
    device = make_device()
    engine = playback.FakePlaybackEngine(fail_with=RuntimeError("driver exploded"))

    record = desktop.play_generated(
        tone, device, engine=engine, hardware_test=True, timestamp="failed"
    )

    assert record.status == "failed"
    assert record.performed is False
    assert "driver exploded" in (record.error or "")
    payload = json.loads(record.json_path.read_text(encoding="utf-8"))
    assert payload["playback"]["status"] == "failed"
    assert "driver exploded" in payload["playback"]["error"]


def test_play_rejects_tampered_wav(tmp_path: Path) -> None:
    tone = make_tone(tmp_path)
    device = make_device()
    engine = playback.FakePlaybackEngine()
    data, rate, channels = read_wav(tone.wav_path)
    data[0, 0] = 0.5
    write_pcm16(tone.wav_path, data[:, 0], rate)

    with pytest.raises(desktop.DesktopError, match="SHA-256"):
        desktop.play_generated(
            tone, device, engine=engine, hardware_test=True
        )
    assert engine.calls == []


def test_play_rejects_input_device(tmp_path: Path) -> None:
    tone = make_tone(tmp_path)
    engine = playback.FakePlaybackEngine()
    microphone = make_device(index=1, output=0, input_channels=2)

    with pytest.raises(desktop.DesktopError, match="выходным"):
        desktop.play_generated(
            tone, microphone, engine=engine, hardware_test=True
        )
    assert engine.calls == []


def test_sounddevice_engine_checks_format_without_fallback() -> None:
    class FakeSoundDevice:
        def __init__(self) -> None:
            self.stream_calls = 0

        def check_output_settings(self, **kwargs: Any) -> None:
            raise RuntimeError("unsupported samplerate")

        def OutputStream(self, **kwargs: Any) -> Any:  # noqa: N802
            self.stream_calls += 1
            raise AssertionError("поток не должен открываться")

    fake = FakeSoundDevice()
    engine = playback.SoundDevicePlaybackEngine(sd_module=fake, block_size=256)
    samples = np.zeros(256, dtype=np.float64)

    outcome = engine.play(samples, 48_000, make_device())

    assert outcome.status == "failed"
    assert "check_output_settings" not in (outcome.error or "")
    assert "unsupported samplerate" in (outcome.error or "")
    assert fake.stream_calls == 0
    assert engine.is_playing is False


def test_sounddevice_engine_releases_stream_on_write_error() -> None:
    class FakeStream:
        def __init__(self) -> None:
            self.closed = False

        def __enter__(self) -> "FakeStream":
            return self

        def __exit__(self, *args: Any) -> None:
            self.closed = True

        def write(self, block: Any) -> None:
            raise RuntimeError("device lost")

        def abort(self) -> None:
            pass

        def stop(self) -> None:
            pass

    stream = FakeStream()

    class FakeSoundDevice:
        def check_output_settings(self, **kwargs: Any) -> None:
            return None

        def OutputStream(self, **kwargs: Any) -> FakeStream:  # noqa: N802
            return stream

    engine = playback.SoundDevicePlaybackEngine(
        sd_module=FakeSoundDevice(), block_size=128
    )
    outcome = engine.play(np.zeros(512), 48_000, make_device())

    assert outcome.status == "failed"
    assert "device lost" in (outcome.error or "")
    assert stream.closed is True
    assert engine.is_playing is False


def test_probe_output_formats_reports_rejection() -> None:
    class FakeSoundDevice:
        def check_output_settings(self, device: int, channels: int, samplerate: int, dtype: str) -> None:
            if samplerate == 44_100:
                raise RuntimeError("not supported")

    result = desktop.probe_output_formats(make_device(), sd_module=FakeSoundDevice())

    assert result[48_000]["supported"] is True
    assert result[44_100]["supported"] is False
    assert "not supported" in result[44_100]["error"]


# ------------------------------------------------------------ phone analysis


def test_analyze_phone_wav_detects_expected_frequency(tmp_path: Path) -> None:
    source = write_sine(tmp_path / "phone.wav", frequency_hz=1_000.0, noise=0.002)
    record = desktop.analyze_phone_wav(
        source,
        expected_frequency_hz=1_000.0,
        results_dir=tmp_path / "results",
        timestamp="phone1",
    )

    assert record.imported_path.is_file()
    assert record.imported_path.read_bytes() == source.read_bytes()
    assert record.json_path is not None and record.json_path.is_file()
    assert record.csv_path is not None and record.csv_path.is_file()
    assert record.snr_db is None
    assert record.detected_frequency_hz == pytest.approx(1_000.0, abs=2.0)
    assert record.frequency_offset_hz is not None
    assert abs(record.frequency_offset_hz) <= 2.0
    assert record.detection_margin_db is not None
    assert record.detection_margin_db > 6.0
    # При Fs=48 кГц полоса 18–19 кГц существует и содержит только шум.
    assert record.band_18000_19000_dbfs is not None
    assert record.band_18000_19000_dbfs < -50.0

    channel = record.analysis.channel_analyses[0]
    assert channel.summary["level"]["rms_dbfs"] == pytest.approx(
        20.0 * math.log10(0.2 / math.sqrt(2.0)), abs=1.0
    )

    payload = json.loads(record.json_path.read_text(encoding="utf-8"))
    assert payload["schema"] == desktop.PHONE_SCHEMA
    assert payload["snr_db"] is None
    assert payload["expected_frequency_hz"] == 1_000.0
    assert payload["detected_frequency_hz"] == pytest.approx(1_000.0, abs=2.0)
    assert payload["channels"][0]["snr_db"] is None
    assert payload["channels"][0]["band_18000_19000_hz"]["available"] is True
    assert payload["source_file"]["sha256"] == record.analysis.sha256
    assert payload["files"]["json"] == str(record.json_path)
    assert payload["files"]["csv"] == str(record.csv_path)

    if importlib_available("matplotlib"):
        assert record.psd_path is not None and record.psd_path.is_file()
        assert record.spectrogram_path is not None
        assert record.spectrogram_path.is_file()


def test_analyze_phone_wav_without_expected_uses_global_peak(tmp_path: Path) -> None:
    source = write_sine(tmp_path / "phone.wav", frequency_hz=3_000.0, duration_s=0.6)
    record = desktop.analyze_phone_wav(
        source,
        results_dir=tmp_path / "results",
        make_plots=False,
        timestamp="global",
    )

    assert record.expected_frequency_hz is None
    assert record.detected_frequency_hz == pytest.approx(3_000.0, abs=5.0)
    assert record.global_dominant_frequency_hz == pytest.approx(3_000.0, abs=5.0)
    payload = json.loads(record.json_path.read_text(encoding="utf-8"))
    assert payload["expected_frequency_hz"] is None
    assert payload["snr_db"] is None


def test_analyze_phone_wav_warns_when_expected_peak_missing(tmp_path: Path) -> None:
    source = write_sine(tmp_path / "phone.wav", frequency_hz=1_000.0)
    record = desktop.analyze_phone_wav(
        source,
        expected_frequency_hz=18_000.0,
        results_dir=tmp_path / "results",
        make_plots=False,
        timestamp="missing",
    )

    assert record.detected_frequency_hz is None
    assert record.frequency_offset_hz is None
    assert any("значимый пик не найден" in item for item in record.warnings)
    assert record.snr_db is None


def test_analyze_phone_wav_rejects_nonpositive_expected(tmp_path: Path) -> None:
    source = write_sine(tmp_path / "phone.wav")
    with pytest.raises(desktop.DesktopError):
        desktop.analyze_phone_wav(source, expected_frequency_hz=0.0)


def test_analyze_missing_wav(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        desktop.analyze_phone_wav(tmp_path / "missing.wav")


def test_analyze_empty_wav_fails(tmp_path: Path) -> None:
    empty = tmp_path / "empty.wav"
    with wave.open(str(empty), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48_000)
        handle.writeframes(b"")

    with pytest.raises(WavFormatError):
        desktop.analyze_phone_wav(empty, results_dir=tmp_path / "results")


def test_analyze_corrupt_wav_fails(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"this is not a wav file")
    with pytest.raises(WavFormatError):
        desktop.analyze_phone_wav(corrupt, results_dir=tmp_path / "results")


def test_band_level_dbfs_handles_missing_band() -> None:
    frequencies = np.linspace(0.0, 10_000.0, 101)
    psd = np.full_like(frequencies, 1e-4)

    level = desktop.band_level_dbfs(frequencies, psd, 2_000.0, 4_000.0)
    assert level is not None
    assert math.isfinite(level)

    assert desktop.band_level_dbfs(frequencies, psd, 18_000.0, 19_000.0) is None
    assert desktop.band_level_dbfs(None, None, 1.0, 2.0) is None
    assert desktop.band_level_dbfs(frequencies, np.zeros_like(psd), 2_000.0, 4_000.0) == analysis.DB_FLOOR


def test_format_phone_summary_is_honest(tmp_path: Path) -> None:
    source = write_sine(tmp_path / "phone.wav", frequency_hz=1_000.0)
    record = desktop.analyze_phone_wav(
        source,
        expected_frequency_hz=1_000.0,
        results_dir=tmp_path / "results",
        make_plots=False,
    )
    text = desktop.format_phone_summary(record)

    assert "Expected frequency vs Detected frequency" in text
    assert "SNR: N/A" in text
    assert "PASS/FAIL автоматически не вычисляется" in text
    assert "1000.0 Hz" in text


def test_desktop_tools_do_not_load_audio_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("аудиобэкенд не должен загружаться")

    monkeypatch.setattr(audio, "load_sounddevice", forbidden)
    monkeypatch.setattr(audio, "list_devices", forbidden)

    tone = make_tone(tmp_path)
    record = desktop.analyze_phone_wav(
        tone.wav_path,
        expected_frequency_hz=1_000.0,
        results_dir=tmp_path / "results",
        make_plots=False,
    )
    assert record.detected_frequency_hz is not None


# ---------------------------------------------------------------------- GUI


def importlib_available(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def make_root() -> Any:
    tk = pytest.importorskip("tkinter")
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


def build_app(tmp_path: Path, *, hardware_test: bool, engine: Any | None = None) -> Any:
    root = make_root()
    app = desktop_app.DesktopApp(
        root,
        hardware_test=hardware_test,
        results_dir=tmp_path / "results",
        engine=engine,
        device_lister=lambda: [make_device()],
        confirm_playback=lambda tone, device: True,
    )
    return root, app


def select_first_device(app: Any) -> None:
    app.refresh_devices()
    children = app.device_tree.get_children()
    assert children
    app.device_tree.selection_set(children[0])
    app._on_device_selected()


def test_gui_starts_without_audio_and_play_disabled(tmp_path: Path) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, hardware_test=False, engine=engine)
    try:
        assert app.hardware_test is False
        assert app.play_button.instate(["disabled"])
        assert app.stop_button.instate(["disabled"])
        assert engine.calls == []
        assert list((tmp_path / "results").glob("*.playback-*.json")) == []

        assert app.request_playback() is False
        assert engine.calls == []
    finally:
        root.destroy()


def test_gui_file_only_generation_has_no_playback(tmp_path: Path) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, hardware_test=False, engine=engine)
    try:
        app.frequency_var.set("1000")
        app.duration_var.set("0.3")
        app.generate_wav()

        assert app.generated is not None
        assert app.generated.wav_path.is_file()
        assert engine.calls == []
        assert app.play_button.instate(["disabled"])
        assert list((tmp_path / "results").glob("*.playback-*.json")) == []
    finally:
        root.destroy()


def test_gui_hardware_test_requires_manual_play(tmp_path: Path) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, hardware_test=True, engine=engine)
    try:
        app.frequency_var.set("18000")
        app.duration_var.set("0.2")
        app.generate_wav()
        select_first_device(app)

        assert app.play_button.instate(["!disabled"])
        assert engine.calls == []
        assert list((tmp_path / "results").glob("*.playback-*.json")) == []

        assert app.request_playback() is True
        deadline = time.monotonic() + 5.0
        while app._playing and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        root.update()

        assert engine.calls
        assert len(engine.calls) == 1
        assert app.playback_status.cget("text").startswith("PLAY завершён")
        assert list((tmp_path / "results").glob("*.playback-*.json"))
    finally:
        root.destroy()


def test_gui_play_cancelled_by_user_creates_no_json(tmp_path: Path) -> None:
    engine = playback.FakePlaybackEngine()
    root = make_root()
    app = desktop_app.DesktopApp(
        root,
        hardware_test=True,
        results_dir=tmp_path / "results",
        engine=engine,
        device_lister=lambda: [make_device()],
        confirm_playback=lambda tone, device: False,
    )
    try:
        app.duration_var.set("0.2")
        app.generate_wav()
        select_first_device(app)

        assert app.request_playback() is False
        assert engine.calls == []
        assert list((tmp_path / "results").glob("*.playback-*.json")) == []
        assert "отменено" in app.playback_status.cget("text")
    finally:
        root.destroy()


def test_gui_device_selection_is_never_automatic(tmp_path: Path) -> None:
    engine = playback.FakePlaybackEngine()
    root, app = build_app(tmp_path, hardware_test=True, engine=engine)
    try:
        app.duration_var.set("0.2")
        app.generate_wav()
        app.refresh_devices()

        assert app.selected_device is None
        assert app.play_button.instate(["disabled"])

        select_first_device(app)
        assert app.selected_device is not None
        assert app.selected_device.index == 3
        assert app.play_button.instate(["!disabled"])
    finally:
        root.destroy()


def test_gui_import_requires_source(tmp_path: Path) -> None:
    root, app = build_app(tmp_path, hardware_test=False)
    try:
        app.analyze_current_phone()
        assert "импортируйте" in app.phone_status.cget("text").lower()
    finally:
        root.destroy()


def test_gui_smoke_test_main_is_file_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = desktop_app.main(
        ["--smoke-test", "--results-dir", str(tmp_path / "results")]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "GUI smoke test OK" in output
    assert "hardware_test=False" in output
    assert "play_enabled=False" in output


def test_gui_hardware_smoke_test_does_not_play(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = desktop_app.main(
        [
            "--hardware-test",
            "--smoke-test",
            "--results-dir",
            str(tmp_path / "results"),
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "hardware_test=True" in output
    assert "play_enabled=False" in output
    assert not list((tmp_path / "results").glob("*.playback-*.json"))
