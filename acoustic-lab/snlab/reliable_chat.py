"""ART/1 в непрерывном акустическом чате (SN-036).

Модуль связывает контракт надёжной доставки ``snlab.reliable`` с PCM-потоком
чата:

* распознаёт пакеты ART/1 внутри payload Frame v1 и отличает их от
  File Transfer v0 (версия 2 против версии 1);
* кодирует раунд отправителя и ответ ``STATUS``/``ABORT`` в отсчёты без
  промежуточного WAV;
* ``ReliableSendController`` ведёт полудуплексный цикл «раунд → ожидание
  статуса → повтор только отсутствующих».

Границы те же, что у ART/1: CRC-16 и SHA-256 не аутентифицируют отправителя,
успешное воспроизведение раунда не доказывает приём через воздух. Полудуплекс
обеспечивает вызывающая сторона: она обязана приостановить микрофон на время
собственного звука.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from snlab.bfsk import DEFAULT_RAMP_MS, modulate
from snlab.config import BfskProfile
from snlab.frame import Frame, build_frame
from snlab.reliable import (
    DEFAULT_ACK_GUARD_MS,
    DEFAULT_ACK_MARGIN_MS,
    DEFAULT_INTER_FRAME_GAP_MS,
    DEFAULT_MAX_ROUNDS,
    DEFAULT_MAX_STALLED_ROUNDS,
    DEFAULT_TURNAROUND_MS,
    DEFAULT_WINDOW,
    MAGIC,
    MAX_FRAME_PAYLOAD_BYTES,
    PACKET_ABORT,
    PACKET_STATUS,
    OutboundRound,
    PacketFormatError,
    ReliableSender,
    ReliableTransportError,
    SenderProgress,
    SenderState,
    TransportConfig,
    TransportPacket,
    UnsupportedVersionError,
    VERSION,
)

#: Небольшая тишина перед раундом: микрофон уже слушает, но драйверу нужен
#: запас на старт воспроизведения. Значения согласованы с окном ``ack_guard``.
DEFAULT_ROUND_LEAD_SILENCE_S = 0.10
DEFAULT_ROUND_TAIL_SILENCE_S = 0.05
DEFAULT_REPLY_LEAD_SILENCE_S = 0.05
DEFAULT_REPLY_TAIL_SILENCE_S = 0.05
MAX_SILENCE_S = 10.0

#: Коды действий контроллера отправителя.
ACTION_IDLE = "idle"
ACTION_ROUND = "round"
ACTION_WAITING = "waiting"
ACTION_DONE = "done"
ACTION_FAILED = "failed"
ACTION_IGNORED = "ignored"


class ReliableChatError(ValueError):
    """Некорректное состояние интеграции ART/1 с чатом."""


class NotTransportPayloadError(ValueError):
    """Payload не является пакетом ART/1."""


@dataclass(frozen=True, slots=True)
class TransportAudio:
    """Готовые к воспроизведению отсчёты одного пакета или раунда."""

    samples: np.ndarray
    sample_rate: int
    packets: tuple[TransportPacket, ...]
    lead_silence_s: float
    tail_silence_s: float
    inter_frame_gap_s: float
    profile_name: str

    @property
    def duration_s(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return float(self.samples.size / self.sample_rate)

    @property
    def packet_types(self) -> tuple[str, ...]:
        return tuple(packet.type_name for packet in self.packets)


def is_transport_payload(payload: bytes) -> bool:
    """Отличить ART/1 (версия 2) от File Transfer v0 (версия 1)."""
    data = bytes(payload)
    return len(data) >= 3 and data[:2] == MAGIC and data[2] == VERSION


def parse_transport_payload(payload: bytes) -> TransportPacket:
    """Разобрать payload как пакет ART/1.

    Raises:
        NotTransportPayloadError: payload не помечен версией ART/1.
        PacketFormatError: пакет помечен ART/1, но повреждён.
    """
    data = bytes(payload)
    if not is_transport_payload(data):
        raise NotTransportPayloadError("payload не является пакетом ART/1")
    try:
        return TransportPacket.parse(data)
    except UnsupportedVersionError:  # pragma: no cover - защита от гонки версий
        raise
    except PacketFormatError:
        raise
    except ReliableTransportError as exc:  # pragma: no cover - прочие ошибки
        raise PacketFormatError(str(exc)) from exc


def config_for_profile(
    profile: BfskProfile,
    *,
    window: int = DEFAULT_WINDOW,
    inter_frame_gap_ms: int = DEFAULT_INTER_FRAME_GAP_MS,
    ack_guard_ms: int = DEFAULT_ACK_GUARD_MS,
    ack_margin_ms: int = DEFAULT_ACK_MARGIN_MS,
    turnaround_ms: int = DEFAULT_TURNAROUND_MS,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_stalled_rounds: int = DEFAULT_MAX_STALLED_ROUNDS,
) -> TransportConfig:
    """Собрать тайминги ART/1 под реальную символьную скорость профиля.

    Обе стороны обязаны использовать одинаковые ``window``, паузы и профиль:
    эти параметры не передаются в пакетах.
    """
    if not isinstance(profile, BfskProfile):
        raise ReliableChatError("профиль должен быть BfskProfile")
    return TransportConfig(
        window=window,
        symbol_rate=float(profile.symbol_rate),
        inter_frame_gap_ms=inter_frame_gap_ms,
        ack_guard_ms=ack_guard_ms,
        ack_margin_ms=ack_margin_ms,
        turnaround_ms=turnaround_ms,
        max_rounds=max_rounds,
        max_stalled_rounds=max_stalled_rounds,
    )


def encode_transport_packets(
    packets: tuple[TransportPacket, ...] | list[TransportPacket],
    profile: BfskProfile,
    *,
    inter_frame_gap_s: float = DEFAULT_INTER_FRAME_GAP_MS / 1000.0,
    lead_silence_s: float = DEFAULT_ROUND_LEAD_SILENCE_S,
    tail_silence_s: float = DEFAULT_ROUND_TAIL_SILENCE_S,
    ramp_ms: float = DEFAULT_RAMP_MS,
) -> TransportAudio:
    """Закодировать пакеты ART/1 в отсчёты с паузами между кадрами."""
    _validate_profile(profile)
    gap = _validate_seconds(inter_frame_gap_s, "inter_frame_gap_s")
    lead = _validate_seconds(lead_silence_s, "lead_silence_s")
    tail = _validate_seconds(tail_silence_s, "tail_silence_s")
    items = tuple(packets)
    if not items:
        raise ReliableChatError("раунд не содержит пакетов")

    frames: list[Frame] = []
    for packet in items:
        if not isinstance(packet, TransportPacket):
            raise ReliableChatError("ожидается TransportPacket")
        raw = packet.to_bytes()
        if len(raw) > MAX_FRAME_PAYLOAD_BYTES:
            raise ReliableChatError(
                f"пакет {len(raw)} байт превышает payload Frame v1"
            )
        frames.append(build_frame(raw, max_payload_bytes=MAX_FRAME_PAYLOAD_BYTES))

    samples_per_symbol = profile.samples_per_symbol
    gap_samples = int(round(gap * profile.sample_rate))
    lead_samples = int(round(lead * profile.sample_rate))
    tail_samples = int(round(tail * profile.sample_rate))
    frame_samples = [frame.total_bits * samples_per_symbol for frame in frames]
    total_samples = (
        lead_samples
        + tail_samples
        + sum(frame_samples)
        + gap_samples * max(0, len(frames) - 1)
    )
    out = np.zeros(total_samples, dtype=np.float64)
    cursor = lead_samples
    for index, frame in enumerate(frames):
        modulation = modulate(frame.bits, profile, ramp_ms=ramp_ms)
        out[cursor : cursor + modulation.samples.size] = modulation.samples
        cursor += modulation.samples.size
        if index + 1 < len(frames):
            cursor += gap_samples

    return TransportAudio(
        samples=out,
        sample_rate=profile.sample_rate,
        packets=items,
        lead_silence_s=lead,
        tail_silence_s=tail,
        inter_frame_gap_s=gap,
        profile_name=profile.name,
    )


def encode_transport_round(
    outbound: OutboundRound,
    profile: BfskProfile,
    *,
    lead_silence_s: float = DEFAULT_ROUND_LEAD_SILENCE_S,
    tail_silence_s: float = DEFAULT_ROUND_TAIL_SILENCE_S,
) -> TransportAudio:
    """Закодировать весь раунд отправителя в один массив отсчётов."""
    if not isinstance(outbound, OutboundRound):
        raise ReliableChatError("ожидается OutboundRound")
    return encode_transport_packets(
        outbound.packets,
        profile,
        lead_silence_s=lead_silence_s,
        tail_silence_s=tail_silence_s,
    )


def encode_transport_reply(
    packet: TransportPacket,
    profile: BfskProfile,
    *,
    lead_silence_s: float = DEFAULT_REPLY_LEAD_SILENCE_S,
    tail_silence_s: float = DEFAULT_REPLY_TAIL_SILENCE_S,
) -> TransportAudio:
    """Закодировать одиночный ``STATUS`` или ``ABORT`` приёмника."""
    if packet.packet_type not in (PACKET_STATUS, PACKET_ABORT):
        raise ReliableChatError("ответом может быть только STATUS или ABORT")
    return encode_transport_packets(
        (packet,),
        profile,
        lead_silence_s=lead_silence_s,
        tail_silence_s=tail_silence_s,
    )


@dataclass(frozen=True, slots=True)
class SendAction:
    """Очередной шаг контроллера отправителя."""

    kind: str
    outbound: OutboundRound | None = None
    progress: SenderProgress | None = None
    at_millis: int = 0

    @property
    def is_final(self) -> bool:
        return self.kind in (ACTION_DONE, ACTION_FAILED)

    @property
    def error_type(self) -> str | None:
        return self.progress.error_type if self.progress is not None else None

    @property
    def message(self) -> str:
        return self.progress.message if self.progress is not None else ""


class ReliableSendController:
    """Полудуплексный цикл отправки ART/1 для GUI/CLI.

    Вызывающая сторона:

    1. ``start()`` — получить первый раунд;
    2. закодировать ``action.outbound`` и воспроизвести, приостановив микрофон;
    3. передавать в ``handle_packet()`` каждый принятый ``STATUS``/``ABORT``;
    4. периодически звать ``tick()``: он выдаст следующий раунд после
       ``STATUS``/таймаута или завершит передачу.

    Время — монотонные миллисекунды. Кодирование звука намеренно вынесено
    наружу: UI может делать его в фоновом потоке.
    """

    def __init__(
        self,
        data: bytes,
        transfer_id: int,
        *,
        config: TransportConfig | None = None,
        filename: str | None = None,
        session_id: int | None = None,
        clock_millis: Callable[[], int] | None = None,
    ) -> None:
        self._clock = clock_millis or (lambda: int(time.monotonic() * 1000))
        self.sender = ReliableSender(
            bytes(data),
            transfer_id,
            config=config or TransportConfig(),
            session_id=session_id,
            filename=filename,
        )
        self._started = False

    @property
    def session_id(self) -> int:
        return self.sender.session_id

    @property
    def transfer_id(self) -> int:
        return self.sender.transfer_id

    @property
    def state(self) -> SenderState:
        return self.sender.state

    @property
    def data_size(self) -> int:
        return self.sender.total_size

    @property
    def total_chunks(self) -> int:
        return self.sender.total_chunks

    def start(self) -> SendAction:
        """Начать передачу и вернуть первый раунд."""
        if self._started:
            raise ReliableChatError("передача уже начата")
        self._started = True
        return self._action(self.sender.advance(self._clock()))

    def tick(self) -> SendAction:
        """Продвинуть таймеры: следующий раунд, ожидание или завершение."""
        if not self._started:
            raise ReliableChatError("передача ещё не начата")
        return self._action(self.sender.advance(self._clock()))

    def handle_packet(self, packet: TransportPacket) -> SendAction:
        """Передать отправителю принятый ``STATUS`` или ``ABORT``."""
        if not isinstance(packet, TransportPacket):
            raise ReliableChatError("ожидается TransportPacket")
        if packet.packet_type not in (PACKET_STATUS, PACKET_ABORT):
            return SendAction(kind=ACTION_IGNORED, at_millis=self._clock())
        progress = self.sender.handle_packet(packet, self._clock())
        return self._action(progress)

    def stop(self) -> None:
        """Прекратить передачу без отправки ABORT (пользователь отменил)."""
        self.sender.abort_packet()

    def _action(self, progress: SenderProgress) -> SendAction:
        if progress.round is not None:
            kind = ACTION_ROUND
        elif progress.state is SenderState.DONE:
            kind = ACTION_DONE
        elif progress.state is SenderState.FAILED:
            kind = ACTION_FAILED
        else:
            kind = ACTION_WAITING
        return SendAction(
            kind=kind,
            outbound=progress.round,
            progress=progress,
            at_millis=self._clock(),
        )


def _validate_profile(profile: BfskProfile) -> None:
    if not isinstance(profile, BfskProfile):
        raise ReliableChatError("профиль должен быть BfskProfile")


def _validate_seconds(value: float, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ReliableChatError(f"{field_name} должен быть числом")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= MAX_SILENCE_S:
        raise ReliableChatError(
            f"{field_name} должен быть в диапазоне 0..{MAX_SILENCE_S:g} с"
        )
    return number


__all__ = [
    "ACTION_DONE",
    "ACTION_FAILED",
    "ACTION_IDLE",
    "ACTION_IGNORED",
    "ACTION_ROUND",
    "ACTION_WAITING",
    "DEFAULT_REPLY_LEAD_SILENCE_S",
    "DEFAULT_REPLY_TAIL_SILENCE_S",
    "DEFAULT_ROUND_LEAD_SILENCE_S",
    "DEFAULT_ROUND_TAIL_SILENCE_S",
    "NotTransportPayloadError",
    "ReliableChatError",
    "ReliableSendController",
    "SendAction",
    "TransportAudio",
    "config_for_profile",
    "encode_transport_packets",
    "encode_transport_reply",
    "encode_transport_round",
    "is_transport_payload",
    "parse_transport_payload",
]
