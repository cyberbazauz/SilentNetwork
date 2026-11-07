"""Acoustic Reliable Transport v1 (ART/1): подтверждение и выборочный повтор.

Модуль реализует логику надёжной доставки поверх кадров Frame v1, не выполняя
DSP и не открывая аудиоустройства:

* ``TransportPacket`` — формат пакета внутри payload Frame v1 (≤64 байта);
* ``StatusInfo`` — ACK/NACK-битовая карта всех чанков;
* ``ReliableSender`` — раунды, таймеры, повтор только отсутствующих чанков;
* ``ReliableReceiver`` — сборка, проверка SHA-256, формирование статуса.

Контракт: ``docs/acoustic-reliable-transport.md``. Frame v1, CRC-16,
BFSK/CPFSK и профили не меняются. CRC-16 и SHA-256 не аутентифицируют
отправителя, поэтому до SCNP/AEAD протокол пригоден только для несекретных
лабораторных данных.
"""

from __future__ import annotations

import hashlib
import math
import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from snlab.file_transfer import FilenameError, validate_filename
from snlab.frame import FRAME_OVERHEAD_BITS

#: Сигнатура уже зарезервирована чатом: текст с ``SF`` запрещён.
MAGIC = b"SF"
VERSION = 2
HEADER_BYTES = 12
MAX_FRAME_PAYLOAD_BYTES = 64
CHUNK_BYTES = MAX_FRAME_PAYLOAD_BYTES - HEADER_BYTES  # 52
MAX_MESSAGE_BYTES = 8192
MAX_CHUNKS = 255
MAX_FILENAME_BYTES = 40
MAX_WINDOW = 15

PACKET_MANIFEST = 1
PACKET_DATA = 2
PACKET_STATUS = 3
PACKET_ABORT = 4
PACKET_META = 5
PACKET_TYPE_NAMES = {
    PACKET_MANIFEST: "manifest",
    PACKET_DATA: "data",
    PACKET_STATUS: "status",
    PACKET_ABORT: "abort",
    PACKET_META: "meta",
}

ABORT_MANIFEST_INVALID = 1
ABORT_HASH_MISMATCH = 2
ABORT_MANIFEST_CONFLICT = 3
ABORT_META_CONFLICT = 4
ABORT_PADDING_INVALID = 5
ABORT_PROTOCOL_ERROR = 6
ABORT_TIMEOUT = 7
ABORT_ROUND_LIMIT = 8
ABORT_REASONS = {
    ABORT_MANIFEST_INVALID: "manifest_invalid",
    ABORT_HASH_MISMATCH: "hash_mismatch",
    ABORT_MANIFEST_CONFLICT: "manifest_conflict",
    ABORT_META_CONFLICT: "meta_conflict",
    ABORT_PADDING_INVALID: "padding_invalid",
    ABORT_PROTOCOL_ERROR: "protocol_error",
    ABORT_TIMEOUT: "timeout",
    ABORT_ROUND_LIMIT: "round_limit",
}

DEFAULT_WINDOW = 12
DEFAULT_SYMBOL_RATE = 300.0
DEFAULT_INTER_FRAME_GAP_MS = 250
DEFAULT_ACK_GUARD_MS = 500
DEFAULT_ACK_MARGIN_MS = 1500
DEFAULT_TURNAROUND_MS = 300
DEFAULT_MAX_ROUNDS = 96
DEFAULT_MAX_STALLED_ROUNDS = 5


class ReliableTransportError(ValueError):
    """Ошибка протокола надёжной доставки."""

    error_type = "reliable_transport_error"


class PacketFormatError(ReliableTransportError):
    error_type = "packet_invalid"


class UnsupportedVersionError(PacketFormatError):
    error_type = "packet_invalid"


class ManifestValidationError(PacketFormatError):
    error_type = "manifest_invalid"


class MessageTooLargeError(ReliableTransportError):
    error_type = "message_too_large"


class TransferStateError(ReliableTransportError):
    error_type = "protocol_error"


@dataclass(frozen=True, slots=True)
class Manifest:
    """Содержимое MANIFEST: неизменяемые параметры передачи."""

    total_size: int
    total_chunks: int
    chunk_bytes: int
    meta_present: bool
    sha256: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "sha256", bytes(self.sha256))
        if not isinstance(self.total_size, int) or isinstance(self.total_size, bool):
            raise ManifestValidationError("total_size должен быть целым числом")
        if not 0 <= self.total_size <= MAX_MESSAGE_BYTES:
            raise ManifestValidationError(
                f"total_size={self.total_size} вне диапазона 0..{MAX_MESSAGE_BYTES}"
            )
        expected = expected_chunks(self.total_size)
        if self.total_chunks != expected:
            raise ManifestValidationError(
                f"для {self.total_size} байт нужно {expected} чанков, "
                f"указано {self.total_chunks}"
            )
        if self.chunk_bytes != CHUNK_BYTES:
            raise ManifestValidationError(
                f"chunk_bytes={self.chunk_bytes}, ожидается {CHUNK_BYTES}"
            )
        if not isinstance(self.meta_present, bool):
            raise ManifestValidationError("meta_present должен быть bool")
        if not isinstance(self.sha256, (bytes, bytearray)) or len(self.sha256) != 32:
            raise ManifestValidationError("sha256 должен быть 32 байта")

    def to_payload(self) -> bytes:
        return (
            self.total_size.to_bytes(4, "big")
            + self.total_chunks.to_bytes(2, "big")
            + bytes((self.chunk_bytes, 1 if self.meta_present else 0))
            + bytes(self.sha256)
        )

    @classmethod
    def from_payload(cls, payload: bytes) -> "Manifest":
        data = bytes(payload)
        if len(data) != 40:
            raise ManifestValidationError(
                f"MANIFEST должен быть 40 байт, получено {len(data)}"
            )
        meta_byte = data[7]
        if meta_byte not in (0, 1):
            raise ManifestValidationError("meta_present должен быть 0 или 1")
        return cls(
            total_size=int.from_bytes(data[0:4], "big"),
            total_chunks=int.from_bytes(data[4:6], "big"),
            chunk_bytes=data[6],
            meta_present=bool(meta_byte),
            sha256=data[8:40],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_size": self.total_size,
            "total_chunks": self.total_chunks,
            "chunk_bytes": self.chunk_bytes,
            "meta_present": self.meta_present,
            "sha256": self.sha256.hex(),
        }


@dataclass(frozen=True, slots=True)
class StatusInfo:
    """ACK/NACK-статус: карта всех чанков передачи."""

    complete: bool
    meta_ok: bool
    total_chunks: int
    received_count: int
    bitmap: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "bitmap", bytes(self.bitmap))
        if not 0 <= self.total_chunks <= MAX_CHUNKS:
            raise PacketFormatError(
                f"total_chunks={self.total_chunks} вне диапазона 0..{MAX_CHUNKS}"
            )
        expected_len = bitmap_length(self.total_chunks)
        if len(self.bitmap) != expected_len:
            raise PacketFormatError(
                f"карта занимает {len(self.bitmap)} байт, "
                f"для {self.total_chunks} чанков нужно {expected_len}"
            )
        if not 0 <= self.received_count <= self.total_chunks:
            raise PacketFormatError(
                f"received_count={self.received_count} вне 0..{self.total_chunks}"
            )
        actual = bitmap_count(self.bitmap)
        if actual != self.received_count:
            raise PacketFormatError(
                f"received_count={self.received_count}, "
                f"а в карте {actual} единичных бит"
            )

    def to_payload(self) -> bytes:
        flags = (1 if self.complete else 0) | (2 if self.meta_ok else 0)
        return (
            bytes((flags,))
            + self.total_chunks.to_bytes(2, "big")
            + self.received_count.to_bytes(2, "big")
            + bytes(self.bitmap)
        )

    @classmethod
    def from_payload(cls, payload: bytes) -> "StatusInfo":
        data = bytes(payload)
        if len(data) < 5:
            raise PacketFormatError("STATUS короче 5 байт")
        flags = data[0]
        if flags & ~0x03:
            raise PacketFormatError(f"неизвестные флаги STATUS: 0x{flags:02X}")
        total_chunks = int.from_bytes(data[1:3], "big")
        received_count = int.from_bytes(data[3:5], "big")
        return cls(
            complete=bool(flags & 0x01),
            meta_ok=bool(flags & 0x02),
            total_chunks=total_chunks,
            received_count=received_count,
            bitmap=data[5:],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "meta_ok": self.meta_ok,
            "total_chunks": self.total_chunks,
            "received_count": self.received_count,
            "missing": list(bitmap_missing(self.bitmap, self.total_chunks)),
        }


@dataclass(frozen=True, slots=True)
class AbortInfo:
    """Содержимое ABORT."""

    reason: int
    message: str = ""

    def __post_init__(self) -> None:
        if self.reason not in ABORT_REASONS:
            raise PacketFormatError(f"неизвестная причина ABORT: {self.reason}")
        encoded = self.message.encode("utf-8")
        if len(encoded) > MAX_FILENAME_BYTES:
            raise PacketFormatError(
                f"сообщение ABORT {len(encoded)} байт превышает "
                f"{MAX_FILENAME_BYTES}"
            )

    @property
    def error_type(self) -> str:
        return ABORT_REASONS[self.reason]

    def to_payload(self) -> bytes:
        return bytes((self.reason,)) + self.message.encode("utf-8")

    @classmethod
    def from_payload(cls, payload: bytes) -> "AbortInfo":
        data = bytes(payload)
        if not 1 <= len(data) <= 1 + MAX_FILENAME_BYTES:
            raise PacketFormatError("ABORT должен быть 1..41 байт")
        reason = data[0]
        if reason not in ABORT_REASONS:
            raise PacketFormatError(f"неизвестная причина ABORT: {reason}")
        try:
            message = data[1:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PacketFormatError(f"сообщение ABORT не UTF-8: {exc.reason}") from exc
        return cls(reason=reason, message=message)


def expected_chunks(total_size: int) -> int:
    """Сколько чанков нужно для сообщения данного размера."""
    if not isinstance(total_size, int) or isinstance(total_size, bool):
        raise ReliableTransportError("размер сообщения должен быть целым числом")
    if not 0 <= total_size <= MAX_MESSAGE_BYTES:
        raise MessageTooLargeError(
            f"сообщение {total_size} байт не входит в лимит 0..{MAX_MESSAGE_BYTES}"
        )
    if total_size == 0:
        return 0
    return (total_size + CHUNK_BYTES - 1) // CHUNK_BYTES


def bitmap_length(total_chunks: int) -> int:
    """Длина битовой карты в байтах."""
    if not isinstance(total_chunks, int) or isinstance(total_chunks, bool):
        raise PacketFormatError("total_chunks должен быть целым числом")
    if not 0 <= total_chunks <= MAX_CHUNKS:
        raise PacketFormatError(
            f"total_chunks={total_chunks} вне диапазона 0..{MAX_CHUNKS}"
        )
    return (total_chunks + 7) // 8


def bitmap_has(bitmap: bytes, seq: int) -> bool:
    """Проверить бит чанка; нумерация — от старшего бита первого байта."""
    if not 0 <= seq < len(bitmap) * 8:
        return False
    return bool(bitmap[seq // 8] & (1 << (7 - (seq % 8))))


def bitmap_set(bitmap: bytearray, seq: int) -> None:
    """Установить бит чанка в изменяемой карте."""
    if not 0 <= seq < len(bitmap) * 8:
        raise PacketFormatError(f"seq={seq} вне карты")
    bitmap[seq // 8] |= 1 << (7 - (seq % 8))


def bitmap_count(bitmap: bytes) -> int:
    """Число единичных бит."""
    return sum(byte.bit_count() for byte in bitmap)


def bitmap_missing(bitmap: bytes, total_chunks: int) -> tuple[int, ...]:
    """Вернуть номера отсутствующих чанков."""
    if len(bitmap) != bitmap_length(total_chunks):
        raise PacketFormatError("длина карты не соответствует total_chunks")
    return tuple(seq for seq in range(total_chunks) if not bitmap_has(bitmap, seq))


def build_bitmap(seqs: Iterable[int], total_chunks: int) -> bytes:
    """Построить карту из набора принятых номеров."""
    buffer = bytearray(bitmap_length(total_chunks))
    for seq in seqs:
        if not 0 <= seq < total_chunks:
            raise PacketFormatError(f"seq={seq} вне 0..{total_chunks - 1}")
        bitmap_set(buffer, seq)
    return bytes(buffer)


@dataclass(frozen=True, slots=True)
class TransportPacket:
    """Пакет ART/1 внутри payload Frame v1."""

    packet_type: int
    transfer_id: int
    session_id: int
    round: int
    seq: int
    slot: int
    slots: int
    payload: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", bytes(self.payload))
        if self.packet_type not in PACKET_TYPE_NAMES:
            raise PacketFormatError(f"неизвестный тип пакета: {self.packet_type}")
        _check_uint(self.transfer_id, 8, "transfer_id")
        _check_uint(self.session_id, 16, "session_id")
        _check_uint(self.round, 8, "round")
        _check_uint(self.seq, 16, "seq")
        _check_uint(self.slot, 4, "slot")
        _check_uint(self.slots, 4, "slots")
        if len(self.payload) > CHUNK_BYTES:
            raise PacketFormatError(
                f"payload {len(self.payload)} байт превышает {CHUNK_BYTES}"
            )

        if self.packet_type == PACKET_DATA:
            if not 1 <= self.slots <= MAX_WINDOW:
                raise PacketFormatError("у DATA slots должен быть 1..15")
            if not 0 <= self.slot < self.slots:
                raise PacketFormatError("у DATA slot должен быть меньше slots")
            if self.seq >= MAX_CHUNKS:
                raise PacketFormatError(f"seq={self.seq} вне 0..{MAX_CHUNKS - 1}")
            if len(self.payload) != CHUNK_BYTES:
                raise PacketFormatError(
                    f"DATA должен быть ровно {CHUNK_BYTES} байт"
                )
            return

        if self.packet_type == PACKET_MANIFEST:
            if self.slot != 0:
                raise PacketFormatError("у MANIFEST slot должен быть 0")
            if self.seq != 0:
                raise PacketFormatError("у MANIFEST seq должен быть 0")
            Manifest.from_payload(self.payload)
            return

        if self.slot != 0 or self.slots != 0:
            raise PacketFormatError(
                f"для {self.type_name} slot/slots должны быть 0"
            )

        if self.packet_type == PACKET_STATUS:
            if self.seq != 0:
                raise PacketFormatError("у STATUS seq должен быть 0")
            StatusInfo.from_payload(self.payload)
        elif self.packet_type == PACKET_ABORT:
            if self.seq != 0:
                raise PacketFormatError("у ABORT seq должен быть 0")
            AbortInfo.from_payload(self.payload)
        elif self.packet_type == PACKET_META:
            if self.seq != 0:
                raise PacketFormatError("у META seq должен быть 0")
            _parse_meta_payload(self.payload)

    @property
    def type_name(self) -> str:
        return PACKET_TYPE_NAMES[self.packet_type]

    def to_bytes(self) -> bytes:
        slot_slots = (self.slot << 4) | self.slots
        return (
            MAGIC
            + bytes(
                (
                    VERSION,
                    self.packet_type,
                    self.transfer_id,
                    self.round,
                )
            )
            + self.seq.to_bytes(2, "big")
            + self.session_id.to_bytes(2, "big")
            + bytes((len(self.payload), slot_slots))
            + self.payload
        )

    @classmethod
    def parse(cls, raw: bytes) -> "TransportPacket":
        data = bytes(raw)
        if len(data) < HEADER_BYTES:
            raise PacketFormatError(f"пакет короче заголовка ({len(data)} байт)")
        if data[:2] != MAGIC:
            raise PacketFormatError("нет сигнатуры SF")
        version = data[2]
        if version != VERSION:
            raise UnsupportedVersionError(
                f"версия ART {version} не поддерживается, ожидается {VERSION}"
            )
        payload_len = data[10]
        if len(data) != HEADER_BYTES + payload_len:
            raise PacketFormatError(
                f"payload_len={payload_len}, а пакет занимает "
                f"{len(data) - HEADER_BYTES} байт"
            )
        slot_slots = data[11]
        return cls(
            packet_type=data[3],
            transfer_id=data[4],
            round=data[5],
            seq=int.from_bytes(data[6:8], "big"),
            session_id=int.from_bytes(data[8:10], "big"),
            slot=slot_slots >> 4,
            slots=slot_slots & 0x0F,
            payload=data[HEADER_BYTES:],
        )

    def manifest_info(self) -> Manifest:
        if self.packet_type != PACKET_MANIFEST:
            raise TransferStateError("пакет не является MANIFEST")
        return Manifest.from_payload(self.payload)

    def status_info(self) -> StatusInfo:
        if self.packet_type != PACKET_STATUS:
            raise TransferStateError("пакет не является STATUS")
        return StatusInfo.from_payload(self.payload)

    def abort_info(self) -> AbortInfo:
        if self.packet_type != PACKET_ABORT:
            raise TransferStateError("пакет не является ABORT")
        return AbortInfo.from_payload(self.payload)

    def meta_name(self) -> str:
        if self.packet_type != PACKET_META:
            raise TransferStateError("пакет не является META")
        return _parse_meta_payload(self.payload)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "transfer_id": self.transfer_id,
            "session_id": self.session_id,
            "round": self.round,
            "seq": self.seq,
            "slot": self.slot,
            "slots": self.slots,
            "payload_bytes": len(self.payload),
            "packet_hex": self.to_bytes().hex(),
        }


def _check_uint(value: int, width: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PacketFormatError(f"{name} должен быть целым числом")
    if not 0 <= value < (1 << width):
        raise PacketFormatError(f"{name}={value} не помещается в {width} бит")


def _parse_meta_payload(payload: bytes) -> str:
    data = bytes(payload)
    if len(data) < 2:
        raise PacketFormatError("META короче 2 байт")
    name_len = data[0]
    if not 1 <= name_len <= MAX_FILENAME_BYTES:
        raise PacketFormatError(f"недопустимая длина имени: {name_len}")
    if len(data) != 1 + name_len:
        raise PacketFormatError(
            f"name_len={name_len}, а имя занимает {len(data) - 1} байт"
        )
    try:
        name = data[1:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PacketFormatError(f"имя файла не UTF-8: {exc.reason}") from exc
    try:
        canonical = validate_filename(name)
    except FilenameError as exc:
        raise PacketFormatError(str(exc)) from exc
    if canonical != data[1:]:
        raise PacketFormatError("имя файла не в канонической форме UTF-8")
    return name


def build_meta_payload(filename: str) -> bytes:
    try:
        encoded = validate_filename(filename)
    except FilenameError as exc:
        raise ReliableTransportError(str(exc)) from exc
    return bytes((len(encoded),)) + encoded


def build_status_payload(
    received: Iterable[int],
    total_chunks: int,
    *,
    complete: bool,
    meta_ok: bool,
) -> bytes:
    seqs = tuple(dict.fromkeys(received))
    bitmap = build_bitmap(seqs, total_chunks)
    return StatusInfo(
        complete=complete,
        meta_ok=meta_ok,
        total_chunks=total_chunks,
        received_count=len(seqs),
        bitmap=bitmap,
    ).to_payload()


@dataclass(frozen=True, slots=True)
class TransportConfig:
    """Тайминги и лимиты ART/1.

    Обе стороны должны использовать одинаковые ``symbol_rate``,
    ``inter_frame_gap_ms`` и профиль BFSK; они не передаются в пакетах.
    """

    window: int = DEFAULT_WINDOW
    symbol_rate: float = DEFAULT_SYMBOL_RATE
    inter_frame_gap_ms: int = DEFAULT_INTER_FRAME_GAP_MS
    ack_guard_ms: int = DEFAULT_ACK_GUARD_MS
    ack_margin_ms: int = DEFAULT_ACK_MARGIN_MS
    turnaround_ms: int = DEFAULT_TURNAROUND_MS
    max_rounds: int = DEFAULT_MAX_ROUNDS
    max_stalled_rounds: int = DEFAULT_MAX_STALLED_ROUNDS
    max_message_bytes: int = MAX_MESSAGE_BYTES

    def __post_init__(self) -> None:
        for name in (
            "window",
            "inter_frame_gap_ms",
            "ack_guard_ms",
            "ack_margin_ms",
            "turnaround_ms",
            "max_rounds",
            "max_stalled_rounds",
            "max_message_bytes",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ReliableTransportError(f"{name} должен быть неотрицательным целым")
        if not 1 <= self.window <= MAX_WINDOW:
            raise ReliableTransportError(f"window должен быть 1..{MAX_WINDOW}")
        if not 1 <= self.max_rounds <= 255:
            raise ReliableTransportError("max_rounds должен быть 1..255")
        if self.max_stalled_rounds < 1:
            raise ReliableTransportError("max_stalled_rounds должен быть ≥ 1")
        if self.max_message_bytes < 1:
            raise ReliableTransportError("max_message_bytes должен быть ≥ 1")
        if (
            not isinstance(self.symbol_rate, (int, float))
            or isinstance(self.symbol_rate, bool)
            or not math.isfinite(self.symbol_rate)
            or self.symbol_rate <= 0
        ):
            raise ReliableTransportError("symbol_rate должен быть положительным")

    def frame_duration_ms(self, payload_bytes: int) -> int:
        """Длительность Frame v1 с данным payload, округлённая вверх до мс."""
        if not 0 <= payload_bytes <= MAX_FRAME_PAYLOAD_BYTES:
            raise ReliableTransportError("payload_bytes вне 0..64")
        bits = FRAME_OVERHEAD_BITS + payload_bytes * 8
        return max(1, int(math.ceil(bits * 1000.0 / self.symbol_rate)))

    @property
    def data_frame_ms(self) -> int:
        return self.frame_duration_ms(CHUNK_BYTES)

    def status_frame_ms(self, total_chunks: int) -> int:
        return self.frame_duration_ms(5 + bitmap_length(total_chunks))


class SenderState(str, Enum):
    IDLE = "idle"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"


class ReceiverState(str, Enum):
    IDLE = "idle"
    ASSEMBLING = "assembling"
    COMPLETE = "complete"
    ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class OutboundRound:
    """Раунд, который вызывающая сторона должна воспроизвести целиком."""

    round: int
    packets: tuple[TransportPacket, ...]
    missing_before: tuple[int, ...]
    start_ms: int
    duration_ms: int
    round_end_ms: int
    listen_until_ms: int

    @property
    def data_seqs(self) -> tuple[int, ...]:
        return tuple(
            packet.seq
            for packet in self.packets
            if packet.packet_type == PACKET_DATA
        )

    @property
    def slots(self) -> int:
        return len(self.data_seqs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "packets": [packet.type_name for packet in self.packets],
            "data_seqs": list(self.data_seqs),
            "missing_before": list(self.missing_before),
            "start_ms": self.start_ms,
            "duration_ms": self.duration_ms,
            "round_end_ms": self.round_end_ms,
            "listen_until_ms": self.listen_until_ms,
        }


@dataclass(frozen=True, slots=True)
class SenderProgress:
    state: SenderState
    event: str
    round: OutboundRound | None = None
    missing: tuple[int, ...] = ()
    error_type: str | None = None
    message: str = ""
    rounds_sent: int = 0
    data_frames_sent: int = 0
    retransmitted_frames: int = 0
    statuses_received: int = 0


@dataclass(frozen=True, slots=True)
class ReceiverProgress:
    state: ReceiverState
    event: str
    received: int = 0
    total: int = 0
    complete: bool = False
    meta_ok: bool = False
    delivered: bytes | None = None
    abort: TransportPacket | None = None
    error_type: str | None = None
    message: str = ""


class ReliableSender:
    """Отправитель ART/1: раунды, таймеры и выборочный повтор.

    Вызывающая сторона:

    1. зовёт :meth:`advance`, пока не получит ``round``;
    2. воспроизводит все пакеты раунда по порядку;
    3. передаёт в :meth:`handle_packet` каждый декодированный ``STATUS``
       или ``ABORT``;
    4. снова зовёт :meth:`advance`.
    """

    def __init__(
        self,
        data: bytes,
        transfer_id: int,
        *,
        config: TransportConfig | None = None,
        session_id: int | None = None,
        filename: str | None = None,
    ) -> None:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ReliableTransportError("данные должны быть bytes")
        content = bytes(data)
        self._config = config or TransportConfig()
        if len(content) > self._config.max_message_bytes:
            raise MessageTooLargeError(
                f"сообщение {len(content)} байт превышает лимит "
                f"{self._config.max_message_bytes}"
            )
        _check_uint(transfer_id, 8, "transfer_id")
        self.transfer_id = transfer_id
        if session_id is None:
            session_id = secrets.randbelow(1 << 16)
        _check_uint(session_id, 16, "session_id")
        self.session_id = session_id
        self.filename = filename
        self._filename_bytes = (
            build_meta_payload(filename) if filename is not None else None
        )
        self.total_chunks = expected_chunks(len(content))
        self._manifest = Manifest(
            total_size=len(content),
            total_chunks=self.total_chunks,
            chunk_bytes=CHUNK_BYTES,
            meta_present=self._filename_bytes is not None,
            sha256=hashlib.sha256(content).digest(),
        )
        self._chunks = tuple(
            content[index * CHUNK_BYTES : (index + 1) * CHUNK_BYTES].ljust(
                CHUNK_BYTES, b"\x00"
            )
            for index in range(self.total_chunks)
        )
        self._state = SenderState.IDLE
        self._missing = set(range(self.total_chunks))
        self._current: OutboundRound | None = None
        self._needs_round = True
        self._not_before_ms = 0
        self._next_round = 0
        self._rounds_sent = 0
        self._stalled_rounds = 0
        self._confirm_rounds = 0
        self._data_frames_sent = 0
        self._retransmitted_frames = 0
        self._sent_once: set[int] = set()
        self._statuses_received = 0
        self._meta_acked = self._filename_bytes is None

    @property
    def state(self) -> SenderState:
        return self._state

    @property
    def missing(self) -> tuple[int, ...]:
        return tuple(sorted(self._missing))

    @property
    def current_round(self) -> OutboundRound | None:
        return self._current

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    @property
    def total_size(self) -> int:
        return self._manifest.total_size

    @property
    def rounds_sent(self) -> int:
        return self._rounds_sent

    @property
    def data_frames_sent(self) -> int:
        return self._data_frames_sent

    @property
    def retransmitted_frames(self) -> int:
        return self._retransmitted_frames

    @property
    def statuses_received(self) -> int:
        return self._statuses_received

    def advance(self, now_ms: int) -> SenderProgress:
        """Продвинуть таймеры и при необходимости выдать новый раунд."""
        _check_time(now_ms)
        if self._state in (SenderState.DONE, SenderState.FAILED):
            return self._progress("finished")

        current = self._current
        if current is not None:
            if now_ms < current.listen_until_ms:
                return self._progress("waiting")
            self._current = None
            self._stalled_rounds += 1
            if self._stalled_rounds > self._config.max_stalled_rounds:
                return self._fail("ack_timeout", "нет STATUS за отведённое окно")
            self._needs_round = True
            self._not_before_ms = now_ms + self._config.turnaround_ms

        if self._needs_round and now_ms >= self._not_before_ms:
            return self._emit_round(now_ms)
        return self._progress("waiting")

    def handle_packet(self, packet: TransportPacket, now_ms: int) -> SenderProgress:
        """Обработать STATUS или ABORT от приёмника."""
        _check_time(now_ms)
        if self._state in (SenderState.DONE, SenderState.FAILED):
            return self._progress("finished")
        if (
            packet.transfer_id != self.transfer_id
            or packet.session_id != self.session_id
        ):
            return self._progress("ignored")
        if packet.packet_type == PACKET_ABORT:
            info = packet.abort_info()
            return self._fail(info.error_type, info.message or "приёмник прервал передачу")
        if packet.packet_type != PACKET_STATUS:
            return self._progress("ignored")
        current = self._current
        if current is None or packet.round != current.round:
            return self._progress("stale_status")
        info = packet.status_info()
        if info.total_chunks != self.total_chunks:
            return self._progress("ignored")
        new_missing = set(bitmap_missing(info.bitmap, self.total_chunks))
        if not new_missing <= self._missing:
            return self._progress("inconsistent_status")
        if info.complete and new_missing:
            return self._progress("inconsistent_status")

        previous_missing = len(self._missing)
        if self.filename is not None and info.meta_ok:
            self._meta_acked = True
        self._missing = new_missing
        self._statuses_received += 1
        self._current = None
        self._needs_round = True
        self._not_before_ms = now_ms + self._config.turnaround_ms

        if len(self._missing) < previous_missing:
            self._stalled_rounds = 0
            self._confirm_rounds = 0
        elif not info.complete:
            self._stalled_rounds += 1
            if self._stalled_rounds > self._config.max_stalled_rounds:
                return self._fail("ack_timeout", "нет прогресса по карте принятых")

        if info.complete and not self._missing:
            if self.filename is not None and not self._meta_acked:
                return self._progress("meta_required")
            self._state = SenderState.DONE
            return self._progress("completed")
        if not self._missing:
            return self._progress("needs_confirmation")
        return self._progress("status")

    def abort_packet(
        self, reason: int = ABORT_TIMEOUT, message: str = ""
    ) -> TransportPacket:
        """Собрать ABORT и перевести отправитель в FAILED."""
        info = AbortInfo(reason=reason, message=message)
        self._state = SenderState.FAILED
        self._current = None
        self._needs_round = False
        current_round = self._current_round_number()
        return TransportPacket(
            packet_type=PACKET_ABORT,
            transfer_id=self.transfer_id,
            session_id=self.session_id,
            round=current_round,
            seq=0,
            slot=0,
            slots=0,
            payload=info.to_payload(),
        )

    def _emit_round(self, now_ms: int) -> SenderProgress:
        if (
            self._rounds_sent >= self._config.max_rounds
            or self._next_round > 0xFF
        ):
            return self._fail("round_limit", "превышен лимит раундов")
        send_seqs = sorted(self._missing)[: self._config.window]
        if not send_seqs and self._confirm_rounds >= self._config.max_stalled_rounds:
            return self._fail(
                "confirmation_timeout", "приёмник не подтвердил завершение"
            )

        round_no = self._next_round
        self._next_round += 1
        slots = len(send_seqs)
        packets: list[TransportPacket] = [
            TransportPacket(
                packet_type=PACKET_MANIFEST,
                transfer_id=self.transfer_id,
                session_id=self.session_id,
                round=round_no,
                seq=0,
                slot=0,
                slots=slots,
                payload=self._manifest.to_payload(),
            )
        ]
        if self._filename_bytes is not None and not self._meta_acked:
            packets.append(
                TransportPacket(
                    packet_type=PACKET_META,
                    transfer_id=self.transfer_id,
                    session_id=self.session_id,
                    round=round_no,
                    seq=0,
                    slot=0,
                    slots=0,
                    payload=self._filename_bytes,
                )
            )
        for slot, seq in enumerate(send_seqs):
            packets.append(
                TransportPacket(
                    packet_type=PACKET_DATA,
                    transfer_id=self.transfer_id,
                    session_id=self.session_id,
                    round=round_no,
                    seq=seq,
                    slot=slot,
                    slots=slots,
                    payload=self._chunks[seq],
                )
            )
            if seq in self._sent_once:
                self._retransmitted_frames += 1
            else:
                self._sent_once.add(seq)
            self._data_frames_sent += 1

        duration_ms = 0
        for index, packet in enumerate(packets):
            duration_ms += self._config.frame_duration_ms(len(packet.to_bytes()))
            if index + 1 < len(packets):
                duration_ms += self._config.inter_frame_gap_ms
        round_end_ms = now_ms + duration_ms
        listen_until_ms = (
            round_end_ms
            + self._config.ack_guard_ms
            + self._config.status_frame_ms(self.total_chunks)
            + self._config.ack_margin_ms
        )
        current = OutboundRound(
            round=round_no,
            packets=tuple(packets),
            missing_before=tuple(sorted(self._missing)),
            start_ms=now_ms,
            duration_ms=duration_ms,
            round_end_ms=round_end_ms,
            listen_until_ms=listen_until_ms,
        )
        self._current = current
        self._needs_round = False
        self._rounds_sent += 1
        if not send_seqs:
            self._confirm_rounds += 1
        if self._state is SenderState.IDLE:
            self._state = SenderState.WAITING
        return self._progress("round", round=current)

    def _current_round_number(self) -> int:
        if self._current is not None:
            return self._current.round
        return max(0, min(255, self._next_round - 1))

    def _fail(self, error_type: str, message: str) -> SenderProgress:
        self._state = SenderState.FAILED
        self._current = None
        self._needs_round = False
        return self._progress("failed", error_type=error_type, message=message)

    def _progress(
        self,
        event: str,
        *,
        round: OutboundRound | None = None,
        error_type: str | None = None,
        message: str = "",
    ) -> SenderProgress:
        return SenderProgress(
            state=self._state,
            event=event,
            round=round,
            missing=tuple(sorted(self._missing)),
            error_type=error_type,
            message=message,
            rounds_sent=self._rounds_sent,
            data_frames_sent=self._data_frames_sent,
            retransmitted_frames=self._retransmitted_frames,
            statuses_received=self._statuses_received,
        )


class ReliableReceiver:
    """Приёмник ART/1: сборка, SHA-256 и ACK/NACK-карта.

    ``handle_packet`` принимает пакеты, ``poll`` возвращает готовый ``STATUS``,
    когда наступает время ответа. Обе функции не блокируются и не работают с
    аудиоустройствами.
    """

    def __init__(self, config: TransportConfig | None = None) -> None:
        self._config = config or TransportConfig()
        self._state = ReceiverState.IDLE
        self._session_id: int | None = None
        self._transfer_id: int | None = None
        self._manifest: Manifest | None = None
        self._chunks: list[bytes | None] = []
        self._received_count = 0
        self._duplicates = 0
        self._meta_bytes: bytes | None = None
        self._meta_ok = False
        self._pending_meta: tuple[int, int, bytes] | None = None
        self._complete = False
        self._delivered: bytes | None = None
        self._last_manifest_round = -1
        self._status_round: int | None = None
        self._status_deadline_ms: int | None = None
        self._status_sent_round: int | None = None

    @property
    def state(self) -> ReceiverState:
        return self._state

    @property
    def session_id(self) -> int | None:
        return self._session_id

    @property
    def manifest(self) -> Manifest | None:
        return self._manifest

    @property
    def received_count(self) -> int:
        return self._received_count

    @property
    def duplicates(self) -> int:
        return self._duplicates

    @property
    def complete(self) -> bool:
        return self._complete

    @property
    def delivered(self) -> bytes | None:
        return self._delivered

    @property
    def filename(self) -> str | None:
        if self._meta_bytes is None:
            return None
        return self._meta_bytes[1:].decode("utf-8")

    def handle_packet(
        self, packet: TransportPacket, now_ms: int
    ) -> ReceiverProgress:
        """Принять MANIFEST, DATA, META или ABORT."""
        _check_time(now_ms)
        if self._state is ReceiverState.ABORTED:
            return self._progress("ignored")

        if packet.packet_type == PACKET_ABORT:
            if self._session_id is None or (
                packet.session_id == self._session_id
                and packet.transfer_id == self._transfer_id
            ):
                info = packet.abort_info()
                self._abort_state()
                return self._progress(
                    "aborted", error_type=info.error_type, message=info.message
                )
            return self._progress("ignored")

        if packet.packet_type == PACKET_META:
            return self._handle_meta(packet)

        if packet.packet_type == PACKET_MANIFEST:
            return self._handle_manifest(packet, now_ms)

        if packet.packet_type == PACKET_DATA:
            return self._handle_data(packet, now_ms)

        return self._progress("ignored")

    def poll(self, now_ms: int) -> TransportPacket | None:
        """Вернуть STATUS, если наступило время ответа."""
        _check_time(now_ms)
        if (
            self._state is ReceiverState.ABORTED
            or self._manifest is None
            or self._status_deadline_ms is None
        ):
            return None
        if now_ms < self._status_deadline_ms:
            return None
        if self._status_sent_round == self._status_round:
            return None
        self._status_sent_round = self._status_round
        return self._build_status()

    def abort_packet(
        self, reason: int, message: str = ""
    ) -> TransportPacket | None:
        """Собрать ABORT для текущей сессии и очистить частичную сборку."""
        if self._session_id is None or self._transfer_id is None:
            return None
        info = AbortInfo(reason=reason, message=message)
        packet = TransportPacket(
            packet_type=PACKET_ABORT,
            transfer_id=self._transfer_id,
            session_id=self._session_id,
            round=max(0, min(255, self._status_round or 0)),
            seq=0,
            slot=0,
            slots=0,
            payload=info.to_payload(),
        )
        self._abort_state()
        return packet

    def _handle_meta(self, packet: TransportPacket) -> ReceiverProgress:
        name_bytes = packet.payload
        if self._manifest is None:
            self._pending_meta = (
                packet.transfer_id,
                packet.session_id,
                name_bytes,
            )
            return self._progress("meta_buffered", meta_ok=self._meta_ok)
        if (
            packet.session_id != self._session_id
            or packet.transfer_id != self._transfer_id
        ):
            return self._progress("ignored")
        if self._meta_bytes is not None and self._meta_bytes != name_bytes:
            return self._abort_progress(ABORT_META_CONFLICT, "meta conflict")
        self._meta_bytes = name_bytes
        self._meta_ok = True
        return self._progress("meta", meta_ok=True)

    def _handle_manifest(
        self, packet: TransportPacket, now_ms: int
    ) -> ReceiverProgress:
        manifest = packet.manifest_info()
        event = "manifest"

        if self._session_id is None:
            self._start_session(packet, manifest)
        elif (
            packet.session_id != self._session_id
            or packet.transfer_id != self._transfer_id
        ):
            # v1 — лабораторный протокол с одной активной передачей. Новая
            # сессия означает перезапуск отправителя и заменяет частичную
            # сборку; аутентификации в v1 нет.
            event = "session_replaced"
            self._start_session(packet, manifest)
        else:
            assert self._manifest is not None
            if not self._same_manifest(self._manifest, manifest):
                return self._abort_progress(
                    ABORT_MANIFEST_CONFLICT,
                    "manifest conflict",
                )

        if packet.round >= self._last_manifest_round:
            self._last_manifest_round = packet.round
            self._status_round = packet.round
            self._status_sent_round = None
            if packet.slots:
                delay = packet.slots * (
                    self._config.data_frame_ms
                    + self._config.inter_frame_gap_ms
                )
            else:
                delay = 0
            self._status_deadline_ms = now_ms + delay + self._config.ack_guard_ms

        if self._manifest is not None and self._manifest.total_chunks == 0:
            return self._finish_if_ready(event)
        return self._progress(event)

    def _handle_data(
        self, packet: TransportPacket, now_ms: int
    ) -> ReceiverProgress:
        manifest = self._manifest
        if manifest is None:
            return self._progress("data_before_manifest")
        if (
            packet.session_id != self._session_id
            or packet.transfer_id != self._transfer_id
        ):
            return self._progress("ignored")
        if not 0 <= packet.seq < manifest.total_chunks:
            return self._progress("ignored")

        valid_bytes = min(
            CHUNK_BYTES, manifest.total_size - packet.seq * CHUNK_BYTES
        )
        if any(packet.payload[valid_bytes:]):
            return self._abort_progress(
                ABORT_PADDING_INVALID, "padding invalid"
            )

        self._update_status_deadline(packet, now_ms)
        if self._chunks[packet.seq] is not None:
            self._duplicates += 1
            return self._progress("duplicate", meta_ok=self._meta_ok)

        self._chunks[packet.seq] = packet.payload
        self._received_count += 1
        if self._received_count == manifest.total_chunks:
            return self._finish_if_ready("progress")
        return self._progress("progress", meta_ok=self._meta_ok)

    def _update_status_deadline(
        self, packet: TransportPacket, now_ms: int
    ) -> None:
        if self._status_round != packet.round:
            return
        if self._status_sent_round == packet.round:
            return
        remaining_slots = packet.slots - 1 - packet.slot
        delay = remaining_slots * (
            self._config.data_frame_ms + self._config.inter_frame_gap_ms
        )
        deadline = now_ms + delay + self._config.ack_guard_ms
        if self._status_deadline_ms is None:
            self._status_deadline_ms = deadline
        else:
            self._status_deadline_ms = max(self._status_deadline_ms, deadline)

    def _finish_if_ready(self, event: str) -> ReceiverProgress:
        manifest = self._manifest
        assert manifest is not None
        if self._complete:
            return self._progress("already_complete", meta_ok=self._meta_ok)
        if self._received_count != manifest.total_chunks:
            return self._progress(event, meta_ok=self._meta_ok)
        content = b"".join(
            chunk for chunk in self._chunks if chunk is not None
        )[: manifest.total_size]
        if hashlib.sha256(content).digest() != manifest.sha256:
            return self._abort_progress(
                ABORT_HASH_MISMATCH, "hash mismatch"
            )
        self._complete = True
        self._delivered = content
        self._state = ReceiverState.COMPLETE
        return self._progress("completed", meta_ok=self._meta_ok)

    def _build_status(self) -> TransportPacket:
        manifest = self._manifest
        assert manifest is not None and self._session_id is not None
        assert self._transfer_id is not None
        received = [
            seq for seq, chunk in enumerate(self._chunks) if chunk is not None
        ]
        payload = build_status_payload(
            received,
            manifest.total_chunks,
            complete=self._complete,
            meta_ok=self._meta_ok,
        )
        return TransportPacket(
            packet_type=PACKET_STATUS,
            transfer_id=self._transfer_id,
            session_id=self._session_id,
            round=max(0, min(255, self._status_round or 0)),
            seq=0,
            slot=0,
            slots=0,
            payload=payload,
        )

    def _start_session(
        self, packet: TransportPacket, manifest: Manifest
    ) -> None:
        self._session_id = packet.session_id
        self._transfer_id = packet.transfer_id
        self._manifest = manifest
        self._chunks = [None] * manifest.total_chunks
        self._received_count = 0
        self._duplicates = 0
        self._meta_bytes = None
        self._meta_ok = False
        self._complete = False
        self._delivered = None
        self._state = ReceiverState.ASSEMBLING
        self._last_manifest_round = -1
        self._status_round = None
        self._status_deadline_ms = None
        self._status_sent_round = None
        pending = self._pending_meta
        self._pending_meta = None
        if pending is not None:
            transfer_id, session_id, name_bytes = pending
            if (
                transfer_id == packet.transfer_id
                and session_id == packet.session_id
            ):
                self._meta_bytes = name_bytes
                self._meta_ok = True

    def _abort_progress(self, reason: int, message: str) -> ReceiverProgress:
        info = AbortInfo(reason=reason, message=message)
        abort = self.abort_packet(reason, message)
        return self._progress(
            "aborted",
            abort=abort,
            error_type=info.error_type,
            message=message,
        )

    def _abort_state(self) -> None:
        self._state = ReceiverState.ABORTED
        self._chunks = []
        self._received_count = 0
        self._manifest = None
        self._complete = False
        self._delivered = None
        self._status_deadline_ms = None
        self._status_round = None
        self._status_sent_round = None

    @staticmethod
    def _same_manifest(left: Manifest, right: Manifest) -> bool:
        return (
            left.total_size == right.total_size
            and left.total_chunks == right.total_chunks
            and left.chunk_bytes == right.chunk_bytes
            and left.meta_present == right.meta_present
            and left.sha256 == right.sha256
        )

    def _progress(
        self,
        event: str,
        *,
        meta_ok: bool | None = None,
        abort: TransportPacket | None = None,
        error_type: str | None = None,
        message: str = "",
    ) -> ReceiverProgress:
        return ReceiverProgress(
            state=self._state,
            event=event,
            received=self._received_count,
            total=(
                self._manifest.total_chunks if self._manifest is not None else 0
            ),
            complete=self._complete,
            meta_ok=self._meta_ok if meta_ok is None else meta_ok,
            delivered=self._delivered if event == "completed" else None,
            abort=abort,
            error_type=error_type,
            message=message,
        )


def _check_time(now_ms: int) -> None:
    if not isinstance(now_ms, int) or isinstance(now_ms, bool) or now_ms < 0:
        raise ReliableTransportError("время должно быть неотрицательным целым")


__all__ = [
    "ABORT_HASH_MISMATCH",
    "ABORT_MANIFEST_CONFLICT",
    "ABORT_MANIFEST_INVALID",
    "ABORT_META_CONFLICT",
    "ABORT_PADDING_INVALID",
    "ABORT_PROTOCOL_ERROR",
    "ABORT_REASONS",
    "ABORT_ROUND_LIMIT",
    "ABORT_TIMEOUT",
    "AbortInfo",
    "CHUNK_BYTES",
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_MAX_STALLED_ROUNDS",
    "DEFAULT_WINDOW",
    "HEADER_BYTES",
    "MAX_CHUNKS",
    "MAX_FILENAME_BYTES",
    "MAX_FRAME_PAYLOAD_BYTES",
    "MAX_MESSAGE_BYTES",
    "MAX_WINDOW",
    "MAGIC",
    "Manifest",
    "ManifestValidationError",
    "MessageTooLargeError",
    "OutboundRound",
    "PACKET_ABORT",
    "PACKET_DATA",
    "PACKET_MANIFEST",
    "PACKET_META",
    "PACKET_STATUS",
    "PacketFormatError",
    "ReceiverProgress",
    "ReceiverState",
    "ReliableReceiver",
    "ReliableSender",
    "ReliableTransportError",
    "SenderProgress",
    "SenderState",
    "StatusInfo",
    "TransferStateError",
    "TransportConfig",
    "TransportPacket",
    "UnsupportedVersionError",
    "VERSION",
    "bitmap_count",
    "bitmap_has",
    "bitmap_length",
    "bitmap_missing",
    "build_bitmap",
    "build_meta_payload",
    "build_status_payload",
    "expected_chunks",
]
