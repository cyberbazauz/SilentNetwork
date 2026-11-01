"""Проверки метрик BER и PER."""

from __future__ import annotations

import numpy as np
import pytest

from snlab.metrics import (
    bit_error_rate,
    bits_to_bytes,
    bytes_to_bits,
    packet_error_rate,
)


def test_bit_error_rate_zero_for_identical() -> None:
    assert bit_error_rate([0, 1, 1, 0], [0, 1, 1, 0]) == 0.0


def test_bit_error_rate_counts_flips() -> None:
    reference = [0] * 7 + [1]
    received = [0] * 8
    assert bit_error_rate(reference, received) == pytest.approx(0.125)


def test_bit_error_rate_length_mismatch_rejected() -> None:
    with pytest.raises(ValueError, match="длины"):
        bit_error_rate([0, 1], [0])


def test_bit_error_rate_empty_rejected() -> None:
    with pytest.raises(ValueError, match="пустая"):
        bit_error_rate([], [])


def test_bit_error_rate_rejects_non_binary() -> None:
    with pytest.raises(ValueError, match="0 или 1"):
        bit_error_rate([0, 2], [0, 1])


def test_bytes_bits_roundtrip() -> None:
    payload = bytes(range(8))
    assert bits_to_bytes(bytes_to_bits(payload)) == payload


def test_bytes_to_bits_shape() -> None:
    assert bytes_to_bits(b"\x80").tolist() == [1, 0, 0, 0, 0, 0, 0, 0]


def test_bits_to_bytes_rejects_partial_byte() -> None:
    with pytest.raises(ValueError, match="кратно 8"):
        bits_to_bytes([1, 0, 1])


def test_packet_error_rate() -> None:
    assert packet_error_rate([b"a", b"b"], [b"a", b"b"]) == 0.0
    assert packet_error_rate([b"a", b"b"], [b"a", b"c"]) == pytest.approx(0.5)


def test_packet_error_rate_rejects_mismatch() -> None:
    with pytest.raises(ValueError, match="число эталонных"):
        packet_error_rate([b"a"], [b"a", b"b"])


def test_packet_error_rate_empty_rejected() -> None:
    with pytest.raises(ValueError, match="пустой"):
        packet_error_rate([], [])


def test_bit_error_rate_accepts_numpy() -> None:
    reference = np.array([1, 0, 1, 1], dtype=np.uint8)
    assert bit_error_rate(reference, reference) == 0.0
