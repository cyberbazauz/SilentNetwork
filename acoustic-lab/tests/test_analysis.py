"""Тесты анализа WAV (SN-014, часть B).

Используются только синтетические файлы: микрофон и динамик не открываются.
"""

from __future__ import annotations

import csv
import json
import math
import wave
from pathlib import Path

import numpy as np
import pytest

from snlab import analysis
from snlab.wavio import WavFormatError, write_pcm16, write_pcm16_channels

SAMPLE_RATE = 48_000


def tone(
    frequency: float,
    seconds: float = 0.5,
    *,
    amplitude: float = 0.5,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    time = np.arange(int(round(seconds * sample_rate)), dtype=np.float64) / sample_rate
    return amplitude * np.sin(2.0 * math.pi * frequency * time)


def write_wav(path: Path, samples: np.ndarray, rate: int = SAMPLE_RATE) -> Path:
    return write_pcm16(path, samples, rate)


def test_tone_levels_and_peak(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "tone.wav", tone(1_000.0, amplitude=0.5))

    result = analysis.analyze_file(path)
    channel = result.channel_analyses[0]
    level = channel.summary["level"]

    assert result.channels == 1
    assert result.sample_rate == SAMPLE_RATE
    assert level["rms_dbfs"] == pytest.approx(-9.03, abs=0.15)
    assert level["peak_dbfs"] == pytest.approx(-6.02, abs=0.15)
    assert level["crest_factor_db"] == pytest.approx(3.01, abs=0.2)
    assert channel.summary["clipping"]["detected"] is False
    assert channel.summary["snr"] is None


def test_dominant_peak_found(tmp_path: Path) -> None:
    path = write_pcm16_channels(
        tmp_path / "stereo.wav",
        np.column_stack([tone(1_000.0), tone(3_000.0)]),
        SAMPLE_RATE,
    )

    result = analysis.analyze_file(path)
    left = result.channel_analyses[0].summary["peaks"][0]
    right = result.channel_analyses[1].summary["peaks"][0]

    assert left["frequency_hz"] == pytest.approx(1_000.0, abs=5.0)
    assert right["frequency_hz"] == pytest.approx(3_000.0, abs=5.0)
    assert any("независимо" in warning for warning in result.warnings)


def test_noise_floor_uses_quiet_frames(tmp_path: Path) -> None:
    rng = np.random.default_rng(5)
    signal = np.concatenate(
        [
            tone(1_000.0, seconds=0.2, amplitude=0.3),
            np.zeros(int(0.8 * SAMPLE_RATE)),
        ]
    )
    signal = signal + rng.normal(0.0, 1e-3, signal.size)
    path = write_wav(tmp_path / "noisy.wav", signal)

    result = analysis.analyze_file(path)
    floor = result.channel_analyses[0].summary["noise_floor"]

    assert floor is not None
    assert floor["dbfs"] == pytest.approx(-60.0, abs=3.0)
    assert floor["method"] == "percentile_of_frame_rms"


def test_digital_silence_is_reported_as_floor(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "silence.wav", np.zeros(24_000))

    result = analysis.analyze_file(path)
    summary = result.channel_analyses[0].summary

    assert summary["level"]["rms_dbfs"] == analysis.DB_FLOOR
    assert summary["level"]["peak_dbfs"] == analysis.DB_FLOOR
    assert summary["noise_floor"]["dbfs"] == analysis.DB_FLOOR


def test_clipping_detected(tmp_path: Path) -> None:
    clipped = np.concatenate([tone(1_000.0, seconds=0.2, amplitude=1.5)])
    path = write_wav(tmp_path / "clipped.wav", clipped)

    result = analysis.analyze_file(path)
    clipping = result.channel_analyses[0].summary["clipping"]

    assert clipping["detected"] is True
    assert clipping["clipped_samples"] > 0
    assert clipping["longest_run"] >= 1


def test_short_record_has_no_noise_floor(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "short.wav", np.full(8, 0.25))

    result = analysis.analyze_file(path)
    channel = result.channel_analyses[0]

    assert channel.summary["noise_floor"] is None
    assert any("100 мс" in warning for warning in channel.warnings)
    assert any("короче" in warning for warning in result.warnings)


def test_json_payload_contains_file_and_channels(tmp_path: Path) -> None:
    path = write_pcm16_channels(
        tmp_path / "pair.wav",
        np.column_stack([tone(1_000.0), tone(2_000.0)]),
        SAMPLE_RATE,
    )
    payload = analysis.analyze_file(path).as_dict()

    assert payload["file"]["sample_rate_hz"] == SAMPLE_RATE
    assert payload["file"]["channels"] == 2
    assert payload["file"]["sample_format"] == "pcm_s16le"
    assert len(payload["channels"]) == 2
    assert payload["channels"][0]["channel"] == 0
    assert "noise_floor" in payload["channels"][0]
    json.dumps(payload)


def test_csv_is_long_format(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "tone.wav", tone(1_000.0))
    result = analysis.analyze_file(path)
    csv_path = analysis.write_csv(tmp_path / "report.csv", result)

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows
    assert set(rows[0]) == {
        "channel",
        "section",
        "name",
        "rank",
        "frequency_hz",
        "low_hz",
        "high_hz",
        "value",
        "unit",
    }
    sections = {row["section"] for row in rows}
    assert {"level", "noise_floor", "clipping", "band", "peak"} <= sections
    assert any(row["name"] == "rms" and row["unit"] == "dBFS" for row in rows)


def test_psd_and_spectrogram_plots_created(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "tone.wav", tone(1_000.0, seconds=1.0))
    result = analysis.analyze_file(path)

    psd = analysis.write_psd_plot(tmp_path / "psd.png", result)
    spectrogram = analysis.write_spectrogram_plot(tmp_path / "spec.png", result)

    assert psd.is_file() and psd.stat().st_size > 1_000
    assert spectrogram.is_file() and spectrogram.stat().st_size > 1_000


def test_spectrogram_requires_enough_samples(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "short.wav", np.zeros(16))
    result = analysis.analyze_file(path)

    with pytest.raises(analysis.AnalysisError, match="спектрограмм"):
        analysis.write_spectrogram_plot(tmp_path / "spec.png", result)


def test_spectrogram_channel_validated(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "tone.wav", tone(1_000.0))
    result = analysis.analyze_file(path)

    with pytest.raises(analysis.AnalysisError, match="канал"):
        analysis.write_spectrogram_plot(tmp_path / "spec.png", result, channel=1)
    with pytest.raises(analysis.AnalysisError, match="nperseg"):
        analysis.write_spectrogram_plot(
            tmp_path / "spec.png", result, nperseg=4
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"top_peaks": 0}, "top_peaks"),
        ({"top_peaks": 51}, "top_peaks"),
        ({"top_peaks": 1.5}, "top_peaks"),
        ({"nperseg": 4}, "nperseg"),
        ({"nperseg": 8.0}, "nperseg"),
    ],
)
def test_invalid_parameters(
    tmp_path: Path, kwargs: dict[str, object], message: str
) -> None:
    path = write_wav(tmp_path / "tone.wav", tone(1_000.0))

    with pytest.raises(analysis.AnalysisError, match=message):
        analysis.analyze_file(path, **kwargs)  # type: ignore[arg-type]


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        analysis.analyze_file(tmp_path / "missing.wav")


def test_empty_wav_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)

    with pytest.raises(WavFormatError, match="не содержит"):
        analysis.analyze_file(path)


def test_wrong_bit_depth_rejected(tmp_path: Path) -> None:
    path = tmp_path / "24bit.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(3)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(b"\x00\x00\x00" * 32)

    with pytest.raises(WavFormatError, match="16-бит"):
        analysis.analyze_file(path)


def test_short_tone_has_psd_but_no_floor(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "tiny.wav", tone(1_000.0, seconds=0.02))
    result = analysis.analyze_file(path)
    summary = result.channel_analyses[0].summary

    assert summary["noise_floor"] is None
    assert summary["psd"]["bins"] > 1
    assert summary["peaks"]
