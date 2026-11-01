"""Сравнение переданного и принятого тестовых WAV (SN-014, часть D).

Модуль отвечает на вопрос «какие частоты проходят через тракт хорошо, а какие
плохо» по двум файлам:

* WAV, который был подан на динамик (transmitted / TX);
* WAV, записанный микрофоном (received / RX).

Методика единая для обеих сторон: `snlab.calibration.analyze_capture` находит
каждый тон плана, измеряет его уровень, SNR и фактическую частоту. Затем
сравниваются уровни (ослабление) и частоты (сдвиг). Это позволяет не зависеть
от абсолютной калибровки микрофона: ослабление считается как разность двух
измерений по одной и той же методике.

Границы:

* ослабление включает громкость воспроизведения, усиление записи, обработку
  Windows и акустический тракт; это не «чувствительность микрофона» сама по
  себе;
* модуль не открывает устройства и не воспроизводит звук;
* результат относится только к данным файлам и не доказывает пригодность
  оборудования для BFSK/CPFSK;
* порог ослабления — настраиваемая эвристика, а не паспортная характеристика.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from snlab import calibration
from snlab.wavio import read_wav

DEFAULT_MIN_RX_SNR_DB = calibration.DEFAULT_MIN_SNR_DB
DEFAULT_MAX_ATTENUATION_DB = 50.0
DEFAULT_MAX_SHIFT_HZ = calibration.DEFAULT_MAX_OFFSET_HZ
DEFAULT_CHANNEL = 0

DB_FLOOR = calibration.DB_FLOOR

LIMITATIONS = [
    "Сравнение WAV не открывает устройства и не доказывает работу тракта.",
    "Ослабление включает громкость динамика, усиление записи и обработку Windows.",
    "Порог ослабления — эвристика для поиска плохих частот, а не паспорт.",
    "Сдвиг частоты измеряется по спектру; он не равен доплеровскому сдвигу.",
    "Успешное сравнение файлов не подтверждает передачу данных или BER.",
]


class ResponseError(ValueError):
    """Пару WAV или параметры сравнения нельзя использовать."""


@dataclass(frozen=True, slots=True)
class ResponsePoint:
    """Результат сравнения одной тон-точки."""

    index: int
    frequency_hz: float
    tx_detected: bool
    tx_level_dbfs: float | None
    tx_snr_db: float | None
    tx_frequency_hz: float | None
    rx_detected: bool
    rx_level_dbfs: float | None
    rx_snr_db: float | None
    rx_frequency_hz: float | None
    attenuation_db: float | None
    frequency_shift_hz: float | None
    supported: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "frequency_hz": self.frequency_hz,
            "tx": {
                "detected": self.tx_detected,
                "level_dbfs": self.tx_level_dbfs,
                "snr_db": self.tx_snr_db,
                "measured_frequency_hz": self.tx_frequency_hz,
            },
            "rx": {
                "detected": self.rx_detected,
                "level_dbfs": self.rx_level_dbfs,
                "snr_db": self.rx_snr_db,
                "measured_frequency_hz": self.rx_frequency_hz,
            },
            "attenuation_db": self.attenuation_db,
            "frequency_shift_hz": self.frequency_shift_hz,
            "supported": self.supported,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ResponseAnalysis:
    """Полный результат сравнения TX/RX."""

    plan: calibration.CalibrationPlan
    points: tuple[ResponsePoint, ...]
    tx: dict[str, Any]
    rx: dict[str, Any]
    verdict: str
    required_supported: bool
    supported_frequencies_hz: tuple[float, ...]
    unsupported_frequencies_hz: tuple[float, ...]
    warnings: tuple[str, ...]
    min_rx_snr_db: float
    max_attenuation_db: float
    max_shift_hz: float
    channel: int = DEFAULT_CHANNEL

    def summary_dict(self) -> dict[str, Any]:
        measured = [
            point.attenuation_db
            for point in self.points
            if point.attenuation_db is not None
        ]
        return {
            "verdict": self.verdict,
            "required_supported": self.required_supported,
            "required_frequencies_hz": list(self.plan.required_frequencies_hz),
            "supported_frequencies_hz": list(self.supported_frequencies_hz),
            "unsupported_frequencies_hz": list(self.unsupported_frequencies_hz),
            "tones_total": len(self.points),
            "tones_supported": len(self.supported_frequencies_hz),
            "min_rx_snr_db": self.min_rx_snr_db,
            "max_attenuation_db": self.max_attenuation_db,
            "max_shift_hz": self.max_shift_hz,
            "attenuation_db_min": min(measured) if measured else None,
            "attenuation_db_max": max(measured) if measured else None,
            "warnings": list(self.warnings),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary_dict(),
            "tx": self.tx,
            "rx": self.rx,
            "points": [point.as_dict() for point in self.points],
        }


def analyze_response(
    tx_samples: np.ndarray,
    tx_sample_rate: int,
    rx_samples: np.ndarray,
    rx_sample_rate: int,
    plan: calibration.CalibrationPlan,
    *,
    min_rx_snr_db: float = DEFAULT_MIN_RX_SNR_DB,
    max_attenuation_db: float = DEFAULT_MAX_ATTENUATION_DB,
    max_shift_hz: float = DEFAULT_MAX_SHIFT_HZ,
) -> ResponseAnalysis:
    """Сравнить переданный и принятый сигналы по одному плану.

    Оба сигнала должны быть одноканальными (или уже сведёнными к одному
    каналу) и иметь частоту дискретизации плана. Тон считается прошедшим,
    если он найден в TX, найден в RX с достаточным SNR и без клиппинга,
    ослабление не больше `max_attenuation_db`, а сдвиг частоты не больше
    `max_shift_hz`.
    """
    if not isinstance(plan, calibration.CalibrationPlan):
        raise ResponseError("ожидается CalibrationPlan")
    for name, value in (
        ("max_attenuation_db", max_attenuation_db),
        ("max_shift_hz", max_shift_hz),
        ("min_rx_snr_db", min_rx_snr_db),
    ):
        if not math.isfinite(value):
            raise ResponseError(f"{name} должен быть конечным числом")
    if max_attenuation_db <= 0.0:
        raise ResponseError("max_attenuation_db должен быть положительным")
    if max_shift_hz <= 0.0:
        raise ResponseError("max_shift_hz должен быть положительным")

    tx_data = _validate_signal(tx_samples, "переданный сигнал")
    rx_data = _validate_signal(rx_samples, "принятый сигнал")
    _check_rate(tx_sample_rate, plan, "переданного")
    _check_rate(rx_sample_rate, plan, "принятого")

    tx_analysis = calibration.analyze_capture(
        tx_data,
        tx_sample_rate,
        plan,
        min_snr_db=min_rx_snr_db,
        max_offset_hz=max_shift_hz,
    )
    rx_analysis = calibration.analyze_capture(
        rx_data,
        rx_sample_rate,
        plan,
        min_snr_db=min_rx_snr_db,
        max_offset_hz=max_shift_hz,
    )

    points = tuple(
        _compare_point(
            tx_analysis.tones[index],
            rx_analysis.tones[index],
            min_rx_snr_db=min_rx_snr_db,
            max_attenuation_db=max_attenuation_db,
            max_shift_hz=max_shift_hz,
        )
        for index in range(len(plan.frequencies_hz))
    )

    supported = tuple(point.frequency_hz for point in points if point.supported)
    unsupported = tuple(point.frequency_hz for point in points if not point.supported)
    required_supported = all(
        frequency in supported for frequency in plan.required_frequencies_hz
    )
    verdict = "PASS" if required_supported else "FAIL"

    warnings = _build_warnings(
        points=points,
        tx_analysis=tx_analysis,
        rx_analysis=rx_analysis,
        plan=plan,
        tx_samples=int(tx_data.size),
        rx_samples=int(rx_data.size),
        min_rx_snr_db=min_rx_snr_db,
        max_attenuation_db=max_attenuation_db,
        max_shift_hz=max_shift_hz,
    )

    return ResponseAnalysis(
        plan=plan,
        points=points,
        tx=_side_dict("tx", tx_sample_rate, tx_data, tx_analysis),
        rx=_side_dict("rx", rx_sample_rate, rx_data, rx_analysis),
        verdict=verdict,
        required_supported=required_supported,
        supported_frequencies_hz=supported,
        unsupported_frequencies_hz=unsupported,
        warnings=warnings,
        min_rx_snr_db=min_rx_snr_db,
        max_attenuation_db=max_attenuation_db,
        max_shift_hz=max_shift_hz,
        channel=DEFAULT_CHANNEL,
    )


def analyze_files(
    tx_path: str | Path,
    rx_path: str | Path,
    plan: calibration.CalibrationPlan,
    *,
    channel: int = DEFAULT_CHANNEL,
    min_rx_snr_db: float = DEFAULT_MIN_RX_SNR_DB,
    max_attenuation_db: float = DEFAULT_MAX_ATTENUATION_DB,
    max_shift_hz: float = DEFAULT_MAX_SHIFT_HZ,
) -> ResponseAnalysis:
    """Прочитать два WAV и сравнить их по плану.

    Для многоканальных файлов берётся канал `channel` (по умолчанию первый);
    остальные каналы не смешиваются, чтобы не размывать частоты.
    """
    if not isinstance(channel, int) or isinstance(channel, bool) or channel < 0:
        raise ResponseError("channel должен быть неотрицательным целым числом")

    tx_data, tx_rate, tx_channels = read_wav(
        tx_path,
        expected_sample_rate=plan.sample_rate,
    )
    rx_data, rx_rate, rx_channels = read_wav(
        rx_path,
        expected_sample_rate=plan.sample_rate,
    )
    if channel >= tx_channels:
        raise ResponseError(
            f"канал {channel} отсутствует в переданном WAV "
            f"(каналов: {tx_channels})"
        )
    if channel >= rx_channels:
        raise ResponseError(
            f"канал {channel} отсутствует в принятом WAV (каналов: {rx_channels})"
        )

    analysis = analyze_response(
        tx_data[:, channel],
        tx_rate,
        rx_data[:, channel],
        rx_rate,
        plan,
        min_rx_snr_db=min_rx_snr_db,
        max_attenuation_db=max_attenuation_db,
        max_shift_hz=max_shift_hz,
    )

    tx = {**analysis.tx, "path": str(Path(tx_path)), "channels": tx_channels}
    rx = {**analysis.rx, "path": str(Path(rx_path)), "channels": rx_channels}
    warnings = list(analysis.warnings)
    if tx_channels > 1 or rx_channels > 1:
        warnings.append(
            f"использован канал {channel}; остальные каналы не анализировались"
        )
    return ResponseAnalysis(
        plan=analysis.plan,
        points=analysis.points,
        tx=tx,
        rx=rx,
        verdict=analysis.verdict,
        required_supported=analysis.required_supported,
        supported_frequencies_hz=analysis.supported_frequencies_hz,
        unsupported_frequencies_hz=analysis.unsupported_frequencies_hz,
        warnings=tuple(warnings),
        min_rx_snr_db=analysis.min_rx_snr_db,
        max_attenuation_db=analysis.max_attenuation_db,
        max_shift_hz=analysis.max_shift_hz,
        channel=channel,
    )


def _compare_point(
    tx_tone: calibration.ToneMeasurement,
    rx_tone: calibration.ToneMeasurement,
    *,
    min_rx_snr_db: float,
    max_attenuation_db: float,
    max_shift_hz: float,
) -> ResponsePoint:
    attenuation: float | None = None
    if tx_tone.level_dbfs is not None and rx_tone.level_dbfs is not None:
        attenuation = float(tx_tone.level_dbfs - rx_tone.level_dbfs)

    shift: float | None = None
    if (
        tx_tone.dominant_frequency_hz is not None
        and rx_tone.dominant_frequency_hz is not None
    ):
        shift = float(rx_tone.dominant_frequency_hz - tx_tone.dominant_frequency_hz)

    if not tx_tone.detected:
        reason = f"tx_{tx_tone.reason}"
        supported = False
    elif not rx_tone.detected:
        reason = f"rx_{rx_tone.reason}"
        supported = False
    elif shift is not None and abs(shift) > max_shift_hz:
        reason = "frequency_shift_exceeded"
        supported = False
    elif attenuation is not None and attenuation > max_attenuation_db:
        reason = "attenuation_exceeded"
        supported = False
    elif not rx_tone.supported:
        reason = f"rx_{rx_tone.reason}"
        supported = False
    else:
        reason = "ok"
        supported = True

    return ResponsePoint(
        index=rx_tone.index,
        frequency_hz=rx_tone.frequency_hz,
        tx_detected=tx_tone.detected,
        tx_level_dbfs=tx_tone.level_dbfs,
        tx_snr_db=tx_tone.snr_db,
        tx_frequency_hz=tx_tone.dominant_frequency_hz,
        rx_detected=rx_tone.detected,
        rx_level_dbfs=rx_tone.level_dbfs,
        rx_snr_db=rx_tone.snr_db,
        rx_frequency_hz=rx_tone.dominant_frequency_hz,
        attenuation_db=attenuation,
        frequency_shift_hz=shift,
        supported=supported,
        reason=reason,
    )


def _build_warnings(
    *,
    points: Sequence[ResponsePoint],
    tx_analysis: calibration.CalibrationAnalysis,
    rx_analysis: calibration.CalibrationAnalysis,
    plan: calibration.CalibrationPlan,
    tx_samples: int,
    rx_samples: int,
    min_rx_snr_db: float,
    max_attenuation_db: float,
    max_shift_hz: float,
) -> tuple[str, ...]:
    warnings: list[str] = []
    warnings.extend(f"TX: {warning}" for warning in tx_analysis.warnings)
    warnings.extend(f"RX: {warning}" for warning in rx_analysis.warnings)

    if tx_samples < plan.total_samples:
        warnings.append(
            f"переданный WAV короче плана: {tx_samples} из {plan.total_samples} "
            "отсчётов; часть тонов не была передана"
        )
    if rx_samples < plan.total_samples:
        warnings.append(
            f"принятый WAV короче плана: {rx_samples} из {plan.total_samples} "
            "отсчётов; часть тонов могла не попасть в запись"
        )

    failed = [point for point in points if not point.supported]
    low_snr = [
        point.frequency_hz
        for point in points
        if point.rx_snr_db is None or point.rx_snr_db < min_rx_snr_db
    ]
    if low_snr:
        text = ", ".join(f"{value:g}" for value in low_snr)
        warnings.append(f"в RX ниже порога SNR {min_rx_snr_db:g} дБ: {text} Гц")

    too_weak = [
        point.frequency_hz
        for point in points
        if point.attenuation_db is not None
        and point.attenuation_db > max_attenuation_db
    ]
    if too_weak:
        text = ", ".join(f"{value:g}" for value in too_weak)
        warnings.append(
            f"ослабление больше {max_attenuation_db:g} дБ: {text} Гц"
        )

    shifted = [
        point.frequency_hz
        for point in points
        if point.frequency_shift_hz is not None
        and abs(point.frequency_shift_hz) > max_shift_hz
    ]
    if shifted:
        text = ", ".join(f"{value:g}" for value in shifted)
        warnings.append(f"сдвиг частоты TX→RX больше {max_shift_hz:g} Гц: {text} Гц")

    if len(failed) == len(points):
        warnings.append("ни одна тон-точка не прошла сравнение")
    return tuple(warnings)


def _side_dict(
    name: str,
    sample_rate: int,
    samples: np.ndarray,
    analysis: calibration.CalibrationAnalysis,
) -> dict[str, Any]:
    summary = analysis.summary_dict()
    return {
        "role": name,
        "sample_rate_hz": sample_rate,
        "channels": 1,
        "frames": int(samples.size),
        "duration_s": float(samples.size / sample_rate),
        "level": summary["level"],
        "noise_floor": summary["noise_floor"],
        "clipping": summary["clipping"],
    }


def load_plan_json(path: str | Path) -> calibration.CalibrationPlan:
    """Загрузить план из JSON, сохранённого `tonegen.py`.

    Принимается как полный отчёт генератора (ключ `plan`), так и сам объект
    плана. Отсутствующие необязательные поля заполняются значениями по
    умолчанию; обязательные частоты по умолчанию — все частоты плана.
    """
    import json

    source = Path(path)
    if not source.is_file():
        raise ResponseError(f"JSON-план не найден: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResponseError(f"не удалось прочитать JSON-план {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResponseError("JSON-план должен быть объектом")
    plan_payload = payload.get("plan", payload)
    if not isinstance(plan_payload, dict):
        raise ResponseError("поле 'plan' должно быть объектом")

    frequencies = plan_payload.get("frequencies_hz")
    if not isinstance(frequencies, list) or not frequencies:
        raise ResponseError("в плане отсутствует непустой список frequencies_hz")
    required = plan_payload.get("required_frequencies_hz", frequencies)
    if not isinstance(required, list) or not required:
        raise ResponseError("required_frequencies_hz должен быть непустым списком")

    sample_rate = plan_payload.get("sample_rate_hz", plan_payload.get("sample_rate"))
    if sample_rate is None:
        raise ResponseError("в плане отсутствует sample_rate_hz")
    try:
        return calibration.CalibrationPlan(
            sample_rate=int(sample_rate),
            frequencies_hz=tuple(float(value) for value in frequencies),
            required_frequencies_hz=tuple(float(value) for value in required),
            tone_ms=float(plan_payload.get("tone_ms", calibration.DEFAULT_TONE_MS)),
            gap_ms=float(plan_payload.get("gap_ms", calibration.DEFAULT_GAP_MS)),
            lead_ms=float(plan_payload.get("lead_ms", calibration.DEFAULT_LEAD_MS)),
            tail_ms=float(plan_payload.get("tail_ms", calibration.DEFAULT_TAIL_MS)),
            amplitude=float(
                plan_payload.get("amplitude", calibration.DEFAULT_AMPLITUDE)
            ),
            ramp_ms=float(plan_payload.get("ramp_ms", calibration.DEFAULT_RAMP_MS)),
        )
    except (TypeError, ValueError, calibration.CalibrationError) as exc:
        raise ResponseError(f"некорректный план: {exc}") from exc


def write_csv(path: str | Path, analysis: ResponseAnalysis) -> Path:
    """Сохранить сравнение точек в CSV (одна строка на частоту)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "index",
        "frequency_hz",
        "tx_detected",
        "tx_level_dbfs",
        "tx_snr_db",
        "tx_frequency_hz",
        "rx_detected",
        "rx_level_dbfs",
        "rx_snr_db",
        "rx_frequency_hz",
        "attenuation_db",
        "frequency_shift_hz",
        "supported",
        "reason",
    ]
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for point in analysis.points:
            writer.writerow(
                [
                    point.index,
                    point.frequency_hz,
                    int(point.tx_detected),
                    _cell(point.tx_level_dbfs),
                    _cell(point.tx_snr_db),
                    _cell(point.tx_frequency_hz),
                    int(point.rx_detected),
                    _cell(point.rx_level_dbfs),
                    _cell(point.rx_snr_db),
                    _cell(point.rx_frequency_hz),
                    _cell(point.attenuation_db),
                    _cell(point.frequency_shift_hz),
                    int(point.supported),
                    point.reason,
                ]
            )
    return target


def write_plot(path: str | Path, analysis: ResponseAnalysis) -> Path:
    """Сохранить график ослабления и SNR от частоты."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    frequencies = [point.frequency_hz for point in analysis.points]
    attenuation = [
        point.attenuation_db if point.attenuation_db is not None else math.nan
        for point in analysis.points
    ]
    rx_snr = [
        point.rx_snr_db if point.rx_snr_db is not None else math.nan
        for point in analysis.points
    ]

    figure, axes = plt.subplots(figsize=(11, 6.5), dpi=120)
    axes.plot(frequencies, attenuation, "o-", linewidth=1.0, label="ослабление")
    axes.axhline(
        analysis.max_attenuation_db,
        linestyle="--",
        linewidth=0.9,
        color="tab:red",
        label=f"порог ослабления {analysis.max_attenuation_db:g} дБ",
    )
    axes.set_xlabel("Частота, Гц")
    axes.set_ylabel("Ослабление TX→RX, дБ")
    axes.set_title(
        f"Частотная характеристика: {analysis.verdict} "
        f"({len(analysis.supported_frequencies_hz)}/{len(analysis.points)} точек)"
    )
    axes.grid(True, which="both", alpha=0.3)
    axes.legend(loc="upper left")
    if len(frequencies) >= 2:
        axes.set_xscale("log")
        axes.set_xlim(min(frequencies) * 0.9, max(frequencies) * 1.1)

    snr_axes = axes.twinx()
    snr_axes.plot(
        frequencies,
        rx_snr,
        "s--",
        markersize=3,
        linewidth=0.8,
        color="tab:green",
        label="SNR в RX",
    )
    snr_axes.set_ylabel("SNR в RX, дБ")
    snr_axes.legend(loc="upper right")

    figure.tight_layout()
    figure.savefig(target)
    plt.close(figure)
    return target


def _validate_signal(samples: np.ndarray, label: str) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 1:
        raise ResponseError(f"{label} должен быть одноканальным")
    if data.size == 0:
        raise ResponseError(f"{label} пуст")
    if not np.all(np.isfinite(data)):
        raise ResponseError(f"{label} содержит NaN или бесконечность")
    return data


def _check_rate(sample_rate: int, plan: calibration.CalibrationPlan, label: str) -> None:
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool):
        raise ResponseError(f"частота дискретизации {label} WAV должна быть целой")
    if sample_rate != plan.sample_rate:
        raise ResponseError(
            f"частота дискретизации {label} WAV {sample_rate} Гц не совпадает "
            f"с планом {plan.sample_rate} Гц"
        )


def _cell(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


if __name__ == "__main__":  # pragma: no cover - модуль не является CLI
    raise SystemExit("используйте response.py")
