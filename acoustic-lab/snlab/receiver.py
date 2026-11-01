"""BFSK-приёмник: PCM16 WAV → кадр → payload (SN-021).

Приёмник не знает, где начинается кадр и какие биты передавались. Он сам:

1. считает некогерентную энергию mark/space для каждого положения окна
   длиной в один символ;
2. ищет преамбулу `1010…` по разности энергий;
3. уточняет начало кадра по максимуму отклика на преамбулу;
4. демодулирует символы, проверяет sync word, версию, длину и CRC-16.

Это файловый канал L0. Здесь нет поиска по реальному воздуху, оценки
доплеровского сдвига, эквалайзера и FEC. Успешное декодирование WAV не
доказывает акустическую передачу.

CRC-16 обнаруживает повреждение кадра, но **не аутентифицирует** данные.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from snlab.config import BfskProfile, MAX_PAYLOAD_BYTES
from snlab.frame import (
    CRC_BITS,
    FRAME_OVERHEAD_BITS,
    FRAME_VERSION,
    LENGTH_BITS,
    PREAMBLE_BITS,
    SYNC_BITS,
    SYNC_WORD,
    VERSION_BITS,
    crc16_ccitt,
)
from snlab.metrics import bits_to_bytes
from snlab.wavio import read_pcm16

PREAMBLE_PATTERN = np.array(
    [1.0 if index % 2 == 0 else -1.0 for index in range(PREAMBLE_BITS)],
    dtype=np.float64,
)
HEADER_BITS = PREAMBLE_BITS + SYNC_BITS + VERSION_BITS + LENGTH_BITS


class ReceiverError(ValueError):
    """WAV не удалось превратить в проверенный кадр."""


class SignalNotFound(ReceiverError):
    """Преамбула не найдена: в записи нет кадра этого профиля."""


class SyncWordError(ReceiverError):
    """Преамбула найдена, но синхрослово не совпало."""


class FrameVersionError(ReceiverError):
    """Версия кадра не поддерживается приёмником."""


class FrameLengthError(ReceiverError):
    """Длина payload вне допустимого диапазона."""


class IncompleteFrame(ReceiverError):
    """В WAV не хватает символов для объявленного кадра."""


class CrcError(ReceiverError):
    """CRC кадра не совпал: payload повреждён или кадр собран неверно."""


@dataclass(frozen=True, slots=True)
class FrameStart:
    """Найденное начало кадра и качество совпадения преамбулы."""

    sample: int
    preamble_matches: int
    coarse_sample: int

    @property
    def sample_offset(self) -> int:
        """Насколько уточнение сдвинуло грубую оценку."""
        return self.sample - self.coarse_sample


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    """Проверенный кадр, восстановленный из аудиосигнала."""

    payload: bytes
    version: int
    crc: int
    start_sample: int
    preamble_matches: int
    total_bits: int
    sample_rate: int
    total_samples: int
    profile_name: str

    @property
    def payload_bits(self) -> int:
        return len(self.payload) * 8

    @property
    def start_time_s(self) -> float:
        return self.start_sample / self.sample_rate

    @property
    def duration_s(self) -> float:
        return self.total_samples / self.sample_rate

    def as_dict(self) -> dict[str, Any]:
        """Описание восстановленного кадра для JSON-отчёта."""
        return {
            "version": self.version,
            "preamble_bits": PREAMBLE_BITS,
            "preamble_matches": self.preamble_matches,
            "sync_word": _hex16(SYNC_WORD),
            "payload_bytes": len(self.payload),
            "payload_bits": self.payload_bits,
            "payload_sha256": hashlib.sha256(self.payload).hexdigest(),
            "overhead_bits": FRAME_OVERHEAD_BITS,
            "total_bits": self.total_bits,
            "crc16": _hex16(self.crc),
            "crc_algorithm": "CRC-16/CCITT-FALSE",
            "start_sample": self.start_sample,
            "start_time_s": self.start_time_s,
        }


@dataclass(frozen=True, slots=True)
class DemodulatedBits:
    """Диагностический результат демодуляции до проверки CRC.

    Это не восстановленный кадр. Биты возвращаются для измерения BER в
    benchmark и не подтверждают целостность payload. Даже если CRC не
    совпадёт, диагностика покажет, какие биты были демодулированы.
    """

    bits: np.ndarray
    start_sample: int
    preamble_matches: int
    symbols_available: int
    declared_payload_bytes: int | None
    declared_total_bits: int | None
    declared_length_valid: bool | None
    complete: bool
    profile_name: str
    total_samples: int

    @property
    def read_bits(self) -> int:
        """Сколько бит реально удалось демодулировать."""
        return int(self.bits.size)

    def as_dict(self) -> dict[str, Any]:
        """Диагностические поля без CRC и без исходных бит передатчика."""
        return {
            "start_sample": self.start_sample,
            "preamble_matches": self.preamble_matches,
            "symbols_available": self.symbols_available,
            "read_bits": self.read_bits,
            "declared_payload_bytes": self.declared_payload_bytes,
            "declared_total_bits": self.declared_total_bits,
            "declared_length_valid": self.declared_length_valid,
            "complete": self.complete,
        }


def decode_wav(
    path: str | Path,
    profile: BfskProfile,
    *,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> DecodedFrame:
    """Прочитать PCM16 WAV и восстановить кадр.

    Args:
        path: входной WAV; формат проверяется строго.
        profile: профиль, которым был промодулирован сигнал.
        max_payload_bytes: верхняя граница длины payload.

    Raises:
        FileNotFoundError: файл отсутствует.
        WavFormatError: не WAV, не PCM16 mono, пустой, обрезанный или
            частота дискретизации не совпадает с профилем.
        SignalNotFound: преамбула не найдена.
        SyncWordError, FrameVersionError, FrameLengthError,
        IncompleteFrame, CrcError: кадр найден, но не прошёл проверку.
    """
    samples, _ = read_pcm16(path, expected_sample_rate=profile.sample_rate)
    return decode_samples(
        samples,
        profile,
        max_payload_bytes=max_payload_bytes,
    )


def decode_samples(
    samples: np.ndarray,
    profile: BfskProfile,
    *,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> DecodedFrame:
    """Восстановить кадр из массива отсчётов в долях полной шкалы."""
    metric = bit_metric(samples, profile)
    start = find_frame_start(metric, profile)
    return decode_from_metric(metric, profile, start, max_payload_bytes=max_payload_bytes)


def demodulate_wav(
    path: str | Path,
    profile: BfskProfile,
    *,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> DemodulatedBits:
    """Прочитать WAV и вернуть демодулированные биты до проверки CRC.

    Контракт `decode_wav(path, profile)` не меняется. Эта функция нужна
    benchmark для BER: она не получает исходные биты передатчика и не
    объявляет payload восстановленным.
    """
    samples, _ = read_pcm16(path, expected_sample_rate=profile.sample_rate)
    return demodulate_samples(
        samples,
        profile,
        max_payload_bytes=max_payload_bytes,
    )


def demodulate_samples(
    samples: np.ndarray,
    profile: BfskProfile,
    *,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> DemodulatedBits:
    """Демодулировать доступные символы, не проверяя CRC и не подменяя кадр.

    Начало кадра по-прежнему ищется только по преамбуле. Если преамбула не
    найдена, выбрасывается `SignalNotFound`; если найдена, возвращаются все
    доступные биты (не больше объявленной длины кадра). Версия, sync word,
    длина и CRC здесь не являются условием возврата: это диагностика до CRC.

    Raises:
        SignalNotFound: преамбула не найдена.
        ReceiverError: сигнал не одномерный или содержит NaN/бесконечность.
    """
    metric = bit_metric(samples, profile)
    start = find_frame_start(metric, profile)
    symbols_per_bit = profile.samples_per_symbol
    symbols_available = max(
        0, (metric.size - start.sample + symbols_per_bit - 1) // symbols_per_bit
    )

    header_count = min(symbols_available, HEADER_BITS)
    header = _read_symbols(metric, start.sample, header_count, symbols_per_bit)

    declared_payload_bytes: int | None = None
    declared_total_bits: int | None = None
    declared_length_valid: bool | None = None
    if header_count == HEADER_BITS:
        length_offset = PREAMBLE_BITS + SYNC_BITS + VERSION_BITS
        length = int.from_bytes(
            bits_to_bytes(header[length_offset : length_offset + LENGTH_BITS]), "big"
        )
        declared_payload_bytes = length
        declared_total_bits = FRAME_OVERHEAD_BITS + length * 8
        declared_length_valid = 1 <= length <= max_payload_bytes

    frame_start = start.sample
    if declared_total_bits is not None:
        frame_start = _fit_frame_start(
            metric, start.sample, declared_total_bits, symbols_per_bit
        )
        symbols_available = max(
            0, (metric.size - frame_start + symbols_per_bit - 1) // symbols_per_bit
        )

    read_count = symbols_available
    if declared_total_bits is not None:
        read_count = min(read_count, declared_total_bits)
    bits = _read_symbols(metric, frame_start, read_count, symbols_per_bit)

    return DemodulatedBits(
        bits=bits,
        start_sample=frame_start,
        preamble_matches=start.preamble_matches,
        symbols_available=symbols_available,
        declared_payload_bytes=declared_payload_bytes,
        declared_total_bits=declared_total_bits,
        declared_length_valid=declared_length_valid,
        complete=declared_total_bits is not None and read_count == declared_total_bits,
        profile_name=profile.name,
        total_samples=metric.size + symbols_per_bit - 1,
    )


def decode_from_metric(
    metric: np.ndarray,
    profile: BfskProfile,
    start: FrameStart,
    *,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> DecodedFrame:
    """Демодулировать кадр, начиная с уже найденной преамбулы."""
    symbols_per_bit = profile.samples_per_symbol

    header = _read_bits(metric, start.sample, HEADER_BITS, symbols_per_bit)
    if not np.array_equal(header[:PREAMBLE_BITS], (PREAMBLE_PATTERN > 0).astype(np.uint8)):
        raise SignalNotFound("преамбула не совпала при повторной проверке")

    offset = PREAMBLE_BITS
    sync = int.from_bytes(bits_to_bytes(header[offset : offset + SYNC_BITS]), "big")
    if sync != SYNC_WORD:
        raise SyncWordError(
            f"ожидалось синхрослово {_hex16(SYNC_WORD)}, получено {_hex16(sync)}"
        )

    offset += SYNC_BITS
    version = int.from_bytes(bits_to_bytes(header[offset : offset + VERSION_BITS]), "big")
    if version != FRAME_VERSION:
        raise FrameVersionError(
            f"версия кадра {version} не поддерживается (ожидается {FRAME_VERSION})"
        )

    offset += VERSION_BITS
    length = int.from_bytes(bits_to_bytes(header[offset : offset + LENGTH_BITS]), "big")
    if not 1 <= length <= max_payload_bytes:
        raise FrameLengthError(
            f"длина payload {length} байт вне диапазона 1..{max_payload_bytes}"
        )

    total_bits = FRAME_OVERHEAD_BITS + length * 8
    frame_start = _fit_frame_start(metric, start.sample, total_bits, symbols_per_bit)
    frame_bits = _read_bits(metric, frame_start, total_bits, symbols_per_bit)

    payload_offset = HEADER_BITS
    payload = bits_to_bytes(frame_bits[payload_offset : payload_offset + length * 8])
    crc_offset = payload_offset + length * 8
    crc = int.from_bytes(bits_to_bytes(frame_bits[crc_offset : crc_offset + CRC_BITS]), "big")

    header_bytes = bytes((version,)) + length.to_bytes(2, "big")
    expected_crc = crc16_ccitt(header_bytes + payload)
    if crc != expected_crc:
        raise CrcError(
            f"CRC-16 не совпал: в кадре {_hex16(crc)}, "
            f"расчёт {_hex16(expected_crc)}"
        )

    return DecodedFrame(
        payload=payload,
        version=version,
        crc=crc,
        start_sample=frame_start,
        preamble_matches=start.preamble_matches,
        total_bits=total_bits,
        sample_rate=profile.sample_rate,
        total_samples=metric.size + symbols_per_bit - 1,
        profile_name=profile.name,
    )


def bit_metric(samples: np.ndarray, profile: BfskProfile) -> np.ndarray:
    """Разность энергий mark и space для окна в один символ.

    Для каждого возможного начала окна `t` считается

        E(f, t) = |sum(x[t+k] * exp(-j*2*pi*f*k/Fs))|^2

    и возвращается `E(mark) - E(space)`. Положительное значение означает
    бит 1, отрицательное — бит 0. Мера некогерентная: начальная фаза символа
    неизвестна и не требуется.

    Returns:
        Массив длиной `samples.size - samples_per_symbol + 1`.

    Raises:
        SignalNotFound: сигнал короче одного символа.
        ReceiverError: сигнал не одномерный или содержит NaN/бесконечность.
    """
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 1:
        raise ReceiverError("ожидается одномерный сигнал")
    if data.size == 0:
        raise SignalNotFound("WAV не содержит отсчётов")
    if not np.all(np.isfinite(data)):
        raise ReceiverError("сигнал содержит NaN или бесконечность")

    symbols_per_bit = profile.samples_per_symbol
    if data.size < symbols_per_bit:
        raise SignalNotFound("сигнал короче одного символа")

    index = np.arange(data.size, dtype=np.float64)
    mark_energy = _window_energy(data, profile.mark_hz, profile.sample_rate, symbols_per_bit, index)
    space_energy = _window_energy(
        data, profile.space_hz, profile.sample_rate, symbols_per_bit, index
    )
    return mark_energy - space_energy


def find_frame_start(metric: np.ndarray, profile: BfskProfile) -> FrameStart:
    """Найти преамбулу и уточнить начало кадра.

    Сначала для каждой фазы внутри символа проверяется корреляция знаков
    разностной энергии с шаблоном `1010…`. Затем начало уточняется по
    максимуму суммарного отклика на 32 символа преамбулы.

    Raises:
        SignalNotFound: совпадение преамбулы меньше 32/32.
    """
    symbols_per_bit = profile.samples_per_symbol
    span = PREAMBLE_BITS * symbols_per_bit
    if metric.size < span:
        raise SignalNotFound("сигнала недостаточно для 32-битной преамбулы")

    best_score = -np.inf
    best_phase = 0
    best_index = 0
    for phase in range(symbols_per_bit):
        sequence = metric[phase::symbols_per_bit]
        if sequence.size < PREAMBLE_BITS:
            continue
        signs = np.sign(sequence)
        correlation = np.correlate(signs, PREAMBLE_PATTERN, mode="valid")
        index = int(np.argmax(correlation))
        score = float(correlation[index])
        if score > best_score:
            best_score = score
            best_phase = phase
            best_index = index

    if best_score < PREAMBLE_BITS:
        matches = max(0, int(round(best_score)))
        raise SignalNotFound(
            f"преамбула не найдена: лучшее совпадение {matches}/{PREAMBLE_BITS}"
        )

    coarse = best_phase + best_index * symbols_per_bit
    low = max(0, coarse - symbols_per_bit)
    high = min(coarse, metric.size - span)
    if high < low:  # pragma: no cover - защита от арифметической ошибки
        high = low

    offsets = np.arange(0, span, symbols_per_bit)
    best_sample = coarse
    best_response = -np.inf
    for sample in range(low, high + 1):
        response = float(np.dot(PREAMBLE_PATTERN, metric[sample + offsets]))
        if response > best_response:
            best_response = response
            best_sample = sample

    matches = int(
        np.count_nonzero(
            (metric[best_sample + offsets] > 0).astype(np.uint8)
            == (PREAMBLE_PATTERN > 0).astype(np.uint8)
        )
    )
    if matches < PREAMBLE_BITS:
        raise SignalNotFound(
            f"преамбула не подтверждена: {matches}/{PREAMBLE_BITS}"
        )

    return FrameStart(
        sample=best_sample,
        preamble_matches=matches,
        coarse_sample=coarse,
    )


def _window_energy(
    data: np.ndarray,
    frequency_hz: float,
    sample_rate: int,
    window: int,
    index: np.ndarray,
) -> np.ndarray:
    mixed = data * np.exp(-2j * np.pi * frequency_hz * index / sample_rate)
    cumulative = np.concatenate((np.zeros(1, dtype=np.complex128), np.cumsum(mixed)))
    window_sum = cumulative[window:] - cumulative[:-window]
    return window_sum.real * window_sum.real + window_sum.imag * window_sum.imag


def _fit_frame_start(
    metric: np.ndarray,
    start: int,
    total_bits: int,
    symbols_per_bit: int,
) -> int:
    """Сдвинуть начало кадра назад, если он упирается в конец записи.

    Уточнение по преамбуле может выбрать положение на несколько отсчётов
    позже истинного. Если кадр заканчивается вплотную к концу файла, такой
    сдвиг отрезает последний символ. Здесь начало возвращается к последнему
    положению, при котором кадр целиком помещается в запись, но не дальше
    одного символа. Если места заведомо не хватает, положение не меняется:
    `_read_bits` сообщит `IncompleteFrame`.
    """
    latest = metric.size - total_bits * symbols_per_bit + (symbols_per_bit - 1)
    if latest < 0 or start <= latest or start - latest > symbols_per_bit:
        return start

    header = _read_bits(metric, latest, HEADER_BITS, symbols_per_bit)
    expected = (PREAMBLE_PATTERN > 0).astype(np.uint8)
    if not np.array_equal(header[:PREAMBLE_BITS], expected):
        return start
    return latest


def _read_symbols(
    metric: np.ndarray,
    start: int,
    count: int,
    symbols_per_bit: int,
) -> np.ndarray:
    """Прочитать ровно `count` символов из метрики без проверки границ кадра."""
    if count <= 0:
        return np.empty(0, dtype=np.uint8)
    indices = start + np.arange(count, dtype=np.int64) * symbols_per_bit
    return (metric[indices] > 0).astype(np.uint8)


def _read_bits(
    metric: np.ndarray,
    start: int,
    count: int,
    symbols_per_bit: int,
) -> np.ndarray:
    last = start + (count - 1) * symbols_per_bit
    if last >= metric.size:
        available = max(0, (metric.size - start + symbols_per_bit - 1) // symbols_per_bit)
        raise IncompleteFrame(
            f"кадр объявляет {count} бит, в записи доступно {available}"
        )
    return (metric[start : last + 1 : symbols_per_bit] > 0).astype(np.uint8)


def _hex16(value: int) -> str:
    return f"0x{value:04X}"
