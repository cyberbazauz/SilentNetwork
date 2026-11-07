"""Детерминированный симулятор полудуплексного канала для ART/1.

Симулятор не выполняет DSP и не открывает аудиоустройства. Он проверяет
логику раундов, потерь, дубликатов и таймеров на уровне пакетов:

* потеря кадра — пакет не доставляется;
* дубликат — пакет доставляется дважды;
* потеря ``STATUS`` — отправитель не получает подтверждение и повторяет набор.

Успешный прогон доказывает только логику протокола, а не передачу через
воздух.
"""

from __future__ import annotations

import random
from typing import Any

from snlab.reliable import (
    PACKET_DATA,
    PACKET_MANIFEST,
    PACKET_META,
    PACKET_STATUS,
    ReliableReceiver,
    ReliableSender,
    SenderState,
    TransportConfig,
    TransportPacket,
)

REPORT_SCHEMA = "silentnetwork.acoustic-lab.reliable-sim/1"


class SimulationError(ValueError):
    """Параметры симуляции некорректны или превышен лимит времени."""


def simulate_transfer(
    data: bytes,
    *,
    transfer_id: int = 7,
    config: TransportConfig | None = None,
    session_id: int = 0x1234,
    filename: str | None = None,
    seed: int = 1,
    data_loss: float = 0.0,
    status_loss: float = 0.0,
    duplicate_prob: float = 0.0,
    max_elapsed_ms: int = 3_600_000,
) -> dict[str, Any]:
    """Прогнать одну передачу через модельный канал.

    Args:
        data: сообщение.
        transfer_id: 0..255.
        config: тайминги и лимиты; по умолчанию ``TransportConfig()``.
        session_id: фиксированный идентификатор для воспроизводимости.
        filename: необязательное имя файла для META.
        seed: seed генератора потерь.
        data_loss: вероятность потерять любой кадр отправителя, 0..1.
        status_loss: вероятность потерять STATUS, 0..1.
        duplicate_prob: вероятность доставить кадр дважды, 0..1.
        max_elapsed_ms: предохранитель от зацикливания.

    Returns:
        JSON-совместимый отчёт.
    """
    config = config or TransportConfig()
    _validate_probability(data_loss, "data_loss")
    _validate_probability(status_loss, "status_loss")
    _validate_probability(duplicate_prob, "duplicate_prob")
    if not isinstance(max_elapsed_ms, int) or max_elapsed_ms <= 0:
        raise SimulationError("max_elapsed_ms должен быть положительным целым")

    rng = random.Random(seed)
    sender = ReliableSender(
        bytes(data),
        transfer_id,
        config=config,
        session_id=session_id,
        filename=filename,
    )
    receiver = ReliableReceiver(config)

    now_ms = 0
    finish_ms: int | None = None
    rounds: list[dict[str, Any]] = []
    status_frames_sent = 0
    status_frames_lost = 0
    data_frames_lost = 0
    data_frames_duplicated = 0
    receiver_delivered: bytes | None = None
    receiver_completed = False
    receiver_abort: dict[str, Any] | None = None
    receiver_error: str | None = None
    last_error: str | None = None

    def deliver_to_receiver(packet: TransportPacket, at_ms: int) -> None:
        nonlocal receiver_delivered, receiver_completed
        nonlocal receiver_abort, receiver_error
        progress = receiver.handle_packet(packet, at_ms)
        if progress.abort is not None:
            receiver_abort = progress.abort.as_dict()
            receiver_error = progress.error_type
        if progress.event == "completed":
            receiver_completed = True
            receiver_delivered = progress.delivered

    while now_ms <= max_elapsed_ms:
        progress = sender.advance(now_ms)
        if progress.state is SenderState.FAILED:
            last_error = progress.error_type
        if progress.round is not None:
            current = progress.round
            rounds.append(current.as_dict())
            cursor = current.start_ms
            for packet in current.packets:
                duration = config.frame_duration_ms(len(packet.to_bytes()))
                arrives_at = cursor + duration
                lost = rng.random() < data_loss
                if lost:
                    if packet.packet_type == PACKET_DATA:
                        data_frames_lost += 1
                else:
                    deliver_to_receiver(packet, arrives_at)
                    if rng.random() < duplicate_prob:
                        deliver_to_receiver(packet, arrives_at)
                        if packet.packet_type == PACKET_DATA:
                            data_frames_duplicated += 1
                cursor = arrives_at + config.inter_frame_gap_ms

            status_due = current.round_end_ms + config.ack_guard_ms
            status = receiver.poll(status_due)
            if status is not None:
                status_frames_sent += 1
                status_duration = config.frame_duration_ms(
                    len(status.to_bytes())
                )
                status_arrives = status_due + status_duration
                if rng.random() < status_loss:
                    status_frames_lost += 1
                else:
                    status_progress = sender.handle_packet(status, status_arrives)
                    if status_progress.state is SenderState.FAILED:
                        last_error = status_progress.error_type
                    if sender.state is SenderState.DONE:
                        finish_ms = status_arrives
            now_ms = current.listen_until_ms
            if sender.state in (SenderState.DONE, SenderState.FAILED):
                break
            continue

        if sender.state in (SenderState.DONE, SenderState.FAILED):
            break
        current = sender.current_round
        if current is not None:
            now_ms = current.listen_until_ms
        else:
            # Таймаут очистил раунд и назначил turnaround: следующий advance
            # должен выдать новый раунд, а не считать это ошибкой.
            now_ms += config.turnaround_ms

    if finish_ms is None:
        finish_ms = now_ms
    if now_ms > max_elapsed_ms:
        raise SimulationError(
            f"симуляция превысила {max_elapsed_ms} мс без завершения"
        )

    completed = (
        sender.state is SenderState.DONE
        and receiver.complete
        and receiver_delivered == bytes(data)
    )
    result = "ok" if completed else "error"
    error_type = None
    if not completed:
        if sender.state is SenderState.FAILED:
            error_type = last_error or "simulation_failed"
        elif receiver_error is not None:
            error_type = receiver_error
        else:
            error_type = "not_completed"

    return {
        "schema": REPORT_SCHEMA,
        "result": result,
        "error_type": error_type,
        "seed": seed,
        "channel": {
            "data_loss": data_loss,
            "status_loss": status_loss,
            "duplicate_prob": duplicate_prob,
        },
        "message": {
            "bytes": len(data),
            "chunks": sender.total_chunks,
            "sha256": sender.manifest.sha256.hex(),
            "filename": filename,
        },
        "config": {
            "window": config.window,
            "symbol_rate": config.symbol_rate,
            "inter_frame_gap_ms": config.inter_frame_gap_ms,
            "ack_guard_ms": config.ack_guard_ms,
            "ack_margin_ms": config.ack_margin_ms,
            "max_rounds": config.max_rounds,
            "max_stalled_rounds": config.max_stalled_rounds,
        },
        "sender": {
            "state": sender.state.value,
            "rounds_sent": sender.rounds_sent,
            "data_frames_sent": sender.data_frames_sent,
            "data_frames_retransmitted": sender.retransmitted_frames,
            "statuses_received": sender.statuses_received,
            "missing": list(sender.missing),
        },
        "receiver": {
            "complete": receiver.complete,
            "received_chunks": receiver.received_count,
            "total_chunks": sender.total_chunks,
            "duplicates": receiver.duplicates,
            "delivered_bytes": (
                len(receiver_delivered)
                if receiver_delivered is not None
                else None
            ),
            "sha256_ok": receiver_delivered == bytes(data),
            "abort": receiver_abort,
            "error_type": receiver_error,
        },
        "channel_stats": {
            "data_frames_lost": data_frames_lost,
            "data_frames_duplicated": data_frames_duplicated,
            "status_frames_sent": status_frames_sent,
            "status_frames_lost": status_frames_lost,
        },
        "elapsed_ms": finish_ms,
        "elapsed_s": round(finish_ms / 1000.0, 3),
        "rounds": rounds,
        "crc_is_authentication": False,
        "sha256_is_authentication": False,
        "air_test": False,
    }


def _validate_probability(value: float, name: str) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0.0 <= float(value) <= 1.0
    ):
        raise SimulationError(f"{name} должен быть в диапазоне 0..1")


__all__ = ["REPORT_SCHEMA", "SimulationError", "simulate_transfer"]
