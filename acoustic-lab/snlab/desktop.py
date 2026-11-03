"""Файловая модель Windows Desktop Acoustic Lab (SN-015).

Здесь нет Tkinter и нет обращений к динамику. Модуль отвечает за:

* параметры одного тестового тона и беззвучную генерацию PCM16 WAV;
* JSON-метаданные генерации и JSON только после фактической попытки PLAY;
* импорт WAV с телефона, сохранение копии и анализ через `snlab.analysis`;
* сравнение ожидаемой и обнаруженной частоты, энергию полосы 18–19 кГц.

Физическое воспроизведение выполняет `snlab.playback` и только после явного
разрешения `hardware_test=True`. Анализ одиночной записи не вычисляет SNR:
в ней нет известного эталонного сигнала, поэтому `snr_db` всегда `null`.
"""

from __future__ import annotations

import math
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from snlab import analysis as wav_analysis
from snlab import audio, calibration, playback, tones
from snlab.reporting import environment, utc_now, write_json
from snlab.wavio import WavFormatError, read_wav

TOOL_NAME = "desktop_app.py"
TASK_ID = "SN-015"

GENERATION_SCHEMA = "silentnetwork.acoustic-lab.desktop-tone/1"
PLAYBACK_SCHEMA = "silentnetwork.acoustic-lab.desktop-playback/1"
PHONE_SCHEMA = "silentnetwork.acoustic-lab.phone-analysis/1"

FREQUENCY_CHOICES_HZ: tuple[float, ...] = (
    1_000.0,
    3_000.0,
    5_000.0,
    8_000.0,
    10_000.0,
    12_000.0,
    15_000.0,
    16_000.0,
    17_000.0,
    18_000.0,
    18_500.0,
    19_000.0,
    20_000.0,
    21_000.0,
    22_000.0,
)
SAMPLE_RATE_CHOICES = (48_000, 44_100)
DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_DURATION_S = 3.0
DEFAULT_AMPLITUDE = 0.10
DEFAULT_RAMP_MS = 5.0
DEFAULT_LEAD_MS = 500.0
DEFAULT_TAIL_MS = 500.0
MIN_DURATION_S = 0.05
MAX_DURATION_S = 5.0
MAX_PADDING_MS = 5_000.0
MAX_RAMP_MS = 500.0
BAND_18_19_LOW_HZ = 18_000.0
BAND_18_19_HIGH_HZ = 19_000.0
DEFAULT_PEAK_SEARCH_HZ = 300.0
DEFAULT_PEAK_MIN_PROMINENCE_DB = 6.0

LIMITATIONS = [
    "Приложение не заменяет физический тест: без --hardware-test звук не воспроизводится.",
    "Наличие WAV не доказывает, что динамик или микрофон работают на этой частоте.",
    "Windows и Android могут применять AGC, шумоподавление и ресемплирование.",
    "SNR одиночной записи не вычисляется: нет известного эталонного сигнала.",
    "Совпадение обнаруженной частоты не является PASS для передачи данных.",
]

PHONE_LIMITATIONS = [
    *LIMITATIONS,
    "Копия WAV сохраняется локально в results/hardware_calibration; оригинал не изменяется.",
    "Анализ описывает только содержимое файла и не измеряет помещение или дальность.",
    "SNR показан как N/A: relationship сигнала и шума без эталона неизвестно.",
]


class DesktopError(ValueError):
    """Некорректное состояние или параметры Desktop Lab."""


class ToneSettingsError(DesktopError):
    """Параметры тона нельзя использовать."""


class PlaybackDisabled(DesktopError):
    """PLAY запрещён без явного режима --hardware-test."""


@dataclass(frozen=True, slots=True)
class ToneSettings:
    """Один тестовый тон: частота, длительность, амплитуда и паузы."""

    frequency_hz: float = 1_000.0
    sample_rate: int = DEFAULT_SAMPLE_RATE
    duration_s: float = DEFAULT_DURATION_S
    amplitude: float = DEFAULT_AMPLITUDE
    ramp_ms: float = DEFAULT_RAMP_MS
    lead_ms: float = DEFAULT_LEAD_MS
    tail_ms: float = DEFAULT_TAIL_MS

    def __post_init__(self) -> None:
        for name, value in (
            ("frequency_hz", self.frequency_hz),
            ("duration_s", self.duration_s),
            ("amplitude", self.amplitude),
            ("ramp_ms", self.ramp_ms),
            ("lead_ms", self.lead_ms),
            ("tail_ms", self.tail_ms),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ToneSettingsError(f"{name} должен быть числом")
            if not math.isfinite(float(value)):
                raise ToneSettingsError(f"{name} должен быть конечным числом")
        if not isinstance(self.sample_rate, int) or isinstance(self.sample_rate, bool):
            raise ToneSettingsError("sample_rate должен быть целым числом")
        if not MIN_DURATION_S <= float(self.duration_s) <= MAX_DURATION_S:
            raise ToneSettingsError(
                f"duration_s должен быть в диапазоне {MIN_DURATION_S:g}.."
                f"{MAX_DURATION_S:g} с"
            )
        try:
            self.plan()
        except (calibration.CalibrationError, tones.ToneError) as exc:
            raise ToneSettingsError(f"некорректные параметры тона: {exc}") from exc

    def plan(self) -> tones.TonePlan:
        """План одного тона для существующего генератора `snlab.tones`."""
        return tones.TonePlan(
            sample_rate=self.sample_rate,
            channels=1,
            frequencies_hz=(float(self.frequency_hz),),
            tone_ms=float(self.duration_s) * 1_000.0,
            gap_ms=0.0,
            lead_ms=float(self.lead_ms),
            tail_ms=float(self.tail_ms),
            amplitude=float(self.amplitude),
            ramp_ms=float(self.ramp_ms),
        )

    @property
    def total_samples(self) -> int:
        return self.plan().total_samples

    @property
    def file_duration_s(self) -> float:
        return self.total_samples / self.sample_rate

    def as_dict(self) -> dict[str, Any]:
        plan = self.plan()
        return {
            "frequency_hz": float(self.frequency_hz),
            "sample_rate_hz": self.sample_rate,
            "duration_s": float(self.duration_s),
            "tone_ms": float(self.duration_s) * 1_000.0,
            "amplitude": float(self.amplitude),
            "ramp_ms": float(self.ramp_ms),
            "lead_ms": float(self.lead_ms),
            "tail_ms": float(self.tail_ms),
            "channels": 1,
            "pcm_format": "pcm_s16le",
            "tone_samples": plan.tone_samples,
            "total_samples": plan.total_samples,
            "file_duration_s": self.file_duration_s,
        }


@dataclass(frozen=True, slots=True)
class GeneratedTone:
    """Созданный WAV и его JSON-метаданные; воспроизведения ещё не было."""

    settings: ToneSettings
    wav_path: Path
    metadata_path: Path
    sha256: str
    frames: int
    file_duration_s: float
    generated_at_utc: str

    @property
    def frequency_hz(self) -> float:
        return float(self.settings.frequency_hz)

    def metadata(self) -> dict[str, Any]:
        return {
            "schema": GENERATION_SCHEMA,
            "tool": TOOL_NAME,
            "task": TASK_ID,
            "generated_at_utc": self.generated_at_utc,
            "environment": environment(),
            "mode": "file_only",
            "tone": self.settings.as_dict(),
            "file": {
                "wav": str(self.wav_path),
                "json": str(self.metadata_path),
                "sha256": self.sha256,
                "format": "pcm_s16le",
                "sample_rate_hz": self.settings.sample_rate,
                "channels": 1,
                "frames": self.frames,
                "duration_s": self.file_duration_s,
                "bytes": self.wav_path.stat().st_size,
            },
            "playback": {
                "performed": False,
                "status": "not_requested",
                "note": "WAV создан беззвучно; PLAY выполняется только вручную.",
            },
            "warnings": [
                "Это синтетический стимул, а не измерение реального оборудования.",
                "Для физического воспроизведения нужен режим --hardware-test.",
            ],
            "limitations": LIMITATIONS,
        }


@dataclass(frozen=True, slots=True)
class PlaybackRecord:
    """Фактический результат одной попытки PLAY и путь к JSON."""

    tone: GeneratedTone
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "performed": self.performed,
            "frames_written": self.frames_written,
            "frames_total": self.frames_total,
            "elapsed_s": self.elapsed_s,
            "error": self.error,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "json_path": str(self.json_path),
        }


@dataclass(frozen=True, slots=True)
class PhoneAnalysisRecord:
    """Импортированная запись телефона и сохранённые результаты анализа."""

    source_path: Path
    imported_path: Path
    analysis: wav_analysis.WavAnalysis
    expected_frequency_hz: float | None
    detected_frequency_hz: float | None
    global_dominant_frequency_hz: float | None
    frequency_offset_hz: float | None
    detection_margin_db: float | None
    band_18000_19000_dbfs: float | None
    snr_db: None
    warnings: tuple[str, ...]
    json_path: Path | None
    csv_path: Path | None
    psd_path: Path | None
    spectrogram_path: Path | None


def default_results_dir() -> Path:
    """`acoustic-lab/results/hardware_calibration` независимо от текущего каталога.

    В собранном EXE (`sys.frozen`) исходный каталог недоступен: результаты
    сохраняются рядом с исполняемым файлом, а не во временном `_MEIPASS`.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "results" / "hardware_calibration"
    return Path(__file__).resolve().parent.parent / "results" / "hardware_calibration"


def list_output_devices() -> list[audio.DeviceInfo]:
    """Перечислить выходные устройства, не открывая аудиопотоки."""
    return [device for device in audio.list_devices() if device.is_output]


def probe_output_formats(
    device: audio.DeviceInfo,
    *,
    rates: tuple[int, ...] = SAMPLE_RATE_CHOICES,
    sd_module: Any | None = None,
) -> dict[int, dict[str, Any]]:
    """Проверить частоты через драйвер без открытия потока."""
    return playback.probe_output_formats(device, rates=rates, sd_module=sd_module)


def generate_tone(
    settings: ToneSettings,
    results_dir: str | Path | None = None,
    *,
    timestamp: str | None = None,
) -> GeneratedTone:
    """Создать WAV и JSON, ничего не воспроизводя."""
    if not isinstance(settings, ToneSettings):
        raise ToneSettingsError("ожидается ToneSettings")
    target_dir = Path(results_dir) if results_dir is not None else default_results_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    stem = _unique_stem(
        target_dir,
        f"tone-{_frequency_token(settings.frequency_hz)}Hz-"
        f"{settings.sample_rate}Hz-{stamp}",
    )
    wav_path = target_dir / f"{stem}.wav"
    metadata_path = target_dir / f"{stem}.json"
    generated_at = utc_now()

    plan = settings.plan()
    signal = tones.generate_signal(plan)
    written, digest = tones.write_tone_wav(wav_path, plan, samples=signal)
    frames = int(plan.total_samples)
    tone = GeneratedTone(
        settings=settings,
        wav_path=written,
        metadata_path=metadata_path,
        sha256=digest,
        frames=frames,
        file_duration_s=frames / settings.sample_rate,
        generated_at_utc=generated_at,
    )
    write_json(metadata_path, tone.metadata())
    return tone


def play_generated(
    tone: GeneratedTone,
    device: audio.DeviceInfo,
    *,
    engine: playback.PlaybackEngine,
    hardware_test: bool,
    timestamp: str | None = None,
) -> PlaybackRecord:
    """Выполнить одну попытку PLAY и записать её фактический результат.

    Без `hardware_test=True` функция отказывает до любых действий: ни поток,
    ни JSON не создаются. JSON создаётся после возврата движка, включая
    ошибку драйвера и частичное воспроизведение.
    """
    if not isinstance(tone, GeneratedTone):
        raise DesktopError("ожидается GeneratedTone")
    if not hardware_test:
        raise PlaybackDisabled(
            "PLAY запрещён: приложение запущено в FILE-ONLY. "
            "Перезапустите его с флагом --hardware-test."
        )
    if not isinstance(device, audio.DeviceInfo):
        raise DesktopError("device должен быть audio.DeviceInfo")
    if not device.is_output:
        raise DesktopError(f"устройство [{device.index}] не является выходным")
    if not tone.wav_path.is_file():
        raise DesktopError(f"WAV не найден: {tone.wav_path}")

    data, sample_rate, channels = read_wav(tone.wav_path)
    if channels != 1:
        raise DesktopError(f"ожидается mono WAV, в файле {channels} каналов")
    if sample_rate != tone.settings.sample_rate:
        raise DesktopError(
            f"частота WAV {sample_rate} Гц не совпадает с настройками "
            f"{tone.settings.sample_rate} Гц"
        )
    digest = tones.sha256_file(tone.wav_path)
    if digest != tone.sha256:
        raise DesktopError(
            "WAV изменился после генерации: SHA-256 не совпадает, "
            "воспроизведение остановлено"
        )

    samples = np.asarray(data[:, 0], dtype=np.float64)
    started_at = utc_now()
    started_monotonic = time.monotonic()
    try:
        outcome = engine.play(samples, sample_rate, device)
    except Exception as exc:  # noqa: BLE001 - ошибка движка должна попасть в JSON
        outcome = playback.PlaybackOutcome(
            status="failed",
            frames_written=0,
            total_frames=int(samples.size),
            elapsed_s=time.monotonic() - started_monotonic,
            error=f"{type(exc).__name__}: {exc}",
        )
    finished_at = utc_now()

    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    json_path = tone.wav_path.with_name(
        f"{tone.wav_path.stem}.playback-{stamp}.json"
    )
    payload = {
        "schema": PLAYBACK_SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": finished_at,
        "environment": environment(),
        "mode": "hardware_test",
        "tone": tone.settings.as_dict(),
        "device": device.as_dict(),
        "file": {
            "wav": str(tone.wav_path),
            "sha256": tone.sha256,
        },
        "playback": {
            **outcome.as_dict(),
            "engine": type(engine).__name__,
            "started_at_utc": started_at,
            "finished_at_utc": finished_at,
            "note": (
                "performed=true только при полном воспроизведении. "
                "STOP и ошибка драйвера не считаются успешным PLAY."
            ),
        },
        "warnings": _playback_warnings(outcome),
        "limitations": LIMITATIONS,
    }
    write_json(json_path, payload)

    return PlaybackRecord(
        tone=tone,
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


def analyze_phone_wav(
    source: str | Path,
    *,
    expected_frequency_hz: float | None = None,
    results_dir: str | Path | None = None,
    timestamp: str | None = None,
    make_plots: bool = True,
) -> PhoneAnalysisRecord:
    """Скопировать WAV телефона, проанализировать и сохранить JSON/CSV/PNG."""
    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(f"WAV не найден: {source_path}")
    if expected_frequency_hz is not None:
        if (
            not isinstance(expected_frequency_hz, (int, float))
            or isinstance(expected_frequency_hz, bool)
            or not math.isfinite(float(expected_frequency_hz))
            or float(expected_frequency_hz) <= 0.0
        ):
            raise DesktopError("expected_frequency_hz должен быть положительным числом")
        expected_frequency_hz = float(expected_frequency_hz)

    target_dir = Path(results_dir) if results_dir is not None else default_results_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_path.stem)[:40] or "phone"
    imported = _unique_path(
        target_dir,
        f"imported-{stamp}-{safe_stem}",
        suffix=".wav",
    )
    shutil.copy2(source_path, imported)

    result = wav_analysis.analyze_file(imported)
    warnings = list(result.warnings)
    enriched_channels: list[dict[str, Any]] = []
    top_detected: float | None = None
    top_margin: float | None = None
    top_band: float | None = None

    for index, channel in enumerate(result.channel_analyses):
        global_dominant = _global_dominant(channel)
        detected: float | None = global_dominant
        margin: float | None = None
        if expected_frequency_hz is not None:
            detected, margin = _peak_near(
                channel.frequencies,
                channel.psd,
                expected_frequency_hz,
                search_hz=DEFAULT_PEAK_SEARCH_HZ,
            )
            if detected is None:
                warnings.append(
                    f"канал {index}: в окне ±{DEFAULT_PEAK_SEARCH_HZ:g} Гц "
                    f"вокруг {expected_frequency_hz:g} Гц значимый пик не найден"
                )
        offset = (
            detected - expected_frequency_hz
            if detected is not None and expected_frequency_hz is not None
            else None
        )
        band = band_level_dbfs(
            channel.frequencies,
            channel.psd,
            BAND_18_19_LOW_HZ,
            BAND_18_19_HIGH_HZ,
        )
        if index == 0:
            top_detected = detected
            top_margin = margin
            top_band = band
        entry = channel.as_dict()
        entry.update(
            {
                "global_dominant_frequency_hz": global_dominant,
                "detected_frequency_hz": detected,
                "detection_margin_db": margin,
                "expected_frequency_hz": expected_frequency_hz,
                "frequency_offset_hz": offset,
                "band_18000_19000_hz": {
                    "low_hz": BAND_18_19_LOW_HZ,
                    "high_hz": BAND_18_19_HIGH_HZ,
                    "level_dbfs": band,
                    "available": band is not None,
                },
                "snr_db": None,
                "snr_note": "SNR не вычисляется по одиночной записи без эталона.",
            }
        )
        enriched_channels.append(entry)

    json_path = imported.with_name(imported.stem + ".analysis.json")
    csv_path = imported.with_name(imported.stem + ".analysis.csv")
    psd_path = imported.with_name(imported.stem + ".psd.png")
    spectrogram_path = imported.with_name(imported.stem + ".spectrogram.png")

    plot_paths: dict[str, str | None] = {
        "psd": None,
        "spectrogram": None,
    }
    if make_plots:
        try:
            wav_analysis.write_psd_plot(psd_path, result)
            plot_paths["psd"] = str(psd_path)
        except ImportError:
            warnings.append("matplotlib не установлен: PSD PNG не создан")
        try:
            wav_analysis.write_spectrogram_plot(spectrogram_path, result)
            plot_paths["spectrogram"] = str(spectrogram_path)
        except ImportError:
            warnings.append("matplotlib не установлен: спектрограмма PNG не создана")
        except wav_analysis.AnalysisError as exc:
            warnings.append(f"спектрограмма не создана: {exc}")

    analysis_payload = result.as_dict()
    analysis_payload["channels"] = enriched_channels
    payload = {
        "schema": PHONE_SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": utc_now(),
        "environment": environment(),
        "mode": "file_only",
        "source_file": {
            "original_path": str(source_path.resolve()),
            "imported_path": str(imported),
            "sha256": result.sha256,
        },
        "expected_frequency_hz": expected_frequency_hz,
        "detected_frequency_hz": top_detected,
        "global_dominant_frequency_hz": _global_dominant(result.channel_analyses[0]),
        "frequency_offset_hz": (
            top_detected - expected_frequency_hz
            if top_detected is not None and expected_frequency_hz is not None
            else None
        ),
        "detection_margin_db": top_margin,
        "band_18000_19000_hz": {
            "low_hz": BAND_18_19_LOW_HZ,
            "high_hz": BAND_18_19_HIGH_HZ,
            "level_dbfs": top_band,
            "available": top_band is not None,
        },
        "snr_db": None,
        "snr_note": (
            "SNR = N/A: в одиночной записи нет известного эталонного сигнала "
            "и нельзя отделить шум от переданного тона."
        ),
        "method": {
            "peak_search": (
                f"Максимум PSD в окне ±{DEFAULT_PEAK_SEARCH_HZ:g} Гц вокруг "
                "ожидаемой частоты; положение уточняется параболической "
                "интерполяцией по трём бинам"
            ),
            "significance": (
                f"пик учитывается при превышении медианы окна на "
                f"{DEFAULT_PEAK_MIN_PROMINENCE_DB:g} дБ"
            ),
            "band_18000_19000": "Интеграл PSD по полосе 18–19 кГц в dBFS",
            "snr": "N/A, не вычисляется",
        },
        **analysis_payload,
        "warnings": warnings,
        "files": {
            "json": str(json_path),
            "csv": str(csv_path),
            "psd": plot_paths["psd"],
            "spectrogram": plot_paths["spectrogram"],
        },
        "limitations": PHONE_LIMITATIONS,
    }
    write_json(json_path, payload)
    wav_analysis.write_csv(csv_path, result)

    return PhoneAnalysisRecord(
        source_path=source_path,
        imported_path=imported,
        analysis=result,
        expected_frequency_hz=expected_frequency_hz,
        detected_frequency_hz=top_detected,
        global_dominant_frequency_hz=_global_dominant(result.channel_analyses[0]),
        frequency_offset_hz=(
            top_detected - expected_frequency_hz
            if top_detected is not None and expected_frequency_hz is not None
            else None
        ),
        detection_margin_db=top_margin,
        band_18000_19000_dbfs=top_band,
        snr_db=None,
        warnings=tuple(warnings),
        json_path=json_path,
        csv_path=csv_path,
        psd_path=Path(plot_paths["psd"]) if plot_paths["psd"] else None,
        spectrogram_path=(
            Path(plot_paths["spectrogram"]) if plot_paths["spectrogram"] else None
        ),
    )


def band_level_dbfs(
    frequencies: np.ndarray | None,
    psd: np.ndarray | None,
    low_hz: float,
    high_hz: float,
) -> float | None:
    """Накопленная энергия полосы по PSD в dBFS.

    Возвращает `None`, если полосы нет в записанном спектре: отсутствие
    данных нельзя выдавать за нулевой уровень.
    """
    if frequencies is None or psd is None:
        return None
    freqs = np.asarray(frequencies, dtype=np.float64)
    density = np.asarray(psd, dtype=np.float64)
    if freqs.shape != density.shape or freqs.size < 2:
        return None
    if not math.isfinite(low_hz) or not math.isfinite(high_hz) or high_hz <= low_hz:
        raise DesktopError("некорректные границы полосы")
    mask = (freqs >= low_hz) & (freqs <= high_hz)
    if np.count_nonzero(mask) < 2:
        return None
    coordinates = freqs[mask]
    values = density[mask]
    steps = np.diff(coordinates)
    power = float(np.sum((values[1:] + values[:-1]) * steps / 2.0))
    if power <= 0.0 or not math.isfinite(power):
        return wav_analysis.DB_FLOOR
    return float(10.0 * math.log10(power))


def format_phone_summary(record: PhoneAnalysisRecord) -> str:
    """Текстовая сводка для GUI: без автоматического PASS."""
    result = record.analysis
    channel = result.channel_analyses[0]
    level = channel.summary["level"]
    clipping = channel.summary["clipping"]
    floor = channel.summary.get("noise_floor")
    lines = [
        "Expected frequency vs Detected frequency",
        f"  Expected: {_hz(record.expected_frequency_hz)}",
        f"  Detected: {_hz(record.detected_frequency_hz)}",
        f"  Offset:   {_hz_signed(record.frequency_offset_hz)}",
        f"  Global dominant peak: {_hz(record.global_dominant_frequency_hz)}",
        f"  Detection margin: {_db(record.detection_margin_db)}",
        "",
        "Файл",
        f"  Original: {record.source_path}",
        f"  Imported: {record.imported_path}",
        f"  SHA-256:  {result.sha256}",
        f"  Format:   PCM16, {result.channels} кан., "
        f"Fs={result.sample_rate} Гц, {result.frames} отсчётов",
        f"  Duration: {result.duration_s:.3f} с",
        "",
        "Уровни",
        f"  RMS:      {level['rms_dbfs']:.2f} dBFS",
        f"  Peak:     {level['peak_dbfs']:.2f} dBFS",
        f"  Crest:    {level['crest_factor_db']:.2f} дБ",
        f"  DC offset:{level['dc_offset']:+.6f} FS",
        f"  Clipping: {'да' if clipping['detected'] else 'нет'} "
        f"({clipping['clipped_samples']} отсчётов)",
        f"  Noise floor: {_db(floor['dbfs']) if floor else 'N/A'}",
        f"  Band 18–19 kHz: {_db(record.band_18000_19000_dbfs)}",
        f"  SNR: N/A — {record.analysis.channel_analyses[0].summary.get('snr_note', '')}",
        "",
        "Вердикт",
        "  PASS/FAIL автоматически не вычисляется. Близость частоты — только",
        "  один из показателей; решение принимает Product Owner по документу",
        "  SN-015-LAPTOP-TO-PHONE.md с учётом уровня, клиппинга и спектрограммы.",
    ]
    if record.warnings:
        lines.append("")
        lines.append("Предупреждения")
        lines.extend(f"  - {warning}" for warning in record.warnings)
    return "\n".join(lines)


def _playback_warnings(outcome: playback.PlaybackOutcome) -> list[str]:
    warnings: list[str] = []
    if outcome.status == "stopped":
        warnings.append(
            "PLAY остановлен пользователем: воспроизведение неполное и не "
            "считается успешным."
        )
    if outcome.status == "failed":
        warnings.append(
            "PLAY завершился ошибкой; фактическая ошибка драйвера записана "
            "в поле playback.error."
        )
    return warnings


def _global_dominant(channel: wav_analysis.ChannelAnalysis) -> float | None:
    peaks = channel.summary.get("peaks") or []
    if not peaks:
        return None
    return float(peaks[0]["frequency_hz"])


def _peak_near(
    frequencies: np.ndarray | None,
    psd: np.ndarray | None,
    expected_hz: float,
    *,
    search_hz: float,
) -> tuple[float | None, float | None]:
    """Найти значимый пик около ожидаемой частоты и уточнить его положение."""
    if frequencies is None or psd is None:
        return None, None
    freqs = np.asarray(frequencies, dtype=np.float64)
    density = np.asarray(psd, dtype=np.float64)
    if freqs.shape != density.shape or freqs.size < 3:
        return None, None
    mask = np.abs(freqs - expected_hz) <= search_hz
    if np.count_nonzero(mask) < 3:
        return None, None
    indices = np.flatnonzero(mask)
    relative = int(np.argmax(density[indices]))
    peak = int(indices[relative])
    window_db = 10.0 * np.log10(np.maximum(density[indices], 1e-30))
    median_db = float(np.median(window_db))
    peak_db = float(window_db[relative])
    margin = peak_db - median_db
    if margin < DEFAULT_PEAK_MIN_PROMINENCE_DB:
        return None, float(margin)

    frequency = float(freqs[peak])
    if 0 < peak < freqs.size - 1:
        left = 10.0 * math.log10(max(float(density[peak - 1]), 1e-30))
        center = 10.0 * math.log10(max(float(density[peak]), 1e-30))
        right = 10.0 * math.log10(max(float(density[peak + 1]), 1e-30))
        denominator = left - 2.0 * center + right
        if abs(denominator) > 1e-18:
            delta = 0.5 * (left - right) / denominator
            delta = max(-0.5, min(0.5, delta))
            frequency += delta * (freqs[1] - freqs[0])
    return float(frequency), float(margin)


def _unique_stem(directory: Path, base: str) -> str:
    stem = base
    counter = 0
    while (directory / f"{stem}.wav").exists() or (directory / f"{stem}.json").exists():
        counter += 1
        stem = f"{base}-{counter}"
    return stem


def _unique_path(directory: Path, base: str, *, suffix: str) -> Path:
    path = directory / f"{base}{suffix}"
    counter = 0
    while path.exists():
        counter += 1
        path = directory / f"{base}-{counter}{suffix}"
    return path


def _frequency_token(value: float) -> str:
    return f"{float(value):g}".replace(".", "p")


def _hz(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1f} Hz"


def _hz_signed(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.1f} Hz"


def _db(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f} dBFS"


__all__ = [
    "BAND_18_19_HIGH_HZ",
    "BAND_18_19_LOW_HZ",
    "DEFAULT_AMPLITUDE",
    "DEFAULT_DURATION_S",
    "DEFAULT_LEAD_MS",
    "DEFAULT_RAMP_MS",
    "DEFAULT_SAMPLE_RATE",
    "DEFAULT_TAIL_MS",
    "DesktopError",
    "FREQUENCY_CHOICES_HZ",
    "GeneratedTone",
    "PHONE_LIMITATIONS",
    "PhoneAnalysisRecord",
    "PlaybackDisabled",
    "PlaybackRecord",
    "SAMPLE_RATE_CHOICES",
    "TASK_ID",
    "ToneSettings",
    "ToneSettingsError",
    "analyze_phone_wav",
    "band_level_dbfs",
    "default_results_dir",
    "format_phone_summary",
    "generate_tone",
    "list_output_devices",
    "play_generated",
    "probe_output_formats",
]
