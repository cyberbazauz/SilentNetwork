"""SN-033: ART/1 — подтверждение приёма и выборочный повтор.

Тесты проверяют только логику пакетов и модельного канала. Они не выполняют
DSP, не открывают аудиоустройства и не доказывают передачу через воздух.

Покрытие: формат и векторы, битовая карта, чистый round-trip, выборочный
повтор, потеря STATUS/MANIFEST, дубликаты, таймауты, лимит раундов,
некорректные и недоверенные пакеты, лимиты ресурсов, CLI и отчёт.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import reliable_sim as cli
from snlab.reliable import (
    ABORT_HASH_MISMATCH,
    ABORT_MANIFEST_CONFLICT,
    ABORT_META_CONFLICT,
    ABORT_PADDING_INVALID,
    CHUNK_BYTES,
    HEADER_BYTES,
    MAX_CHUNKS,
    MAX_FILENAME_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_WINDOW,
    PACKET_ABORT,
    PACKET_DATA,
    PACKET_MANIFEST,
    PACKET_META,
    PACKET_STATUS,
    AbortInfo,
    Manifest,
    ManifestValidationError,
    MessageTooLargeError,
    PacketFormatError,
    ReceiverState,
    ReliableReceiver,
    ReliableSender,
    ReliableTransportError,
    SenderState,
    StatusInfo,
    TransportConfig,
    TransportPacket,
    UnsupportedVersionError,
    bitmap_count,
    bitmap_has,
    bitmap_length,
    bitmap_missing,
    build_bitmap,
    build_meta_payload,
    build_status_payload,
    expected_chunks,
)
from snlab.reliable_sim import REPORT_SCHEMA, SimulationError, simulate_transfer

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "reliable-transport-vectors.json"
)


def load_fixture() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


FIXTURE = load_fixture()
VECTORS = {vector["name"]: vector for vector in FIXTURE["vectors"]}


def manifest_payload(
    data: bytes,
    *,
    total_chunks: int | None = None,
    chunk_bytes: int = CHUNK_BYTES,
    meta_present: bool = False,
    sha256: bytes | None = None,
) -> bytes:
    digest = hashlib.sha256(data).digest() if sha256 is None else sha256
    chunks = expected_chunks(len(data)) if total_chunks is None else total_chunks
    return (
        len(data).to_bytes(4, "big")
        + chunks.to_bytes(2, "big")
        + bytes((chunk_bytes, 1 if meta_present else 0))
        + digest
    )


def make_manifest(
    data: bytes,
    *,
    sha256: bytes | None = None,
    meta_present: bool = False,
) -> Manifest:
    return Manifest(
        total_size=len(data),
        total_chunks=expected_chunks(len(data)),
        chunk_bytes=CHUNK_BYTES,
        meta_present=meta_present,
        sha256=hashlib.sha256(data).digest() if sha256 is None else sha256,
    )


def deliver_packets(
    receiver: ReliableReceiver,
    current: object,
    config: TransportConfig,
    *,
    drop: frozenset[int] = frozenset(),
) -> None:
    """Доставить приёмнику пакеты раунда, теряя указанные DATA."""
    cursor = current.start_ms
    for packet in current.packets:
        duration = config.frame_duration_ms(len(packet.to_bytes()))
        arrives_at = cursor + duration
        if packet.packet_type != PACKET_DATA or packet.seq not in drop:
            receiver.handle_packet(packet, arrives_at)
        cursor = arrives_at + config.inter_frame_gap_ms


def run_round(
    sender: ReliableSender,
    receiver: ReliableReceiver,
    now_ms: int,
    config: TransportConfig,
    *,
    drop: frozenset[int] = frozenset(),
) -> tuple[object, TransportPacket | None, int | None]:
    """Передать один раунд в модельном канале.

    Возвращает раунд, STATUS (если приёмник успел его собрать) и модельное
    время прихода STATUS. ``drop`` — номера DATA, которые теряются.
    """
    progress = sender.advance(now_ms)
    current = progress.round
    assert current is not None, f"нет раунда: {progress.event}"
    deliver_packets(receiver, current, config, drop=drop)
    status = receiver.poll(current.round_end_ms + config.ack_guard_ms)
    status_arrives = None
    if status is not None:
        status_duration = config.frame_duration_ms(len(status.to_bytes()))
        status_arrives = (
            current.round_end_ms + config.ack_guard_ms + status_duration
        )
    return current, status, status_arrives


def advance_until_failure(
    sender: ReliableSender,
    config: TransportConfig,
    now_ms: int,
    *,
    limit: int = 64,
) -> object:
    """Продвигать таймеры, пока отправитель не завершится ошибкой."""
    for _ in range(limit):
        progress = sender.advance(now_ms)
        if sender.state is SenderState.FAILED:
            return progress
        current = sender.current_round or progress.round
        if current is not None:
            now_ms = current.listen_until_ms + config.turnaround_ms
        else:
            now_ms += config.turnaround_ms
    pytest.fail("отправитель не завершился ошибкой за отведённые шаги")


# ---------------------------------------------------------------------------
# Формат пакетов и тестовые векторы
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(VECTORS))
def test_packet_vectors_round_trip(name: str) -> None:
    vector = VECTORS[name]
    raw = bytes.fromhex(vector["packet_hex"])
    packet = TransportPacket.parse(raw)
    assert packet.type_name == vector["type"]
    assert packet.to_bytes() == raw
    for field in (
        "transfer_id",
        "session_id",
        "round",
        "seq",
        "slot",
        "slots",
    ):
        assert getattr(packet, field) == vector["fields"][field]
    assert len(raw) <= 64
    assert packet.to_bytes()[:HEADER_BYTES] == raw[:HEADER_BYTES]


def test_manifest_vector_fields() -> None:
    packet = TransportPacket.parse(bytes.fromhex(VECTORS["manifest"]["packet_hex"]))
    manifest = packet.manifest_info()
    fields = VECTORS["manifest"]["fields"]
    assert manifest.total_size == fields["total_size"]
    assert manifest.total_chunks == fields["total_chunks"]
    assert manifest.chunk_bytes == fields["chunk_bytes"]
    assert manifest.meta_present is fields["meta_present"]
    assert manifest.sha256.hex() == fields["sha256_hex"]
    assert manifest.to_payload() == packet.payload


def test_status_vector_fields() -> None:
    packet = TransportPacket.parse(bytes.fromhex(VECTORS["status"]["packet_hex"]))
    info = packet.status_info()
    fields = VECTORS["status"]["fields"]
    assert info.complete is fields["complete"]
    assert info.meta_ok is fields["meta_ok"]
    assert info.total_chunks == fields["total_chunks"]
    assert info.received_count == fields["received_count"]
    assert info.bitmap.hex() == fields["bitmap_hex"]
    assert list(bitmap_missing(info.bitmap, info.total_chunks)) == fields["missing"]


def test_meta_and_abort_vectors() -> None:
    meta = TransportPacket.parse(bytes.fromhex(VECTORS["meta"]["packet_hex"]))
    assert meta.meta_name() == "note.txt"
    abort = TransportPacket.parse(bytes.fromhex(VECTORS["abort"]["packet_hex"]))
    info = abort.abort_info()
    assert info.reason == 2
    assert info.error_type == "hash_mismatch"
    assert info.message == "bad hash"


def test_packet_is_exactly_header_plus_payload() -> None:
    raw = bytes.fromhex(VECTORS["data"]["packet_hex"])
    assert len(raw) == HEADER_BYTES + CHUNK_BYTES
    assert HEADER_BYTES == 12
    assert CHUNK_BYTES == 52


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"SF",
        bytes.fromhex(VECTORS["data"]["packet_hex"])[:-1],
        bytes.fromhex(VECTORS["data"]["packet_hex"]) + b"\x00",
    ],
)
def test_parse_rejects_broken_length(raw: bytes) -> None:
    with pytest.raises(PacketFormatError):
        TransportPacket.parse(raw)


def test_parse_rejects_bad_magic_and_version() -> None:
    raw = bytes.fromhex(VECTORS["data"]["packet_hex"])
    with pytest.raises(PacketFormatError):
        TransportPacket.parse(b"XX" + raw[2:])
    with pytest.raises(UnsupportedVersionError):
        TransportPacket.parse(raw[:2] + b"\x01" + raw[3:])


def test_parse_rejects_bad_slot_fields() -> None:
    raw = bytearray.fromhex(VECTORS["data"]["packet_hex"])
    raw[11] = 0x00  # slots=0 у DATA
    with pytest.raises(PacketFormatError):
        TransportPacket.parse(bytes(raw))
    raw[11] = 0x10  # slot=1, slots=0
    with pytest.raises(PacketFormatError):
        TransportPacket.parse(bytes(raw))
    manifest_raw = bytearray.fromhex(VECTORS["manifest"]["packet_hex"])
    manifest_raw[11] = 0x10  # slot=1 у MANIFEST
    with pytest.raises(PacketFormatError):
        TransportPacket.parse(bytes(manifest_raw))
    status_raw = bytearray.fromhex(VECTORS["status"]["packet_hex"])
    status_raw[6:8] = b"\x00\x01"  # seq у STATUS
    with pytest.raises(PacketFormatError):
        TransportPacket.parse(bytes(status_raw))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"transfer_id": 256},
        {"transfer_id": -1},
        {"session_id": 1 << 16},
        {"round": 256},
        {"slot": 16},
        {"slots": 16},
    ],
)
def test_packet_rejects_out_of_range_header(kwargs: dict) -> None:
    base = dict(
        packet_type=PACKET_DATA,
        transfer_id=7,
        session_id=0x1234,
        round=0,
        seq=0,
        slot=0,
        slots=1,
        payload=b"\x00" * CHUNK_BYTES,
    )
    base.update(kwargs)
    with pytest.raises(PacketFormatError):
        TransportPacket(**base)


def test_data_packet_requires_exact_chunk_size() -> None:
    for size in (0, CHUNK_BYTES - 1, CHUNK_BYTES + 1):
        with pytest.raises(PacketFormatError):
            TransportPacket(
                packet_type=PACKET_DATA,
                transfer_id=7,
                session_id=0x1234,
                round=0,
                seq=0,
                slot=0,
                slots=1,
                payload=b"\x00" * size,
            )


# ---------------------------------------------------------------------------
# Битовая карта
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chunks", "length"),
    [(0, 0), (1, 1), (7, 1), (8, 1), (9, 2), (158, 20), (255, 32)],
)
def test_bitmap_length(chunks: int, length: int) -> None:
    assert bitmap_length(chunks) == length


def test_bitmap_is_msb_first() -> None:
    bitmap = build_bitmap([0, 7], 8)
    assert bitmap == b"\x81"
    assert bitmap_has(bitmap, 0)
    assert bitmap_has(bitmap, 7)
    assert not bitmap_has(bitmap, 1)
    assert bitmap_count(bitmap) == 2
    assert bitmap_missing(bitmap, 8) == (1, 2, 3, 4, 5, 6)


def test_bitmap_ignores_third_byte_for_three_chunks() -> None:
    bitmap = b"\xa0"
    assert bitmap_missing(bitmap, 3) == (1,)
    with pytest.raises(PacketFormatError):
        bitmap_missing(bitmap, 9)


def test_build_bitmap_rejects_out_of_range() -> None:
    with pytest.raises(PacketFormatError):
        build_bitmap([3], 3)
    with pytest.raises(PacketFormatError):
        build_bitmap([-1], 3)


def test_build_status_deduplicates_received() -> None:
    payload = build_status_payload(
        [0, 0, 2, 2], 3, complete=False, meta_ok=True
    )
    info = StatusInfo.from_payload(payload)
    assert info.received_count == 2
    assert info.bitmap == b"\xa0"


# ---------------------------------------------------------------------------
# Manifest / Status / Meta / Abort: валидация
# ---------------------------------------------------------------------------


def test_manifest_round_trip() -> None:
    data = b"payload"
    manifest = make_manifest(data, meta_present=True)
    assert Manifest.from_payload(manifest.to_payload()) == manifest


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00" * 39,
        b"\x00" * 41,
    ],
)
def test_manifest_rejects_bad_length(payload: bytes) -> None:
    with pytest.raises(ManifestValidationError):
        Manifest.from_payload(payload)


def test_manifest_rejects_inconsistent_chunks() -> None:
    data = b"x" * 120
    with pytest.raises(ManifestValidationError):
        Manifest(
            total_size=120,
            total_chunks=2,
            chunk_bytes=CHUNK_BYTES,
            meta_present=False,
            sha256=hashlib.sha256(data).digest(),
        )
    with pytest.raises(ManifestValidationError):
        Manifest(
            total_size=120,
            total_chunks=3,
            chunk_bytes=CHUNK_BYTES - 1,
            meta_present=False,
            sha256=hashlib.sha256(data).digest(),
        )


def test_manifest_rejects_bad_meta_byte_and_digest() -> None:
    payload = bytearray(manifest_payload(b"abc"))
    payload[7] = 2
    with pytest.raises(ManifestValidationError):
        Manifest.from_payload(bytes(payload))
    with pytest.raises(ManifestValidationError):
        Manifest(
            total_size=3,
            total_chunks=1,
            chunk_bytes=CHUNK_BYTES,
            meta_present=False,
            sha256=b"\x00" * 31,
        )


def test_manifest_rejects_size_over_limit() -> None:
    with pytest.raises(ManifestValidationError):
        Manifest(
            total_size=MAX_MESSAGE_BYTES + 1,
            total_chunks=MAX_CHUNKS,
            chunk_bytes=CHUNK_BYTES,
            meta_present=False,
            sha256=b"\x00" * 32,
        )


@pytest.mark.parametrize(
    ("total_size", "chunks"),
    [(0, 0), (1, 1), (52, 1), (53, 2), (8192, 158)],
)
def test_expected_chunks(total_size: int, chunks: int) -> None:
    assert expected_chunks(total_size) == chunks


def test_expected_chunks_rejects_too_large() -> None:
    with pytest.raises(MessageTooLargeError):
        expected_chunks(MAX_MESSAGE_BYTES + 1)


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00" * 4,
    ],
)
def test_status_rejects_short_payload(payload: bytes) -> None:
    with pytest.raises(PacketFormatError):
        StatusInfo.from_payload(payload)


def test_status_rejects_unknown_flags() -> None:
    payload = bytearray(build_status_payload([0], 1, complete=False, meta_ok=False))
    payload[0] = 0x04
    with pytest.raises(PacketFormatError):
        StatusInfo.from_payload(bytes(payload))


def test_status_rejects_bitmap_and_count_mismatch() -> None:
    payload = bytearray(build_status_payload([0, 2], 3, complete=False, meta_ok=False))
    payload[3:5] = b"\x00\x03"  # received_count=3, а в карте 2 бита
    with pytest.raises(PacketFormatError):
        StatusInfo.from_payload(bytes(payload))
    with pytest.raises(PacketFormatError):
        StatusInfo(
            complete=False,
            meta_ok=False,
            total_chunks=9,
            received_count=0,
            bitmap=b"\x00",
        )


def test_status_rejects_total_chunks_over_limit() -> None:
    payload = (
        bytes((0,))
        + (MAX_CHUNKS + 1).to_bytes(2, "big")
        + b"\x00\x00"
        + b"\x00" * 32
    )
    with pytest.raises(PacketFormatError):
        StatusInfo.from_payload(payload)


def test_meta_payload_round_trip() -> None:
    for name in ("note.txt", "a.bin", "файл.txt", "x" * MAX_FILENAME_BYTES):
        payload = build_meta_payload(name)
        packet = TransportPacket(
            packet_type=PACKET_META,
            transfer_id=7,
            session_id=0x1234,
            round=0,
            seq=0,
            slot=0,
            slots=0,
            payload=payload,
        )
        assert packet.meta_name() == name


@pytest.mark.parametrize(
    "name",
    ["", "../x", "a/b", "a\\b", "CON", "note.txt.", "note.txt ", "x" * 41],
)
def test_meta_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(ReliableTransportError):
        build_meta_payload(name)


def test_abort_round_trip() -> None:
    for reason in range(1, 9):
        info = AbortInfo(reason=reason, message="")
        assert AbortInfo.from_payload(info.to_payload()) == info
    with pytest.raises(PacketFormatError):
        AbortInfo(reason=0)
    with pytest.raises(PacketFormatError):
        AbortInfo.from_payload(b"\x09")
    with pytest.raises(PacketFormatError):
        AbortInfo(reason=ABORT_HASH_MISMATCH, message="x" * 41)


# ---------------------------------------------------------------------------
# Валидация конфигурации и тайминги
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"window": 0},
        {"window": MAX_WINDOW + 1},
        {"max_rounds": 0},
        {"max_rounds": 256},
        {"max_stalled_rounds": 0},
        {"max_message_bytes": 0},
        {"symbol_rate": 0},
        {"symbol_rate": -1},
        {"symbol_rate": float("nan")},
        {"inter_frame_gap_ms": -1},
    ],
)
def test_config_rejects_invalid_values(kwargs: dict) -> None:
    with pytest.raises(ReliableTransportError):
        TransportConfig(**kwargs)


def test_frame_durations_match_contract() -> None:
    config = TransportConfig(symbol_rate=300.0)
    assert config.data_frame_ms == 1680
    assert config.frame_duration_ms(40) == 1360
    assert config.status_frame_ms(158) == 960
    with pytest.raises(ReliableTransportError):
        config.frame_duration_ms(65)


# ---------------------------------------------------------------------------
# Чистая передача и выборочный повтор
# ---------------------------------------------------------------------------


def test_clean_round_trip_completes_in_one_round() -> None:
    data = bytes(range(120))
    config = TransportConfig()
    sender = ReliableSender(data, 7, config=config, session_id=0x1234)
    receiver = ReliableReceiver(config)

    current, status, arrives_at = run_round(sender, receiver, 0, config)
    assert current.data_seqs == (0, 1, 2)
    assert status is not None
    assert arrives_at is not None
    assert receiver.complete
    assert receiver.delivered == data
    progress = sender.handle_packet(status, arrives_at)
    assert progress.event == "completed"
    assert sender.state is SenderState.DONE
    assert sender.missing == ()
    assert sender.rounds_sent == 1
    assert sender.retransmitted_frames == 0


def test_selective_retransmission_only_missing_chunks() -> None:
    data = bytes(range(120))
    config = TransportConfig()
    sender = ReliableSender(data, 7, config=config, session_id=0x1234)
    receiver = ReliableReceiver(config)

    first, status, arrives_at = run_round(
        sender, receiver, 0, config, drop=frozenset({1})
    )
    assert first.data_seqs == (0, 1, 2)
    assert status is not None
    info = status.status_info()
    assert bitmap_missing(info.bitmap, info.total_chunks) == (1,)
    assert not info.complete
    sender.handle_packet(status, arrives_at)
    assert sender.missing == (1,)

    second, status2, arrives_at2 = run_round(
        sender, receiver, arrives_at + config.turnaround_ms, config
    )
    assert second.data_seqs == (1,)
    assert second.slots == 1
    assert status2 is not None
    progress = sender.handle_packet(status2, arrives_at2)
    assert progress.event == "completed"
    assert sender.state is SenderState.DONE
    assert sender.retransmitted_frames == 1
    assert receiver.delivered == data


def test_status_loss_repeats_missing_set() -> None:
    data = bytes(range(120))
    config = TransportConfig()
    sender = ReliableSender(data, 7, config=config, session_id=0x1234)
    receiver = ReliableReceiver(config)

    first, status, _ = run_round(sender, receiver, 0, config, drop=frozenset({1}))
    assert status is not None
    # STATUS потерян: отправитель не получает карту.
    progress = sender.advance(first.listen_until_ms)
    assert progress.state is SenderState.WAITING
    progress = sender.advance(first.listen_until_ms + config.turnaround_ms)
    second = progress.round
    assert second is not None
    # Карта не дошла, поэтому повторяется весь неподтверждённый набор.
    assert second.data_seqs == (0, 1, 2)

    deliver_packets(receiver, second, config)
    status2 = receiver.poll(second.round_end_ms + config.ack_guard_ms)
    assert status2 is not None
    status2_arrives = (
        second.round_end_ms
        + config.ack_guard_ms
        + config.frame_duration_ms(len(status2.to_bytes()))
    )
    progress = sender.handle_packet(status2, status2_arrives)
    assert progress.event == "completed"
    assert receiver.delivered == data
    assert sender.retransmitted_frames > 0


def test_manifest_loss_recovers_without_data_loss() -> None:
    data = bytes(range(120))
    config = TransportConfig()
    sender = ReliableSender(data, 7, config=config, session_id=0x1234)
    receiver = ReliableReceiver(config)

    first = sender.advance(0).round
    assert first is not None
    # Теряется только MANIFEST: DATA без манифеста отбрасываются.
    for packet in first.packets:
        if packet.packet_type != PACKET_MANIFEST:
            receiver.handle_packet(packet, first.start_ms + 100)
    assert receiver.received_count == 0
    assert receiver.poll(first.round_end_ms + config.ack_guard_ms) is None

    sender.advance(first.listen_until_ms)
    progress = sender.advance(first.listen_until_ms + config.turnaround_ms)
    second = progress.round
    assert second is not None
    assert second.data_seqs == (0, 1, 2)
    for packet in second.packets:
        receiver.handle_packet(packet, second.start_ms + 100)
    status = receiver.poll(second.round_end_ms + config.ack_guard_ms)
    assert status is not None
    status_arrives = (
        second.round_end_ms
        + config.ack_guard_ms
        + config.frame_duration_ms(len(status.to_bytes()))
    )
    assert sender.handle_packet(status, status_arrives).event == "completed"
    assert receiver.delivered == data


def test_duplicate_data_is_idempotent() -> None:
    data = bytes(range(60))
    config = TransportConfig()
    sender = ReliableSender(data, 7, config=config, session_id=0x1234)
    receiver = ReliableReceiver(config)

    current, status, arrives_at = run_round(sender, receiver, 0, config)
    data_packet = next(p for p in current.packets if p.packet_type == PACKET_DATA)
    progress = receiver.handle_packet(data_packet, current.round_end_ms)
    assert progress.event == "duplicate"
    assert receiver.duplicates == 1
    assert receiver.received_count == 2
    assert receiver.delivered == data
    assert sender.handle_packet(status, arrives_at).event == "completed"


def test_empty_message_completes_without_data() -> None:
    config = TransportConfig()
    sender = ReliableSender(b"", 7, config=config, session_id=0x1234)
    receiver = ReliableReceiver(config)
    current, status, arrives_at = run_round(sender, receiver, 0, config)
    assert current.data_seqs == ()
    assert receiver.complete
    assert receiver.delivered == b""
    assert status is not None
    assert sender.handle_packet(status, arrives_at).event == "completed"


# ---------------------------------------------------------------------------
# Отказы и недоверенные данные
# ---------------------------------------------------------------------------


def test_hash_mismatch_aborts_without_delivery() -> None:
    data = b"abc"
    config = TransportConfig()
    sender = ReliableSender(data, 7, config=config, session_id=0x1234)
    receiver = ReliableReceiver(config)
    current = sender.advance(0).round
    assert current is not None

    tampered = Manifest(
        total_size=len(data),
        total_chunks=1,
        chunk_bytes=CHUNK_BYTES,
        meta_present=False,
        sha256=b"\x00" * 32,
    )
    receiver.handle_packet(
        TransportPacket(
            packet_type=PACKET_MANIFEST,
            transfer_id=7,
            session_id=0x1234,
            round=0,
            seq=0,
            slot=0,
            slots=1,
            payload=tampered.to_payload(),
        ),
        10,
    )
    data_packet = next(p for p in current.packets if p.packet_type == PACKET_DATA)
    progress = receiver.handle_packet(data_packet, 20)
    assert progress.event == "aborted"
    assert progress.error_type == "hash_mismatch"
    assert progress.abort is not None
    assert progress.abort.abort_info().reason == ABORT_HASH_MISMATCH
    assert receiver.complete is False
    assert receiver.delivered is None
    assert receiver.state is ReceiverState.ABORTED


def test_nonzero_padding_aborts() -> None:
    config = TransportConfig()
    receiver = ReliableReceiver(config)
    manifest = Manifest(
        total_size=1,
        total_chunks=1,
        chunk_bytes=CHUNK_BYTES,
        meta_present=False,
        sha256=hashlib.sha256(b"A").digest(),
    )
    receiver.handle_packet(
        TransportPacket(
            PACKET_MANIFEST, 7, 0x1234, 0, 0, 0, 1, manifest.to_payload()
        ),
        0,
    )
    progress = receiver.handle_packet(
        TransportPacket(
            PACKET_DATA,
            7,
            0x1234,
            0,
            0,
            0,
            1,
            b"AB" + b"\x00" * (CHUNK_BYTES - 2),
        ),
        10,
    )
    assert progress.event == "aborted"
    assert progress.error_type == "padding_invalid"
    assert progress.abort is not None
    assert progress.abort.abort_info().reason == ABORT_PADDING_INVALID


def test_data_before_manifest_is_ignored() -> None:
    receiver = ReliableReceiver()
    progress = receiver.handle_packet(
        TransportPacket(PACKET_DATA, 7, 0x1234, 0, 0, 0, 1, b"\x00" * CHUNK_BYTES),
        0,
    )
    assert progress.event == "data_before_manifest"
    assert progress.received == 0


def test_manifest_conflict_aborts() -> None:
    config = TransportConfig()
    receiver = ReliableReceiver(config)
    first = make_manifest(b"abc")
    receiver.handle_packet(
        TransportPacket(PACKET_MANIFEST, 7, 0x1234, 0, 0, 0, 0, first.to_payload()),
        0,
    )
    other = make_manifest(b"abcdef")
    progress = receiver.handle_packet(
        TransportPacket(PACKET_MANIFEST, 7, 0x1234, 1, 0, 0, 0, other.to_payload()),
        100,
    )
    assert progress.event == "aborted"
    assert progress.error_type == "manifest_conflict"
    assert progress.abort is not None
    assert progress.abort.abort_info().reason == ABORT_MANIFEST_CONFLICT


def test_meta_conflict_aborts() -> None:
    config = TransportConfig()
    receiver = ReliableReceiver(config)
    manifest = make_manifest(b"abc", meta_present=True)
    receiver.handle_packet(
        TransportPacket(PACKET_MANIFEST, 7, 0x1234, 0, 0, 0, 0, manifest.to_payload()),
        0,
    )
    receiver.handle_packet(
        TransportPacket(
            PACKET_META, 7, 0x1234, 0, 0, 0, 0, build_meta_payload("a.txt")
        ),
        10,
    )
    progress = receiver.handle_packet(
        TransportPacket(
            PACKET_META, 7, 0x1234, 0, 0, 0, 0, build_meta_payload("b.txt")
        ),
        20,
    )
    assert progress.event == "aborted"
    assert progress.error_type == "meta_conflict"
    assert progress.abort is not None
    assert progress.abort.abort_info().reason == ABORT_META_CONFLICT


def test_meta_before_manifest_is_buffered() -> None:
    config = TransportConfig()
    receiver = ReliableReceiver(config)
    receiver.handle_packet(
        TransportPacket(
            PACKET_META, 7, 0x1234, 0, 0, 0, 0, build_meta_payload("note.txt")
        ),
        0,
    )
    manifest = make_manifest(b"abc", meta_present=True)
    progress = receiver.handle_packet(
        TransportPacket(PACKET_MANIFEST, 7, 0x1234, 0, 0, 0, 0, manifest.to_payload()),
        10,
    )
    assert progress.event == "manifest"
    assert progress.meta_ok is True
    assert receiver.filename == "note.txt"


def test_new_session_replaces_partial_assembly() -> None:
    config = TransportConfig()
    receiver = ReliableReceiver(config)
    first_data = bytes(range(60))
    first = make_manifest(first_data)
    receiver.handle_packet(
        TransportPacket(PACKET_MANIFEST, 7, 0x1111, 0, 0, 0, 2, first.to_payload()),
        0,
    )
    receiver.handle_packet(
        TransportPacket(
            PACKET_DATA,
            7,
            0x1111,
            0,
            0,
            0,
            2,
            first_data[:CHUNK_BYTES],
        ),
        10,
    )
    assert receiver.received_count == 1
    assert receiver.state is ReceiverState.ASSEMBLING
    second = make_manifest(b"xyz")
    progress = receiver.handle_packet(
        TransportPacket(PACKET_MANIFEST, 8, 0x2222, 0, 0, 0, 0, second.to_payload()),
        20,
    )
    assert progress.event == "session_replaced"
    assert receiver.session_id == 0x2222
    assert receiver.received_count == 0


# ---------------------------------------------------------------------------
# Состояния отправителя: устаревшие статусы, ABORT, таймауты, лимиты
# ---------------------------------------------------------------------------


def test_stale_and_inconsistent_statuses_are_ignored() -> None:
    data = bytes(range(120))
    config = TransportConfig()
    sender = ReliableSender(data, 7, config=config, session_id=0x1234)
    receiver = ReliableReceiver(config)

    first, status, arrives_at = run_round(
        sender, receiver, 0, config, drop=frozenset({1})
    )
    assert status is not None
    sender.handle_packet(status, arrives_at)
    second = sender.advance(arrives_at + config.turnaround_ms).round
    assert second is not None

    stale = sender.handle_packet(
        TransportPacket(
            PACKET_STATUS,
            7,
            0x1234,
            0,
            0,
            0,
            0,
            build_status_payload([0, 2], 3, complete=False, meta_ok=False),
        ),
        arrives_at + config.turnaround_ms,
    )
    assert stale.event == "stale_status"

    fake_complete = TransportPacket(
        PACKET_STATUS,
        7,
        0x1234,
        second.round,
        0,
        0,
        0,
        build_status_payload([0], 3, complete=True, meta_ok=False),
    )
    inconsistent = sender.handle_packet(
        fake_complete, arrives_at + config.turnaround_ms
    )
    assert inconsistent.event == "inconsistent_status"
    assert sender.state is SenderState.WAITING


def test_wrong_transfer_or_session_is_ignored() -> None:
    sender = ReliableSender(b"abc", 7, config=TransportConfig(), session_id=0x1234)
    current = sender.advance(0).round
    assert current is not None
    packet = TransportPacket(
        PACKET_STATUS,
        8,
        0x1234,
        0,
        0,
        0,
        0,
        build_status_payload([0], 1, complete=True, meta_ok=False),
    )
    assert sender.handle_packet(packet, 1000).event == "ignored"
    packet = TransportPacket(
        PACKET_STATUS,
        7,
        0x9999,
        0,
        0,
        0,
        0,
        build_status_payload([0], 1, complete=True, meta_ok=False),
    )
    assert sender.handle_packet(packet, 1000).event == "ignored"


def test_sender_accepts_abort() -> None:
    sender = ReliableSender(b"abc", 7, config=TransportConfig(), session_id=0x1234)
    sender.advance(0)
    abort = TransportPacket(
        PACKET_ABORT,
        7,
        0x1234,
        0,
        0,
        0,
        0,
        AbortInfo(ABORT_HASH_MISMATCH, "bad").to_payload(),
    )
    progress = sender.handle_packet(abort, 1000)
    assert progress.event == "failed"
    assert progress.error_type == "hash_mismatch"
    assert sender.state is SenderState.FAILED


def test_no_status_leads_to_ack_timeout() -> None:
    config = TransportConfig(max_stalled_rounds=1, max_rounds=10)
    sender = ReliableSender(b"abc", 7, config=config, session_id=0x1234)
    progress = advance_until_failure(sender, config, 0)
    assert progress.event == "failed"
    assert progress.error_type == "ack_timeout"
    assert sender.state is SenderState.FAILED


def test_round_limit_fails_instead_of_hanging() -> None:
    config = TransportConfig(max_rounds=1, max_stalled_rounds=10)
    sender = ReliableSender(b"abc", 7, config=config, session_id=0x1234)
    first = sender.advance(0).round
    assert first is not None
    progress = advance_until_failure(sender, config, first.listen_until_ms)
    assert progress.event == "failed"
    assert progress.error_type == "round_limit"
    assert sender.state is SenderState.FAILED


def test_confirmation_timeout_when_status_never_confirms() -> None:
    config = TransportConfig(max_stalled_rounds=1, max_rounds=10)
    receiver = ReliableReceiver(config)
    sender = ReliableSender(b"abc", 7, config=config, session_id=0x1234)
    current = sender.advance(0).round
    assert current is not None
    for packet in current.packets:
        receiver.handle_packet(packet, current.start_ms + 10)
    assert receiver.complete
    # Отправитель видит полную карту, но complete=0 (потеря флага).
    forged = TransportPacket(
        PACKET_STATUS,
        7,
        0x1234,
        current.round,
        0,
        0,
        0,
        build_status_payload([0], 1, complete=False, meta_ok=False),
    )
    progress = sender.handle_packet(
        forged, current.round_end_ms + config.ack_guard_ms
    )
    assert progress.event == "needs_confirmation"
    assert sender.missing == ()
    progress = advance_until_failure(
        sender, config, current.listen_until_ms + config.turnaround_ms
    )
    assert progress.event == "failed"
    assert progress.error_type == "confirmation_timeout"


def test_message_too_large_fails_before_transfer() -> None:
    with pytest.raises(MessageTooLargeError):
        ReliableSender(b"x" * (MAX_MESSAGE_BYTES + 1), 7)
    with pytest.raises(MessageTooLargeError):
        ReliableSender(
            b"x" * 10, 7, config=TransportConfig(max_message_bytes=5)
        )


def test_invalid_data_type_rejected() -> None:
    with pytest.raises(ReliableTransportError):
        ReliableSender("text", 7)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Имя файла (META) в протоколе
# ---------------------------------------------------------------------------


def test_filename_is_confirmed_end_to_end() -> None:
    data = b"report contents"
    config = TransportConfig()
    sender = ReliableSender(
        data, 7, config=config, session_id=0x1234, filename="note.txt"
    )
    receiver = ReliableReceiver(config)
    current, status, arrives_at = run_round(sender, receiver, 0, config)
    assert current is not None
    assert [p.type_name for p in current.packets][:2] == ["manifest", "meta"]
    assert receiver.filename == "note.txt"
    assert status is not None
    assert status.status_info().meta_ok
    assert sender.handle_packet(status, arrives_at).event == "completed"
    assert receiver.delivered == data


def test_meta_repeated_until_acknowledged() -> None:
    data = b"abc"
    config = TransportConfig()
    sender = ReliableSender(
        data, 7, config=config, session_id=0x1234, filename="note.txt"
    )
    receiver = ReliableReceiver(config)
    # META теряется, DATA доходят; карта полная, но meta_ok=0.
    current = sender.advance(0).round
    assert current is not None
    for packet in current.packets:
        if packet.packet_type != PACKET_META:
            receiver.handle_packet(packet, current.start_ms + 10)
    status = receiver.poll(current.round_end_ms + config.ack_guard_ms)
    assert status is not None
    assert status.status_info().complete
    assert not status.status_info().meta_ok
    progress = sender.handle_packet(
        status,
        current.round_end_ms
        + config.ack_guard_ms
        + config.frame_duration_ms(len(status.to_bytes())),
    )
    assert progress.event == "meta_required"
    assert sender.state is SenderState.WAITING
    next_round = sender.advance(
        current.listen_until_ms + config.turnaround_ms
    ).round
    assert next_round is not None
    # Данных для повтора нет, но META повторяется вместе с манифестом.
    assert [p.type_name for p in next_round.packets] == ["manifest", "meta"]
    assert next_round.data_seqs == ()


# ---------------------------------------------------------------------------
# Симулятор канала и CLI
# ---------------------------------------------------------------------------


def test_simulation_clean_transfer() -> None:
    data = bytes(range(200))
    report = simulate_transfer(data, seed=1)
    assert report["schema"] == REPORT_SCHEMA
    assert report["result"] == "ok"
    assert report["sender"]["rounds_sent"] == 1
    assert report["sender"]["data_frames_retransmitted"] == 0
    assert report["receiver"]["complete"] is True
    assert report["receiver"]["sha256_ok"] is True
    assert report["channel_stats"]["data_frames_lost"] == 0
    assert report["crc_is_authentication"] is False
    assert report["sha256_is_authentication"] is False
    assert report["air_test"] is False


@pytest.mark.parametrize(
    ("data_loss", "status_loss", "duplicate_prob", "seed"),
    [
        (0.2, 0.0, 0.0, 7),
        (0.0, 0.4, 0.0, 3),
        (0.15, 0.15, 0.0, 11),
        (0.1, 0.1, 0.3, 5),
        (0.35, 0.0, 0.0, 2),
    ],
)
def test_simulation_recovers_on_lossy_channel(
    data_loss: float, status_loss: float, duplicate_prob: float, seed: int
) -> None:
    data = bytes((index * 37) & 0xFF for index in range(500))
    report = simulate_transfer(
        data,
        seed=seed,
        data_loss=data_loss,
        status_loss=status_loss,
        duplicate_prob=duplicate_prob,
    )
    assert report["result"] == "ok", report
    assert report["receiver"]["complete"] is True
    assert report["receiver"]["sha256_ok"] is True
    assert report["sender"]["missing"] == []
    assert report["receiver"]["delivered_bytes"] == len(data)


def test_simulation_is_deterministic_for_seed() -> None:
    data = bytes((index * 37) & 0xFF for index in range(300))
    first = simulate_transfer(data, seed=42, data_loss=0.2, status_loss=0.1)
    second = simulate_transfer(data, seed=42, data_loss=0.2, status_loss=0.1)
    assert first["rounds"] == second["rounds"]
    assert first["elapsed_ms"] == second["elapsed_ms"]
    assert first["channel_stats"] == second["channel_stats"]


def test_simulation_with_filename() -> None:
    report = simulate_transfer(
        b"report contents", seed=1, filename="note.txt", data_loss=0.1
    )
    assert report["result"] == "ok"
    assert report["message"]["filename"] == "note.txt"


def test_simulation_rejects_invalid_probabilities() -> None:
    for name in ("data_loss", "status_loss", "duplicate_prob"):
        with pytest.raises(SimulationError):
            simulate_transfer(b"abc", **{name: 1.5})
        with pytest.raises(SimulationError):
            simulate_transfer(b"abc", **{name: -0.1})


def test_cli_writes_report_and_returns_zero(tmp_path: Path) -> None:
    report_path = tmp_path / "reliable.json"
    code = cli.main(
        [
            "--size",
            "120",
            "--loss",
            "0.1",
            "--seed",
            "3",
            "--report",
            str(report_path),
        ]
    )
    assert code == cli.EXIT_OK
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == REPORT_SCHEMA
    assert report["result"] == "ok"
    assert report["message"]["bytes"] == 120


def test_cli_rejects_invalid_size(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--size", str(MAX_MESSAGE_BYTES + 1)]) == cli.EXIT_USAGE
    captured = capsys.readouterr()
    assert "0.." in captured.err or "Размер" in captured.err


def test_cli_reads_file(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_bytes(b"hello reliable transport")
    report_path = tmp_path / "report.json"
    code = cli.main(
        ["--file", str(source), "--report", str(report_path), "--seed", "1"]
    )
    assert code == cli.EXIT_OK
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["message"]["filename"] == "note.txt"
    assert report["result"] == "ok"
