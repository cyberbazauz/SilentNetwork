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
MAX_CHANNELS = 8


class WavFormatError(ValueError):
    """WAV не соответствует требованиям Acoustic Lab."""


def to_pcm16_bytes(samples: np.ndarray) -> bytes:
    """Преобразовать сигнал в байты PCM16 mono little-endian.

    Это единственное место, где сигнал в долях полной шкалы превращается в
    PCM. И `write_pcm16`, и модулятор обязаны использовать его, иначе SHA-256
    «того же» сигнала может разойтись из-за разных правил округления.

    Args:
        samples: одномерный сигнал в долях полной шкалы; клиппируется в [-1, 1].

    Raises:
        ValueError: не одномерный, пустой сигнал, NaN/бесконечность.
    """
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 1:
        raise ValueError("ожидается одномерный сигнал")
    if data.size == 0:
        raise ValueError("нельзя записать пустой сигнал")
    if not np.all(np.isfinite(data)):
        raise ValueError("сигнал содержит NaN или бесконечность")

    pcm = np.round(np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    return pcm.tobytes()


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
    payload = to_pcm16_bytes(samples)
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool):
        raise ValueError("sample_rate должен быть целым числом")
    if sample_rate <= 0:
        raise ValueError("sample_rate должен быть положительным целым числом")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Открываем файл сами: если путь указывает на каталог, ошибка возникает
    # до создания Wave_write и не оставляет предупреждений в __del__.
    with target.open("wb") as handle:
        with wave.open(handle, "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(payload)
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


def to_pcm16_bytes_channels(samples: np.ndarray) -> bytes:
    """Преобразовать многоканальный сигнал в чередующиеся байты PCM16.

    Args:
        samples: массив формы `(frames, channels)` или одномерный моно;
            значения в долях полной шкалы клиппируются в [-1, 1].

    Raises:
        ValueError: неверная размерность, пустой сигнал, больше
            `MAX_CHANNELS` каналов, NaN/бесконечность.
    """
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim == 1:
        data = data[:, None]
    if data.ndim != 2:
        raise ValueError("ожидается массив формы (frames, channels)")
    if data.shape[0] == 0 or data.shape[1] == 0:
        raise ValueError("нельзя записать пустой сигнал")
    if data.shape[1] > MAX_CHANNELS:
        raise ValueError(
            f"поддерживается не более {MAX_CHANNELS} каналов, "
            f"запрошено {data.shape[1]}"
        )
    if not np.all(np.isfinite(data)):
        raise ValueError("сигнал содержит NaN или бесконечность")

    pcm = np.round(np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    return pcm.tobytes(order="C")


def write_pcm16_channels(
    path: str | Path,
    samples: np.ndarray,
    sample_rate: int,
) -> Path:
    """Записать многоканальный PCM16 WAV (чередование каналов).

    Моно-сигнал принимается как частный случай. Формат всегда 16 бит;
    частота дискретизации записывается в заголовок как есть.
    """
    payload = to_pcm16_bytes_channels(samples)
    data = np.asarray(samples)
    channels = 1 if data.ndim == 1 else int(data.shape[1])
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool):
        raise ValueError("sample_rate должен быть целым числом")
    if sample_rate <= 0:
        raise ValueError("sample_rate должен быть положительным целым числом")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        with wave.open(handle, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(payload)
    return target


def read_wav(
    path: str | Path,
    *,
    expected_sample_rate: int | None = None,
) -> tuple[np.ndarray, int, int]:
    """Прочитать PCM16 WAV любой канальности: (frames, channels), rate, channels.

    В отличие от `read_pcm16`, моно-файл возвращается двумерным массивом
    формы `(frames, 1)`, чтобы вызывающий код не различал 1 и N каналов.

    Raises:
        FileNotFoundError: файл отсутствует.
        WavFormatError: не WAV, не PCM16, пустой, обрезанный или не совпадает
            ожидаемая частота дискретизации.
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

    if channels < 1 or channels > MAX_CHANNELS:
        raise WavFormatError(
            f"ожидается 1..{MAX_CHANNELS} каналов, в файле {channels}"
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

    flat = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    data = flat.reshape(frames, channels)
    return data, rate, channels
