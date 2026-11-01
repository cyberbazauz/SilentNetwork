"""Проверки модели канала AWGN."""

from __future__ import annotations

import numpy as np
import pytest

from snlab.channel import add_awgn


def test_same_seed_is_deterministic() -> None:
    signal = np.sin(np.linspace(0.0, 2.0 * np.pi, 4_800))
    first = add_awgn(signal, 10.0, seed=1234)
    second = add_awgn(signal, 10.0, seed=1234)
    np.testing.assert_array_equal(first, second)


def test_different_seed_changes_noise() -> None:
    signal = np.ones(4_800)
    first = add_awgn(signal, 10.0, seed=1)
    second = add_awgn(signal, 10.0, seed=2)
    assert not np.array_equal(first, second)


def test_noise_power_matches_snr() -> None:
    signal = np.ones(200_000)
    noisy = add_awgn(signal, 10.0, seed=7)
    measured_std = float(np.std(noisy - signal))
    expected_std = float(np.sqrt(1.0 / 10.0))
    assert measured_std == pytest.approx(expected_std, rel=0.02)


def test_shape_preserved() -> None:
    signal = np.full(10, 0.5)
    assert add_awgn(signal, 3.0, seed=42).shape == signal.shape


@pytest.mark.parametrize(
    "bad_signal",
    [
        np.array([]),
        np.zeros((2, 2)),
        np.array([0.0, np.inf]),
    ],
)
def test_invalid_signal_rejected(bad_signal: np.ndarray) -> None:
    with pytest.raises(ValueError):
        add_awgn(bad_signal, 10.0, seed=1)


def test_zero_power_signal_rejected() -> None:
    with pytest.raises(ValueError, match="нулевая мощность"):
        add_awgn(np.zeros(100), 10.0, seed=1)


def test_non_finite_snr_rejected() -> None:
    with pytest.raises(ValueError, match="snr_db"):
        add_awgn(np.ones(10), float("nan"))
