"""Непрерывный акустический чат: поток PCM → кадры Frame v1 → сообщения.

Модуль добавляет поверх ``receiver`` и ``file_transfer`` слой, который умеет
работать с бесконечным микрофонным потоком, а не с заранее записанным WAV:

* ``StreamingFrameDecoder`` держит ограниченный хвост PCM, ищет преамбулу,
  проверяет sync word / версию / длину / CRC-16 и выдаёт только те кадры,
  которые прошли проверку;
* ``ChatSession`` превращает payload в UTF-8-текст или собирает файл
  File Transfer v0;
* ``encode_text_message`` / ``encode_file_message`` сразу готовят отсчёты для
  воспроизведения, без промежуточного WAV.

Границы те же, что у Data Link v0 и File Transfer v0:

* CRC-16 обнаруживает повреждение, но **не аутентифицирует** отправителя;
* SHA-256 проверяет целостность файла, но не подлинность;
* успешное декодирование потока не доказывает передачу через воздух;
* ACK нет: «передано» означает только «воспроизведено в динамик».

Чат полудуплексный: на время собственного воспроизведения вызывающая сторона
должна приостановить приём, иначе микрофон услышит собственный динамик.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from snlab.bfsk import DEFAULT_RAMP_MS, modulate
from snlab.config import BfskProfile
from snlab.datalink import (
    ACOUSTIC_MIN_PREAMBLE_MATCHES,
    MAX_DATA_PAYLOAD_BYTES,
    DataLinkPayloadError,
    payload_to_text,
    text_to_payload,
)
from snlab.file_transfer import (
    DEFAULT_END_REPEATS,
    DEFAULT_INTER_FRAME_GAP_S,
    DEFAULT_MANIFEST_REPEAT_EVERY,
    FT_MAGIC,
    FileAssembler,
    FileTransferError,
    NotFileTransferError,
    build_transfer_plan,
    parse_packet,
    write_assembled_file,
)
from snlab.frame import FRAME_OVERHEAD_BITS, PREAMBLE_BITS, Frame, build_frame
from snlab.receiver import (
    IncompleteFrame,
    ReceiverError,
    SignalNotFound,
    bit_metric,
    decode_candidates,
    find_frame_candidates,
    validate_min_preamble_matches,
)

#: Тишина перед кадром в чате. Микрофон уже слушает, поэтому длинная пауза
#: не нужна; запас остаётся для рампы и задержки старта драйвера.
DEFAULT_CHAT_LEAD_SILENCE_S = 0.2
DEFAULT_CHAT_TAIL_SILENCE_S = 0.1
MAX_SILENCE_S = 10.0


class ChatError(ValueError):
    """Поток чата нельзя разобрать или сообщение нельзя отправить."""


class ReservedPayloadError(ChatError):
    """Текст начинается с ``SF`` — этот префикс занят файловым пакетом."""


@dataclass(frozen=True, slots=True)
class StreamFrame:
    """Проверенный Frame v1, найденный в непрерывном потоке."""

    payload: bytes
    absolute_start_sample: int
    preamble_matches: int
    crc: int
    payload_sha256: str


class StreamingFrameDecoder:
    """Инкрементальный приёмник Frame v1 для постоянно открытого микрофона.

    Буфер ограничен самым длинным допустимым кадром плюс шаг декодирования.
    Кадр, который ещё не доехал целиком, остаётся в буфере до следующего
    блока. Повреждённый кадр не останавливает приём: сканер переходит к
    следующей преамбуле, а CRC-16 остаётся единственным арбитром.
    """

    def __init__(
        self,
        profile: BfskProfile,
        *,
        max_payload_bytes: int = MAX_DATA_PAYLOAD_BYTES,
        min_preamble_matches: int = ACOUSTIC_MIN_PREAMBLE_MATCHES,
    ) -> None:
        if not isinstance(profile, BfskProfile):
            raise ChatError("профиль должен быть BfskProfile")
        if (
            not isinstance(max_payload_bytes, int)
            or isinstance(max_payload_bytes, bool)
            or max_payload_bytes < 1
        ):
            raise ChatError("max_payload_bytes должен быть положительным целым")
        validate_min_preamble_matches(min_preamble_matches)
        self.profile = profile
        self.max_payload_bytes = max_payload_bytes
        self.min_preamble_matches = min_preamble_matches

        self._samples_per_symbol = profile.samples_per_symbol
        self._preamble_span = PREAMBLE_BITS * self._samples_per_symbol
        max_frame_bits = FRAME_OVERHEAD_BITS + max_payload_bytes * 8
        # Точная длина кадра — max_frame_bits*samples_per_symbol. Запас в два
        # символа покрывает уточнение начала по преамбуле.
        self._max_frame_samples = (
            max_frame_bits * self._samples_per_symbol + 2 * self._samples_per_symbol
        )
        self._min_decode_samples = max(
            self._preamble_span, profile.sample_rate // 4
        )
        self._decode_step_samples = max(1, profile.sample_rate // 2)

        self._buffer = np.empty(0, dtype=np.float64)
        self._buffer_start_sample = 0
        self._samples_since_decode = 0

    @property
    def received_samples(self) -> int:
        """Сколько отсчётов принято или ещё лежит в буфере."""
        return self._buffer_start_sample + int(self._buffer.size)

    @property
    def buffered_samples(self) -> int:
        """Сколько отсчётов удерживается для повторной синхронизации."""
        return int(self._buffer.size)

    @property
    def buffered_start_sample(self) -> int:
        """Абсолютный индекс первого отсчёта буфера."""
        return self._buffer_start_sample

    def feed(self, samples: np.ndarray) -> list[StreamFrame]:
        """Добавить PCM в [-1, 1] и вернуть кадры, найденные этим вызовом."""
        data = np.asarray(samples, dtype=np.float64)
        if data.ndim != 1:
            raise ChatError("сигнал должен быть одномерным")
        if data.size == 0:
            return []

        self._buffer = (
            data.copy()
            if self._buffer.size == 0
            else np.concatenate((self._buffer, data))
        )
        self._samples_since_decode += int(data.size)
        if (
            self._buffer.size < self._min_decode_samples
            or self._samples_since_decode < self._decode_step_samples
        ):
            return []
        self._samples_since_decode = 0
        return self._decode_buffered()

    def flush(self) -> list[StreamFrame]:
        """Разобрать остаток буфера; вызывать после остановки захвата."""
        if self._buffer.size < self._preamble_span:
            return []
        self._samples_since_decode = 0
        return self._decode_buffered()

    def reset(self) -> None:
        """Сбросить PCM-буфер после разрыва или смены профиля."""
        self._buffer_start_sample += int(self._buffer.size)
        self._buffer = np.empty(0, dtype=np.float64)
        self._samples_since_decode = 0

    def _decode_buffered(self) -> list[StreamFrame]:
        if self._buffer.size < self._preamble_span:
            return []
        try:
            metric = bit_metric(self._buffer, self.profile)
        except SignalNotFound:
            return []
        except ReceiverError:
            self.reset()
            return []

        events: list[StreamFrame] = []
        scan = 0
        retain_from = 0
        span = self._preamble_span

        while scan < metric.size:
            if metric.size - scan < span:
                break
            view = metric[scan:]
            try:
                candidates = find_frame_candidates(
                    view,
                    self.profile,
                    min_preamble_matches=self.min_preamble_matches,
                    limit=8,
                )
            except SignalNotFound:
                break

            best_matches = max(item.preamble_matches for item in candidates)
            anchor = min(
                item.sample
                for item in candidates
                if item.preamble_matches == best_matches
            )
            nearby = [
                item
                for item in candidates
                if 0 <= item.sample - anchor <= 4 * self._samples_per_symbol
            ]
            if not nearby:
                break
            failure_start = min(item.sample for item in nearby)

            try:
                frame = decode_candidates(
                    view,
                    self.profile,
                    nearby,
                    max_payload_bytes=self.max_payload_bytes,
                    min_preamble_matches=self.min_preamble_matches,
                )
            except IncompleteFrame:
                # Кадр начался, но ещё не доехал целиком.
                retain_from = scan + failure_start
                break
            except ReceiverError:
                resync = scan + failure_start + span
                if resync >= metric.size:
                    retain_from = scan + failure_start
                    break
                scan = resync
                retain_from = scan
                continue

            start = scan + frame.start_sample
            events.append(
                StreamFrame(
                    payload=frame.payload,
                    absolute_start_sample=self._buffer_start_sample + start,
                    preamble_matches=frame.preamble_matches,
                    crc=frame.crc,
                    payload_sha256=hashlib.sha256(frame.payload).hexdigest(),
                )
            )
            consumed = start + frame.total_bits * self._samples_per_symbol
            if consumed <= scan:
                break
            scan = consumed
            retain_from = consumed

        max_retained = self._max_frame_samples + self._decode_step_samples
        if retain_from < self._buffer.size - max_retained:
            retain_from = self._buffer.size - max_retained
        retain_from = max(0, min(retain_from, int(self._buffer.size)))
        if retain_from > 0:
            self._buffer = self._buffer[retain_from:].copy()
            self._buffer_start_sample += retain_from
        return events


@dataclass(frozen=True, slots=True)
class TextReceived:
    text: str
    payload_bytes: int
    start_sample: int
    preamble_matches: int
    at_millis: int


@dataclass(frozen=True, slots=True)
class FileStarted:
    transfer_id: int
    filename: str
    file_size: int
    total_chunks: int
    start_sample: int
    at_millis: int


@dataclass(frozen=True, slots=True)
class FileProgress:
    transfer_id: int
    filename: str | None
    received_chunks: int
    total_chunks: int
    duplicates: int
    start_sample: int
    at_millis: int


@dataclass(frozen=True, slots=True)
class FileCompleted:
    transfer_id: int
    filename: str
    file_size: int
    sha256: str
    saved_to: str | None
    at_millis: int


@dataclass(frozen=True, slots=True)
class FileFailed:
    transfer_id: int | None
    error_type: str
    message: str
    at_millis: int


@dataclass(frozen=True, slots=True)
class FrameFailed:
    error_type: str
    message: str
    at_millis: int


ChatEvent = (
    TextReceived | FileStarted | FileProgress | FileCompleted | FileFailed | FrameFailed
)


class ChatSession:
    """Полудуплексная сессия чата над ``StreamingFrameDecoder``.

    Все методы должны вызываться из одного потока-потребителя (обычно это
    поток ``ContinuousCapture``). События содержат абсолютное смещение
    отсчётов, но не подтверждают доставку: ACK в v0 нет.
    """

    def __init__(
        self,
        profile: BfskProfile,
        *,
        output_dir: str | Path | None = None,
        min_preamble_matches: int = ACOUSTIC_MIN_PREAMBLE_MATCHES,
        clock_millis: Callable[[], int] | None = None,
    ) -> None:
        self.profile = profile
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self._decoder = StreamingFrameDecoder(
            profile,
            max_payload_bytes=MAX_DATA_PAYLOAD_BYTES,
            min_preamble_matches=min_preamble_matches,
        )
        self._clock = clock_millis or (lambda: int(time.time() * 1000))
        self._assembler: FileAssembler | None = None
        self._announced_transfer_id: int | None = None
        self._completed_transfer_id: int | None = None

    @property
    def received_samples(self) -> int:
        return self._decoder.received_samples

    def feed(self, samples: np.ndarray) -> list[ChatEvent]:
        try:
            frames = self._decoder.feed(samples)
        except ChatError as exc:
            return [
                FrameFailed(
                    error_type="stream_decode_error",
                    message=str(exc),
                    at_millis=self._clock(),
                )
            ]
        events: list[ChatEvent] = []
        for frame in frames:
            events.extend(self._process_frame(frame))
        return events

    def flush(self) -> list[ChatEvent]:
        events: list[ChatEvent] = []
        for frame in self._decoder.flush():
            events.extend(self._process_frame(frame))
        return events

    def reset_decoder(self) -> None:
        """Сбросить только PCM-буфер после разрыва потока.

        Уже принятые файловые чанки сохраняются: у отправителя нет повторов,
        и разрыв не должен уничтожать корректно декодированные пакеты.
        """
        self._decoder.reset()

    def reset_stream(self) -> None:
        """Сбросить буфер и незавершённую файловую сборку."""
        self._decoder.reset()
        self._release_assembler()

    def _process_frame(self, frame: StreamFrame) -> list[ChatEvent]:
        now = self._clock()
        if len(frame.payload) >= 2 and frame.payload[:2] == FT_MAGIC:
            return self._process_file_packet(frame, now)
        try:
            text = payload_to_text(frame.payload, max_bytes=MAX_DATA_PAYLOAD_BYTES)
        except DataLinkPayloadError as exc:
            return [
                FrameFailed(
                    error_type="payload_utf8_error",
                    message=str(exc),
                    at_millis=now,
                )
            ]
        return [
            TextReceived(
                text=text,
                payload_bytes=len(frame.payload),
                start_sample=frame.absolute_start_sample,
                preamble_matches=frame.preamble_matches,
                at_millis=now,
            )
        ]

    def _process_file_packet(
        self, frame: StreamFrame, now: int
    ) -> list[ChatEvent]:
        try:
            packet = parse_packet(frame.payload)
        except NotFileTransferError:
            return [
                FrameFailed(
                    error_type="file_packet_invalid",
                    message=(
                        "payload начинается с SF, но не является файловым пакетом"
                    ),
                    at_millis=now,
                )
            ]
        except FileTransferError as exc:
            return [
                FileFailed(
                    transfer_id=None,
                    error_type=exc.error_type,
                    message=str(exc),
                    at_millis=now,
                )
            ]

        current = self._assembler
        if (
            current is not None
            and current.transfer_id is not None
            and current.transfer_id != packet.transfer_id
        ):
            self._release_assembler()
        target = self._assembler
        if target is None:
            target = FileAssembler()
            self._assembler = target
            self._announced_transfer_id = None
            self._completed_transfer_id = None

        events: list[ChatEvent] = []
        try:
            target.add(packet)
        except FileTransferError as exc:
            # Конфликт делает частичное состояние недостоверным. Отправитель
            # повторяет MANIFEST, поэтому следующая передача начнётся заново.
            self._release_assembler()
            events.append(
                FileFailed(
                    transfer_id=packet.transfer_id,
                    error_type=exc.error_type,
                    message=str(exc),
                    at_millis=now,
                )
            )
            return events

        state = target.state()
        manifest = target.manifest
        if (
            manifest is not None
            and self._announced_transfer_id != packet.transfer_id
        ):
            self._announced_transfer_id = packet.transfer_id
            events.append(
                FileStarted(
                    transfer_id=packet.transfer_id,
                    filename=manifest.filename,
                    file_size=manifest.file_size,
                    total_chunks=state.total_chunks or 0,
                    start_sample=frame.absolute_start_sample,
                    at_millis=now,
                )
            )
        events.append(
            FileProgress(
                transfer_id=packet.transfer_id,
                filename=manifest.filename if manifest else None,
                received_chunks=state.chunks_received,
                total_chunks=state.total_chunks or 0,
                duplicates=state.duplicate_chunks,
                start_sample=frame.absolute_start_sample,
                at_millis=now,
            )
        )

        if not state.complete or self._completed_transfer_id == packet.transfer_id:
            return events

        try:
            content = target.assemble()
        except FileTransferError as exc:
            events.append(
                FileFailed(
                    transfer_id=packet.transfer_id,
                    error_type=exc.error_type,
                    message=str(exc),
                    at_millis=now,
                )
            )
            self._completed_transfer_id = packet.transfer_id
            return events

        digest = hashlib.sha256(content).hexdigest()
        saved_to: str | None = None
        if self.output_dir is not None and manifest is not None:
            try:
                target_dir = self.output_dir / (
                    f"rx-{now}-{packet.transfer_id}"
                )
                saved_to = str(
                    write_assembled_file(target_dir, manifest.filename, content)
                )
            except Exception as exc:  # noqa: BLE001 - ошибку показываем событием
                events.append(
                    FileFailed(
                        transfer_id=packet.transfer_id,
                        error_type="output_error",
                        message=str(exc),
                        at_millis=now,
                    )
                )
        self._completed_transfer_id = packet.transfer_id
        events.append(
            FileCompleted(
                transfer_id=packet.transfer_id,
                filename=(
                    manifest.filename
                    if manifest is not None
                    else f"file-{packet.transfer_id:02x}"
                ),
                file_size=len(content),
                sha256=digest,
                saved_to=saved_to,
                at_millis=now,
            )
        )
        return events

    def _release_assembler(self) -> None:
        self._assembler = None
        self._announced_transfer_id = None
        self._completed_transfer_id = None


@dataclass(frozen=True, slots=True)
class ChatTextMessage:
    """Готовое к воспроизведению текстовое сообщение чата."""

    text: str
    payload: bytes
    frame: Frame
    samples: np.ndarray
    sample_rate: int
    lead_silence_s: float
    tail_silence_s: float
    ramp_ms: float
    profile_name: str

    @property
    def duration_s(self) -> float:
        return float(self.samples.size / self.sample_rate)

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


@dataclass(frozen=True, slots=True)
class ChatFileMessage:
    """Готовое к воспроизведению файловое сообщение чата."""

    filename: str
    content: bytes
    samples: np.ndarray
    sample_rate: int
    transfer_id: int
    total_chunks: int
    packet_count: int
    frame_duration_s: float
    inter_frame_gap_s: float
    lead_silence_s: float
    tail_silence_s: float
    ramp_ms: float
    profile_name: str

    @property
    def file_size(self) -> int:
        return len(self.content)

    @property
    def duration_s(self) -> float:
        return float(self.samples.size / self.sample_rate)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def validate_chat_text(text: str) -> bytes:
    """Проверить текст чата и вернуть payload UTF-8.

    Payload, начинающийся с ``SF``, зарезервирован под файловый пакет.
    """
    payload = text_to_payload(text, max_bytes=MAX_DATA_PAYLOAD_BYTES)
    if payload[:2] == FT_MAGIC:
        raise ReservedPayloadError(
            "текст не может начинаться с SF: это префикс файлового пакета"
        )
    return payload


def encode_text_message(
    text: str,
    profile: BfskProfile,
    *,
    lead_silence_s: float = DEFAULT_CHAT_LEAD_SILENCE_S,
    tail_silence_s: float = DEFAULT_CHAT_TAIL_SILENCE_S,
    ramp_ms: float = DEFAULT_RAMP_MS,
) -> ChatTextMessage:
    """Собрать текст в отсчёты без записи WAV."""
    _validate_profile(profile)
    lead = _validate_seconds(lead_silence_s, "lead_silence_s")
    tail = _validate_seconds(tail_silence_s, "tail_silence_s")
    payload = validate_chat_text(text)
    frame = build_frame(payload, max_payload_bytes=MAX_DATA_PAYLOAD_BYTES)
    modulation = modulate(frame.bits, profile, ramp_ms=ramp_ms)
    lead_samples = int(round(lead * profile.sample_rate))
    tail_samples = int(round(tail * profile.sample_rate))
    samples = np.concatenate(
        (
            np.zeros(lead_samples, dtype=np.float64),
            modulation.samples,
            np.zeros(tail_samples, dtype=np.float64),
        )
    )
    return ChatTextMessage(
        text=text,
        payload=payload,
        frame=frame,
        samples=samples,
        sample_rate=profile.sample_rate,
        lead_silence_s=lead,
        tail_silence_s=tail,
        ramp_ms=float(ramp_ms),
        profile_name=profile.name,
    )


def encode_file_message(
    content: bytes,
    filename: str,
    profile: BfskProfile,
    transfer_id: int,
    *,
    manifest_repeat_every: int = DEFAULT_MANIFEST_REPEAT_EVERY,
    end_repeats: int = DEFAULT_END_REPEATS,
    inter_frame_gap_s: float = DEFAULT_INTER_FRAME_GAP_S,
    lead_silence_s: float = DEFAULT_CHAT_LEAD_SILENCE_S,
    tail_silence_s: float = DEFAULT_CHAT_TAIL_SILENCE_S,
    ramp_ms: float = DEFAULT_RAMP_MS,
) -> ChatFileMessage:
    """Собрать файл File Transfer v0 в отсчёты без записи WAV.

    Отсчёты выделяются один раз на весь сигнал: для 8192 байт это около
    364 с и ~140 МБ в float64. Временный массив модуляции одного кадра
    добавляется к этому, а не ко всему файлу.
    """
    _validate_profile(profile)
    gap = _validate_seconds(inter_frame_gap_s, "inter_frame_gap_s")
    lead = _validate_seconds(lead_silence_s, "lead_silence_s")
    tail = _validate_seconds(tail_silence_s, "tail_silence_s")
    plan = build_transfer_plan(
        bytes(content),
        filename,
        transfer_id,
        manifest_repeat_every=manifest_repeat_every,
        end_repeats=end_repeats,
    )

    frames: list[Frame] = [
        build_frame(packet.to_bytes(), max_payload_bytes=MAX_DATA_PAYLOAD_BYTES)
        for packet in plan.packets
    ]
    samples_per_symbol = profile.samples_per_symbol
    gap_samples = int(round(gap * profile.sample_rate))
    lead_samples = int(round(lead * profile.sample_rate))
    tail_samples = int(round(tail * profile.sample_rate))
    frame_samples = [
        frame.total_bits * samples_per_symbol for frame in frames
    ]
    total_samples = (
        lead_samples
        + tail_samples
        + sum(frame_samples)
        + gap_samples * max(0, len(frames) - 1)
    )
    out = np.zeros(total_samples, dtype=np.float64)
    cursor = lead_samples
    frame_duration_s = 0.0
    for index, frame in enumerate(frames):
        modulation = modulate(frame.bits, profile, ramp_ms=ramp_ms)
        frame_duration_s = frame.total_bits / profile.symbol_rate
        out[cursor : cursor + modulation.samples.size] = modulation.samples
        cursor += modulation.samples.size
        if index + 1 < len(frames):
            cursor += gap_samples

    return ChatFileMessage(
        filename=plan.manifest.filename,
        content=bytes(content),
        samples=out,
        sample_rate=profile.sample_rate,
        transfer_id=transfer_id,
        total_chunks=plan.total_chunks,
        packet_count=len(frames),
        frame_duration_s=frame_duration_s,
        inter_frame_gap_s=gap,
        lead_silence_s=lead,
        tail_silence_s=tail,
        ramp_ms=float(ramp_ms),
        profile_name=profile.name,
    )


def _validate_profile(profile: BfskProfile) -> None:
    if not isinstance(profile, BfskProfile):
        raise ChatError("профиль должен быть BfskProfile")


def _validate_seconds(value: float, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ChatError(f"{field_name} должен быть числом")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= MAX_SILENCE_S:
        raise ChatError(
            f"{field_name} должен быть в диапазоне 0..{MAX_SILENCE_S:g} с"
        )
    return number


__all__ = [
    "ChatEvent",
    "ChatError",
    "ChatFileMessage",
    "ChatSession",
    "ChatTextMessage",
    "DEFAULT_CHAT_LEAD_SILENCE_S",
    "DEFAULT_CHAT_TAIL_SILENCE_S",
    "FileCompleted",
    "FileFailed",
    "FileProgress",
    "FileStarted",
    "FrameFailed",
    "ReservedPayloadError",
    "StreamFrame",
    "StreamingFrameDecoder",
    "TextReceived",
    "encode_file_message",
    "encode_text_message",
    "validate_chat_text",
]
