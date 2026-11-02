"""Acoustic Data Link v0: UTF-8 текст → Frame v1 → BFSK → WAV и обратно.

Модуль связывает уже проверенные части Acoustic Lab и не меняет ни Frame v1,
ни CRC-16, ни BFSK-модуляцию. Он добавляет только предметный уровень:
сообщение, его байты, отчёт и человекочитаемую причину отказа.

Границы:

* payload — UTF-8 текст; для v0 не более ``MAX_DATA_PAYLOAD_BYTES`` байт;
* Frame v1 и CRC-16/CCITT-FALSE остаются без изменений;
* CRC обнаруживает случайные ошибки, но **не аутентифицирует** отправителя;
* приём файла или WAV не доказывает передачу через воздух.

Контракт описан в ``docs/acoustic-data-link.md``.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np

from snlab.audio import clipping_stats
from snlab.bfsk import DEFAULT_RAMP_MS, modulate
from snlab.config import BfskProfile, MAX_PAYLOAD_BYTES
from snlab.frame import PREAMBLE_BITS, Frame, build_frame
from snlab.receiver import (
    CrcError,
    FrameLengthError,
    FrameVersionError,
    IncompleteFrame,
    ReceiverError,
    SignalNotFound,
    SyncWordError,
    decode_samples,
    validate_min_preamble_matches,
)
from snlab.wavio import WavFormatError, read_pcm16, write_pcm16

REPORT_SCHEMA = "silentnetwork.acoustic-lab.data-link/1"

#: Лимит payload для v0. Полный лимит Frame v1 (512 байт) не используется.
MAX_DATA_PAYLOAD_BYTES = 64

#: Порог преамбулы для реального акустического канала (28/32).
ACOUSTIC_MIN_PREAMBLE_MATCHES = 28

DEFAULT_LEAD_SILENCE_S = 1.0
DEFAULT_TAIL_SILENCE_S = 0.5
MAX_SILENCE_S = 10.0


class DataLinkError(ValueError):
    """Сообщение нельзя превратить в кадр или кадр — в сообщение."""

    def __init__(self, message: str, *, error_type: str = "data_link_error") -> None:
        super().__init__(message)
        self.error_type = error_type


class DataLinkPayloadError(DataLinkError):
    """Payload пуст, слишком велик или не является текстом UTF-8."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_type="payload_invalid")


class DataLinkProfileError(DataLinkError):
    """Профиль не является допустимым профилем передачи данных."""

    def __init__(self, message: str) -> None:
        super().__init__(message, error_type="profile_invalid")


def text_to_payload(text: str, *, max_bytes: int = MAX_DATA_PAYLOAD_BYTES) -> bytes:
    """Преобразовать текст в UTF-8 payload и проверить лимит.

    Пустая строка запрещена: Frame v1 не поддерживает пустой payload.

    Raises:
        DataLinkPayloadError: текст пуст, не является строкой или превышает
            лимит в байтах UTF-8.
    """
    _validate_max_bytes(max_bytes)
    if not isinstance(text, str):
        raise DataLinkPayloadError("сообщение должно быть строкой")
    if text == "":
        raise DataLinkPayloadError("сообщение пустое: передавать нечего")

    payload = text.encode("utf-8")
    if not payload:  # pragma: no cover - пустая строка уже отсечена
        raise DataLinkPayloadError("сообщение пустое: передавать нечего")
    if len(payload) > max_bytes:
        raise DataLinkPayloadError(
            f"сообщение занимает {len(payload)} байт UTF-8, "
            f"лимит {max_bytes} байт"
        )
    return payload


def payload_to_text(
    payload: bytes, *, max_bytes: int = MAX_DATA_PAYLOAD_BYTES
) -> str:
    """Декодировать payload как UTF-8 и проверить лимит.

    Raises:
        DataLinkPayloadError: payload пуст, не bytes, превышает лимит или
            не является корректным UTF-8.
    """
    _validate_max_bytes(max_bytes)
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise DataLinkPayloadError("payload должен быть bytes")
    data = bytes(payload)
    if not data:
        raise DataLinkPayloadError("payload пуст")
    if len(data) > max_bytes:
        raise DataLinkPayloadError(
            f"payload {len(data)} байт превышает лимит {max_bytes} байт"
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DataLinkPayloadError(
            f"payload не является корректным UTF-8: {exc.reason} "
            f"на позиции {exc.start}"
        ) from exc


def encode_text_to_wav(
    text: str,
    profile: BfskProfile,
    out_path: str | Path,
    *,
    max_bytes: int = MAX_DATA_PAYLOAD_BYTES,
    lead_silence_s: float = DEFAULT_LEAD_SILENCE_S,
    tail_silence_s: float = DEFAULT_TAIL_SILENCE_S,
    ramp_ms: float = DEFAULT_RAMP_MS,
) -> dict[str, Any]:
    """Собрать кадр из текста, смодулировать и записать PCM16 WAV.

    Перед кадром и после него добавляется тишина: она даёт время нажать
    запись на телефоне и не обрезает хвост кадра. SHA-256 считается по PCM
    без заголовка WAV, как в ``Modulation.pcm_sha256``.

    Returns:
        JSON-совместимый отчёт ``silentnetwork.acoustic-lab.data-link/1``.

    Raises:
        DataLinkPayloadError: текст пуст или превышает лимит.
        DataLinkProfileError: профиль не является ``BfskProfile``.
        DataLinkError: неверная длительность тишины или ramp.
    """
    _validate_profile(profile)
    lead = _validate_silence(lead_silence_s, "lead_silence_s")
    tail = _validate_silence(tail_silence_s, "tail_silence_s")
    payload = text_to_payload(text, max_bytes=max_bytes)
    frame = build_frame(payload, max_payload_bytes=max_bytes)

    modulation = modulate(frame.bits, profile, ramp_ms=ramp_ms)
    lead_samples = int(round(lead * profile.sample_rate))
    tail_samples = int(round(tail * profile.sample_rate))
    samples = np.concatenate(
        [
            np.zeros(lead_samples, dtype=np.float64),
            modulation.samples,
            np.zeros(tail_samples, dtype=np.float64),
        ]
    )

    target = write_pcm16(out_path, samples, profile.sample_rate)
    return {
        "schema": REPORT_SCHEMA,
        "direction": "tx",
        "result": "ok",
        "error_type": None,
        "message": "WAV создан; воспроизведение ещё не является приёмом",
        "profile": profile.as_dict(),
        "text": text,
        "payload_hex": payload.hex(),
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "frame": frame.as_dict(),
        "signal": {
            "scheme": "continuous-phase BFSK",
            "modulated_samples": modulation.sample_count,
            "modulated_duration_s": modulation.duration_s,
            "lead_silence_s": lead,
            "tail_silence_s": tail,
            "total_samples": int(samples.size),
            "total_duration_s": float(samples.size / profile.sample_rate),
            "ramp_ms": modulation.ramp_ms,
            "peak_amplitude": float(np.max(np.abs(samples))) if samples.size else 0.0,
            "pcm_sha256": hashlib.sha256(
                np.round(np.clip(samples, -1.0, 1.0) * 32767.0)
                .astype("<i2")
                .tobytes()
            ).hexdigest(),
        },
        "wav_path": str(target),
        "crc_is_authentication": False,
    }


def decode_wav_to_text(
    path: str | Path,
    profile: BfskProfile,
    *,
    min_preamble_matches: int = PREAMBLE_BITS,
    max_bytes: int = MAX_DATA_PAYLOAD_BYTES,
) -> dict[str, Any]:
    """Прочитать WAV, восстановить кадр и декодировать UTF-8 текст.

    Ошибка не выбрасывается наружу как исключение: вместо неё возвращается
    отчёт с ``result="error"`` и машинным ``error_type``. Так вызывающая
    сторона (Windows UI, JSON, Android) показывает причину, а не «успех».

    Args:
        path: входной WAV формата Acoustic Lab.
        profile: профиль, которым передавался кадр.
        min_preamble_matches: порог преамбулы. 32/32 — строгий файловый
            режим, 28/32 — реальный акустический канал.
        max_bytes: лимит payload v0.

    Returns:
        JSON-совместимый отчёт ``silentnetwork.acoustic-lab.data-link/1``.
    """
    _validate_profile(profile)
    _validate_max_bytes(max_bytes)
    validate_min_preamble_matches(min_preamble_matches)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "direction": "rx",
        "profile": profile.as_dict(),
        "min_preamble_matches": min_preamble_matches,
        "max_payload_bytes": max_bytes,
        "wav_path": str(path),
        "crc_is_authentication": False,
    }

    try:
        samples, sample_rate = read_pcm16(
            path, expected_sample_rate=profile.sample_rate
        )
    except FileNotFoundError:
        return _failure(report, "wav_not_found", f"WAV не найден: {path}")
    except WavFormatError as exc:
        return _failure(report, "wav_format_error", str(exc))

    report["recording"] = _recording_metrics(samples, sample_rate)

    try:
        decoded = decode_samples(
            samples,
            profile,
            max_payload_bytes=max_bytes,
            min_preamble_matches=min_preamble_matches,
        )
    except (SignalNotFound, SyncWordError, FrameVersionError, FrameLengthError,
            IncompleteFrame, CrcError, ReceiverError) as exc:
        return _failure(report, _receiver_error_type(exc), str(exc))

    report.update(
        {
            "result": "ok",
            "error_type": None,
            "message": "Сообщение принято, CRC верный",
            "crc_ok": True,
            "frame": decoded.as_dict(),
            "payload_hex": decoded.payload.hex(),
            "payload_bytes": len(decoded.payload),
            "payload_sha256": hashlib.sha256(decoded.payload).hexdigest(),
            "start_sample": decoded.start_sample,
            "start_time_s": decoded.start_time_s,
            "preamble_matches": decoded.preamble_matches,
        }
    )

    try:
        report["text"] = payload_to_text(decoded.payload, max_bytes=max_bytes)
    except DataLinkPayloadError as exc:
        report["result"] = "error"
        report["error_type"] = "payload_utf8_error"
        report["message"] = str(exc)
        report["text"] = None

    return report


def decode_report_ok(report: dict[str, Any]) -> bool:
    """Успешен ли отчёт: кадр найден, CRC верный и текст декодирован."""
    return report.get("result") == "ok" and report.get("text") is not None


def _failure(report: dict[str, Any], error_type: str, message: str) -> dict[str, Any]:
    report.update(
        {
            "result": "error",
            "error_type": error_type,
            "message": message,
            "crc_ok": False,
            "text": None,
        }
    )
    return report


def _receiver_error_type(exc: ReceiverError) -> str:
    if isinstance(exc, SignalNotFound):
        return "signal_not_found"
    if isinstance(exc, SyncWordError):
        return "sync_mismatch"
    if isinstance(exc, FrameVersionError):
        return "version_unsupported"
    if isinstance(exc, FrameLengthError):
        return "length_invalid"
    if isinstance(exc, IncompleteFrame):
        return "incomplete_frame"
    if isinstance(exc, CrcError):
        return "crc_mismatch"
    return "receiver_error"


def _recording_metrics(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    data = np.asarray(samples, dtype=np.float64)
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
    peak = float(np.max(np.abs(data)))
    return {
        "sample_rate_hz": sample_rate,
        "samples": int(data.size),
        "duration_s": float(data.size / sample_rate),
        "rms_dbfs": _dbfs(rms),
        "peak_dbfs": _dbfs(peak),
        "clipping": clipping_stats(data),
    }


def _dbfs(value: float) -> float:
    if value <= 0.0:
        return -999.0
    return float(20.0 * math.log10(value))


def _validate_profile(profile: BfskProfile) -> None:
    if not isinstance(profile, BfskProfile):
        raise DataLinkProfileError("профиль должен быть BfskProfile")


def _validate_max_bytes(max_bytes: int) -> None:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise DataLinkPayloadError("max_bytes должен быть целым числом")
    if not 1 <= max_bytes <= MAX_PAYLOAD_BYTES:
        raise DataLinkPayloadError(
            f"max_bytes должен быть в диапазоне 1..{MAX_PAYLOAD_BYTES}"
        )


def _validate_silence(value: float, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DataLinkError(f"{field_name} должен быть числом")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= MAX_SILENCE_S:
        raise DataLinkError(
            f"{field_name} должен быть в диапазоне 0..{MAX_SILENCE_S:g} с"
        )
    return number


__all__ = [
    "ACOUSTIC_MIN_PREAMBLE_MATCHES",
    "DATA_LINK_ERROR_TYPES",
    "DataLinkError",
    "DataLinkPayloadError",
    "DataLinkProfileError",
    "MAX_DATA_PAYLOAD_BYTES",
    "REPORT_SCHEMA",
    "decode_report_ok",
    "decode_wav_to_text",
    "encode_text_to_wav",
    "payload_to_text",
    "text_to_payload",
]

#: Машинные типы ошибок, которые может вернуть ``decode_wav_to_text``.
DATA_LINK_ERROR_TYPES = (
    "signal_not_found",
    "sync_mismatch",
    "version_unsupported",
    "length_invalid",
    "incomplete_frame",
    "crc_mismatch",
    "wav_not_found",
    "wav_format_error",
    "payload_utf8_error",
    "receiver_error",
)
