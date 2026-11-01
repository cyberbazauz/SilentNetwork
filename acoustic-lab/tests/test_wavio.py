"""Проверки строгого чтения и записи WAV."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import wave

from snlab.wavio import WavFormatError, read_pcm16, write_pcm16


def test_roundtrip_preserves_signal(tmp_path: Path) -> None:
    rate = 48_000
    time = np.arange(480, dtype=np.float64) / rate
    tone = 0.5 * np.sin(2.0 * np.pi * 1_000.0 * time)

    path = write_pcm16(tmp_path / "tone.wav", tone, rate)
    samples, read_rate = read_pcm16(path, expected_sample_rate=rate)

    assert read_rate == rate
    assert samples.shape == tone.shape
    assert np.max(np.abs(samples - tone)) <= 4.0 / 32768


def test_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        read_pcm16("нет-такого-файла.wav")


def test_empty_signal_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="пустой"):
        write_pcm16(tmp_path / "empty.wav", np.array([], dtype=np.float64), 48_000)


def test_nan_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="NaN"):
        write_pcm16(tmp_path / "nan.wav", np.array([0.0, np.nan]), 48_000)


def test_wrong_sample_rate_rejected(tmp_path: Path) -> None:
    path = write_pcm16(tmp_path / "tone.wav", np.zeros(16), 48_000)
    with pytest.raises(WavFormatError, match="частот"):
        read_pcm16(path, expected_sample_rate=44_100)


def test_stereo_rejected(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48_000)
        wav_file.writeframes(np.zeros(32, dtype="<i2").tobytes())

    with pytest.raises(WavFormatError, match="канал"):
        read_pcm16(path)


def test_24_bit_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pcm24.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(3)
        wav_file.setframerate(48_000)
        wav_file.writeframes(b"\x00" * 300)

    with pytest.raises(WavFormatError, match="16"):
        read_pcm16(path)


def test_empty_wav_rejected(tmp_path: Path) -> None:
    path = tmp_path / "zero.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48_000)
        wav_file.writeframes(b"")

    with pytest.raises(WavFormatError, match="не содержит"):
        read_pcm16(path)


def test_truncated_data_rejected(tmp_path: Path) -> None:
    path = write_pcm16(tmp_path / "cut.wav", np.zeros(1_000), 48_000)
    raw = path.read_bytes()
    path.write_bytes(raw[:-100])

    with pytest.raises(WavFormatError, match="обрезан|прочитать"):
        read_pcm16(path)
