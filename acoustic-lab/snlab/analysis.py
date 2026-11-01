"""Анализ WAV-файлов: уровни, спектр, шумовой пол, клиппинг (SN-014, часть B).

Модуль работает только с файлами. Он не открывает микрофон и динамик, поэтому
его можно и нужно запускать без физического оборудования.

Методика не изобретает новую: уровни, шумовой пол, PSD, полосы и пики берутся
из `snlab.audio` (SN-010). Здесь добавляются:

* чтение многоканального PCM16 WAV и независимый анализ каждого канала;
* JSON-отчёт, длинный CSV (`channel,section,name,...`) и графики PSD и
  спектрограммы;
* честная пометка, когда запись слишком коротка для шумового пола.

Границы: анализатор не знает, что было передано. Он не вычисляет SNR, BER и
не делает выводов о поддержке частот оборудованием.
"""

from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from snlab import audio
from snlab.wavio import read_wav

DEFAULT_TOP_PEAKS = 10
MIN_ANALYSIS_SAMPLES = 8
MIN_NOISE_FLOOR_S = 0.1
MIN_SPECTROGRAM_SAMPLES = 32
DB_FLOOR = audio.DB_FLOOR

LIMITATIONS = [
    "Анализ WAV не открывает аудиоустройства и не доказывает работу тракта.",
    "Метрики относятся только к содержимому файла, а не к помещению.",
    "SNR не вычисляется: в файле нет известного эталонного сигнала.",
    "Windows мог применить AGC, шумоподавление и ресемплирование до записи.",
    "Поддержка частот оборудованием не выводится из наличия спектральных пиков.",
]


class AnalysisError(ValueError):
    """WAV или параметры анализа нельзя использовать."""


@dataclass(frozen=True, slots=True)
class ChannelAnalysis:
    """Результаты одного канала."""

    index: int
    summary: dict[str, Any]
    frequencies: np.ndarray | None
    psd: np.ndarray | None
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.index,
            **self.summary,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class WavAnalysis:
    """Полный результат анализа файла."""

    path: Path
    sha256: str
    sample_rate: int
    channels: int
    frames: int
    duration_s: float
    data: np.ndarray
    channel_analyses: tuple[ChannelAnalysis, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "file": {
                "path": str(self.path),
                "sha256": self.sha256,
                "sample_rate_hz": self.sample_rate,
                "channels": self.channels,
                "frames": self.frames,
                "duration_s": self.duration_s,
                "sample_format": "pcm_s16le",
            },
            "channels": [channel.as_dict() for channel in self.channel_analyses],
            "warnings": list(self.warnings),
        }


def analyze_file(
    path: str | Path,
    *,
    top_peaks: int = DEFAULT_TOP_PEAKS,
    nperseg: int | None = None,
) -> WavAnalysis:
    """Прочитать WAV и посчитать метрики по каждому каналу.

    Raises:
        FileNotFoundError: файл отсутствует.
        WavFormatError: файл не PCM16 WAV, пустой или обрезанный.
        AnalysisError: запись короче 8 отсчётов или неверные параметры.
    """
    if not isinstance(top_peaks, int) or isinstance(top_peaks, bool):
        raise AnalysisError("top_peaks должен быть целым числом")
    if not 1 <= top_peaks <= 50:
        raise AnalysisError("top_peaks должен быть в диапазоне 1..50")
    if nperseg is not None and (not isinstance(nperseg, int) or nperseg < 8):
        raise AnalysisError("nperseg должен быть целым числом не меньше 8")

    source = Path(path)
    data, sample_rate, channels = read_wav(source)
    frames = int(data.shape[0])
    if frames < MIN_ANALYSIS_SAMPLES:
        raise AnalysisError(
            f"запись короче {MIN_ANALYSIS_SAMPLES} отсчётов: анализировать нечего"
        )

    analyses = tuple(
        _analyze_channel(
            data[:, index],
            sample_rate,
            index,
            top_peaks=top_peaks,
            nperseg=nperseg,
        )
        for index in range(channels)
    )

    warnings: list[str] = []
    if channels > 1:
        warnings.append(
            "каналы анализируются независимо; метрики не складываются между каналами"
        )
    if frames < MIN_NOISE_FLOOR_S * sample_rate:
        warnings.append(
            f"запись короче {MIN_NOISE_FLOOR_S * 1000:.0f} мс: шумовой пол "
            "не оценивается (нужен хотя бы один кадр 100 мс)"
        )

    return WavAnalysis(
        path=source,
        sha256=sha256_file(source),
        sample_rate=sample_rate,
        channels=channels,
        frames=frames,
        duration_s=frames / sample_rate,
        data=data,
        channel_analyses=analyses,
        warnings=tuple(warnings),
    )


def _analyze_channel(
    samples: np.ndarray,
    sample_rate: int,
    index: int,
    *,
    top_peaks: int,
    nperseg: int | None,
) -> ChannelAnalysis:
    data = np.asarray(samples, dtype=np.float64)
    warnings: list[str] = []

    if data.size >= int(round(MIN_NOISE_FLOOR_S * sample_rate)):
        ambient = audio.analyze_ambient(data, sample_rate, nperseg=nperseg)
        summary = ambient.summary
        frequencies = ambient.frequencies
        psd = ambient.psd
        summary = dict(summary)
        summary["peaks"] = summary["peaks"][:top_peaks]
        return ChannelAnalysis(
            index=index,
            summary=summary,
            frequencies=frequencies,
            psd=psd,
            warnings=(),
        )

    # Короткая запись: PSD ещё считается, шумовой пол — нет.
    frequencies, psd, segment = audio.compute_psd(data, sample_rate, nperseg=nperseg)
    rms_linear = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
    peak_linear = float(np.max(np.abs(data)))
    clipping = audio.clipping_stats(data)
    peaks = audio.spectral_peaks(frequencies, psd, count=top_peaks)
    warnings.append(
        "запись короче 100 мс: шумовой пол не оценивается"
    )
    summary = {
        "sample_rate_hz": sample_rate,
        "samples": int(data.size),
        "duration_s": float(data.size / sample_rate),
        "level": {
            "rms_linear": rms_linear,
            "rms_dbfs": _db(rms_linear),
            "peak_linear": peak_linear,
            "peak_dbfs": _db(peak_linear),
            "crest_factor_db": _db(peak_linear) - _db(rms_linear),
            "dc_offset": float(np.mean(data)),
        },
        "noise_floor": None,
        "clipping": clipping,
        "psd": {
            "method": "scipy.signal.welch",
            "window": "hann",
            "nperseg": segment,
            "resolution_hz": sample_rate / segment,
            "bins": int(frequencies.size),
            "max_frequency_hz": float(frequencies[-1]),
        },
        "bands": audio.band_levels(frequencies, psd),
        "peaks": peaks,
        "snr": None,
        "snr_note": "SNR не вычисляется без известного эталонного сигнала.",
    }
    return ChannelAnalysis(
        index=index,
        summary=summary,
        frequencies=frequencies,
        psd=psd,
        warnings=tuple(warnings),
    )


def write_csv(path: str | Path, analysis: WavAnalysis) -> Path:
    """Сохранить метрики в длинный CSV с колонкой `section`.

    Формат устойчив к разному числу пиков и полос: каждая строка — одно
    измерение, а не одна строка на канал.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "channel",
        "section",
        "name",
        "rank",
        "frequency_hz",
        "low_hz",
        "high_hz",
        "value",
        "unit",
    ]

    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for channel in analysis.channel_analyses:
            writer.writerows(_csv_rows(channel))
    return target


def _csv_rows(channel: ChannelAnalysis) -> list[list[Any]]:
    summary = channel.summary
    level = summary["level"]
    index = channel.index
    rows: list[list[Any]] = []

    def add(section: str, name: str, value: Any, unit: str, **extra: Any) -> None:
        rows.append(
            [
                index,
                section,
                name,
                extra.get("rank", ""),
                extra.get("frequency_hz", ""),
                extra.get("low_hz", ""),
                extra.get("high_hz", ""),
                "" if value is None else value,
                unit,
            ]
        )

    add("level", "rms", level["rms_dbfs"], "dBFS")
    add("level", "peak", level["peak_dbfs"], "dBFS")
    add("level", "crest_factor", level["crest_factor_db"], "dB")
    add("level", "dc_offset", level["dc_offset"], "FS")

    floor = summary.get("noise_floor")
    add("noise_floor", "percentile_rms", floor["dbfs"] if floor else None, "dBFS")

    clipping = summary["clipping"]
    add("clipping", "clipped_samples", clipping["clipped_samples"], "samples")
    add("clipping", "clipped_ratio", clipping["clipped_ratio"], "ratio")
    add("clipping", "longest_run", clipping["longest_run"], "samples")

    psd = summary.get("psd") or {}
    add("spectrum", "spectral_centroid", psd.get("spectral_centroid_hz"), "Hz")
    add("spectrum", "spectral_flatness", psd.get("spectral_flatness"), "ratio")
    add(
        "spectrum",
        "median_level",
        psd.get("median_level_dbfs_per_hz"),
        "dBFS/Hz",
    )

    for band in summary.get("bands", []):
        add(
            "band",
            band["name"],
            band.get("level_dbfs"),
            "dBFS",
            low_hz=band.get("low_hz", ""),
            high_hz=band.get("high_hz", ""),
        )
    for rank, peak in enumerate(summary.get("peaks", []), start=1):
        add(
            "peak",
            f"peak_{rank}",
            peak.get("level_dbfs_per_hz"),
            "dBFS/Hz",
            rank=rank,
            frequency_hz=peak.get("frequency_hz", ""),
        )
    return rows


def write_psd_plot(path: str | Path, analysis: WavAnalysis) -> Path:
    """Сохранить PSD всех каналов в PNG (лог. шкала частот)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    figure, axes = plt.subplots(figsize=(11, 6.5), dpi=120)
    plotted = False
    for channel in analysis.channel_analyses:
        if channel.frequencies is None or channel.psd is None:
            continue
        density = 10.0 * np.log10(np.maximum(channel.psd, 1e-30))
        label = (
            "моно" if analysis.channels == 1 else f"канал {channel.index}"
        )
        axes.semilogx(
            channel.frequencies[1:],
            density[1:],
            linewidth=0.8,
            label=label,
        )
        plotted = True
    if not plotted:  # pragma: no cover - защита от пустого графика
        raise AnalysisError("нет данных PSD для графика")

    axes.set_xlabel("Частота, Гц")
    axes.set_ylabel("dBFS/Гц")
    axes.set_title(f"PSD: {analysis.path.name}")
    axes.grid(True, which="both", alpha=0.3)
    axes.legend(loc="lower left")
    figure.tight_layout()
    figure.savefig(target)
    plt.close(figure)
    return target


def write_spectrogram_plot(
    path: str | Path,
    analysis: WavAnalysis,
    *,
    channel: int = 0,
    nperseg: int | None = None,
) -> Path:
    """Сохранить спектрограмму выбранного канала в PNG."""
    if not 0 <= channel < analysis.channels:
        raise AnalysisError(
            f"канал {channel} вне диапазона 0..{analysis.channels - 1}"
        )
    if nperseg is not None and (
        not isinstance(nperseg, int) or isinstance(nperseg, bool) or nperseg < 8
    ):
        raise AnalysisError("nperseg должен быть целым числом не меньше 8")
    if analysis.frames < MIN_SPECTROGRAM_SAMPLES:
        raise AnalysisError(
            f"для спектрограммы нужно не меньше {MIN_SPECTROGRAM_SAMPLES} отсчётов"
        )

    from scipy.signal import spectrogram

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = np.asarray(analysis.data[:, channel], dtype=np.float64)
    segment = min(nperseg or 1_024, data.size)
    frequencies, times, power = spectrogram(
        data,
        fs=analysis.sample_rate,
        window="hann",
        nperseg=segment,
        noverlap=segment // 2,
        detrend="constant",
        scaling="density",
        mode="psd",
    )
    decibels = 10.0 * np.log10(np.maximum(power, 1e-30))

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(figsize=(11, 6.5), dpi=120)
    mesh = axes.pcolormesh(times, frequencies, decibels, shading="auto")
    figure.colorbar(mesh, ax=axes, label="dBFS/Гц")
    axes.set_xlabel("Время, с")
    axes.set_ylabel("Частота, Гц")
    axes.set_ylim(0.0, min(analysis.sample_rate / 2.0, 24_000.0))
    axes.set_title(f"Спектрограмма: {analysis.path.name}")
    figure.tight_layout()
    figure.savefig(target)
    plt.close(figure)
    return target


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _db(value: float) -> float:
    if value <= 0.0 or not math.isfinite(value):
        return DB_FLOOR
    return float(20.0 * math.log10(value))
