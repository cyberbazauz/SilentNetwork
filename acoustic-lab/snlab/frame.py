"""Формат кадра Acoustic Lab для SN-011/SN-021.

Кадр — это уровень PHY, а не SCNP. Он нужен, чтобы приёмник нашёл начало
передачи, узнал длину и проверил, что принял именно этот кадр.

Структура (биты передаются старшим вперёд):

| Поле | Биты | Значение |
|---|---|---|
| Преамбула | 32 | чередование 1,0,…
| Синхрослово | 16 | 0x1ACF |
| Версия | 8 | 0x01 |
| Длина payload | 16 | байты, big-endian |
| Payload | 8·N | текст UTF-8 или произвольные байты |
| CRC-16 | 16 | CRC-16/CCITT-FALSE по версии, длине и payload |

CRC обнаруживает ошибки передачи, но **не является аутентификацией**: он не
защищает от подмены и не заменяет AEAD. Это будет отдельный уровень.

Формат версионирован: любое изменение раскладки требует новой версии,
тестовых векторов и обновления ADR.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np

from snlab.config import MAX_PAYLOAD_BYTES
from snlab.metrics import bytes_to_bits

PREAMBLE_BITS = 32
SYNC_WORD = 0x1ACF
SYNC_BITS = 16
VERSION_BITS = 8
LENGTH_BITS = 16
CRC_BITS = 16
FRAME_VERSION = 1
CRC_POLYNOMIAL = 0x1021
CRC_INITIAL = 0xFFFF
FRAME_OVERHEAD_BITS = PREAMBLE_BITS + SYNC_BITS + VERSION_BITS + LENGTH_BITS + CRC_BITS


class FrameError(ValueError):
    """Кадр невозможно построить из заданных данных."""


@dataclass(frozen=True, slots=True)
class Frame:
    """Готовый к модуляции кадр."""

    payload: bytes
    version: int
    crc: int
    bits: np.ndarray

    @property
    def payload_bits(self) -> int:
        return len(self.payload) * 8

    @property
    def total_bits(self) -> int:
        return int(self.bits.size)

    def as_dict(self) -> dict[str, Any]:
        """Описание кадра для JSON-отчёта."""
        return {
            "version": self.version,
            "preamble_bits": PREAMBLE_BITS,
            "sync_word": _hex16(SYNC_WORD),
            "payload_bytes": len(self.payload),
            "payload_sha256": hashlib.sha256(self.payload).hexdigest(),
            "crc16": _hex16(self.crc),
            "crc_algorithm": "CRC-16/CCITT-FALSE",
            "overhead_bits": FRAME_OVERHEAD_BITS,
            "payload_bits": self.payload_bits,
            "total_bits": self.total_bits,
        }


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, без отражения.

    Проверочный вектор: `crc16_ccitt(b"123456789") == 0x29B1`.
    """
    crc = CRC_INITIAL
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC_POLYNOMIAL) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def build_frame(
    payload: bytes,
    *,
    version: int = FRAME_VERSION,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> Frame:
    """Собрать кадр: преамбула, синхрослово, заголовок, payload, CRC.

    Args:
        payload: произвольные байты; текст кодируется в UTF-8 вызывающей стороной.
        version: версия формата, 1 байт.
        max_payload_bytes: верхняя граница размера payload.

    Raises:
        FrameError: payload пуст, слишком велик или не является байтами;
            версия вне диапазона 0..255.
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise FrameError(
            "payload должен быть bytes; текст кодируется в UTF-8 до вызова"
        )
    data = bytes(payload)
    if not data:
        raise FrameError("пустой payload: передавать нечего")
    if len(data) > max_payload_bytes:
        raise FrameError(
            f"payload {len(data)} байт превышает лимит {max_payload_bytes}"
        )
    if not isinstance(version, int) or isinstance(version, bool):
        raise FrameError("version должен быть целым числом")
    if not 0 <= version <= 0xFF:
        raise FrameError("version должен помещаться в 8 бит (0..255)")

    header = bytes((version,)) + len(data).to_bytes(2, "big")
    crc = crc16_ccitt(header + data)

    preamble = np.tile(np.array((1, 0), dtype=np.uint8), PREAMBLE_BITS // 2)
    bits = np.concatenate(
        [
            preamble,
            _uint_to_bits(SYNC_WORD, SYNC_BITS),
            _uint_to_bits(version, VERSION_BITS),
            _uint_to_bits(len(data), LENGTH_BITS),
            bytes_to_bits(data),
            _uint_to_bits(crc, CRC_BITS),
        ]
    )
    expected = FRAME_OVERHEAD_BITS + len(data) * 8
    if bits.size != expected:  # pragma: no cover - защита от ошибки в раскладке
        raise FrameError(f"внутренняя ошибка: {bits.size} бит вместо {expected}")

    return Frame(payload=data, version=version, crc=crc, bits=bits)


def _uint_to_bits(value: int, width: int) -> np.ndarray:
    if not 0 <= value < (1 << width):
        raise FrameError(f"значение {value} не помещается в {width} бит")
    return np.array(
        [(value >> shift) & 1 for shift in range(width - 1, -1, -1)],
        dtype=np.uint8,
    )


def _hex16(value: int) -> str:
    return f"0x{value:04X}"
