"""SN-036: ART/1 внутри непрерывного акустического чата.

Тесты прогоняют PCM через реальный BFSK-модулятор и потоковый декодер, но не
открывают аудиоустройства. Успешный разбор синтетического буфера не является
передачей через воздух: CRC-16 и SHA-256 не аутентифицируют отправителя.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from snlab.chat import (
    ChatSession,
    FrameFailed,
    StreamingFrameDecoder,
    TransportAborted,
    TransportCompleted,
    TransportPacketSeen,
    TransportProgress,
    TransportReplyDue,
    TransportStarted,
)
from snlab.config import PROFILE_DATA_AUDIBLE
from snlab.file_transfer import build_transfer_plan
from snlab.reliable import (
    ABORT_HASH_MISMATCH,
    PACKET_ABORT,
    PACKET_DATA,
    PACKET_MANIFEST,
    PACKET_STATUS,
    ReliableReceiver,
    ReliableSender,
    SenderState,
    StatusInfo,
    TransportPacket,
)
from snlab.reliable_chat import (
    ACTION_DONE,
    ACTION_FAILED,
    ACTION_ROUND,
    ACTION_WAITING,
    NotTransportPayloadError,
    ReliableSendController,
    config_for_profile,
    encode_transport_packets,
    encode_transport_reply,
    encode_transport_round,
    is_transport_payload,
    parse_transport_payload,
)

PROFILE = PROFILE_DATA_AUDIBLE


class Clock:
    """Управляемые монотонные миллисекунды."""

    def __init__(self, now: int = 0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, millis: int) -> None:
        self.now += millis


def feed_blocks(
    decoder: StreamingFrameDecoder,
    samples: np.ndarray,
    clock: Clock,
    *,
    block: int = 4_800,
    step_ms: int = 100,
) -> list[TransportPacket]:
    """Скормить PCM блоками и вернуть разобранные пакеты ART/1."""
    packets: list[TransportPacket] = []
    for index in range(0, samples.size, block):
        frames = decoder.feed(samples[index : index + block])
        for frame in frames:
            packets.append(parse_transport_payload(frame.payload))
        clock.advance(step_ms)
    for frame in decoder.flush():
        packets.append(parse_transport_payload(frame.payload))
    return packets


def first_round(controller: ReliableSendController):
    action = controller.start()
    assert action.kind == ACTION_ROUND
    assert action.outbound is not None
    return action


# --- формат и кодирование ---------------------------------------------------


def test_transport_payload_is_distinguished_from_file_transfer_v0() -> None:
    plan = build_transfer_plan(b"hello", "note.txt", 3)
    file_payload = plan.packets[0].to_bytes()
    assert not is_transport_payload(file_payload)
    with pytest.raises(NotTransportPayloadError):
        parse_transport_payload(file_payload)

    sender = ReliableSender(b"hello", 3, session_id=0x1234)
    action = sender.advance(0)
    assert action.round is not None
    art_payload = action.round.packets[0].to_bytes()
    assert is_transport_payload(art_payload)
    assert parse_transport_payload(art_payload).packet_type == PACKET_MANIFEST


def test_encoded_round_decodes_into_the_same_packets() -> None:
    controller = ReliableSendController(
        b"x" * 60, 9, config=config_for_profile(PROFILE), session_id=0x4321, clock_millis=Clock()
    )
    action = first_round(controller)
    assert action.outbound is not None

    audio = encode_transport_round(action.outbound, PROFILE)
    decoder = StreamingFrameDecoder(PROFILE)
    clock = Clock()
    packets = feed_blocks(decoder, audio.samples, clock)

    assert [packet.to_bytes() for packet in packets] == [
        packet.to_bytes() for packet in action.outbound.packets
    ]
    assert audio.duration_s > 3.0
    assert audio.packet_types[0] == "manifest"


def test_encoded_reply_is_a_single_status_frame() -> None:
    receiver = ReliableReceiver()
    sender = ReliableSender(b"reply", 1, session_id=7)
    progress = sender.advance(0)
    assert progress.round is not None
    for packet in progress.round.packets:
        receiver.handle_packet(packet, 0)
    status = receiver.poll(10_000)
    assert status is not None

    audio = encode_transport_reply(status, PROFILE)
    decoder = StreamingFrameDecoder(PROFILE)
    packets = feed_blocks(decoder, audio.samples, Clock())
    assert len(packets) == 1
    assert packets[0].packet_type == PACKET_STATUS
    assert packets[0].to_bytes() == status.to_bytes()


# --- контроллер отправителя -------------------------------------------------


def test_controller_repeats_only_missing_chunks() -> None:
    clock = Clock()
    controller = ReliableSendController(
        b"y" * 120, 4, config=config_for_profile(PROFILE, window=2), session_id=0x1111, clock_millis=clock
    )
    action = first_round(controller)
    assert action.outbound is not None
    assert action.outbound.data_seqs == (0, 1)

    status = TransportPacket(
        packet_type=PACKET_STATUS,
        transfer_id=4,
        session_id=0x1111,
        round=action.outbound.round,
        seq=0,
        slot=0,
        slots=0,
        payload=StatusInfo(
            complete=False,
            meta_ok=True,
            total_chunks=3,
            received_count=1,
            bitmap=bytes((0b10000000,)),
        ).to_payload(),
    )
    result = controller.handle_packet(status)
    assert result.kind == ACTION_WAITING

    clock.advance(1_000)
    action = controller.tick()
    assert action.kind == ACTION_ROUND
    assert action.outbound is not None
    assert action.outbound.data_seqs == (1, 2)


def test_controller_fails_after_stalled_rounds() -> None:
    clock = Clock()
    controller = ReliableSendController(
        b"z" * 10,
        5,
        config=config_for_profile(
            PROFILE,
            window=1,
            ack_guard_ms=0,
            ack_margin_ms=0,
            inter_frame_gap_ms=0,
            max_stalled_rounds=1,
        ),
        session_id=0x2222,
        clock_millis=clock,
    )
    first_round(controller)

    clock.advance(10_000)
    waiting = controller.tick()
    assert waiting.kind == ACTION_WAITING

    clock.advance(1_000)
    retry = controller.tick()
    assert retry.kind == ACTION_ROUND

    clock.advance(10_000)
    failed = controller.tick()
    assert failed.kind == ACTION_FAILED
    assert failed.error_type == "ack_timeout"


# --- полный обмен через PCM -------------------------------------------------


def test_full_transfer_over_synthetic_pcm() -> None:
    data = b"reliable hello"
    clock = Clock()
    controller = ReliableSendController(
        data, 6, config=config_for_profile(PROFILE), session_id=0x3333, clock_millis=clock
    )
    sender_action = first_round(controller)
    assert sender_action.outbound is not None

    receiver = ReliableReceiver(config_for_profile(PROFILE))
    receiver_clock = Clock()
    decoder = StreamingFrameDecoder(PROFILE)
    packets = feed_blocks(
        decoder, encode_transport_round(sender_action.outbound, PROFILE).samples, receiver_clock
    )
    for packet in packets:
        receiver.handle_packet(packet, receiver_clock.now)

    status = receiver.poll(receiver_clock.now + 30_000)
    assert status is not None
    assert receiver.complete is True
    assert receiver.delivered == data

    status_decoder = StreamingFrameDecoder(PROFILE)
    reply = feed_blocks(
        status_decoder,
        encode_transport_reply(status, PROFILE).samples,
        receiver_clock,
    )
    assert len(reply) == 1

    result = controller.handle_packet(reply[0])
    assert result.kind == ACTION_DONE
    assert controller.state is SenderState.DONE


# --- ChatSession: приём ART/1 ------------------------------------------------


def test_chat_session_receives_text_and_replies_status() -> None:
    clock = Clock()
    session = ChatSession(PROFILE, clock_millis=clock, enable_transport=True)
    controller = ReliableSendController(
        b"art text", 7, config=config_for_profile(PROFILE), session_id=0x4444, clock_millis=Clock()
    )
    action = first_round(controller)
    assert action.outbound is not None
    audio = encode_transport_round(action.outbound, PROFILE)

    events = []
    for index in range(0, audio.samples.size, 4_800):
        events.extend(session.feed(audio.samples[index : index + 4_800]))
        clock.advance(100)
    events.extend(session.flush())

    started = [event for event in events if isinstance(event, TransportStarted)]
    progress = [event for event in events if isinstance(event, TransportProgress)]
    completed = [event for event in events if isinstance(event, TransportCompleted)]
    assert started and started[0].filename is None
    assert started[0].total_chunks == 1
    assert progress
    assert completed and completed[0].text == "art text"
    assert completed[0].file_size == len("art text")

    clock.advance(30_000)
    replies = session.poll_transport()
    assert len(replies) == 1
    assert isinstance(replies[0], TransportReplyDue)
    assert replies[0].packet.packet_type == PACKET_STATUS
    status = replies[0].packet.status_info()
    assert status.complete is True
    assert session.poll_transport() == []


def test_chat_session_saves_file_with_filename(tmp_path: Path) -> None:
    clock = Clock()
    output_dir = tmp_path / "received"
    session = ChatSession(
        PROFILE,
        output_dir=output_dir,
        clock_millis=clock,
        enable_transport=True,
    )
    controller = ReliableSendController(
        b"file bytes",
        8,
        config=config_for_profile(PROFILE),
        session_id=0x5555,
        filename="note.txt",
        clock_millis=Clock(),
    )
    action = first_round(controller)
    assert action.outbound is not None
    audio = encode_transport_round(action.outbound, PROFILE)
    events = []
    for index in range(0, audio.samples.size, 4_800):
        events.extend(session.feed(audio.samples[index : index + 4_800]))
        clock.advance(100)
    events.extend(session.flush())

    completed = [event for event in events if isinstance(event, TransportCompleted)]
    assert completed and completed[0].filename == "note.txt"
    assert completed[0].text is None
    assert completed[0].saved_to is not None
    assert Path(completed[0].saved_to).read_bytes() == b"file bytes"


def test_chat_session_emits_sender_status_packet() -> None:
    clock = Clock()
    session = ChatSession(PROFILE, clock_millis=clock, enable_transport=True)
    receiver = ReliableReceiver(config_for_profile(PROFILE))
    sender = ReliableSender(b"seen", 9, session_id=0x6666)
    progress = sender.advance(0)
    assert progress.round is not None
    for packet in progress.round.packets:
        receiver.handle_packet(packet, 0)
    status = receiver.poll(30_000)
    assert status is not None

    audio = encode_transport_packets((status,), PROFILE)
    events = session.feed(audio.samples)
    seen = [event for event in events if isinstance(event, TransportPacketSeen)]
    assert seen and seen[0].packet.packet_type == PACKET_STATUS


def test_chat_session_aborts_on_hash_mismatch() -> None:
    clock = Clock()
    session = ChatSession(PROFILE, clock_millis=clock, enable_transport=True)
    sender = ReliableSender(b"payload", 10, session_id=0x7777)
    action = sender.advance(0)
    assert action.round is not None
    manifest = action.round.packets[0]
    bad_manifest = TransportPacket(
        packet_type=PACKET_MANIFEST,
        transfer_id=manifest.transfer_id,
        session_id=manifest.session_id,
        round=manifest.round,
        seq=0,
        slot=0,
        slots=manifest.slots,
        payload=manifest.manifest_info().__class__(
            total_size=manifest.manifest_info().total_size,
            total_chunks=manifest.manifest_info().total_chunks,
            chunk_bytes=52,
            meta_present=False,
            sha256=b"\x00" * 32,
        ).to_payload(),
    )
    data_packet = next(
        packet for packet in action.round.packets if packet.packet_type == PACKET_DATA
    )
    audio = encode_transport_packets((bad_manifest, data_packet), PROFILE)
    events = session.feed(audio.samples)
    aborted = [event for event in events if isinstance(event, TransportAborted)]
    assert aborted and aborted[0].error_type == "hash_mismatch"
    replies = [event for event in events if isinstance(event, TransportReplyDue)]
    assert replies and replies[0].packet.packet_type == PACKET_ABORT
    assert replies[0].packet.abort_info().reason == ABORT_HASH_MISMATCH


def test_chat_session_reports_art_when_transport_disabled() -> None:
    session = ChatSession(PROFILE, clock_millis=Clock(), enable_transport=False)
    controller = ReliableSendController(
        b"off", 11, config=config_for_profile(PROFILE), session_id=0x8888, clock_millis=Clock()
    )
    action = first_round(controller)
    assert action.outbound is not None
    events = session.feed(encode_transport_round(action.outbound, PROFILE).samples)
    failed = [event for event in events if isinstance(event, FrameFailed)]
    assert failed and failed[0].error_type == "transport_disabled"


def test_missing_chunk_is_not_reported_complete() -> None:
    session = ChatSession(PROFILE, clock_millis=Clock(), enable_transport=True)
    sender = ReliableSender(b"x" * 80, 12, session_id=0x9999)
    action = sender.advance(0)
    assert action.round is not None
    manifest = action.round.packets[0]
    first = next(
        packet for packet in action.round.packets if packet.packet_type == PACKET_DATA
    )
    events = session.feed(encode_transport_packets((manifest, first), PROFILE).samples)
    assert not any(isinstance(event, TransportCompleted) for event in events)
    progress = [event for event in events if isinstance(event, TransportProgress)]
    assert progress and progress[-1].received_chunks == 1
    assert progress[-1].total_chunks == 2
