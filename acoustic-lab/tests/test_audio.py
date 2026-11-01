"""Проверки аудиомодуля SN-010 без реального оборудования.

Все тесты используют поддельный бэкенд sounddevice или чистые функции
анализа. Ни один тест не открывает микрофон и не утверждает, что запись
через воздух выполнена.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from snlab import audio

SAMPLE_RATE = 48_000


def make_device(
    index: int,
    *,
    name: str | None = None,
    inputs: int = 1,
    outputs: int = 0,
    default_rate: float = 48_000.0,
    hostapi: int = 0,
    default_input: bool = False,
    default_output: bool = False,
) -> audio.DeviceInfo:
    return audio.DeviceInfo(
        index=index,
        name=name or f"device-{index}",
        hostapi=hostapi,
        hostapi_name="MME",
        max_input_channels=inputs,
        max_output_channels=outputs,
        default_samplerate=default_rate,
        default_low_input_latency=0.01,
        default_low_output_latency=0.01,
        is_default_input=default_input,
        is_default_output=default_output,
    )


class FakeBackend:
    """Минимальная замена sounddevice для тестов."""

    def __init__(
        self,
        devices: list[dict[str, Any]],
        *,
        default: Any = (0, 0),
        hostapis: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._devices = devices
        self._hostapis = hostapis or [{"name": "MME"}]
        self._error = error
        self.default = types.SimpleNamespace(device=default)

    def query_devices(self) -> list[dict[str, Any]]:
        if self._error is not None:
            raise self._error
        return self._devices

    def query_hostapis(self) -> list[dict[str, Any]]:
        return self._hostapis


def raw_device(
    index: int,
    *,
    name: str = "dev",
    inputs: int = 1,
    outputs: int = 0,
) -> dict[str, Any]:
    return {
        "index": index,
        "name": name,
        "hostapi": 0,
        "max_input_channels": inputs,
        "max_output_channels": outputs,
        "default_samplerate": 48_000.0,
        "default_low_input_latency": 0.01,
        "default_low_output_latency": 0.02,
    }


def tone(frequency_hz: float, amplitude: float, seconds: float = 1.0) -> np.ndarray:
    time = np.arange(int(SAMPLE_RATE * seconds), dtype=np.float64) / SAMPLE_RATE
    return amplitude * np.sin(2.0 * np.pi * frequency_hz * time)


# --- Перечисление и выбор устройств ----------------------------------------


def test_list_devices_maps_fields_and_defaults() -> None:
    backend = FakeBackend(
        [
            raw_device(0, name="Realtek Output", inputs=0, outputs=2),
            raw_device(1, name="Array Mic", inputs=2, outputs=0),
        ],
        default=(1, 0),
    )

    devices = audio.list_devices(backend)

    assert [device.index for device in devices] == [0, 1]
    assert devices[0].is_output and not devices[0].is_input
    assert devices[0].is_default_output
    assert devices[1].is_input and devices[1].is_default_input
    assert devices[1].default_samplerate == 48_000.0
    assert devices[1].hostapi_name == "MME"


def test_list_devices_accepts_sounddevice_pair_type() -> None:
    """`sd.default.device` не является tuple/list — регрессия SN-010."""

    class PairLike:
        def __init__(self, values: list[int]) -> None:
            self._values = values

        def __iter__(self):
            return iter(self._values)

    backend = FakeBackend([raw_device(0), raw_device(1)], default=PairLike([1, 0]))

    devices = audio.list_devices(backend)

    assert devices[1].is_default_input
    assert devices[0].is_default_output


def test_list_devices_reports_backend_failure() -> None:
    backend = FakeBackend([], error=RuntimeError("PortAudio не инициализирован"))

    with pytest.raises(audio.AudioBackendUnavailable, match="PortAudio"):
        audio.list_devices(backend)


def test_select_device_uses_default_input() -> None:
    devices = [
        make_device(0, inputs=1, outputs=1),
        make_device(3, inputs=1, default_input=True),
    ]
    assert audio.select_device(None, "input", devices).index == 3


def test_select_device_by_index() -> None:
    devices = [make_device(2), make_device(5)]
    assert audio.select_device("5", "input", devices).index == 5


def test_select_device_by_name_fragment() -> None:
    devices = [make_device(1, name="Microphone (Realtek)"), make_device(2, name="Array")]
    assert audio.select_device("realtek", "input", devices).index == 1


def test_select_device_rejects_ambiguous_name() -> None:
    devices = [make_device(1, name="Mic A"), make_device(2, name="Mic B")]
    with pytest.raises(audio.DeviceNotFound, match="неоднозначен"):
        audio.select_device("mic", "input", devices)


def test_select_device_rejects_missing_index() -> None:
    with pytest.raises(audio.DeviceNotFound, match="индексом 99"):
        audio.select_device("99", "input", [make_device(1)])


def test_select_device_rejects_output_device_for_input() -> None:
    devices = [make_device(0, inputs=0, outputs=2)]
    with pytest.raises(audio.NoAudioDevice, match="входных"):
        audio.select_device(None, "input", devices)


def test_select_device_rejects_unknown_direction() -> None:
    with pytest.raises(ValueError, match="direction"):
        audio.select_device(None, "sideways", [make_device(1)])


# --- Проверка форматов ------------------------------------------------------


class FakeFormatBackend:
    def __init__(
        self,
        allowed_rates: set[int],
        allowed_dtypes: set[str] | None = None,
    ) -> None:
        self.allowed_rates = allowed_rates
        self.allowed_dtypes = allowed_dtypes or {"float32", "int16"}
        self.calls: list[dict[str, Any]] = []

    def check_input_settings(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if kwargs["samplerate"] not in self.allowed_rates:
            raise RuntimeError("Invalid sample rate")
        if kwargs["dtype"] not in self.allowed_dtypes:
            raise RuntimeError("Invalid dtype")

    check_output_settings = check_input_settings


def test_probe_formats_separates_supported_and_rejected_rates() -> None:
    backend = FakeFormatBackend({16_000, 48_000, 96_000})
    device = make_device(4)

    result = audio.probe_formats(device, direction="input", sd_module=backend)

    assert result["supported_sample_rates"] == [16_000, 48_000, 96_000]
    assert "8000" in result["rejected_sample_rates"]
    assert result["supported_dtypes"] == ["float32", "int16"]
    assert "int24" in result["rejected_dtypes"]
    assert result["dtypes_probed_at_hz"] == 48_000
    assert "не доказывает" in result["note"]
    assert len(backend.calls) == len(audio.CANDIDATE_SAMPLE_RATES) + len(
        audio.CANDIDATE_DTYPES
    )


def test_probe_formats_rejects_bad_direction() -> None:
    with pytest.raises(ValueError, match="direction"):
        audio.probe_formats(make_device(1), direction="both", sd_module=FakeFormatBackend({48_000}))


# --- Запись ----------------------------------------------------------------


class FakeStream:
    def __init__(
        self,
        *,
        block_size: int = 256,
        overflow_reads: set[int] | None = None,
        fail: Exception | None = None,
        **_: Any,
    ) -> None:
        self.block_size = block_size
        self.overflow_reads = overflow_reads or set()
        self.fail = fail
        self.reads = 0
        self.closed = False

    def __enter__(self) -> "FakeStream":
        if self.fail is not None:
            raise self.fail
        return self

    def __exit__(self, *_: Any) -> None:
        self.closed = True

    def read(self, frames: int) -> tuple[np.ndarray, bool]:
        index = self.reads
        self.reads += 1
        block = np.full((frames, 1), 0.25, dtype=np.float32)
        return block, index in self.overflow_reads


def fake_sd(**stream_kwargs: Any) -> Any:
    return types.SimpleNamespace(
        InputStream=lambda **kwargs: FakeStream(**{**stream_kwargs, **kwargs})
    )


def test_record_ambient_collects_exact_frame_count() -> None:
    recording = audio.record_ambient(
        duration_s=0.1,
        device=make_device(2),
        sample_rate=8_000,
        block_size=256,
        sd_module=fake_sd(),
    )

    assert recording.frames_requested == 800
    assert recording.frames_captured == 800
    assert recording.samples.shape == (800,)
    assert recording.samples.dtype == np.float32
    assert recording.overflows == 0
    assert recording.duration_s == pytest.approx(0.1)
    assert recording.device_index == 2


def test_record_ambient_counts_overflows() -> None:
    recording = audio.record_ambient(
        duration_s=0.1,
        sample_rate=8_000,
        block_size=256,
        sd_module=fake_sd(overflow_reads={0, 2}),
    )

    assert recording.overflows == 2
    assert recording.frames_captured == 800


def test_record_ambient_wraps_driver_error() -> None:
    backend = fake_sd(fail=RuntimeError("Device unavailable"))

    with pytest.raises(audio.CaptureError, match="Device unavailable"):
        audio.record_ambient(
            duration_s=0.1,
            device=make_device(7, name="Broken Mic"),
            sample_rate=8_000,
            sd_module=backend,
        )


@pytest.mark.parametrize("duration", [0.0, -1.0, 301.0, float("nan")])
def test_record_ambient_rejects_bad_duration(duration: float) -> None:
    with pytest.raises(ValueError, match="длительность"):
        audio.record_ambient(duration_s=duration, sd_module=fake_sd())


def test_record_ambient_rejects_bad_sample_rate() -> None:
    with pytest.raises(ValueError, match="sample_rate"):
        audio.record_ambient(duration_s=1.0, sample_rate=4_000, sd_module=fake_sd())


def test_record_ambient_times_out_when_stream_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(audio.time, "monotonic", lambda: clock["now"])

    class StallingStream(FakeStream):
        def read(self, frames: int) -> tuple[np.ndarray, bool]:
            clock["now"] += 100.0
            return super().read(frames)

    backend = types.SimpleNamespace(InputStream=lambda **kwargs: StallingStream(**kwargs))

    with pytest.raises(audio.CaptureError, match="за отведённое время"):
        audio.record_ambient(
            duration_s=0.1,
            sample_rate=8_000,
            block_size=256,
            sd_module=backend,
        )


# --- Спектр и уровни -------------------------------------------------------


def test_compute_psd_locates_tone() -> None:
    frequencies, psd, nperseg = audio.compute_psd(tone(1_000.0, 0.5), SAMPLE_RATE)

    assert nperseg == 8192
    peak = frequencies[int(np.argmax(psd))]
    assert peak == pytest.approx(1_000.0, abs=SAMPLE_RATE / nperseg)


def test_compute_psd_rejects_short_signal() -> None:
    with pytest.raises(ValueError, match="коротка"):
        audio.compute_psd(np.zeros(4), SAMPLE_RATE)


def test_analyze_ambient_reports_levels_without_snr() -> None:
    analysis = audio.analyze_ambient(tone(2_000.0, 0.1), SAMPLE_RATE)
    summary = analysis.summary

    assert summary["level"]["rms_linear"] == pytest.approx(0.1 / np.sqrt(2.0), rel=1e-3)
    assert summary["level"]["peak_linear"] == pytest.approx(0.1, rel=1e-3)
    assert summary["clipping"]["detected"] is False
    assert summary["snr"] is None
    assert "SNR не вычисляется" in summary["snr_note"]

    levels = {band["name"]: band["level_dbfs"] for band in summary["bands"]}
    assert levels["1000_4000"] == pytest.approx(-23.0, abs=0.3)
    assert levels["1000_4000"] - levels["16000_18000"] > 40.0
    assert levels["20000_22000"] == pytest.approx(audio.DB_FLOOR, abs=1e-6)
    assert summary["psd"]["resolution_hz"] == pytest.approx(
        SAMPLE_RATE / analysis.nperseg
    )


def test_spectral_peaks_find_the_tone() -> None:
    analysis = audio.analyze_ambient(tone(1_000.0, 0.3), SAMPLE_RATE)
    peaks = analysis.summary["peaks"]

    assert peaks
    assert peaks[0]["frequency_hz"] == pytest.approx(1_000.0, abs=10.0)


def test_band_levels_mark_out_of_range_band() -> None:
    frequencies = np.linspace(0.0, 8_000.0, 64)
    psd = np.ones_like(frequencies)

    bands = audio.band_levels(
        frequencies, psd, bands=(("too_high", 16_000.0, 18_000.0),)
    )

    assert bands[0]["level_dbfs"] is None
    assert "вне записанного диапазона" in bands[0]["note"]


def test_noise_floor_is_tenth_percentile_of_frames() -> None:
    frame = 800  # 100 мс при 8 кГц
    quiet = np.full((frame, 20), 0.01).ravel()
    loud = np.full((frame, 80), 0.5).ravel()
    samples = np.concatenate([quiet, loud])

    floor = audio.noise_floor(samples, 8_000)

    assert floor["frames"] == 100
    assert floor["linear"] == pytest.approx(0.01, rel=1e-3)
    assert floor["dbfs"] == pytest.approx(-40.0, abs=0.1)
    assert floor["frames_db_min"] == pytest.approx(-40.0, abs=0.1)
    assert floor["frames_db_max"] == pytest.approx(-6.02, abs=0.1)


def test_noise_floor_rejects_signal_shorter_than_frame() -> None:
    with pytest.raises(ValueError, match="короче одного кадра"):
        audio.noise_floor(np.zeros(10), 8_000)


def test_clipping_detects_consecutive_samples() -> None:
    samples = np.zeros(100)
    samples[10:13] = [1.0, -1.0, 0.999]

    stats = audio.clipping_stats(samples)

    assert stats["detected"] is True
    assert stats["clipped_samples"] == 3
    assert stats["longest_run"] == 3
    assert stats["first_index"] == 10


def test_clipping_absent_in_quiet_signal() -> None:
    stats = audio.clipping_stats(np.full(100, 0.5))
    assert stats["detected"] is False
    assert stats["clipped_samples"] == 0


@pytest.mark.parametrize(
    ("bad_signal", "message"),
    [
        (np.array([]), "пустой"),
        (np.zeros((2, 10)), "одномерный"),
        (np.array([0.0, np.nan]), "NaN"),
    ],
)
def test_analysis_rejects_invalid_signal(bad_signal: np.ndarray, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        audio.analyze_ambient(bad_signal, SAMPLE_RATE)


def test_peak_dbfs_is_minus_six_for_half_scale() -> None:
    stats = audio.analyze_ambient(np.full(SAMPLE_RATE, 0.5), SAMPLE_RATE).summary
    assert stats["level"]["peak_dbfs"] == pytest.approx(-6.02, abs=0.01)
    assert stats["level"]["crest_factor_db"] == pytest.approx(0.0, abs=1e-6)


# --- Сохранение результатов ------------------------------------------------


def test_write_psd_csv_has_header_and_rows(tmp_path: Path) -> None:
    analysis = audio.analyze_ambient(tone(1_000.0, 0.1, 0.5), SAMPLE_RATE)
    path = tmp_path / "psd.csv"

    audio.write_psd_csv(str(path), analysis)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "frequency_hz,psd_linear,psd_dbfs_per_hz"
    assert len(lines) == analysis.frequencies.size + 1


def test_write_spectrum_plot_creates_png(tmp_path: Path) -> None:
    analysis = audio.analyze_ambient(tone(1_000.0, 0.1, 0.5), SAMPLE_RATE)
    path = tmp_path / "spectrum.png"

    audio.write_spectrum_plot(str(path), analysis)

    assert path.is_file()
    assert path.stat().st_size > 1_000
