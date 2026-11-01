"""BFSK-модулятор SN-011: частота, длительность, рампы и детерминизм.

Тесты работают только с массивами и временными WAV. Динамик и микрофон не
открываются, поэтому успех здесь не означает передачу через воздух.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from snlab.bfsk import (
    DEFAULT_RAMP_MS,
    MAX_RAMP_MS,
    ModulationError,
    apply_ramp,
    bits_to_samples,
    modulate,
)
from snlab.config import PROFILE_BFSK_16K, PROFILE_BFSK_18K
from snlab.wavio import read_pcm16, to_pcm16_bytes, write_pcm16


def estimate_frequency(samples: np.ndarray, sample_rate: int) -> float:
    """Оценить частоту по числу переходов через ноль."""
    crossings = int(np.count_nonzero(np.diff(np.signbit(samples))))
    return crossings / 2.0 / (samples.size / sample_rate)


def test_exact_sample_count_for_both_profiles() -> None:
    bits = np.array([1, 0, 1, 1, 0, 0, 1, 0], dtype=np.uint8)
    for profile in (PROFILE_BFSK_16K, PROFILE_BFSK_18K):
        samples = bits_to_samples(bits, profile)
        assert samples.shape == (bits.size * profile.samples_per_symbol,)
        assert samples.dtype == np.float64


def test_mark_and_space_frequencies_are_distinguishable() -> None:
    for profile in (PROFILE_BFSK_16K, PROFILE_BFSK_18K):
        marks = bits_to_samples(np.ones(40, dtype=np.uint8), profile, ramp_ms=0.0)
        spaces = bits_to_samples(np.zeros(40, dtype=np.uint8), profile, ramp_ms=0.0)

        assert estimate_frequency(marks, profile.sample_rate) == pytest.approx(
            profile.mark_hz, abs=80.0
        )
        assert estimate_frequency(spaces, profile.sample_rate) == pytest.approx(
            profile.space_hz, abs=80.0
        )


def test_amplitude_is_bounded() -> None:
    profile = PROFILE_BFSK_18K
    samples = bits_to_samples(np.array([1, 0] * 100, dtype=np.uint8), profile)
    peak = float(np.max(np.abs(samples)))
    assert 0.0 < peak <= profile.amplitude + 1e-12
    assert np.all(np.isfinite(samples))


def test_ramp_starts_and_ends_at_zero_without_changing_length() -> None:
    profile = PROFILE_BFSK_18K
    bits = np.array([1, 0, 1, 1], dtype=np.uint8)
    plain = bits_to_samples(bits, profile, ramp_ms=0.0)
    ramped = bits_to_samples(bits, profile, ramp_ms=DEFAULT_RAMP_MS)

    expected_ramp = round(DEFAULT_RAMP_MS / 1000.0 * profile.sample_rate)
    modulation = modulate(bits, profile, ramp_ms=DEFAULT_RAMP_MS)

    assert modulation.ramp_samples == expected_ramp
    assert ramped.size == plain.size
    assert ramped[0] == pytest.approx(0.0)
    assert ramped[-1] == pytest.approx(0.0)
    assert np.max(np.abs(ramped[expected_ramp:-expected_ramp])) > 0.0
    assert not np.array_equal(ramped, plain)


def test_ramp_is_capped_for_short_signals() -> None:
    samples = apply_ramp(np.ones(10), 50)
    assert samples.size == 10
    assert samples[0] == pytest.approx(0.0)
    assert samples[-1] == pytest.approx(0.0)
    assert samples[4] > 0.0


def test_zero_ramp_keeps_signal() -> None:
    samples = np.array([0.5, -0.5, 0.25])
    assert np.array_equal(apply_ramp(samples, 0), samples)


def test_pcm_matches_shared_conversion_and_is_deterministic() -> None:
    profile = PROFILE_BFSK_18K
    bits = np.array([1, 0, 0, 1, 1, 0, 1, 0], dtype=np.uint8)

    first = modulate(bits, profile)
    second = modulate(bits, profile)

    assert first.pcm16_bytes() == second.pcm16_bytes()
    assert first.pcm_sha256() == second.pcm_sha256()
    assert len(first.pcm_sha256()) == 64
    assert len(first.pcm16_bytes()) == first.sample_count * 2
    assert first.pcm16_bytes() == to_pcm16_bytes(first.samples)


def test_wav_roundtrip_preserves_sample_count_and_rate(tmp_path: Path) -> None:
    profile = PROFILE_BFSK_18K
    modulation = modulate(np.array([1, 0, 1, 1, 0], dtype=np.uint8), profile)
    path = write_pcm16(tmp_path / "tx.wav", modulation.samples, profile.sample_rate)

    samples, rate = read_pcm16(path, expected_sample_rate=profile.sample_rate)
    assert rate == profile.sample_rate
    assert samples.size == modulation.sample_count
    assert np.max(np.abs(samples - modulation.samples)) <= 2.0 / 32768.0


def test_modulation_report_contains_deterministic_hashes() -> None:
    profile = PROFILE_BFSK_18K
    modulation = modulate(np.array([1, 0, 1], dtype=np.uint8), profile)
    report = modulation.as_dict()

    assert report["bits"] == 3
    assert report["samples"] == 3 * profile.samples_per_symbol
    assert report["duration_s"] == pytest.approx(
        report["samples"] / profile.sample_rate
    )
    assert report["pcm_bytes"] == report["samples"] * 2
    assert report["pcm_sha256"] == modulation.pcm_sha256()
    assert report["scheme"] == "continuous-phase BFSK"


@pytest.mark.parametrize("bits", [[], [[0, 1]], [0, 2], [-1, 0], [0.0, 1.0]])
def test_invalid_bits_are_rejected(bits: object) -> None:
    with pytest.raises(ModulationError):
        bits_to_samples(bits, PROFILE_BFSK_18K)  # type: ignore[arg-type]


@pytest.mark.parametrize("ramp_ms", [-1.0, MAX_RAMP_MS + 1.0, math.nan, math.inf, "5"])
def test_invalid_ramp_is_rejected(ramp_ms: object) -> None:
    with pytest.raises(ModulationError):
        bits_to_samples([1, 0], PROFILE_BFSK_18K, ramp_ms=ramp_ms)  # type: ignore[arg-type]


def test_invalid_phase_is_rejected() -> None:
    with pytest.raises(ModulationError):
        bits_to_samples([1, 0], PROFILE_BFSK_18K, phase0=math.inf)


def test_apply_ramp_rejects_invalid_input() -> None:
    with pytest.raises(ModulationError):
        apply_ramp(np.array([]), 1)
    with pytest.raises(ModulationError):
        apply_ramp(np.zeros((2, 2)), 1)
    with pytest.raises(ModulationError):
        apply_ramp(np.ones(4), -1)
