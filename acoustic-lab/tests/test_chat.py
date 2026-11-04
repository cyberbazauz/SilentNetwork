"""SN-035: непрерывный акустический чат — потоковый декодер и сессия.

Тесты проверяют только Python-ядро и синтетический PCM. Они не передают
ничего через воздух и не объявляют такую передачу успешной: успешный разбор
буфера доказывает лишь, что кадр Frame v1 прошёл CRC-16, а не что его
услышал микрофон.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest

from snlab import audio
from snlab.chat import (
    ChatSession,
    FileCompleted,
    FileFailed,
    FileProgress,
    FileStarted,
    FrameFailed,
    ReservedPayloadError,
    StreamingFrameDecoder,
    TextReceived,
    encode_file_message,
    encode_text_message,
    validate_chat_text,
)
from snlab.config import PROFILE_DATA_AUDIBLE
from snlab.file_transfer import build_transfer_plan

PROFILE = PROFILE_DATA_AUDIBLE


# --- Вспомогательные функции ------------------------------------------------


def wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def chunked(samples: np.ndarray, block: int) -> list[np.ndarray]:
    return [samples[index : index + block] for index in range(0, samples.size, block)]


# --- StreamingFrameDecoder --------------------------------------------------


def test_decoder_returns_text_frame_from_synthetic_pcm() -> None:
    message = encode_text_message("привет", PROFILE)
    decoder = StreamingFrameDecoder(PROFILE)

    frames = decoder.feed(message.samples)

    assert len(frames) == 1
    assert frames[0].payload == message.payload
    assert frames[0].absolute_start_sample >= 0
    assert frames[0].crc == message.frame.crc
    assert decoder.buffered_samples < message.samples.size


def test_decoder_reads_two_frames_in_one_stream() -> None:
    first = encode_text_message("первый", PROFILE)
    second = encode_text_message("второй", PROFILE)
    gap = np.zeros(int(0.3 * PROFILE.sample_rate), dtype=np.float64)
    stream = np.concatenate((first.samples, gap, second.samples))
    decoder = StreamingFrameDecoder(PROFILE)

    frames = decoder.feed(stream)

    assert [frame.payload for frame in frames] == [first.payload, second.payload]
    positions = [frame.absolute_start_sample for frame in frames]
    assert positions[0] < positions[1]


def test_decoder_resynchronises_after_noise() -> None:
    rng = np.random.default_rng(20261104)
    noise = rng.normal(0.0, 0.05, int(1.0 * PROFILE.sample_rate))
    message = encode_text_message("после шума", PROFILE)
    decoder = StreamingFrameDecoder(PROFILE)

    decoder.feed(noise)
    frames = decoder.feed(message.samples)

    assert [frame.payload for frame in frames] == [message.payload]
    assert frames[0].absolute_start_sample >= noise.size


def test_decoder_keeps_incomplete_frame_until_the_rest_arrives() -> None:
    message = encode_text_message("длинное сообщение", PROFILE)
    split = int(message.samples.size * 0.6)
    decoder = StreamingFrameDecoder(PROFILE)

    assert decoder.feed(message.samples[:split]) == []
    assert decoder.buffered_samples > 0
    frames = decoder.feed(message.samples[split:])

    assert [frame.payload for frame in frames] == [message.payload]


def test_decoder_does_not_repeat_a_frame_after_trailing_silence() -> None:
    message = encode_text_message("один раз", PROFILE)
    decoder = StreamingFrameDecoder(PROFILE)
    assert len(decoder.feed(message.samples)) == 1

    silence = np.zeros(int(2.0 * PROFILE.sample_rate), dtype=np.float64)
    assert decoder.feed(silence) == []


def test_decoder_buffer_stays_bounded_during_long_silence() -> None:
    decoder = StreamingFrameDecoder(PROFILE)
    silence = np.zeros(PROFILE.sample_rate, dtype=np.float64)

    for _ in range(20):
        assert decoder.feed(silence) == []

    # Держим не больше максимального кадра плюс шаг декодирования.
    max_frame = (96 + 64 * 8) * PROFILE.samples_per_symbol
    assert decoder.buffered_samples <= max_frame + PROFILE.sample_rate
    assert decoder.received_samples == 20 * PROFILE.sample_rate


def test_decoder_rejects_bad_profile_and_threshold() -> None:
    with pytest.raises(Exception):
        StreamingFrameDecoder("data-audible")  # type: ignore[arg-type]
    with pytest.raises(Exception):
        StreamingFrameDecoder(PROFILE, min_preamble_matches=0)
    with pytest.raises(Exception):
        StreamingFrameDecoder(PROFILE, min_preamble_matches=33)


def test_decoder_reset_keeps_absolute_position() -> None:
    decoder = StreamingFrameDecoder(PROFILE)
    decoder.feed(np.zeros(PROFILE.sample_rate, dtype=np.float64))
    before = decoder.received_samples

    decoder.reset()

    assert decoder.buffered_samples == 0
    assert decoder.received_samples == before


# --- ChatSession ------------------------------------------------------------


def test_chat_session_reports_text_received() -> None:
    message = encode_text_message("привет", PROFILE)
    session = ChatSession(PROFILE, clock_millis=lambda: 1_700_000_000_000)

    events = session.feed(message.samples)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, TextReceived)
    assert event.text == "привет"
    assert event.payload_bytes == len(message.payload)
    assert event.at_millis == 1_700_000_000_000


def test_chat_session_reports_bad_utf8_without_crashing() -> None:
    session = ChatSession(PROFILE, clock_millis=lambda: 1)
    # payload из одного байта 0xFF — не UTF-8; собираем кадр напрямую.
    from snlab.bfsk import modulate
    from snlab.frame import build_frame

    frame = build_frame(b"\xff")
    signal = modulate(frame.bits, PROFILE).samples
    # Потоковый декодер начинает разбор, только когда накопилось 0.5 с
    # звука; в реальном чате тишина после кадра это обеспечивает.
    samples = np.concatenate(
        (signal, np.zeros(PROFILE.sample_rate // 2, dtype=np.float64))
    )

    events = session.feed(samples)

    assert len(events) == 1
    assert isinstance(events[0], FrameFailed)
    assert events[0].error_type == "payload_utf8_error"


def test_chat_session_assembles_and_saves_file_once(tmp_path: Path) -> None:
    content = b"silent-network chat file payload"
    message = encode_file_message(
        content,
        "chat.bin",
        PROFILE,
        transfer_id=7,
    )
    session = ChatSession(
        PROFILE,
        output_dir=tmp_path,
        clock_millis=lambda: 1_700_000_000_123,
    )

    events = session.feed(message.samples)

    started = [event for event in events if isinstance(event, FileStarted)]
    progress = [event for event in events if isinstance(event, FileProgress)]
    completed = [event for event in events if isinstance(event, FileCompleted)]
    failed = [event for event in events if isinstance(event, FileFailed)]

    assert failed == []
    assert len(started) == 1
    assert started[0].filename == "chat.bin"
    assert started[0].transfer_id == 7
    assert len(progress) >= 3
    assert len(completed) == 1
    saved = Path(completed[0].saved_to or "")
    assert saved.read_bytes() == content
    assert saved.parent.name == "rx-1700000000123-7"


def test_chat_session_does_not_repeat_completed_event_for_same_transfer(
    tmp_path: Path,
) -> None:
    content = b"x" * 40
    message = encode_file_message(content, "one.bin", PROFILE, transfer_id=3)
    session = ChatSession(PROFILE, output_dir=tmp_path)

    first = session.feed(message.samples)
    # END и MANIFEST повторяются в одной передаче; повторный проход тех же
    # отсчётов не должен порождать второе FileCompleted.
    second = session.feed(message.samples)

    assert len([event for event in first if isinstance(event, FileCompleted)]) == 1
    assert [event for event in second if isinstance(event, FileCompleted)] == []


def test_chat_session_reports_new_transfer_with_new_id(tmp_path: Path) -> None:
    first_message = encode_file_message(b"first", "a.bin", PROFILE, transfer_id=3)
    second_message = encode_file_message(b"second", "b.bin", PROFILE, transfer_id=4)
    session = ChatSession(PROFILE, output_dir=tmp_path)

    session.feed(first_message.samples)
    events = session.feed(second_message.samples)

    completed = [event for event in events if isinstance(event, FileCompleted)]
    assert len(completed) == 1
    assert completed[0].filename == "b.bin"
    assert Path(completed[0].saved_to or "").read_bytes() == b"second"


def test_chat_session_reset_decoder_keeps_file_state() -> None:
    content = b"partial file chunks"
    session = ChatSession(PROFILE)
    plan = build_transfer_plan(content, "keep.bin", 9)
    packets = plan.packets
    decoder = session._decoder  # noqa: SLF001 - тест внутреннего состояния

    from snlab.bfsk import modulate
    from snlab.frame import build_frame

    # MANIFEST и первый DATA доходят, затем «разрыв» микрофона.
    for packet in packets[:2]:
        frame = build_frame(packet.to_bytes())
        session.feed(modulate(frame.bits, PROFILE).samples)
    assert session._assembler is not None  # noqa: SLF001

    session.reset_decoder()

    assert decoder.buffered_samples == 0
    assert session._assembler is not None  # noqa: SLF001
    assert session._assembler.state().chunks_received >= 1  # noqa: SLF001


def test_validate_chat_text_rejects_reserved_prefix() -> None:
    with pytest.raises(ReservedPayloadError):
        validate_chat_text("SFnot-a-file")


def test_validate_chat_text_accepts_normal_text() -> None:
    assert validate_chat_text("привет") == "привет".encode("utf-8")


# --- ContinuousCapture ------------------------------------------------------


class GatedStream:
    """Поток, который отдаёт блоки только когда тест их положил."""

    def __init__(self) -> None:
        self.blocks: queue.Queue[np.ndarray | None] = queue.Queue()
        self.started = False

    def put(self, block: np.ndarray) -> None:
        self.blocks.put(block)

    def start(self) -> None:
        self.started = True

    def read(self, frames: int) -> tuple[np.ndarray, bool]:
        block = self.blocks.get(timeout=10)
        if block is None:
            raise RuntimeError("поток остановлен")
        return block, False

    def abort(self) -> None:
        self.blocks.put(None)

    def stop(self) -> None:
        self.blocks.put(None)

    def close(self) -> None:
        self.blocks.put(None)


def make_capture(
    stream: GatedStream,
    **kwargs: Any,
) -> tuple[audio.ContinuousCapture, Any]:
    backend = type("Backend", (), {"InputStream": staticmethod(lambda **_: stream)})
    capture_kwargs: dict[str, Any] = {
        "sample_rate": 8_000,
        "block_size": 4,
        "queue_capacity": 4,
    }
    capture_kwargs.update(kwargs)
    capture = audio.ContinuousCapture(sd_module=backend, **capture_kwargs)
    return capture, backend


def test_continuous_capture_delivers_blocks_until_stop() -> None:
    stream = GatedStream()
    received: list[np.ndarray] = []
    capture, _ = make_capture(stream, on_samples=received.append)

    capture.start()
    assert stream.started
    blocks = [
        np.full(4, 0.1, dtype=np.float32),
        np.full(4, 0.2, dtype=np.float32),
        np.full(4, 0.3, dtype=np.float32),
    ]
    for block in blocks:
        stream.put(block)
    assert wait_until(lambda: len(received) == 3)
    capture.stop()

    assert not capture.is_running
    assert [float(block[0]) for block in received] == pytest.approx([0.1, 0.2, 0.3])


def test_continuous_capture_pause_drops_audio_and_reports_gap() -> None:
    stream = GatedStream()
    received: list[float] = []
    gaps: list[str] = []
    capture, _ = make_capture(
        stream,
        on_samples=lambda block: received.append(float(block[0])),
        on_gap=gaps.append,
    )

    capture.start()
    stream.put(np.full(4, 0.1, dtype=np.float32))
    assert wait_until(lambda: received == pytest.approx([0.1]))

    capture.pause()
    stream.put(np.full(4, 0.9, dtype=np.float32))
    assert wait_until(lambda: capture.dropped_blocks >= 1)

    capture.resume()
    stream.put(np.full(4, 0.3, dtype=np.float32))
    assert wait_until(lambda: received == pytest.approx([0.1, 0.3]))
    capture.stop()

    assert gaps == ["pause"]
    assert capture.dropped_blocks >= 1


def test_continuous_capture_overflow_clears_queue_and_reports_gap() -> None:
    stream = GatedStream()
    gate = threading.Event()
    release = threading.Event()
    received: list[float] = []
    gaps: list[str] = []

    def on_samples(block: np.ndarray) -> None:
        received.append(float(block[0]))
        if len(received) == 1:
            gate.set()
            release.wait(timeout=10)

    capture, _ = make_capture(
        stream,
        queue_capacity=1,
        on_samples=on_samples,
        on_gap=gaps.append,
    )

    capture.start()
    stream.put(np.full(4, 0.1, dtype=np.float32))
    assert gate.wait(timeout=10)
    # Потребитель заблокирован в on_samples: очередь заполняется, следующий
    # блок теряется вместе с уже стоявшим в очереди.
    stream.put(np.full(4, 0.2, dtype=np.float32))
    stream.put(np.full(4, 0.3, dtype=np.float32))
    assert wait_until(lambda: capture.dropped_blocks >= 1)
    release.set()
    stream.put(np.full(4, 0.4, dtype=np.float32))
    assert wait_until(lambda: len(received) == 2)
    capture.stop()

    assert gaps and gaps[0] == "overflow"
    assert received == pytest.approx([0.1, 0.4])


def test_continuous_capture_reports_driver_error() -> None:
    stream = GatedStream()
    errors: list[Exception] = []
    capture, _ = make_capture(stream, on_samples=lambda block: None, on_error=errors.append)

    capture.start()
    stream.abort()  # None → RuntimeError внутри read
    assert wait_until(lambda: len(errors) == 1)
    capture.stop()

    assert isinstance(errors[0], RuntimeError)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sample_rate": 4_000},
        {"sample_rate": "48000"},
        {"block_size": 0},
        {"queue_capacity": 0},
    ],
)
def test_continuous_capture_rejects_bad_parameters(kwargs: dict[str, Any]) -> None:
    stream = GatedStream()
    with pytest.raises(ValueError):
        make_capture(stream, on_samples=lambda block: None, **kwargs)


def test_continuous_capture_rejects_non_callable() -> None:
    stream = GatedStream()
    with pytest.raises(ValueError, match="on_samples"):
        audio.ContinuousCapture(sample_rate=8_000, on_samples=None)  # type: ignore[arg-type]
