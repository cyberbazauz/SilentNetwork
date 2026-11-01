"""Метрики качества канала: BER и PER.

Метрики считаются по эталонным данным эксперимента, а не по признаку
«программа завершилась без ошибок». Несовпадение длин и пустые данные —
это ошибка эксперимента, а не нулевая вероятность ошибки.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np


def bytes_to_bits(data: bytes) -> np.ndarray:
    """Развернуть байты в массив бит (старший бит первым)."""
    return np.unpackbits(np.frombuffer(bytes(data), dtype=np.uint8))


def bits_to_bytes(bits: Sequence[int] | np.ndarray) -> bytes:
    """Собрать байты из массива бит (старший бит первым)."""
    array = _as_bits(bits)
    if array.size % 8 != 0:
        raise ValueError("число бит должно быть кратно 8")
    return np.packbits(array).tobytes()


def bit_error_rate(
    reference: Sequence[int] | np.ndarray,
    received: Sequence[int] | np.ndarray,
) -> float:
    """Доля неверных бит относительно эталона."""
    expected = _as_bits(reference)
    actual = _as_bits(received)
    if expected.size == 0:
        raise ValueError("пустая эталонная последовательность")
    if expected.size != actual.size:
        raise ValueError("длины эталонной и принятой последовательностей не совпадают")
    return float(np.count_nonzero(expected != actual) / expected.size)


def packet_error_rate(
    reference: Iterable[bytes],
    received: Iterable[bytes],
) -> float:
    """Доля пакетов, не совпавших с эталоном."""
    expected = [bytes(packet) for packet in reference]
    actual = [bytes(packet) for packet in received]
    if not expected:
        raise ValueError("пустой список эталонных пакетов")
    if len(expected) != len(actual):
        raise ValueError("число эталонных и принятых пакетов не совпадает")
    errors = sum(1 for left, right in zip(expected, actual) if left != right)
    return errors / len(expected)


def _as_bits(values: Sequence[int] | np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.uint8).ravel()
    if array.size and not np.all(array <= 1):
        raise ValueError("биты должны принимать значения 0 или 1")
    return array
