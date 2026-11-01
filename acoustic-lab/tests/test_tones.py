"""Тесты генератора тонов (SN-014, часть A).

Все тесты работают только с массивами и файлами: звук не воспроизводится,
аудиоустройства не открываются.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from snlab import calibration, tones
from snlab.wavio import read_wav


def make_plan(**overrides: object) -> tones.TonePlan:
    values: dict[str, object] = {
        "sample_rate": 48_000,
        "channels": 1,
        "frequencies_hz": (8_000.0, 18_000.0, 19_000.0),
        "tone_ms": 300.0,
        "gap_ms": 150.0,
        "lead_ms": 150.0,
        "tail_ms": 150.0,
        "amplitude": 0.4,
        "ramp_ms": 10.0,
    }
    values.update(overrides)
    return tones.TonePlan(**values)  # type: ignore[arg-type]


def test_plan_reuses_calibration_math() -> None:
    plan = make_plan()

    assert plan.tone_samples == 14_400
    assert plan.gap_samples == 7_200
    assert plan.total_samples == 7_200 + 3 * 14_400 + 2 * 7_200 + 7_200
    assert plan.duration_s == pytest.approx(1.5)
    assert plan.tone_start_s(0) == pytest.approx(0.15)
    assert plan.tone_end_s(2) == pytest.approx(1.35)


def test_schedule_matches_generated_signal() -> None:
    plan = make_plan()
    schedule = plan.schedule()

    assert [item["frequency_hz"] for item in schedule] == [8_000.0, 18_000.0, 19_000.0]
    for index, item in enumerate(schedule):
        assert item["start_s"] == pytest.approx(plan.tone_start_s(index))
        assert item["end_s"] == pytest.approx(plan.tone_end_s(index))


def test_generate_signal_is_deterministic_and_silent_between_tones() -> None:
    plan = make_plan()
    first = tones.generate_signal(plan)
    second = tones.generate_signal(plan)

    assert first.shape == (plan.total_samples, 1)
    assert np.array_equal(first, second)
    assert np.all(first[: plan.lead_samples] == 0.0)
    assert np.all(first[-plan.tail_samples :] == 0.0)
    gap_start = plan.tone_end_sample(0)
    gap_end = plan.tone_start_sample(1)
    assert np.all(first[gap_start:gap_end] == 0.0)
    assert np.max(np.abs(first)) <= plan.amplitude + 1e-12


def test_generate_signal_copies_mono_to_all_channels() -> None:
    stereo = make_plan(channels=2)
    signal = tones.generate_signal(stereo)

    assert signal.shape == (stereo.total_samples, 2)
    assert np.array_equal(signal[:, 0], signal[:, 1])


def test_generated_frequencies_are_measurable() -> None:
    plan = make_plan()
    signal = tones.generate_signal(plan)[:, 0]

    analysis = calibration.analyze_capture(
        signal,
        plan.sample_rate,
        plan.as_calibration_plan(),
        min_snr_db=10.0,
        max_offset_hz=50.0,
    )

    assert analysis.verdict == "PASS"
    for measured in analysis.tones:
        assert measured.detected is True
        assert measured.frequency_offset_hz == pytest.approx(0.0, abs=2.0)


def test_write_tone_wav_roundtrip_and_hash(tmp_path: Path) -> None:
    plan = make_plan(channels=2)
    path, digest = tones.write_tone_wav(tmp_path / "tones.wav", plan)

    assert path.is_file()
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == expected == tones.sha256_file(path)

    data, rate, channels = read_wav(path)
    assert rate == plan.sample_rate
    assert channels == 2
    assert data.shape == (plan.total_samples, 2)
    assert np.array_equal(data[:, 0], data[:, 1])


def test_same_plan_writes_identical_file(tmp_path: Path) -> None:
    plan = make_plan(channels=2)
    first, first_hash = tones.write_tone_wav(tmp_path / "a.wav", plan)
    second, second_hash = tones.write_tone_wav(tmp_path / "b.wav", plan)

    assert first_hash == second_hash
    assert first.read_bytes() == second.read_bytes()


def test_amplitude_changes_file(tmp_path: Path) -> None:
    quiet, quiet_hash = tones.write_tone_wav(
        tmp_path / "quiet.wav", make_plan(amplitude=0.1)
    )
    loud, loud_hash = tones.write_tone_wav(
        tmp_path / "loud.wav", make_plan(amplitude=0.8)
    )

    assert quiet_hash != loud_hash
    assert loud.stat().st_size == quiet.stat().st_size


def test_generate_silence_has_requested_shape() -> None:
    plan = make_plan(channels=2)
    silence = tones.generate_silence(plan, seconds=0.25)

    assert silence.shape == (12_000, 2)
    assert np.all(silence == 0.0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"channels": 0},
        {"channels": 9},
        {"channels": True},
        {"sample_rate": 47_999.5},
        {"frequencies_hz": (19_000.0, 18_000.0)},
        {"frequencies_hz": ()},
        {"amplitude": 0.0},
        {"amplitude": 1.5},
        {"tone_ms": 0.0},
        {"ramp_ms": 501.0},
    ],
)
def test_invalid_plan_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(tones.ToneError):
        make_plan(**overrides)


def test_frequency_above_nyquist_rejected() -> None:
    with pytest.raises(tones.ToneError):
        make_plan(sample_rate=32_000, frequencies_hz=(18_000.0,))


def test_generate_silence_invalid_duration() -> None:
    plan = make_plan()
    for seconds in (0.0, -1.0, float("nan")):
        with pytest.raises(tones.ToneError):
            tones.generate_silence(plan, seconds=seconds)


def test_generate_signal_requires_plan() -> None:
    with pytest.raises(tones.ToneError):
        tones.generate_signal(object())  # type: ignore[arg-type]


def test_plan_dict_has_channels_and_tone_count() -> None:
    payload = make_plan(channels=2).as_dict()

    assert payload["channels"] == 2
    assert payload["tones"] == 3
    assert payload["frequencies_hz"] == [8_000.0, 18_000.0, 19_000.0]
    assert "required_frequencies_hz" not in payload
