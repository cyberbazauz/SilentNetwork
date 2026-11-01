"""Тесты калибровки тракта (SN-012): план, генерация, анализ, воспроизведение.

Реальный динамик и микрофон не открываются: воспроизведение подменяется
фейковым бэкендом, анализ проверяется на синтезированных записях с известным
уровнем, шумом и смещением частоты.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from snlab import audio, calibration
from snlab.bfsk import apply_ramp

SAMPLE_RATE = 48_000


def make_plan(
    *,
    sample_rate: int = SAMPLE_RATE,
    frequencies_hz: tuple[float, ...] = (8_000.0, 18_000.0, 19_000.0),
    required_frequencies_hz: tuple[float, ...] = (18_000.0, 19_000.0),
    tone_ms: float = 300.0,
    gap_ms: float = 150.0,
    lead_ms: float = 150.0,
    tail_ms: float = 150.0,
    amplitude: float = 0.4,
    ramp_ms: float = 10.0,
) -> calibration.CalibrationPlan:
    return calibration.CalibrationPlan(
        sample_rate=sample_rate,
        frequencies_hz=frequencies_hz,
        required_frequencies_hz=required_frequencies_hz,
        tone_ms=tone_ms,
        gap_ms=gap_ms,
        lead_ms=lead_ms,
        tail_ms=tail_ms,
        amplitude=amplitude,
        ramp_ms=ramp_ms,
    )


def make_capture(
    plan: calibration.CalibrationPlan,
    *,
    latency_samples: int = 480,
    noise_rms: float = 1e-3,
    shift_hz: float = 0.0,
    mute: frozenset[float] = frozenset(),
    seed: int = 1,
) -> np.ndarray:
    """Синтезировать запись: сигнал + задержка + шум.

    `shift_hz` сдвигает все тоны, `mute` выключает выбранные частоты; так
    проверяются обнаружение смещения и отсутствие ложных срабатываний.
    """
    signal = calibration.generate_signal(plan)
    if shift_hz or mute:
        signal = np.zeros(plan.total_samples, dtype=np.float64)
        for index, frequency in enumerate(plan.frequencies_hz):
            if frequency in mute:
                continue
            start = plan.tone_start_sample(index)
            stop = plan.tone_end_sample(index)
            time_axis = np.arange(stop - start, dtype=np.float64) / plan.sample_rate
            tone = plan.amplitude * np.sin(
                2.0 * math.pi * (frequency + shift_hz) * time_axis
            )
            signal[start:stop] = apply_ramp(tone, plan.ramp_samples)

    padded = np.concatenate(
        (np.zeros(latency_samples), signal, np.zeros(latency_samples))
    )
    rng = np.random.default_rng(seed)
    return (padded + rng.normal(0.0, noise_rms, padded.size)).astype(np.float64)


def make_device(
    index: int,
    *,
    name: str,
    inputs: int,
    outputs: int,
    hostapi: int = 0,
    hostapi_name: str = "MME",
) -> audio.DeviceInfo:
    return audio.DeviceInfo(
        index=index,
        name=name,
        hostapi=hostapi,
        hostapi_name=hostapi_name,
        max_input_channels=inputs,
        max_output_channels=outputs,
        default_samplerate=48_000.0,
        default_low_input_latency=0.01,
        default_low_output_latency=0.01,
        is_default_input=bool(inputs),
        is_default_output=bool(outputs),
    )


def test_plan_sample_math() -> None:
    plan = make_plan()

    assert plan.tone_samples == 14_400
    assert plan.gap_samples == 7_200
    assert plan.lead_samples == 7_200
    assert plan.tail_samples == 7_200
    assert plan.total_samples == 7_200 + 3 * 14_400 + 2 * 7_200 + 7_200
    assert plan.tone_start_sample(0) == 7_200
    assert plan.tone_end_sample(2) == plan.total_samples - 7_200
    assert plan.tone_start_s(1) == pytest.approx(0.6)
    assert plan.duration_s == pytest.approx(1.5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sample_rate": 1_000},
        {"frequencies_hz": ()},
        {"frequencies_hz": (8_000.0, 30_000.0)},
        {"frequencies_hz": (19_000.0, 18_000.0)},
        {"frequencies_hz": (18_000.0, 18_000.0)},
        {"required_frequencies_hz": (17_000.0,)},
        {"tone_ms": 0.0},
        {"tone_ms": 6_000.0},
        {"gap_ms": -1.0},
        {"lead_ms": 6_000.0},
        {"amplitude": 0.0},
        {"amplitude": 1.5},
        {"ramp_ms": -1.0},
    ],
)
def test_plan_rejects_invalid_parameters(kwargs: dict[str, Any]) -> None:
    with pytest.raises(calibration.CalibrationError):
        make_plan(**kwargs)


def test_generate_signal_is_deterministic_and_quiet_between_tones() -> None:
    plan = make_plan()
    first = calibration.generate_signal(plan)
    second = calibration.generate_signal(plan)

    assert first.size == plan.total_samples
    assert np.array_equal(first, second)
    assert hashlib.sha256(first.tobytes()).hexdigest() == hashlib.sha256(
        second.tobytes()
    ).hexdigest()
    assert np.all(first[: plan.lead_samples] == 0.0)
    assert np.all(first[-plan.tail_samples :] == 0.0)
    for index in range(len(plan.frequencies_hz) - 1):
        gap_start = plan.tone_end_sample(index)
        gap_end = plan.tone_start_sample(index + 1)
        assert np.all(first[gap_start:gap_end] == 0.0)

    tone = first[plan.tone_start_sample(0) : plan.tone_end_sample(0)]
    flat = tone[plan.ramp_samples : -plan.ramp_samples]
    assert float(np.sqrt(np.mean(np.square(flat)))) == pytest.approx(
        plan.amplitude / math.sqrt(2.0), rel=1e-3
    )
    assert float(np.max(np.abs(first))) <= plan.amplitude + 1e-12


def test_analysis_recovers_tones() -> None:
    plan = make_plan()
    capture = make_capture(plan)

    analysis = calibration.analyze_capture(capture, SAMPLE_RATE, plan)

    assert analysis.verdict == "PASS"
    assert analysis.required_supported is True
    assert analysis.supported_frequencies_hz == (8_000.0, 18_000.0, 19_000.0)
    for tone in analysis.tones:
        assert tone.detected is True
        assert tone.supported is True
        assert tone.reason == "ok"
        assert tone.snr_db is not None and tone.snr_db > 40.0
        assert tone.frequency_offset_hz is not None
        assert abs(tone.frequency_offset_hz) < 2.0
        assert tone.level_dbfs == pytest.approx(-10.97, abs=0.3)
        assert tone.clipped_samples == 0
        # Окно не должно выезжать за пределы тона больше чем на задержку.
        assert tone.measured_start_s is not None
        assert tone.expected_start_s - 0.02 <= tone.measured_start_s
        assert tone.measured_end_s <= tone.expected_end_s + 0.02


def test_analysis_flags_missing_required_tone() -> None:
    plan = make_plan()
    capture = make_capture(plan, mute=frozenset({19_000.0}))

    analysis = calibration.analyze_capture(capture, SAMPLE_RATE, plan)

    missing = next(tone for tone in analysis.tones if tone.frequency_hz == 19_000.0)
    assert missing.supported is False
    assert missing.reason == "snr_below_threshold"
    assert analysis.verdict == "FAIL"
    assert analysis.required_supported is False
    assert 19_000.0 in analysis.unsupported_frequencies_hz
    assert any("19" in warning for warning in analysis.warnings)


def test_analysis_detects_frequency_offset() -> None:
    plan = make_plan()
    capture = make_capture(plan, shift_hz=30.0)

    analysis = calibration.analyze_capture(
        capture, SAMPLE_RATE, plan, max_offset_hz=20.0
    )

    tone = analysis.tones[0]
    assert tone.frequency_offset_hz == pytest.approx(30.0, abs=0.5)
    assert tone.detected is True
    assert tone.supported is False
    assert tone.reason == "frequency_offset"
    assert analysis.verdict == "FAIL"


def test_analysis_flags_clipping() -> None:
    plan = make_plan()
    capture = calibration.generate_signal(plan) * 3.0
    capture = np.clip(capture, -1.0, 1.0)

    analysis = calibration.analyze_capture(capture, SAMPLE_RATE, plan)

    for tone in analysis.tones:
        assert tone.clipped_samples > 0
        assert tone.reason == "clipping"
        assert tone.supported is False
    assert analysis.verdict == "FAIL"
    assert any("клиппинг" in warning for warning in analysis.warnings)


def test_analysis_of_digital_silence_is_not_a_false_pass() -> None:
    plan = make_plan()
    silence = np.zeros(plan.total_samples, dtype=np.float32)

    analysis = calibration.analyze_capture(silence, SAMPLE_RATE, plan)

    assert analysis.verdict == "FAIL"
    assert analysis.supported_frequencies_hz == ()
    for tone in analysis.tones:
        assert tone.detected is False
        assert tone.supported is False
        assert tone.snr_db == calibration.DB_FLOOR
    assert any("тишина" in warning for warning in analysis.warnings)


def test_analysis_of_truncated_capture_marks_late_tone_missing() -> None:
    plan = make_plan()
    capture = make_capture(plan)
    truncated = capture[: plan.tone_start_sample(2) + 100]

    analysis = calibration.analyze_capture(truncated, SAMPLE_RATE, plan)

    last = analysis.tones[-1]
    assert last.supported is False
    assert last.reason in {"capture_too_short", "snr_below_threshold"}
    assert analysis.verdict == "FAIL"
    assert any("короче плана" in warning for warning in analysis.warnings)


@pytest.mark.parametrize(
    "capture",
    [
        np.array([], dtype=np.float32),
        np.array([[0.0, 0.0]], dtype=np.float32),
        np.array([0.0, np.nan, 0.0], dtype=np.float32),
        np.array([0.0, np.inf, 0.0], dtype=np.float32),
    ],
)
def test_analysis_rejects_invalid_capture(capture: np.ndarray) -> None:
    with pytest.raises(calibration.CalibrationError):
        calibration.analyze_capture(capture, SAMPLE_RATE, make_plan())


def test_analysis_rejects_wrong_sample_rate() -> None:
    with pytest.raises(calibration.CalibrationError):
        calibration.analyze_capture(
            np.zeros(1_000, dtype=np.float32), 44_100, make_plan()
        )


def test_analysis_is_reproducible() -> None:
    plan = make_plan()
    capture = make_capture(plan)
    first = calibration.analyze_capture(capture, SAMPLE_RATE, plan)
    second = calibration.analyze_capture(capture, SAMPLE_RATE, plan)

    assert first.as_dict()["tones"] == second.as_dict()["tones"]
    assert first.as_dict()["summary"] == second.as_dict()["summary"]


@pytest.mark.parametrize(
    "text",
    ["", "18000,", "abc", "18000,8000", "18000,18000", "18000,nan"],
)
def test_parse_frequency_list_rejects_invalid(text: str) -> None:
    with pytest.raises(calibration.CalibrationError):
        calibration.parse_frequency_list(text)


def test_parse_frequency_list_accepts_ascending() -> None:
    assert calibration.parse_frequency_list(" 8000, 18000,19000 ") == (
        8_000.0,
        18_000.0,
        19_000.0,
    )


class FakePlayrecBackend:
    def __init__(self, samples: np.ndarray) -> None:
        self.samples = samples
        self.calls: list[dict[str, Any]] = []
        self.played: np.ndarray | None = None

    def playrec(self, data: np.ndarray, **kwargs: Any) -> np.ndarray:
        self.calls.append(kwargs)
        self.played = data
        return self.samples.reshape(-1, 1)

    def InputStream(self, **kwargs: Any) -> Any:  # pragma: no cover - не должен вызываться
        raise AssertionError("дуплексный путь не должен доходить до InputStream")


class FakeConcurrentBackend:
    def __init__(self, duration_samples: int) -> None:
        self.duration_samples = duration_samples
        self.callback: Any = None
        self.input_kwargs: dict[str, Any] = {}
        self.play_kwargs: dict[str, Any] = {}

    def playrec(self, data: np.ndarray, **kwargs: Any) -> np.ndarray:
        raise RuntimeError("дуплекс не поддерживается")

    def InputStream(self, **kwargs: Any) -> "FakeStream":
        self.callback = kwargs["callback"]
        self.input_kwargs = kwargs
        return FakeStream(self)

    def play(self, data: np.ndarray, **kwargs: Any) -> None:
        self.play_kwargs = kwargs
        block = np.full((data.size, 1), 0.1, dtype=np.float32)
        self.callback(block, data.size, None, None)


class FakeStream:
    def __init__(self, backend: FakeConcurrentBackend) -> None:
        self.backend = backend
        self.closed = False

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.closed = True
        return False


def test_play_and_record_uses_duplex_when_hostapis_match() -> None:
    samples = np.zeros(1_000, dtype=np.float32)
    backend = FakePlayrecBackend(samples)
    input_device = make_device(1, name="Mic", inputs=1, outputs=0)
    output_device = make_device(2, name="Speaker", inputs=0, outputs=2)

    capture = calibration.play_and_record(
        np.zeros(1_000, dtype=np.float64),
        SAMPLE_RATE,
        input_device,
        output_device,
        sd_module=backend,
    )

    assert capture.mode == "duplex"
    assert capture.samples.size == 1_000
    assert capture.input_device_index == 1
    assert capture.output_device_index == 2
    assert backend.calls[0]["device"] == (1, 2)
    assert backend.calls[0]["channels"] == 1
    assert backend.calls[0]["dtype"] == "float32"
    assert backend.played is not None


def test_play_and_record_falls_back_to_separate_streams() -> None:
    backend = FakeConcurrentBackend(duration_samples=1_000)
    input_device = make_device(1, name="Mic", inputs=1, outputs=0)
    output_device = make_device(2, name="Speaker", inputs=0, outputs=2)

    capture = calibration.play_and_record(
        np.zeros(1_000, dtype=np.float64),
        SAMPLE_RATE,
        input_device,
        output_device,
        sd_module=backend,
        concurrent_margin_s=0.0,
    )

    assert capture.mode == "concurrent"
    assert capture.samples.size == 1_000
    assert backend.input_kwargs["device"] == 1
    assert backend.play_kwargs["device"] == 2
    assert capture.warnings
    assert "дуплекс" in capture.warnings[0]


def test_play_and_record_reports_both_failures() -> None:
    class BrokenBackend:
        def playrec(self, data: np.ndarray, **kwargs: Any) -> np.ndarray:
            raise RuntimeError("duplex broken")

        def InputStream(self, **kwargs: Any) -> Any:
            raise RuntimeError("input broken")

    with pytest.raises(calibration.PlaybackError) as excinfo:
        calibration.play_and_record(
            np.zeros(1_000, dtype=np.float64),
            SAMPLE_RATE,
            make_device(1, name="Mic", inputs=1, outputs=0),
            make_device(2, name="Speaker", inputs=0, outputs=2),
            sd_module=BrokenBackend(),
        )
    assert "duplex broken" in str(excinfo.value)
    assert "input broken" in str(excinfo.value)


@pytest.mark.parametrize(
    "signal",
    [
        np.zeros((2, 10), dtype=np.float64),
        np.array([], dtype=np.float64),
        np.array([0.0, np.nan], dtype=np.float64),
    ],
)
def test_play_and_record_validates_signal(signal: np.ndarray) -> None:
    with pytest.raises(calibration.PlaybackError):
        calibration.play_and_record(
            signal,
            SAMPLE_RATE,
            make_device(1, name="Mic", inputs=1, outputs=0),
            make_device(2, name="Speaker", inputs=0, outputs=2),
            sd_module=FakePlayrecBackend(np.zeros(1, dtype=np.float32)),
        )
