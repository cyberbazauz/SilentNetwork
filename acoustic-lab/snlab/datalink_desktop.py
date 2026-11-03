"""Windows Desktop: отправка и приём текста через звук (SN-025).

Модуль не содержит Tkinter. Он связывает `snlab.datalink` с устройствами,
воспроизведением и отчётами Windows Desktop Lab:

* создать WAV с текстом и сохранить JSON;
* воспроизвести его через выбранный динамик и записать фактический PLAY;
* записать микрофон, декодировать кадр и сохранить WAV + JSON.

Успешное декодирование файла не доказывает передачу через воздух. CRC-16
обнаруживает случайные ошибки, но не аутентифицирует отправителя.
"""

from __future__ import annotations

import hashlib
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from snlab import audio, playback
from snlab.config import DATA_PROFILES, BfskProfile
from snlab import datalink
from snlab.reporting import environment, utc_now, write_json
from snlab.wavio import read_wav, write_pcm16

TOOL_NAME = "desktop_app.py"
TASK_ID = "SN-025"

PLAYBACK_SCHEMA = "silentnetwork.acoustic-lab.data-link-playback/1"
RECEIVE_SCHEMA = "silentnetwork.acoustic-lab.data-link-receive/1"

DEFAULT_LEAD_SILENCE_S = 1.0
DEFAULT_TAIL_SILENCE_S = 0.5
DEFAULT_RECORD_SECONDS = 12.0
MIN_RECORD_SECONDS = 1.0
MAX_RECORD_SECONDS = 30.0

DATA_LIMITATIONS = [
    "WAV и JSON сами по себе не доказывают передачу через воздух.",
    "CRC-16 обнаруживает ошибки, но не аутентифицирует отправителя; секреты не передавать.",
    "На этом этапе нет SCNP, ACK, повторов и гарантии доставки.",
    "Системная громкость не изменяется; запись начинается только по кнопке.",
]

#: Имена профилей, допустимых в data link v0 (для UI и валидации).
DATA_PROFILE_NAMES = tuple(sorted(DATA_PROFILES))


class DataDesktopError(ValueError):
    """Некорректное состояние Windows data link."""


class DataPlaybackDisabled(DataDesktopError):
    """PLAY данных запрещён без явного разрешения."""


@dataclass(frozen=True, slots=True)
class GeneratedData:
    """Созданный WAV с текстом; воспроизведения ещё не было."""

    text: str
    profile: BfskProfile
    wav_path: Path
    metadata_path: Path
    sha256: str
    report: dict[str, Any]
    frames: int
    file_duration_s: float
    generated_at_utc: str


@dataclass(frozen=True, slots=True)
class DataPlaybackRecord:
    """Фактический результат PLAY WAV с данными."""

    generated: GeneratedData
    device: audio.DeviceInfo
    status: str
    performed: bool
    frames_written: int
    frames_total: int
    elapsed_s: float
    error: str | None
    started_at_utc: str
    finished_at_utc: str
    json_path: Path


@dataclass(frozen=True, slots=True)
class ReceivedData:
    """Запись микрофона и результат декодирования."""

    source: str
    profile: BfskProfile
    wav_path: Path
    json_path: Path
    report: dict[str, Any]
    recording: audio.Recording
    received_at_utc: str

    @property
    def ok(self) -> bool:
        return datalink.decode_report_ok(self.report)

    @property
    def text(self) -> str | None:
        return self.report.get("text")

    @property
    def error_type(self) -> str | None:
        return self.report.get("error_type")

    @property
    def message(self) -> str:
        return str(self.report.get("message") or "")


def list_input_devices() -> list[audio.DeviceInfo]:
    """Перечислить микрофоны, не открывая аудиопоток."""
    return [device for device in audio.list_devices() if device.is_input]


def get_data_profile(name: str) -> BfskProfile:
    """Вернуть только профиль, разрешённый для data link v0."""
    if not isinstance(name, str):
        raise DataDesktopError("имя профиля должно быть строкой")
    try:
        return DATA_PROFILES[name]
    except KeyError:
        available = ", ".join(sorted(DATA_PROFILES))
        raise DataDesktopError(
            f"профиль {name!r} не предназначен для передачи данных; "
            f"доступны: {available}"
        ) from None


def generate_data_message(
    text: str,
    profile: BfskProfile | str,
    results_dir: str | Path | None = None,
    *,
    lead_silence_s: float = DEFAULT_LEAD_SILENCE_S,
    tail_silence_s: float = DEFAULT_TAIL_SILENCE_S,
    timestamp: str | None = None,
) -> GeneratedData:
    """Создать WAV с Frame v1 и JSON, ничего не воспроизводя."""
    selected = _coerce_profile(profile)
    target_dir = Path(results_dir) if results_dir is not None else default_results_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    stem = _unique_stem(target_dir, f"data-tx-{selected.name}-{stamp}")
    wav_path = target_dir / f"{stem}.wav"
    metadata_path = target_dir / f"{stem}.json"
    generated_at = utc_now()

    report = datalink.encode_text_to_wav(
        text,
        selected,
        wav_path,
        lead_silence_s=lead_silence_s,
        tail_silence_s=tail_silence_s,
    )
    digest = _sha256_file(wav_path)
    data, sample_rate, channels = read_wav(wav_path)
    if channels != 1 or sample_rate != selected.sample_rate:
        raise DataDesktopError(
            "созданный WAV не соответствует профилю данных"
        )

    metadata = {
        **report,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": generated_at,
        "environment": environment(),
        "file": {
            "wav": str(wav_path),
            "json": str(metadata_path),
            "sha256": digest,
            "format": "pcm_s16le",
            "sample_rate_hz": sample_rate,
            "channels": channels,
            "frames": int(data.shape[0]),
            "bytes": wav_path.stat().st_size,
        },
        "warnings": [
            "Сообщение передаётся открытым текстом: CRC не является защитой.",
            "Воспроизведение выполняется только по кнопке и после подтверждения.",
        ],
        "limitations": DATA_LIMITATIONS,
    }
    write_json(metadata_path, metadata)
    return GeneratedData(
        text=text,
        profile=selected,
        wav_path=wav_path,
        metadata_path=metadata_path,
        sha256=digest,
        report=report,
        frames=int(data.shape[0]),
        file_duration_s=float(data.shape[0] / sample_rate),
        generated_at_utc=generated_at,
    )


def play_data_message(
    generated: GeneratedData,
    device: audio.DeviceInfo,
    *,
    engine: playback.PlaybackEngine,
    hardware_test: bool,
    timestamp: str | None = None,
) -> DataPlaybackRecord:
    """Воспроизвести WAV с данными и записать фактический результат PLAY.

    JSON создаётся после возврата движка, включая ошибку драйвера. Без
    `hardware_test=True` функция отказывает до открытия потока.
    """
    if not hardware_test:
        raise DataPlaybackDisabled(
            "PLAY данных запрещён: приложение запущено в FILE-ONLY."
        )
    if not isinstance(generated, GeneratedData):
        raise DataDesktopError("ожидается GeneratedData")
    if not isinstance(device, audio.DeviceInfo):
        raise DataDesktopError("device должен быть audio.DeviceInfo")
    if not device.is_output:
        raise DataDesktopError(f"устройство [{device.index}] не является выходным")
    if not generated.wav_path.is_file():
        raise DataDesktopError(f"WAV не найден: {generated.wav_path}")

    data, sample_rate, channels = read_wav(generated.wav_path)
    if channels != 1:
        raise DataDesktopError(f"ожидается mono WAV, в файле {channels} каналов")
    if sample_rate != generated.profile.sample_rate:
        raise DataDesktopError(
            f"частота WAV {sample_rate} Гц не совпадает с профилем "
            f"{generated.profile.sample_rate} Гц"
        )
    digest = _sha256_file(generated.wav_path)
    if digest != generated.sha256:
        raise DataDesktopError(
            "WAV изменился после генерации: SHA-256 не совпадает, PLAY остановлен"
        )

    samples = np.asarray(data[:, 0], dtype=np.float64)
    started_at = utc_now()
    started_monotonic = time.monotonic()
    try:
        outcome = engine.play(samples, sample_rate, device)
    except Exception as exc:  # noqa: BLE001 - ошибка драйвера должна попасть в JSON
        outcome = playback.PlaybackOutcome(
            status="failed",
            frames_written=0,
            total_frames=int(samples.size),
            elapsed_s=time.monotonic() - started_monotonic,
            error=f"{type(exc).__name__}: {exc}",
        )
    finished_at = utc_now()

    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    json_path = generated.wav_path.with_name(
        f"{generated.wav_path.stem}.playback-{stamp}.json"
    )
    payload = {
        "schema": PLAYBACK_SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": finished_at,
        "environment": environment(),
        "mode": "hardware_test",
        "direction": "tx",
        "profile": generated.profile.as_dict(),
        "message": {
            "text": generated.text,
            "payload_hex": generated.report["payload_hex"],
            "payload_sha256": generated.report["payload_sha256"],
            "crc16": generated.report["frame"]["crc16"],
        },
        "device": device.as_dict(),
        "file": {
            "wav": str(generated.wav_path),
            "sha256": generated.sha256,
        },
        "playback": {
            **outcome.as_dict(),
            "engine": type(engine).__name__,
            "started_at_utc": started_at,
            "finished_at_utc": finished_at,
        },
        "crc_is_authentication": False,
        "warnings": [
            "performed=true означает полное воспроизведение WAV, а не успешный приём.",
            "Сообщение не зашифровано и не аутентифицировано.",
        ],
        "limitations": DATA_LIMITATIONS,
    }
    write_json(json_path, payload)

    return DataPlaybackRecord(
        generated=generated,
        device=device,
        status=outcome.status,
        performed=outcome.performed,
        frames_written=outcome.frames_written,
        frames_total=outcome.total_frames,
        elapsed_s=outcome.elapsed_s,
        error=outcome.error,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        json_path=json_path,
    )


def receive_data(
    profile: BfskProfile | str,
    device: audio.DeviceInfo,
    duration_s: float = DEFAULT_RECORD_SECONDS,
    results_dir: str | Path | None = None,
    *,
    recorder: Callable[..., audio.Recording] | None = None,
    min_preamble_matches: int = datalink.ACOUSTIC_MIN_PREAMBLE_MATCHES,
    max_bytes: int = datalink.MAX_DATA_PAYLOAD_BYTES,
    timestamp: str | None = None,
) -> ReceivedData:
    """Записать микрофон, сохранить WAV и декодировать сообщение.

    Args:
        profile: профиль `data-audible` или `data-robust`.
        device: входное устройство.
        duration_s: длительность записи, 1..30 с.
        results_dir: каталог результатов.
        recorder: функция записи для тестов; по умолчанию `audio.record_ambient`.
        min_preamble_matches: порог преамбулы; для воздуха по умолчанию 28/32.
        max_bytes: лимит payload v0.
        timestamp: фиксированная метка для воспроизводимых тестов.

    Raises:
        DataDesktopError: неверные параметры или устройство не входное.
        audio.CaptureError: драйвер не смог записать звук.
    """
    selected = _coerce_profile(profile)
    if not isinstance(device, audio.DeviceInfo):
        raise DataDesktopError("device должен быть audio.DeviceInfo")
    if not device.is_input:
        raise DataDesktopError(f"устройство [{device.index}] не является входным")
    if (
        not isinstance(duration_s, (int, float))
        or isinstance(duration_s, bool)
        or not math.isfinite(float(duration_s))
        or not MIN_RECORD_SECONDS <= float(duration_s) <= MAX_RECORD_SECONDS
    ):
        raise DataDesktopError(
            f"длительность записи должна быть в диапазоне "
            f"{MIN_RECORD_SECONDS:g}..{MAX_RECORD_SECONDS:g} с"
        )

    target_dir = Path(results_dir) if results_dir is not None else default_results_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    stem = _unique_stem(target_dir, f"data-rx-{selected.name}-{stamp}")
    wav_path = target_dir / f"{stem}.wav"
    json_path = target_dir / f"{stem}.json"
    received_at = utc_now()

    record = (recorder or audio.record_ambient)(
        duration_s=float(duration_s),
        device=device,
        sample_rate=selected.sample_rate,
    )
    if not isinstance(record, audio.Recording):
        raise DataDesktopError("recorder должен вернуть audio.Recording")
    if record.sample_rate != selected.sample_rate:
        raise DataDesktopError(
            f"запись сделана на {record.sample_rate} Гц, профиль требует "
            f"{selected.sample_rate} Гц"
        )
    samples = np.asarray(record.samples, dtype=np.float64)
    if samples.size == 0:
        raise DataDesktopError("запись пустая")

    write_pcm16(wav_path, samples, record.sample_rate)
    report = datalink.decode_wav_to_text(
        wav_path,
        selected,
        min_preamble_matches=min_preamble_matches,
        max_bytes=max_bytes,
    )

    payload = {
        **report,
        "schema": RECEIVE_SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "received_at_utc": received_at,
        "environment": environment(),
        "direction": "rx",
        "profile": selected.as_dict(),
        "device": device.as_dict(),
        "capture": {
            "requested_duration_s": float(duration_s),
            "captured_frames": record.frames_captured,
            "requested_frames": record.frames_requested,
            "captured_duration_s": record.duration_s,
            "overflows": record.overflows,
            "wall_time_s": record.wall_time_s,
            "sample_rate_hz": record.sample_rate,
            "dtype": record.dtype,
        },
        "wav_file_sha256": _sha256_file(wav_path),
        "crc_is_authentication": False,
        "warnings": [
            "Один удачный файл не доказывает устойчивую передачу через воздух.",
            "CRC-16 не аутентифицирует отправителя.",
        ],
        "limitations": DATA_LIMITATIONS,
    }
    write_json(json_path, payload)

    return ReceivedData(
        source=str(wav_path),
        profile=selected,
        wav_path=wav_path,
        json_path=json_path,
        report=report,
        recording=record,
        received_at_utc=received_at,
    )


def default_results_dir() -> Path:
    """Тот же каталог результатов, что и у остальных тестов Desktop Lab.

    В собранном EXE результаты лежат рядом с `.exe`, а не во временном
    распакованном каталоге PyInstaller.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "results" / "hardware_calibration"
    return Path(__file__).resolve().parent.parent / "results" / "hardware_calibration"


def _coerce_profile(profile: BfskProfile | str) -> BfskProfile:
    if isinstance(profile, str):
        return get_data_profile(profile)
    if not isinstance(profile, BfskProfile):
        raise DataDesktopError("профиль должен быть BfskProfile или его именем")
    if profile.name not in DATA_PROFILES or DATA_PROFILES[profile.name] != profile:
        raise DataDesktopError(
            f"профиль {profile.name!r} не входит в DATA_PROFILES"
        )
    return profile


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_stem(directory: Path, base: str) -> str:
    candidate = base
    counter = 2
    while (directory / f"{candidate}.wav").exists() or (
        directory / f"{candidate}.json"
    ).exists():
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


__all__ = [
    "DATA_LIMITATIONS",
    "DATA_PROFILE_NAMES",
    "DEFAULT_RECORD_SECONDS",
    "DataDesktopError",
    "DataPlaybackDisabled",
    "DataPlaybackRecord",
    "GeneratedData",
    "PLAYBACK_SCHEMA",
    "RECEIVE_SCHEMA",
    "ReceivedData",
    "generate_data_message",
    "get_data_profile",
    "list_input_devices",
    "play_data_message",
    "receive_data",
]
