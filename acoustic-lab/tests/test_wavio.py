"""Проверки строгого чтения и записи WAV."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import wave

from snlab.wavio import (
    MAX_CHANNELS,
    WavFormatError,
    read_pcm16,
    read_wav,
    to_pcm16_bytes_channels,
    write_pcm16,
    write_pcm16_channels,
)


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

def test_multichannel_roundtrip_keeps_channels_separate(tmp_path: Path) -> None:
    rate = 48_000
    time = np.arange(480, dtype=np.float64) / rate
    left = 0.5 * np.sin(2.0 * np.pi * 500.0 * time)
    right = 0.25 * np.sin(2.0 * np.pi * 1_500.0 * time)
    samples = np.column_stack([left, right])

    path = write_pcm16_channels(tmp_path / "stereo.wav", samples, rate)
    data, read_rate, channels = read_wav(path, expected_sample_rate=rate)

    assert read_rate == rate
    assert channels == 2
    assert data.shape == samples.shape
    assert np.max(np.abs(data[:, 0] - left)) <= 4.0 / 32768
    assert np.max(np.abs(data[:, 1] - right)) <= 4.0 / 32768


def test_mono_through_channels_api_is_2d(tmp_path: Path) -> None:
    samples = np.linspace(-0.5, 0.5, 64)
    path = write_pcm16_channels(tmp_path / "mono.wav", samples, 16_000)

    data, rate, channels = read_wav(path)

    assert rate == 16_000
    assert channels == 1
    assert data.shape == (64, 1)
    assert np.max(np.abs(data[:, 0] - samples)) <= 2.0 / 32768


def test_channel_bytes_are_interleaved() -> None:
    samples = np.array([[1.0, -1.0], [0.5, -0.5]], dtype=np.float64)
    payload = to_pcm16_bytes_channels(samples)

    values = np.frombuffer(payload, dtype="<i2")
    assert values.tolist() == [32767, -32767, 16384, -16384]


def test_too_many_channels_rejected(tmp_path: Path) -> None:
    samples = np.zeros((32, MAX_CHANNELS + 1))

    with pytest.raises(ValueError, match="каналов"):
        to_pcm16_bytes_channels(samples)
    with pytest.raises(ValueError, match="каналов"):
        write_pcm16_channels(tmp_path / "many.wav", samples, 48_000)


@pytest.mark.parametrize(
    "samples",
    [
        np.zeros((0, 1)),
        np.zeros((16, 0)),
        np.zeros((2, 2, 2)),
        np.array([[0.0, np.nan]]),
        np.array([[0.0, np.inf]]),
    ],
)
def test_invalid_multichannel_signal_rejected(
    tmp_path: Path, samples: np.ndarray
) -> None:
    with pytest.raises(ValueError):
        write_pcm16_channels(tmp_path / "bad.wav", samples, 48_000)


def test_read_wav_rejects_too_many_channels(tmp_path: Path) -> None:
    path = tmp_path / "many.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(MAX_CHANNELS + 1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48_000)
        wav_file.writeframes(np.zeros(MAX_CHANNELS + 1, dtype="<i2").tobytes())

    with pytest.raises(WavFormatError, match=f"1..{MAX_CHANNELS}"):
        read_wav(path)


def test_read_wav_checks_expected_rate(tmp_path: Path) -> None:
    path = write_pcm16_channels(tmp_path / "stereo.wav", np.zeros((32, 2)), 48_000)

    with pytest.raises(WavFormatError, match="44100"):
        read_wav(path, expected_sample_rate=44_100)


def test_write_pcm16_channels_rejects_bad_rate(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sample_rate"):
        write_pcm16_channels(tmp_path / "bad.wav", np.zeros((16, 1)), 0)
    with pytest.raises(ValueError, match="sample_rate"):
        write_pcm16_channels(tmp_path / "bad.wav", np.zeros((16, 1)), 48_000.0)  # type: ignore[arg-type]
