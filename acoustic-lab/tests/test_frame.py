"""Формат кадра SN-011: раскладка, CRC и защита от неверного payload."""

from __future__ import annotations

import numpy as np
import pytest

from snlab.frame import (
    CRC_BITS,
    FRAME_OVERHEAD_BITS,
    FRAME_VERSION,
    LENGTH_BITS,
    PREAMBLE_BITS,
    SYNC_BITS,
    SYNC_WORD,
    VERSION_BITS,
    FrameError,
    build_frame,
    crc16_ccitt,
)
from snlab.metrics import bits_to_bytes


def test_crc16_known_vector() -> None:
    assert crc16_ccitt(b"123456789") == 0x29B1


def test_crc16_is_deterministic_and_sensitive() -> None:
    left = crc16_ccitt(b"SILENTNETWORK")
    assert left == crc16_ccitt(b"SILENTNETWORK")
    assert left != crc16_ccitt(b"SILENTNETWORM")


def test_frame_layout_matches_specification() -> None:
    payload = b"SN"
    frame = build_frame(payload)

    assert frame.version == FRAME_VERSION
    assert frame.total_bits == FRAME_OVERHEAD_BITS + len(payload) * 8
    assert frame.payload_bits == 16

    preamble = frame.bits[:PREAMBLE_BITS]
    assert preamble.tolist() == [1, 0] * (PREAMBLE_BITS // 2)

    sync = bits_to_bytes(frame.bits[PREAMBLE_BITS : PREAMBLE_BITS + SYNC_BITS])
    assert int.from_bytes(sync, "big") == SYNC_WORD

    offset = PREAMBLE_BITS + SYNC_BITS
    version = bits_to_bytes(frame.bits[offset : offset + VERSION_BITS])
    assert int.from_bytes(version, "big") == FRAME_VERSION

    offset += VERSION_BITS
    length = bits_to_bytes(frame.bits[offset : offset + LENGTH_BITS])
    assert int.from_bytes(length, "big") == len(payload)

    offset += LENGTH_BITS
    assert bits_to_bytes(frame.bits[offset : offset + len(payload) * 8]) == payload

    offset += len(payload) * 8
    crc = bits_to_bytes(frame.bits[offset : offset + CRC_BITS])
    assert int.from_bytes(crc, "big") == frame.crc
    assert frame.bits.size == offset + CRC_BITS


def test_frame_bits_depend_on_payload() -> None:
    left = build_frame(b"abc")
    right = build_frame(b"abd")
    assert not np.array_equal(left.bits, right.bits)
    assert left.crc != right.crc


def test_frame_report_is_complete() -> None:
    frame = build_frame(b"hello")
    report = frame.as_dict()

    assert report["version"] == FRAME_VERSION
    assert report["preamble_bits"] == PREAMBLE_BITS
    assert report["sync_word"] == f"0x{SYNC_WORD:04X}"
    assert report["payload_bytes"] == 5
    assert report["payload_bits"] == 40
    assert report["overhead_bits"] == FRAME_OVERHEAD_BITS
    assert report["total_bits"] == FRAME_OVERHEAD_BITS + 40
    assert report["crc_algorithm"] == "CRC-16/CCITT-FALSE"
    assert len(report["payload_sha256"]) == 64


def test_max_payload_is_accepted() -> None:
    payload = bytes(range(256)) * 2
    frame = build_frame(payload, max_payload_bytes=512)
    assert frame.total_bits == FRAME_OVERHEAD_BITS + 512 * 8


def test_empty_payload_is_rejected() -> None:
    with pytest.raises(FrameError, match="пустой"):
        build_frame(b"")


def test_oversized_payload_is_rejected() -> None:
    with pytest.raises(FrameError, match="превышает"):
        build_frame(b"x" * 513)


def test_non_bytes_payload_is_rejected() -> None:
    with pytest.raises(FrameError, match="bytes"):
        build_frame("SILENTNETWORK")  # type: ignore[arg-type]


@pytest.mark.parametrize("version", [-1, 256, True, 1.5, "1"])
def test_invalid_version_is_rejected(version: object) -> None:
    with pytest.raises(FrameError, match="version"):
        build_frame(b"x", version=version)  # type: ignore[arg-type]


def test_frame_is_deterministic() -> None:
    first = build_frame(b"deterministic payload")
    second = build_frame(b"deterministic payload")
    assert np.array_equal(first.bits, second.bits)
    assert first.crc == second.crc
