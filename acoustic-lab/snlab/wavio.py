"""Строгое чтение и запись WAV для Acoustic Lab.

Формат Acoustic Lab: PCM 16 бит, моно, явно заданная частота дискретизации.
Любое отклонение считается ошибкой формата, а не поводом для догадок:
неверно прочитанный WAV не должен выглядеть как успешный эксперимент.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SAMPLE_WIDTH_BYTES = 2
CHANNELS = 1


class WavFormatError(ValueError):
    """WAV не соответствует требованиям Acoustic Lab."""


def write_pcm16(path: str | Path, samples: np.ndarray, sample_rate: int) -> Path:
    """Записать одномерный сигнал в PCM16 WAV.

    Args:
        path: целевой файл; родительские каталоги создаются автоматически.
        samples: значения в долях полной шкалы; клиппируются в [-1, 1].
        sample_rate: частота дискретизации, Гц.

    Returns:
        Путь к записанному файлу.

    Raises:
        ValueError: пустой сигнал, NaN/бесконечность или неверная частота.
    """
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 1:
        raise ValueError("ожидается одномерный сигнал")
    if data.size == 0:
        raise ValueError("нельзя записать пустой сигнал")
    if not np.all(np.isfinite(data)):
        raise ValueError("сигнал содержит NaN или бесконечность")
    if not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("sample_rate должен быть положительным целым числом")

    pcm = np.round(np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return target


def read_pcm16(
    path: str | Path,
    *,
    expected_sample_rate: int | None = None,
    expected_channels: int = CHANNELS,
) -> tuple[np.ndarray, int]:
    """Прочитать PCM16 WAV и вернуть (сигнал в [-1, 1], sample_rate).

    Raises:
        FileNotFoundError: файл отсутствует.
        WavFormatError: не WAV, не PCM16, не моно, пустой, обрезанный
            или не совпадает ожидаемая частота дискретизации.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"WAV не найден: {source}")

    try:
        with wave.open(str(source), "rb") as wav_file:
            channels = wav_file.getnchannels()
            width = wav_file.getsampwidth()
            rate = wav_file.getframerate()
            frames = wav_file.getnframes()
            raw = wav_file.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise WavFormatError(f"не удалось прочитать WAV {source}: {exc}") from exc

    if channels != expected_channels:
        raise WavFormatError(
            f"ожидается {expected_channels} канал(ов), в файле {channels}"
        )
    if width != SAMPLE_WIDTH_BYTES:
        raise WavFormatError(
            f"ожидается 16-битный PCM, в файле {width * 8} бит на отсчёт"
        )
    if expected_sample_rate is not None and rate != expected_sample_rate:
        raise WavFormatError(
            f"ожидается частота дискретизации {expected_sample_rate} Гц, "
            f"в файле {rate} Гц"
        )
    if frames == 0:
        raise WavFormatError("WAV не содержит аудиоданных")

    expected_bytes = frames * channels * width
    if len(raw) != expected_bytes:
        raise WavFormatError(
            "данные WAV обрезаны: заголовок обещает больше байт, чем есть в файле"
        )

    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return samples, rate
