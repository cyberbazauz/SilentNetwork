"""Acoustic File Transfer v0: файл → пакеты → Frame v1 → BFSK → WAV.

Модуль добавляет файловый слой над уже проверенным Acoustic Data Link v0.
Frame v1, CRC-16/CCITT-FALSE, BFSK, профили и лимит 64 байта payload не
меняются. Контракт описан в ``docs/acoustic-file-transfer.md``.

Границы v0:

* один файл за передачу, 0..4096 байт, имя до 40 байт UTF-8;
* пакеты MANIFEST / DATA / END внутри payload Frame v1;
* SHA-256 проверяет целостность файла, но не аутентифицирует отправителя;
* файловый round-trip через WAV не доказывает передачу через воздух.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from snlab.bfsk import DEFAULT_RAMP_MS, modulate
from snlab.config import BfskProfile
from snlab.datalink import ACOUSTIC_MIN_PREAMBLE_MATCHES, MAX_DATA_PAYLOAD_BYTES
from snlab.frame import PREAMBLE_BITS, build_frame
from snlab.receiver import (
    ReceiverError,
    SignalNotFound,
    bit_metric,
    decode_from_metric,
    find_frame_start,
)
from snlab.wavio import WavFormatError, read_pcm16, write_pcm16

REPORT_SCHEMA = "silentnetwork.acoustic-lab.file-transfer/1"
VECTOR_SCHEMA = "silentnetwork.acoustic-lab.file-transfer-vectors/1"

FT_MAGIC = b"SF"
FT_VERSION = 1
PACKET_HEADER_BYTES = 10
CHUNK_DATA_BYTES = MAX_DATA_PAYLOAD_BYTES - PACKET_HEADER_BYTES
MAX_FILENAME_BYTES = 40
MAX_FILE_BYTES = 4096
MAX_CHUNKS = (MAX_FILE_BYTES + CHUNK_DATA_BYTES - 1) // CHUNK_DATA_BYTES

PACKET_MANIFEST = 1
PACKET_DATA = 2
PACKET_END = 3
PACKET_TYPE_NAMES = {
    PACKET_MANIFEST: "manifest",
    PACKET_DATA: "data",
    PACKET_END: "end",
}

DEFAULT_INTER_FRAME_GAP_S = 0.25
DEFAULT_LEAD_SILENCE_S = 1.0
DEFAULT_TAIL_SILENCE_S = 0.5
DEFAULT_MANIFEST_REPEAT_EVERY = 16
DEFAULT_END_REPEATS = 2

_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class FileTransferError(ValueError):
    """Файл нельзя упаковать, передать или собрать."""

    error_type = "file_transfer_error"


class FileTooLargeError(FileTransferError):
    error_type = "file_too_large"


class FilenameError(FileTransferError):
    error_type = "filename_invalid"


class PacketError(FileTransferError):
    error_type = "packet_invalid"


class NotFileTransferError(PacketError):
    error_type = "not_file_transfer"


class ManifestError(PacketError):
    error_type = "manifest_invalid"


class TransferConflictError(FileTransferError):
    error_type = "transfer_conflict"


class IncompleteTransferError(FileTransferError):
    error_type = "incomplete_transfer"


class HashMismatchError(FileTransferError):
    error_type = "hash_mismatch"


@dataclass(frozen=True, slots=True)
class Manifest:
    """Содержимое MANIFEST-пакета."""

    filename: str
    filename_bytes: bytes
    file_size: int
    chunk_data_bytes: int = CHUNK_DATA_BYTES

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "filename_bytes": len(self.filename_bytes),
            "file_size": self.file_size,
            "chunk_data_bytes": self.chunk_data_bytes,
        }


@dataclass(frozen=True, slots=True)
class EndInfo:
    """Содержимое END-пакета."""

    file_size: int
    total_chunks: int
    sha256: bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "file_size": self.file_size,
            "total_chunks": self.total_chunks,
            "sha256": self.sha256.hex(),
        }


@dataclass(frozen=True, slots=True)
class FilePacket:
    """Один пакет файлового слоя внутри payload Frame v1."""

    packet_type: int
    transfer_id: int
    seq: int
    total_chunks: int
    payload: bytes

    @property
    def type_name(self) -> str:
        return PACKET_TYPE_NAMES.get(self.packet_type, f"unknown-{self.packet_type}")

    def to_bytes(self) -> bytes:
        if self.packet_type not in PACKET_TYPE_NAMES:
            raise PacketError(f"неизвестный тип пакета: {self.packet_type}")
        _validate_transfer_id(self.transfer_id)
        _validate_total_chunks(self.total_chunks)
        if not 0 <= self.seq <= 0xFFFF:
            raise PacketError("seq должен помещаться в 16 бит")
        if self.packet_type in (PACKET_MANIFEST, PACKET_END) and self.seq != 0:
            raise PacketError("у MANIFEST и END seq должен быть 0")
        data = bytes(self.payload)
        if len(data) > CHUNK_DATA_BYTES:
            raise PacketError(
                f"payload пакета {len(data)} байт превышает {CHUNK_DATA_BYTES}"
            )
        return (
            FT_MAGIC
            + bytes((FT_VERSION, self.packet_type, self.transfer_id))
            + self.seq.to_bytes(2, "big")
            + self.total_chunks.to_bytes(2, "big")
            + bytes((len(data),))
            + data
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "transfer_id": self.transfer_id,
            "seq": self.seq,
            "total_chunks": self.total_chunks,
            "payload_bytes": len(self.payload),
        }


@dataclass(frozen=True, slots=True)
class FileTransferPlan:
    """Пакеты и метаданные одной передачи до модуляции."""

    manifest: Manifest
    transfer_id: int
    total_chunks: int
    packets: tuple[FilePacket, ...]
    data_bytes: bytes

    @property
    def packet_count(self) -> int:
        return len(self.packets)

    def as_dict(self) -> dict[str, Any]:
        counts = {name: 0 for name in PACKET_TYPE_NAMES.values()}
        for packet in self.packets:
            counts[packet.type_name] = counts.get(packet.type_name, 0) + 1
        return {
            "transfer_id": self.transfer_id,
            "manifest": self.manifest.as_dict(),
            "total_chunks": self.total_chunks,
            "packet_count": self.packet_count,
            "packet_types": counts,
        }


@dataclass(frozen=True, slots=True)
class DecodedFilePacket:
    """Пакет, извлечённый из успешно проверенного кадра Frame v1."""

    packet: FilePacket
    start_sample: int
    start_time_s: float
    preamble_matches: int
    crc: int

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.packet.as_dict(),
            "start_sample": self.start_sample,
            "start_time_s": self.start_time_s,
            "preamble_matches": self.preamble_matches,
            "crc16": f"0x{self.crc:04X}",
        }


@dataclass(frozen=True, slots=True)
class FrameFailure:
    """Кадр найден, но не прошёл проверку; поток продолжает поиск."""

    start_sample: int
    start_time_s: float
    error_type: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_sample": self.start_sample,
            "start_time_s": self.start_time_s,
            "error_type": self.error_type,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class PacketStream:
    """Результат разбора одного WAV/массива: пакеты и ошибки."""

    packets: tuple[DecodedFilePacket, ...]
    frame_failures: tuple[FrameFailure, ...]
    packet_errors: tuple[dict[str, Any], ...]
    frames_found: int
    total_samples: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "frames_found": self.frames_found,
            "packets_decoded": len(self.packets),
            "frame_failures": [item.as_dict() for item in self.frame_failures],
            "packet_errors": list(self.packet_errors),
        }


def expected_chunks(file_size: int) -> int:
    """Сколько DATA-чанков нужно для файла данного размера."""
    _validate_file_size(file_size)
    if file_size == 0:
        return 0
    return (file_size + CHUNK_DATA_BYTES - 1) // CHUNK_DATA_BYTES


def validate_filename(filename: str) -> bytes:
    """Проверить имя файла и вернуть его UTF-8 байты.

    Имя не должно содержать разделителей пути, управляющих символов и не
    должно быть зарезервированным именем Windows.
    """
    if not isinstance(filename, str) or filename == "":
        raise FilenameError("имя файла не может быть пустым")
    if filename in (".", ".."):
        raise FilenameError("имя файла не может быть '.' или '..'")
    if "/" in filename or "\\" in filename:
        raise FilenameError("имя файла не может содержать разделители пути")
    if any(ord(char) < 32 for char in filename):
        raise FilenameError("имя файла не может содержать управляющие символы")
    if filename != filename.strip():
        raise FilenameError("имя файла не может начинаться или заканчиваться пробелом")
    if filename.endswith("."):
        raise FilenameError("имя файла не может заканчиваться точкой")
    stem = filename.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        raise FilenameError(f"зарезервированное имя Windows: {filename}")
    encoded = filename.encode("utf-8")
    if not 1 <= len(encoded) <= MAX_FILENAME_BYTES:
        raise FilenameError(
            f"имя файла занимает {len(encoded)} байт UTF-8, "
            f"допустимо 1..{MAX_FILENAME_BYTES}"
        )
    return encoded


def _validate_file_size(file_size: int) -> None:
    if not isinstance(file_size, int) or isinstance(file_size, bool):
        raise FileTransferError("размер файла должен быть целым числом")
    if not 0 <= file_size <= MAX_FILE_BYTES:
        raise FileTooLargeError(
            f"файл {file_size} байт не входит в лимит v0 0..{MAX_FILE_BYTES}"
        )


def _validate_transfer_id(transfer_id: int) -> None:
    if (
        not isinstance(transfer_id, int)
        or isinstance(transfer_id, bool)
        or not 0 <= transfer_id <= 0xFF
    ):
        raise FileTransferError("transfer_id должен быть в диапазоне 0..255")


def _validate_total_chunks(total_chunks: int) -> None:
    if (
        not isinstance(total_chunks, int)
        or isinstance(total_chunks, bool)
        or not 0 <= total_chunks <= MAX_CHUNKS
    ):
        raise PacketError(
            f"total_chunks должен быть в диапазоне 0..{MAX_CHUNKS}"
        )


def _manifest_payload(manifest: Manifest) -> bytes:
    size = manifest.file_size
    expected = expected_chunks(size)
    return (
        bytes((len(manifest.filename_bytes),))
        + manifest.filename_bytes
        + size.to_bytes(4, "big")
        + bytes((manifest.chunk_data_bytes,))
    )


def _parse_manifest_payload(payload: bytes) -> Manifest:
    data = bytes(payload)
    if len(data) < 6:
        raise ManifestError("MANIFEST короче минимальной длины")
    name_len = data[0]
    if not 1 <= name_len <= MAX_FILENAME_BYTES:
        raise ManifestError(f"недопустимая длина имени: {name_len}")
    end_of_name = 1 + name_len
    if len(data) < end_of_name + 5:
        raise ManifestError("MANIFEST обрезан")
    if len(data) != end_of_name + 5:
        raise ManifestError("MANIFEST содержит лишние байты")
    name_bytes = data[1:end_of_name]
    try:
        filename = name_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"имя файла не UTF-8: {exc.reason}") from exc
    try:
        canonical = validate_filename(filename)
    except FilenameError as exc:
        raise ManifestError(str(exc)) from exc
    if canonical != name_bytes:
        raise ManifestError("имя файла не в канонической форме UTF-8")
    file_size = int.from_bytes(data[end_of_name : end_of_name + 4], "big")
    chunk_data_bytes = data[end_of_name + 4]
    if chunk_data_bytes != CHUNK_DATA_BYTES:
        raise ManifestError(
            f"chunk_data_bytes={chunk_data_bytes}, ожидается {CHUNK_DATA_BYTES}"
        )
    try:
        _validate_file_size(file_size)
    except FileTransferError as exc:
        raise ManifestError(str(exc)) from exc
    return Manifest(
        filename=filename,
        filename_bytes=name_bytes,
        file_size=file_size,
        chunk_data_bytes=chunk_data_bytes,
    )


def _end_payload(file_size: int, total_chunks: int, sha256: bytes) -> bytes:
    if len(sha256) != 32:
        raise FileTransferError("SHA-256 должен быть длиной 32 байта")
    return (
        file_size.to_bytes(4, "big")
        + total_chunks.to_bytes(2, "big")
        + bytes(sha256)
    )


def _parse_end_payload(payload: bytes) -> EndInfo:
    data = bytes(payload)
    if len(data) != 38:
        raise PacketError(f"END должен быть 38 байт, получено {len(data)}")
    file_size = int.from_bytes(data[0:4], "big")
    total_chunks = int.from_bytes(data[4:6], "big")
    try:
        _validate_file_size(file_size)
        _validate_total_chunks(total_chunks)
    except FileTransferError as exc:
        raise PacketError(str(exc)) from exc
    if expected_chunks(file_size) != total_chunks:
        raise PacketError(
            f"END: для {file_size} байт нужно {expected_chunks(file_size)} "
            f"чанков, указано {total_chunks}"
        )
    return EndInfo(
        file_size=file_size,
        total_chunks=total_chunks,
        sha256=data[6:38],
    )


def parse_packet(raw: bytes) -> FilePacket:
    """Разобрать payload Frame v1 как пакет File Transfer v0."""
    data = bytes(raw)
    if len(data) < 2 or data[:2] != FT_MAGIC:
        raise NotFileTransferError("нет сигнатуры файлового пакета SF")
    if len(data) < PACKET_HEADER_BYTES:
        raise PacketError("пакет короче заголовка")
    if data[2] != FT_VERSION:
        raise PacketError(f"версия файлового протокола {data[2]} не поддерживается")
    packet_type = data[3]
    if packet_type not in PACKET_TYPE_NAMES:
        raise PacketError(f"неизвестный тип пакета: {packet_type}")
    transfer_id = data[4]
    seq = int.from_bytes(data[5:7], "big")
    total_chunks = int.from_bytes(data[7:9], "big")
    payload_len = data[9]
    if len(data) != PACKET_HEADER_BYTES + payload_len:
        raise PacketError(
            f"payload_len={payload_len}, а пакет занимает "
            f"{len(data) - PACKET_HEADER_BYTES} байт"
        )
    if payload_len > CHUNK_DATA_BYTES:
        raise PacketError(
            f"payload {payload_len} байт превышает {CHUNK_DATA_BYTES}"
        )
    payload = data[PACKET_HEADER_BYTES:]
    _validate_transfer_id(transfer_id)
    _validate_total_chunks(total_chunks)

    if packet_type == PACKET_MANIFEST:
        if seq != 0:
            raise ManifestError("у MANIFEST seq должен быть 0")
        manifest = _parse_manifest_payload(payload)
        if expected_chunks(manifest.file_size) != total_chunks:
            raise ManifestError(
                f"MANIFEST: для {manifest.file_size} байт нужно "
                f"{expected_chunks(manifest.file_size)} чанков, "
                f"указано {total_chunks}"
            )
    elif packet_type == PACKET_DATA:
        if total_chunks == 0:
            raise PacketError("DATA при total_chunks=0")
        if not 0 <= seq < total_chunks:
            raise PacketError(f"seq={seq} вне диапазона 0..{total_chunks - 1}")
        if payload_len == 0:
            raise PacketError("пустой DATA-пакет")
    else:
        if seq != 0:
            raise PacketError("у END seq должен быть 0")
        _parse_end_payload(payload)

    return FilePacket(
        packet_type=packet_type,
        transfer_id=transfer_id,
        seq=seq,
        total_chunks=total_chunks,
        payload=payload,
    )


def build_transfer_plan(
    data: bytes,
    filename: str,
    transfer_id: int,
    *,
    manifest_repeat_every: int = DEFAULT_MANIFEST_REPEAT_EVERY,
    end_repeats: int = DEFAULT_END_REPEATS,
) -> FileTransferPlan:
    """Разбить файл на пакеты MANIFEST / DATA / END."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise FileTransferError("содержимое файла должно быть bytes")
    content = bytes(data)
    _validate_file_size(len(content))
    _validate_transfer_id(transfer_id)
    if (
        not isinstance(manifest_repeat_every, int)
        or isinstance(manifest_repeat_every, bool)
        or not 1 <= manifest_repeat_every <= 1000
    ):
        raise FileTransferError("manifest_repeat_every должен быть 1..1000")
    if (
        not isinstance(end_repeats, int)
        or isinstance(end_repeats, bool)
        or not 1 <= end_repeats <= 5
    ):
        raise FileTransferError("end_repeats должен быть 1..5")

    name_bytes = validate_filename(filename)
    manifest = Manifest(
        filename=filename,
        filename_bytes=name_bytes,
        file_size=len(content),
    )
    total_chunks = expected_chunks(len(content))
    digest = hashlib.sha256(content).digest()
    manifest_packet = FilePacket(
        packet_type=PACKET_MANIFEST,
        transfer_id=transfer_id,
        seq=0,
        total_chunks=total_chunks,
        payload=_manifest_payload(manifest),
    )

    packets: list[FilePacket] = [manifest_packet]
    for index in range(total_chunks):
        chunk = content[index * CHUNK_DATA_BYTES : (index + 1) * CHUNK_DATA_BYTES]
        packets.append(
            FilePacket(
                packet_type=PACKET_DATA,
                transfer_id=transfer_id,
                seq=index,
                total_chunks=total_chunks,
                payload=chunk,
            )
        )
        if (index + 1) % manifest_repeat_every == 0 and index + 1 < total_chunks:
            packets.append(manifest_packet)

    end_packet = FilePacket(
        packet_type=PACKET_END,
        transfer_id=transfer_id,
        seq=0,
        total_chunks=total_chunks,
        payload=_end_payload(len(content), total_chunks, digest),
    )
    packets.extend(end_packet for _ in range(end_repeats))

    return FileTransferPlan(
        manifest=manifest,
        transfer_id=transfer_id,
        total_chunks=total_chunks,
        packets=tuple(packets),
        data_bytes=content,
    )


def encode_file_to_wav(
    source: str | Path,
    profile: BfskProfile,
    out_path: str | Path,
    *,
    transfer_id: int | None = None,
    manifest_repeat_every: int = DEFAULT_MANIFEST_REPEAT_EVERY,
    end_repeats: int = DEFAULT_END_REPEATS,
    inter_frame_gap_s: float = DEFAULT_INTER_FRAME_GAP_S,
    lead_silence_s: float = DEFAULT_LEAD_SILENCE_S,
    tail_silence_s: float = DEFAULT_TAIL_SILENCE_S,
    ramp_ms: float = DEFAULT_RAMP_MS,
) -> dict[str, Any]:
    """Упаковать файл в один WAV с последовательностью кадров Frame v1."""
    _validate_profile(profile)
    gap = _validate_seconds(inter_frame_gap_s, "inter_frame_gap_s", maximum=5.0)
    lead = _validate_seconds(lead_silence_s, "lead_silence_s", maximum=10.0)
    tail = _validate_seconds(tail_silence_s, "tail_silence_s", maximum=10.0)
    source_path = Path(source)
    content = source_path.read_bytes()
    if transfer_id is None:
        transfer_id = secrets.randbelow(0x100)
    plan = build_transfer_plan(
        content,
        source_path.name,
        transfer_id,
        manifest_repeat_every=manifest_repeat_every,
        end_repeats=end_repeats,
    )

    gap_samples = int(round(gap * profile.sample_rate))
    lead_samples = int(round(lead * profile.sample_rate))
    tail_samples = int(round(tail * profile.sample_rate))
    segments: list[np.ndarray] = [np.zeros(lead_samples, dtype=np.float64)]
    frame_duration_s = 0.0
    for index, packet in enumerate(plan.packets):
        frame = build_frame(
            packet.to_bytes(), max_payload_bytes=MAX_DATA_PAYLOAD_BYTES
        )
        modulation = modulate(frame.bits, profile, ramp_ms=ramp_ms)
        frame_duration_s = frame.total_bits / profile.symbol_rate
        segments.append(modulation.samples)
        if index + 1 < len(plan.packets):
            segments.append(np.zeros(gap_samples, dtype=np.float64))
    segments.append(np.zeros(tail_samples, dtype=np.float64))
    samples = np.concatenate(segments)

    target = write_pcm16(out_path, samples, profile.sample_rate)
    wav_bytes = target.read_bytes()
    total_duration_s = samples.size / profile.sample_rate
    return {
        "schema": REPORT_SCHEMA,
        "direction": "tx",
        "result": "ok",
        "error_type": None,
        "message": "WAV создан; воспроизведение ещё не является приёмом",
        "profile": profile.as_dict(),
        "file": {
            "filename": plan.manifest.filename,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        "transfer": {
            **plan.as_dict(),
            "chunk_data_bytes": CHUNK_DATA_BYTES,
        },
        "timing": {
            "frame_duration_s": frame_duration_s,
            "inter_frame_gap_s": gap,
            "lead_silence_s": lead,
            "tail_silence_s": tail,
            "total_duration_s": total_duration_s,
        },
        "wav": {
            "path": str(target),
            "bytes": len(wav_bytes),
            "sha256": hashlib.sha256(wav_bytes).hexdigest(),
        },
        "crc_is_authentication": False,
    }


def decode_packets_from_samples(
    samples: np.ndarray,
    profile: BfskProfile,
    *,
    min_preamble_matches: int = ACOUSTIC_MIN_PREAMBLE_MATCHES,
) -> PacketStream:
    """Найти все кадры Frame v1 в записи и разобрать их как FT-пакеты.

    Один повреждённый кадр не останавливает разбор: приёмник переходит к
    следующей преамбуле. CRC каждого кадра проверяется штатным кодом.
    """
    _validate_profile(profile)
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 1 or data.size == 0:
        raise FileTransferError("сигнал должен быть непустым одномерным массивом")
    if not np.all(np.isfinite(data)):
        raise FileTransferError("сигнал содержит NaN или бесконечность")

    metric = bit_metric(data, profile)
    symbols_per_bit = profile.samples_per_symbol
    offset = 0
    packets: list[DecodedFilePacket] = []
    failures: list[FrameFailure] = []
    packet_errors: list[dict[str, Any]] = []
    frames_found = 0

    while offset < metric.size:
        view = metric[offset:]
        try:
            start = find_frame_start(
                view, profile, min_preamble_matches=min_preamble_matches
            )
        except SignalNotFound:
            break
        try:
            frame = decode_from_metric(
                view,
                profile,
                start,
                max_payload_bytes=MAX_DATA_PAYLOAD_BYTES,
                min_preamble_matches=min_preamble_matches,
            )
        except ReceiverError as exc:
            failures.append(
                FrameFailure(
                    start_sample=offset + start.sample,
                    start_time_s=(offset + start.sample) / profile.sample_rate,
                    error_type=_receiver_error_type(exc),
                    message=str(exc),
                )
            )
            resync = start.sample + PREAMBLE_BITS * symbols_per_bit
            if resync >= view.size:
                break
            try:
                next_start = find_frame_start(
                    view[resync:],
                    profile,
                    min_preamble_matches=min_preamble_matches,
                )
            except SignalNotFound:
                break
            offset += resync + next_start.sample
            continue

        frames_found += 1
        consumed = frame.start_sample + frame.total_bits * symbols_per_bit
        try:
            packet = parse_packet(frame.payload)
        except PacketError as exc:
            packet_errors.append(
                {
                    "start_sample": offset + frame.start_sample,
                    "start_time_s": (offset + frame.start_sample)
                    / profile.sample_rate,
                    "error_type": exc.error_type,
                    "message": str(exc),
                }
            )
        else:
            packets.append(
                DecodedFilePacket(
                    packet=packet,
                    start_sample=offset + frame.start_sample,
                    start_time_s=(offset + frame.start_sample)
                    / profile.sample_rate,
                    preamble_matches=frame.preamble_matches,
                    crc=frame.crc,
                )
            )
        if consumed <= 0:  # pragma: no cover - защита от арифметической ошибки
            break
        offset += consumed

    return PacketStream(
        packets=tuple(packets),
        frame_failures=tuple(failures),
        packet_errors=tuple(packet_errors),
        frames_found=frames_found,
        total_samples=int(data.size),
    )


@dataclass(frozen=True, slots=True)
class TransferState:
    """Собранное состояние одного файла."""

    transfer_id: int | None
    manifest: Manifest | None
    total_chunks: int | None
    chunks_received: int
    missing_chunks: tuple[int, ...]
    duplicate_chunks: int
    manifest_seen: bool
    end_seen: bool
    expected_sha256: str | None
    complete: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "transfer_id": self.transfer_id,
            "filename": self.manifest.filename if self.manifest else None,
            "file_size": self.manifest.file_size if self.manifest else None,
            "total_chunks": self.total_chunks,
            "chunks_received": self.chunks_received,
            "missing_chunks": list(self.missing_chunks),
            "duplicate_chunks": self.duplicate_chunks,
            "manifest_seen": self.manifest_seen,
            "end_seen": self.end_seen,
            "expected_sha256": self.expected_sha256,
            "complete": self.complete,
        }


class FileAssembler:
    """Собирает один файл из пакетов, проверяя согласованность полей."""

    def __init__(self) -> None:
        self.transfer_id: int | None = None
        self.manifest: Manifest | None = None
        self.total_chunks: int | None = None
        self.end: EndInfo | None = None
        self._chunks: dict[int, bytes] = {}
        self.duplicates = 0

    def add(self, packet: FilePacket) -> None:
        if self.transfer_id is None:
            self.transfer_id = packet.transfer_id
        elif packet.transfer_id != self.transfer_id:
            raise TransferConflictError(
                f"transfer_id {packet.transfer_id} не совпадает с "
                f"{self.transfer_id}"
            )
        if self.total_chunks is None:
            self.total_chunks = packet.total_chunks
        elif packet.total_chunks != self.total_chunks:
            raise TransferConflictError(
                f"total_chunks {packet.total_chunks} не совпадает с "
                f"{self.total_chunks}"
            )

        if packet.packet_type == PACKET_MANIFEST:
            self._add_manifest(packet)
        elif packet.packet_type == PACKET_DATA:
            self._add_data(packet)
        else:
            self._add_end(packet)

    def _add_manifest(self, packet: FilePacket) -> None:
        manifest = _parse_manifest_payload(packet.payload)
        if self.manifest is not None and manifest != self.manifest:
            raise TransferConflictError("повтор MANIFEST отличается от первого")
        self.manifest = manifest
        for seq, chunk in self._chunks.items():
            self._validate_chunk_length(seq, chunk)

    def _add_data(self, packet: FilePacket) -> None:
        if self.total_chunks is None or packet.total_chunks != self.total_chunks:
            raise TransferConflictError("не согласовано число чанков")
        if not 0 <= packet.seq < packet.total_chunks:
            raise TransferConflictError(f"seq={packet.seq} вне диапазона")
        chunk = bytes(packet.payload)
        if not 1 <= len(chunk) <= CHUNK_DATA_BYTES:
            raise TransferConflictError(
                f"чанк {packet.seq} имеет длину {len(chunk)}"
            )
        if self.manifest is not None:
            self._validate_chunk_length(packet.seq, chunk)
        previous = self._chunks.get(packet.seq)
        if previous is not None:
            if previous != chunk:
                raise TransferConflictError(
                    f"чанк {packet.seq} повторён с другими данными"
                )
            self.duplicates += 1
            return
        self._chunks[packet.seq] = chunk

    def _validate_chunk_length(self, seq: int, chunk: bytes) -> None:
        if self.manifest is None:
            return
        if not 0 <= seq < self.total_chunks:
            raise TransferConflictError(f"seq={seq} вне диапазона")
        offset = seq * self.manifest.chunk_data_bytes
        expected = min(
            self.manifest.chunk_data_bytes,
            self.manifest.file_size - offset,
        )
        if expected <= 0 or len(chunk) != expected:
            raise TransferConflictError(
                f"чанк {seq}: длина {len(chunk)}, ожидается {expected}"
            )

    def _add_end(self, packet: FilePacket) -> None:
        info = _parse_end_payload(packet.payload)
        if self.manifest is not None:
            if info.file_size != self.manifest.file_size:
                raise TransferConflictError("END: размер файла не совпал")
        if self.total_chunks is not None and info.total_chunks != self.total_chunks:
            raise TransferConflictError("END: число чанков не совпало")
        if self.end is not None and info != self.end:
            raise TransferConflictError("повтор END отличается от первого")
        self.end = info

    @property
    def missing_chunks(self) -> tuple[int, ...]:
        if self.total_chunks is None:
            return ()
        return tuple(
            seq for seq in range(self.total_chunks) if seq not in self._chunks
        )

    @property
    def complete(self) -> bool:
        return (
            self.manifest is not None
            and self.end is not None
            and self.total_chunks is not None
            and not self.missing_chunks
        )

    def state(self) -> TransferState:
        return TransferState(
            transfer_id=self.transfer_id,
            manifest=self.manifest,
            total_chunks=self.total_chunks,
            chunks_received=len(self._chunks),
            missing_chunks=self.missing_chunks,
            duplicate_chunks=self.duplicates,
            manifest_seen=self.manifest is not None,
            end_seen=self.end is not None,
            expected_sha256=self.end.sha256.hex() if self.end else None,
            complete=self.complete,
        )

    def assemble(self) -> bytes:
        """Собрать файл и проверить размер и SHA-256."""
        if not self.complete or self.manifest is None or self.end is None:
            missing = self.missing_chunks
            raise IncompleteTransferError(
                "передача не завершена: "
                f"чанков {len(self._chunks)} из {self.total_chunks}, "
                f"нет {len(missing)}"
            )
        content = b"".join(
            self._chunks[seq] for seq in range(self.total_chunks or 0)
        )
        if len(content) != self.manifest.file_size:
            raise IncompleteTransferError(
                f"собрано {len(content)} байт вместо {self.manifest.file_size}"
            )
        actual = hashlib.sha256(content).digest()
        if actual != self.end.sha256:
            raise HashMismatchError(
                f"SHA-256 не совпал: получен {actual.hex()}, "
                f"ожидался {self.end.sha256.hex()}"
            )
        return content


def decode_wav_to_file(
    wav_path: str | Path,
    profile: BfskProfile,
    out_dir: str | Path,
    *,
    min_preamble_matches: int = ACOUSTIC_MIN_PREAMBLE_MATCHES,
) -> dict[str, Any]:
    """Прочитать WAV, собрать файл и сохранить его только при полном успехе."""
    _validate_profile(profile)
    source = Path(wav_path)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "direction": "rx",
        "profile": profile.as_dict(),
        "min_preamble_matches": min_preamble_matches,
        "wav_path": str(source),
        "crc_is_authentication": False,
    }
    try:
        samples, sample_rate = read_pcm16(
            source, expected_sample_rate=profile.sample_rate
        )
    except FileNotFoundError:
        return _receive_failure(report, "wav_not_found", f"WAV не найден: {source}")
    except WavFormatError as exc:
        return _receive_failure(report, "wav_format_error", str(exc))

    report["recording"] = _recording_metrics(samples, sample_rate)
    try:
        stream = decode_packets_from_samples(
            samples,
            profile,
            min_preamble_matches=min_preamble_matches,
        )
    except FileTransferError as exc:
        return _receive_failure(report, exc.error_type, str(exc))

    report["stream"] = stream.as_dict()
    if stream.frames_found == 0:
        return _receive_failure(
            report,
            "signal_not_found",
            "ни одного кадра Frame v1 не найдено",
        )
    if not stream.packets:
        if stream.packet_errors and all(
            item.get("error_type") == "not_file_transfer"
            for item in stream.packet_errors
        ):
            return _receive_failure(
                report,
                "not_file_transfer",
                "кадры найдены, но это не файловые пакеты",
            )
        return _receive_failure(
            report,
            "packet_invalid",
            "ни одного корректного файлового пакета",
        )

    assembler = FileAssembler()
    conflicts: list[str] = []
    for item in stream.packets:
        try:
            assembler.add(item.packet)
        except FileTransferError as exc:
            conflicts.append(str(exc))

    state = assembler.state()
    report["transfer"] = state.as_dict()
    report["transfer"]["hash_ok"] = False
    report["transfer"]["output_path"] = None
    if conflicts:
        report["transfer"]["conflicts"] = conflicts

    if conflicts:
        return _receive_failure(
            report, "transfer_conflict", "; ".join(conflicts)
        )

    if not state.complete:
        missing = state.missing_chunks
        detail = (
            f"чанков {state.chunks_received} из {state.total_chunks}"
            if state.total_chunks is not None
            else "нет манифеста или END"
        )
        if missing:
            detail += f", отсутствуют {len(missing)}"
        return _receive_failure(
            report,
            "incomplete_transfer",
            f"передача не завершена: {detail}",
        )

    try:
        content = assembler.assemble()
    except IncompleteTransferError as exc:
        return _receive_failure(report, "incomplete_transfer", str(exc))
    except HashMismatchError as exc:
        report["transfer"]["hash_ok"] = False
        return _receive_failure(report, "hash_mismatch", str(exc))

    actual_sha256 = hashlib.sha256(content).hexdigest()
    report["transfer"]["hash_ok"] = True
    report["transfer"]["actual_sha256"] = actual_sha256
    try:
        target = _safe_output_path(out_dir, assembler.manifest.filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    except (OSError, FilenameError) as exc:
        return _receive_failure(report, "output_error", str(exc))

    report.update(
        {
            "result": "ok",
            "error_type": None,
            "message": "Файл собран, размер и SHA-256 совпали",
            "file": {
                "filename": assembler.manifest.filename,
                "size": len(content),
                "sha256": actual_sha256,
            },
            "output_path": str(target),
        }
    )
    report["transfer"]["output_path"] = str(target)
    return report


def decode_report_ok(report: dict[str, Any]) -> bool:
    """Успешен ли приём файла."""
    return report.get("result") == "ok" and report.get("file") is not None


def _safe_output_path(out_dir: str | Path, filename: str) -> Path:
    """Собрать путь, не позволяя выйти за пределы каталога назначения."""
    name = validate_filename(filename).decode("utf-8")
    root = Path(out_dir).resolve()
    target = (root / name).resolve()
    if target.parent != root:
        raise FilenameError("имя файла выводит за пределы каталога назначения")
    return target


def _validate_profile(profile: BfskProfile) -> None:
    if not isinstance(profile, BfskProfile):
        raise FileTransferError("профиль должен быть BfskProfile")


def _validate_seconds(
    value: float, field_name: str, *, maximum: float
) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise FileTransferError(f"{field_name} должен быть числом")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= maximum:
        raise FileTransferError(
            f"{field_name} должен быть в диапазоне 0..{maximum:g} с"
        )
    return number


def _receiver_error_type(exc: ReceiverError) -> str:
    name = type(exc).__name__
    mapping = {
        "SignalNotFound": "signal_not_found",
        "SyncWordError": "sync_mismatch",
        "FrameVersionError": "version_unsupported",
        "FrameLengthError": "length_invalid",
        "IncompleteFrame": "incomplete_frame",
        "CrcError": "crc_mismatch",
    }
    return mapping.get(name, "receiver_error")


def _recording_metrics(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    data = np.asarray(samples, dtype=np.float64)
    rms = float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    return {
        "sample_rate_hz": sample_rate,
        "samples": int(data.size),
        "duration_s": float(data.size / sample_rate),
        "rms_dbfs": _dbfs(rms),
        "peak_dbfs": _dbfs(peak),
        "clipped_samples": int(np.count_nonzero(np.abs(data) >= 0.999)),
    }


def _dbfs(value: float) -> float:
    if value <= 0.0:
        return -240.0
    return float(20.0 * math.log10(value))


def _receive_failure(
    report: dict[str, Any], error_type: str, message: str
) -> dict[str, Any]:
    report.update(
        {
            "result": "error",
            "error_type": error_type,
            "message": message,
        }
    )
    return report


def report_to_json(report: dict[str, Any]) -> str:
    """Сериализовать отчёт в стабильный JSON."""
    return json.dumps(report, ensure_ascii=False, indent=2)


__all__ = [
    "CHUNK_DATA_BYTES",
    "DecodedFilePacket",
    "EndInfo",
    "FileAssembler",
    "FilePacket",
    "FileTransferError",
    "FileTransferPlan",
    "FileTooLargeError",
    "FilenameError",
    "FrameFailure",
    "HashMismatchError",
    "IncompleteTransferError",
    "MAX_CHUNKS",
    "MAX_FILE_BYTES",
    "MAX_FILENAME_BYTES",
    "Manifest",
    "ManifestError",
    "NotFileTransferError",
    "PACKET_DATA",
    "PACKET_END",
    "PACKET_MANIFEST",
    "PacketError",
    "PacketStream",
    "REPORT_SCHEMA",
    "TransferConflictError",
    "TransferState",
    "VECTOR_SCHEMA",
    "build_transfer_plan",
    "decode_packets_from_samples",
    "decode_report_ok",
    "decode_wav_to_file",
    "encode_file_to_wav",
    "expected_chunks",
    "parse_packet",
    "report_to_json",
    "validate_filename",
]
