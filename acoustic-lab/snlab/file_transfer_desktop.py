"""Windows Desktop: передача и приём файла через звук (SN-034).

Модуль не содержит Tkinter. Он связывает файловый слой
`snlab.file_transfer` с устройствами, воспроизведением и отчётами Windows
Desktop Lab:

* создать WAV с пакетами MANIFEST/DATA/END и сохранить JSON;
* воспроизвести его через выбранный динамик и записать фактический PLAY;
* записать микрофон, собрать файл и сохранить WAV + JSON.

Успешный round-trip через WAV не доказывает передачу через воздух. SHA-256
проверяет целостность файла, но не аутентифицирует отправителя.
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
from snlab import datalink
from snlab import file_transfer
from snlab.config import DATA_PROFILES, BfskProfile
from snlab.reporting import environment, utc_now, write_json
from snlab.wavio import read_wav, write_pcm16

TOOL_NAME = "desktop_app.py"
TASK_ID = "SN-034"

TX_SCHEMA = "silentnetwork.acoustic-lab.file-transfer-tx/1"
PLAYBACK_SCHEMA = "silentnetwork.acoustic-lab.file-transfer-playback/1"
RECEIVE_SCHEMA = "silentnetwork.acoustic-lab.file-transfer-receive/1"

DEFAULT_LEAD_SILENCE_S = file_transfer.DEFAULT_LEAD_SILENCE_S
DEFAULT_TAIL_SILENCE_S = file_transfer.DEFAULT_TAIL_SILENCE_S
DEFAULT_INTER_FRAME_GAP_S = file_transfer.DEFAULT_INTER_FRAME_GAP_S
DEFAULT_RECORD_SECONDS = 30.0
MIN_RECORD_SECONDS = 1.0
MAX_RECORD_SECONDS = 300.0

FILE_LIMITATIONS = [
    "WAV и JSON сами по себе не доказывают передачу через воздух.",
    "Один файл 0..4096 байт: нет ACK, докачки и повторов по запросу.",
    "SHA-256 проверяет целостность файла, но не аутентифицирует отправителя.",
    "Нет шифрования и SCNP/AEAD: секреты и личные данные передавать нельзя.",
    "Системная громкость не изменяется; запись начинается только по кнопке.",
]

#: Имена профилей, допустимых в файловом слое v0 (для UI и валидации).
DATA_PROFILE_NAMES = tuple(sorted(DATA_PROFILES))


class FileDesktopError(ValueError):
    """Некорректное состояние Windows file transfer."""


class FilePlaybackDisabled(FileDesktopError):
    """PLAY файла запрещён без явного разрешения."""


@dataclass(frozen=True, slots=True)
class GeneratedFile:
    """Созданный WAV с файлом; воспроизведения ещё не было."""

    source_path: Path
    filename: str
    file_size: int
    sha256: str
    wav_sha256: str
    profile: BfskProfile
    wav_path: Path
    metadata_path: Path
    report: dict[str, Any]
    packet_count: int
    total_chunks: int
    file_duration_s: float
    generated_at_utc: str


@dataclass(frozen=True, slots=True)
class FilePlaybackRecord:
    """Фактический результат PLAY WAV с файлом."""

    generated: GeneratedFile
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
class ReceivedFile:
    """Запись микрофона и результат сборки файла."""

    source: str
    profile: BfskProfile
    wav_path: Path
    json_path: Path
    report: dict[str, Any]
    recording: audio.Recording
    received_at_utc: str

    @property
    def ok(self) -> bool:
        return file_transfer.decode_report_ok(self.report)

    @property
    def filename(self) -> str | None:
        file_info = self.report.get("file")
        return file_info.get("filename") if isinstance(file_info, dict) else None

    @property
    def output_path(self) -> str | None:
        return self.report.get("output_path")

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
    """Вернуть только профиль, разрешённый для файлового слоя v0."""
    if not isinstance(name, str):
        raise FileDesktopError("имя профиля должно быть строкой")
    try:
        return DATA_PROFILES[name]
    except KeyError:
        available = ", ".join(sorted(DATA_PROFILES))
        raise FileDesktopError(
            f"профиль {name!r} не предназначен для передачи данных; "
            f"доступны: {available}"
        ) from None


def generate_file_message(
    source: str | Path,
    profile: BfskProfile | str,
    results_dir: str | Path | None = None,
    *,
    transfer_id: int | None = None,
    inter_frame_gap_s: float = DEFAULT_INTER_FRAME_GAP_S,
    lead_silence_s: float = DEFAULT_LEAD_SILENCE_S,
    tail_silence_s: float = DEFAULT_TAIL_SILENCE_S,
    timestamp: str | None = None,
) -> GeneratedFile:
    """Создать WAV с пакетами файла и JSON, ничего не воспроизводя."""
    selected = _coerce_profile(profile)
    source_path = Path(source)
    if not source_path.is_file():
        raise FileDesktopError(f"файл не найден: {source_path}")
    target_dir = (
        Path(results_dir) if results_dir is not None else default_results_dir()
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    stem = _unique_stem(target_dir, f"file-tx-{selected.name}-{stamp}")
    wav_path = target_dir / f"{stem}.wav"
    metadata_path = target_dir / f"{stem}.json"
    generated_at = utc_now()

    report = file_transfer.encode_file_to_wav(
        source_path,
        selected,
        wav_path,
        transfer_id=transfer_id,
        inter_frame_gap_s=inter_frame_gap_s,
        lead_silence_s=lead_silence_s,
        tail_silence_s=tail_silence_s,
    )
    digest = _sha256_file(wav_path)
    data, sample_rate, channels = read_wav(wav_path)
    if channels != 1 or sample_rate != selected.sample_rate:
        raise FileDesktopError("созданный WAV не соответствует профилю данных")

    metadata = {
        **report,
        "schema": TX_SCHEMA,
        "contract_schema": report.get("schema"),
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "direction": "tx",
        "generated_at_utc": generated_at,
        "environment": environment(),
        "source": {
            "path": str(source_path),
            "filename": report["file"]["filename"],
            "size": report["file"]["size"],
            "sha256": report["file"]["sha256"],
        },
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
            "Файл передаётся открытым текстом: SHA-256 не является защитой.",
            "Воспроизведение выполняется только по кнопке и после подтверждения.",
        ],
        "limitations": FILE_LIMITATIONS,
    }
    write_json(metadata_path, metadata)
    transfer = report["transfer"]
    return GeneratedFile(
        source_path=source_path,
        filename=report["file"]["filename"],
        file_size=int(report["file"]["size"]),
        sha256=str(report["file"]["sha256"]),
        wav_sha256=digest,
        profile=selected,
        wav_path=wav_path,
        metadata_path=metadata_path,
        report=report,
        packet_count=int(transfer["packet_count"]),
        total_chunks=int(transfer["total_chunks"]),
        file_duration_s=float(data.shape[0] / sample_rate),
        generated_at_utc=generated_at,
    )


def play_file_message(
    generated: GeneratedFile,
    device: audio.DeviceInfo,
    *,
    engine: playback.PlaybackEngine,
    hardware_test: bool,
    timestamp: str | None = None,
) -> FilePlaybackRecord:
    """Воспроизвести WAV с файлом и записать фактический результат PLAY.

    JSON создаётся после возврата движка, включая ошибку драйвера. Без
    `hardware_test=True` функция отказывает до открытия потока.
    """
    if not hardware_test:
        raise FilePlaybackDisabled(
            "PLAY файла запрещён: приложение запущено в FILE-ONLY."
        )
    if not isinstance(generated, GeneratedFile):
        raise FileDesktopError("ожидается GeneratedFile")
    if not isinstance(device, audio.DeviceInfo):
        raise FileDesktopError("device должен быть audio.DeviceInfo")
    if not device.is_output:
        raise FileDesktopError(f"устройство [{device.index}] не является выходным")
    if not generated.wav_path.is_file():
        raise FileDesktopError(f"WAV не найден: {generated.wav_path}")

    data, sample_rate, channels = read_wav(generated.wav_path)
    if channels != 1:
        raise FileDesktopError(f"ожидается mono WAV, в файле {channels} каналов")
    if sample_rate != generated.profile.sample_rate:
        raise FileDesktopError(
            f"частота WAV {sample_rate} Гц не совпадает с профилем "
            f"{generated.profile.sample_rate} Гц"
        )
    digest = _sha256_file(generated.wav_path)
    if digest != generated.wav_sha256:
        raise FileDesktopError(
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
        "file": {
            "filename": generated.filename,
            "size": generated.file_size,
            "sha256": generated.sha256,
            "wav": str(generated.wav_path),
            "wav_sha256": digest,
        },
        "transfer": {
            "packet_count": generated.packet_count,
            "total_chunks": generated.total_chunks,
        },
        "device": device.as_dict(),
        "playback": {
            **outcome.as_dict(),
            "engine": type(engine).__name__,
            "started_at_utc": started_at,
            "finished_at_utc": finished_at,
        },
        "crc_is_authentication": False,
        "sha256_is_authentication": False,
        "warnings": [
            "performed=true означает полное воспроизведение WAV, а не успешный приём.",
            "Файл не зашифрован и не аутентифицирован.",
        ],
        "limitations": FILE_LIMITATIONS,
    }
    write_json(json_path, payload)

    return FilePlaybackRecord(
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


def receive_file(
    profile: BfskProfile | str,
    device: audio.DeviceInfo,
    duration_s: float = DEFAULT_RECORD_SECONDS,
    results_dir: str | Path | None = None,
    *,
    recorder: Callable[..., audio.Recording] | None = None,
    min_preamble_matches: int = datalink.ACOUSTIC_MIN_PREAMBLE_MATCHES,
    out_dir: str | Path | None = None,
    timestamp: str | None = None,
) -> ReceivedFile:
    """Записать микрофон, сохранить WAV и собрать файл.

    Args:
        profile: профиль `data-audible` или `data-robust`.
        device: входное устройство.
        duration_s: длительность записи, 1..300 с.
        results_dir: каталог результатов.
        recorder: функция записи для тестов; по умолчанию `audio.record_ambient`.
        min_preamble_matches: порог преамбулы; для воздуха по умолчанию 28/32.
        out_dir: каталог для собранного файла; по умолчанию отдельный
            подкаталог `received/<stem>` этой записи.
        timestamp: фиксированная метка для воспроизводимых тестов.

    Raises:
        FileDesktopError: неверные параметры или устройство не входное.
        audio.CaptureError: драйвер не смог записать звук.
    """
    selected = _coerce_profile(profile)
    if not isinstance(device, audio.DeviceInfo):
        raise FileDesktopError("device должен быть audio.DeviceInfo")
    if not device.is_input:
        raise FileDesktopError(f"устройство [{device.index}] не является входным")
    if (
        not isinstance(duration_s, (int, float))
        or isinstance(duration_s, bool)
        or not math.isfinite(float(duration_s))
        or not MIN_RECORD_SECONDS <= float(duration_s) <= MAX_RECORD_SECONDS
    ):
        raise FileDesktopError(
            f"длительность записи должна быть в диапазоне "
            f"{MIN_RECORD_SECONDS:g}..{MAX_RECORD_SECONDS:g} с"
        )

    target_dir = (
        Path(results_dir) if results_dir is not None else default_results_dir()
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    stem = _unique_stem(target_dir, f"file-rx-{selected.name}-{stamp}")
    wav_path = target_dir / f"{stem}.wav"
    json_path = target_dir / f"{stem}.json"
    received_at = utc_now()

    record = (recorder or audio.record_ambient)(
        duration_s=float(duration_s),
        device=device,
        sample_rate=selected.sample_rate,
    )
    if not isinstance(record, audio.Recording):
        raise FileDesktopError("recorder должен вернуть audio.Recording")
    if record.sample_rate != selected.sample_rate:
        raise FileDesktopError(
            f"запись сделана на {record.sample_rate} Гц, профиль требует "
            f"{selected.sample_rate} Гц"
        )
    samples = np.asarray(record.samples, dtype=np.float64)
    if samples.size == 0:
        raise FileDesktopError("запись пустая")

    write_pcm16(wav_path, samples, record.sample_rate)
    destination = (
        Path(out_dir) if out_dir is not None else target_dir / "received" / stem
    )
    report = file_transfer.decode_wav_to_file(
        wav_path,
        selected,
        destination,
        min_preamble_matches=min_preamble_matches,
    )

    contract_schema = report.get("schema")
    payload = {
        **report,
        "schema": RECEIVE_SCHEMA,
        "contract_schema": contract_schema,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "direction": "rx",
        "received_at_utc": received_at,
        "environment": environment(),
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
        "sha256_is_authentication": False,
        "warnings": [
            "Один удачный файл не доказывает устойчивую передачу через воздух.",
            "SHA-256 не аутентифицирует отправителя.",
        ],
        "limitations": FILE_LIMITATIONS,
    }
    write_json(json_path, payload)

    return ReceivedFile(
        source=str(wav_path),
        profile=selected,
        wav_path=wav_path,
        json_path=json_path,
        report=payload,
        recording=record,
        received_at_utc=received_at,
    )


def recommended_record_seconds(duration_s: float, *, margin_s: float = 3.0) -> float:
    """Рекомендуемая длительность записи для WAV данной длительности."""
    number = float(duration_s)
    if not math.isfinite(number) or number < 0.0:
        raise FileDesktopError("длительность WAV должна быть неотрицательной")
    return min(MAX_RECORD_SECONDS, max(MIN_RECORD_SECONDS, number + margin_s))


def default_results_dir() -> Path:
    """Тот же каталог результатов, что и у остальных тестов Desktop Lab."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "results" / "hardware_calibration"
    return Path(__file__).resolve().parent.parent / "results" / "hardware_calibration"


def _coerce_profile(profile: BfskProfile | str) -> BfskProfile:
    if isinstance(profile, str):
        return get_data_profile(profile)
    if not isinstance(profile, BfskProfile):
        raise FileDesktopError("профиль должен быть BfskProfile или его именем")
    if profile.name not in DATA_PROFILES or DATA_PROFILES[profile.name] != profile:
        raise FileDesktopError(
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
    "DATA_PROFILE_NAMES",
    "DEFAULT_RECORD_SECONDS",
    "FILE_LIMITATIONS",
    "FileDesktopError",
    "FilePlaybackDisabled",
    "FilePlaybackRecord",
    "GeneratedFile",
    "MAX_RECORD_SECONDS",
    "MIN_RECORD_SECONDS",
    "PLAYBACK_SCHEMA",
    "RECEIVE_SCHEMA",
    "ReceivedFile",
    "TX_SCHEMA",
    "generate_file_message",
    "get_data_profile",
    "list_input_devices",
    "play_file_message",
    "receive_file",
    "recommended_record_seconds",
]
