"""Калибровка акустического тракта: тон-сетка → динамик → микрофон (SN-012).

Модуль отвечает на измеримые вопросы этапа Hardware Calibration:

* какие частоты реально проходят от динамика к микрофону;
* какой уровень принимается на каждой частоте;
* насколько фактическая частота отличается от переданной;
* есть ли клиппинг и насколько уровень выше шумового пола.

Методика намеренно простая и воспроизводимая: передаётся набор синусоид с
известными частотами, каждая в своём временном окне. Для каждой тон-точки
измеряются мощность тона, мощность остатка (шум) и частота максимума спектра.

Границы:

* это не передача данных: успешный PASS означает только обнаружение тонов;
* измеряется один тракт в одном положении, результат не переносится на другое
  оборудование и помещение;
* Windows может применять AGC, шумоподавление и «улучшения» — они влияют на
  уровень и АЧХ и не отключаются этим инструментом;
* задержка, реверберация и многолучёвость здесь не измеряются.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from snlab import audio
from snlab.bfsk import apply_ramp

DB_FLOOR = -200.0
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 192_000
MIN_FREQ_HZ = 100.0
MAX_TONE_MS = 5_000.0
MAX_GAP_MS = 5_000.0
MAX_PADDING_MS = 5_000.0
MAX_AMPLITUDE = 1.0
MAX_RAMP_MS = 500.0

#: Тон-сетка по умолчанию: низкочастотные якоря и весь исследуемый диапазон
#: 16–22 кГц с шагом 500 Гц около верхней границы. 18 и 19 кГц — текущие
#: mark/space профиля bfsk-18k, поэтому они обязательны для PASS.
DEFAULT_FREQUENCIES_HZ: tuple[float, ...] = (
    500.0,
    1_000.0,
    2_000.0,
    4_000.0,
    8_000.0,
    12_000.0,
    14_000.0,
    16_000.0,
    17_000.0,
    18_000.0,
    18_500.0,
    19_000.0,
    19_500.0,
    20_000.0,
    20_500.0,
    21_000.0,
    21_500.0,
    22_000.0,
)
REQUIRED_FREQUENCIES_HZ: tuple[float, ...] = (18_000.0, 19_000.0)

DEFAULT_TONE_MS = 800.0
DEFAULT_GAP_MS = 400.0
DEFAULT_LEAD_MS = 800.0
DEFAULT_TAIL_MS = 800.0
DEFAULT_AMPLITUDE = 0.5
DEFAULT_RAMP_MS = 10.0
DEFAULT_MIN_SNR_DB = 10.0
DEFAULT_MAX_OFFSET_HZ = 50.0

#: Окно, по которому измеряются мощность тона и остаток. Полсекунды при
#: 48 кГц дают разрешение 2 Гц — достаточно, чтобы отличить 18.0 от 18.5 кГц.
MEASUREMENT_WINDOW_S = 0.5
#: Минимальная длительность окна; более короткие тоны анализируются целиком.
MIN_MEASUREMENT_S = 0.05
#: Допустимый поиск частотного максимума вокруг ожидаемой частоты.
PEAK_SEARCH_HZ = 300.0
#: Минимальный запас поиска по времени, если пауза между тонами короткая.
MIN_SEARCH_MARGIN_S = 0.25
#: Уровень ниже этого считается подозрительно низким для калибровки.
LOW_LEVEL_WARNING_DBFS = -60.0


class CalibrationError(ValueError):
    """Параметры калибровки или запись нельзя использовать."""


class PlaybackError(RuntimeError):
    """Не удалось воспроизвести сигнал и/или записать его с микрофона."""


@dataclass(frozen=True, slots=True)
class CalibrationPlan:
    """План тон-сетки: какие частоты, как долго и с какой амплитудой.

    Все временные параметры заданы в миллисекундах, границы тонов вычисляются
    в отсчётах. Паузы есть только между тонами: `tail_ms` — тишина после
    последнего тона, `lead_ms` — тишина до первого.
    """

    sample_rate: int = 48_000
    frequencies_hz: tuple[float, ...] = DEFAULT_FREQUENCIES_HZ
    required_frequencies_hz: tuple[float, ...] = REQUIRED_FREQUENCIES_HZ
    tone_ms: float = DEFAULT_TONE_MS
    gap_ms: float = DEFAULT_GAP_MS
    lead_ms: float = DEFAULT_LEAD_MS
    tail_ms: float = DEFAULT_TAIL_MS
    amplitude: float = DEFAULT_AMPLITUDE
    ramp_ms: float = DEFAULT_RAMP_MS

    def __post_init__(self) -> None:
        if not isinstance(self.sample_rate, int) or isinstance(self.sample_rate, bool):
            raise CalibrationError("sample_rate должен быть целым числом")
        if not MIN_SAMPLE_RATE <= self.sample_rate <= MAX_SAMPLE_RATE:
            raise CalibrationError(
                f"sample_rate должен быть в диапазоне "
                f"{MIN_SAMPLE_RATE}..{MAX_SAMPLE_RATE} Гц"
            )

        frequencies = tuple(float(value) for value in self.frequencies_hz)
        if not frequencies:
            raise CalibrationError("список частот не может быть пустым")
        nyquist = self.sample_rate / 2.0
        previous = 0.0
        for value in frequencies:
            if not math.isfinite(value):
                raise CalibrationError("частоты должны быть конечными числами")
            if not MIN_FREQ_HZ <= value < nyquist:
                raise CalibrationError(
                    f"частота {value:g} Гц должна быть в диапазоне "
                    f"{MIN_FREQ_HZ:g}..{nyquist:g} Гц (ниже Найквиста)"
                )
            if value <= previous:
                raise CalibrationError(
                    "частоты должны быть строго возрастающими без повторов"
                )
            previous = value
        object.__setattr__(self, "frequencies_hz", frequencies)

        required = tuple(float(value) for value in self.required_frequencies_hz)
        if not required:
            raise CalibrationError("нужно указать хотя бы одну обязательную частоту")
        missing = [value for value in required if value not in frequencies]
        if missing:
            text = ", ".join(f"{value:g}" for value in missing)
            raise CalibrationError(
                f"обязательные частоты отсутствуют в тон-сетке: {text} Гц"
            )
        object.__setattr__(self, "required_frequencies_hz", required)

        for name, value, maximum in (
            ("tone_ms", self.tone_ms, MAX_TONE_MS),
            ("gap_ms", self.gap_ms, MAX_GAP_MS),
            ("lead_ms", self.lead_ms, MAX_PADDING_MS),
            ("tail_ms", self.tail_ms, MAX_PADDING_MS),
            ("ramp_ms", self.ramp_ms, MAX_RAMP_MS),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise CalibrationError(f"{name} должен быть числом")
            if not math.isfinite(value):
                raise CalibrationError(f"{name} должен быть конечным числом")
            if not 0.0 <= value <= maximum:
                raise CalibrationError(
                    f"{name} должен быть в диапазоне 0..{maximum:g}"
                )
        if self.tone_ms <= 0.0:
            raise CalibrationError("tone_ms должен быть положительным")
        if self.tone_ms / 1000.0 < MIN_MEASUREMENT_S:
            raise CalibrationError(
                f"тон короче {MIN_MEASUREMENT_S * 1000:.0f} мс: анализ невозможен"
            )

        if (
            not isinstance(self.amplitude, (int, float))
            or isinstance(self.amplitude, bool)
            or not math.isfinite(self.amplitude)
            or not 0.0 < self.amplitude <= MAX_AMPLITUDE
        ):
            raise CalibrationError(
                f"amplitude должен быть в диапазоне (0, {MAX_AMPLITUDE:g}]"
            )

    @property
    def lead_samples(self) -> int:
        return int(round(self.lead_ms / 1000.0 * self.sample_rate))

    @property
    def tail_samples(self) -> int:
        return int(round(self.tail_ms / 1000.0 * self.sample_rate))

    @property
    def tone_samples(self) -> int:
        return int(round(self.tone_ms / 1000.0 * self.sample_rate))

    @property
    def gap_samples(self) -> int:
        return int(round(self.gap_ms / 1000.0 * self.sample_rate))

    @property
    def ramp_samples(self) -> int:
        return int(round(self.ramp_ms / 1000.0 * self.sample_rate))

    @property
    def total_samples(self) -> int:
        count = len(self.frequencies_hz)
        return (
            self.lead_samples
            + count * self.tone_samples
            + max(0, count - 1) * self.gap_samples
            + self.tail_samples
        )

    @property
    def duration_s(self) -> float:
        return self.total_samples / self.sample_rate

    def tone_start_sample(self, index: int) -> int:
        _check_index(index, len(self.frequencies_hz))
        return self.lead_samples + index * (self.tone_samples + self.gap_samples)

    def tone_end_sample(self, index: int) -> int:
        return self.tone_start_sample(index) + self.tone_samples

    def tone_start_s(self, index: int) -> float:
        return self.tone_start_sample(index) / self.sample_rate

    def tone_end_s(self, index: int) -> float:
        return self.tone_end_sample(index) / self.sample_rate

    def tone_boundaries(self, index: int) -> tuple[int, int]:
        """Границы поиска тона: пауза до/после, но не соседние тоны."""
        previous_end = 0 if index == 0 else self.tone_end_sample(index - 1)
        next_start = (
            self.total_samples
            if index == len(self.frequencies_hz) - 1
            else self.tone_start_sample(index + 1)
        )
        margin = max(self.gap_samples // 2, int(MIN_SEARCH_MARGIN_S * self.sample_rate))
        return (
            max(previous_end, self.tone_start_sample(index) - margin),
            min(next_start, self.tone_end_sample(index) + margin),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_rate_hz": self.sample_rate,
            "frequencies_hz": list(self.frequencies_hz),
            "required_frequencies_hz": list(self.required_frequencies_hz),
            "tone_ms": self.tone_ms,
            "gap_ms": self.gap_ms,
            "lead_ms": self.lead_ms,
            "tail_ms": self.tail_ms,
            "amplitude": self.amplitude,
            "ramp_ms": self.ramp_ms,
            "tone_samples": self.tone_samples,
            "gap_samples": self.gap_samples,
            "lead_samples": self.lead_samples,
            "tail_samples": self.tail_samples,
            "total_samples": self.total_samples,
            "duration_s": self.duration_s,
            "tones": len(self.frequencies_hz),
        }


@dataclass(frozen=True, slots=True)
class ToneMeasurement:
    """Результат одной тон-точки."""

    index: int
    frequency_hz: float
    expected_start_s: float
    expected_end_s: float
    search_start_s: float
    search_end_s: float
    measured_start_s: float | None
    measured_end_s: float | None
    detected: bool
    supported: bool
    level_dbfs: float | None
    noise_dbfs: float | None
    snr_db: float | None
    dominant_frequency_hz: float | None
    frequency_offset_hz: float | None
    clipped_samples: int
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "frequency_hz": self.frequency_hz,
            "expected_start_s": self.expected_start_s,
            "expected_end_s": self.expected_end_s,
            "search_start_s": self.search_start_s,
            "search_end_s": self.search_end_s,
            "measured_start_s": self.measured_start_s,
            "measured_end_s": self.measured_end_s,
            "detected": self.detected,
            "supported": self.supported,
            "level_dbfs": self.level_dbfs,
            "noise_dbfs": self.noise_dbfs,
            "snr_db": self.snr_db,
            "dominant_frequency_hz": self.dominant_frequency_hz,
            "frequency_offset_hz": self.frequency_offset_hz,
            "clipped_samples": self.clipped_samples,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class CalibrationAnalysis:
    """Полный результат анализа записи калибровки."""

    plan: CalibrationPlan
    tones: tuple[ToneMeasurement, ...]
    ambient: audio.AmbientAnalysis
    verdict: str
    required_supported: bool
    supported_frequencies_hz: tuple[float, ...]
    unsupported_frequencies_hz: tuple[float, ...]
    warnings: tuple[str, ...]
    min_snr_db: float
    max_offset_hz: float
    capture_samples: int
    capture_duration_s: float

    def summary_dict(self) -> dict[str, Any]:
        ambient_summary = self.ambient.summary
        return {
            "verdict": self.verdict,
            "required_supported": self.required_supported,
            "required_frequencies_hz": list(self.plan.required_frequencies_hz),
            "supported_frequencies_hz": list(self.supported_frequencies_hz),
            "unsupported_frequencies_hz": list(self.unsupported_frequencies_hz),
            "tones_total": len(self.tones),
            "tones_detected": sum(1 for tone in self.tones if tone.detected),
            "tones_supported": len(self.supported_frequencies_hz),
            "min_snr_db": self.min_snr_db,
            "max_offset_hz": self.max_offset_hz,
            "capture_samples": self.capture_samples,
            "capture_duration_s": self.capture_duration_s,
            "level": ambient_summary["level"],
            "noise_floor": ambient_summary["noise_floor"],
            "clipping": ambient_summary["clipping"],
            "warnings": list(self.warnings),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary_dict(),
            "tones": [tone.as_dict() for tone in self.tones],
            "bands": self.ambient.summary["bands"],
            "peaks": self.ambient.summary["peaks"],
            "psd": self.ambient.summary["psd"],
        }


def generate_signal(plan: CalibrationPlan) -> np.ndarray:
    """Построить тон-сетку с паузами и рампами.

    Число отсчётов всегда равно `plan.total_samples`, повторный вызов даёт
    побитово тот же массив. Рампа применяется к каждому тону отдельно, чтобы
    не было щелчков на границах; тишина остаётся точными нулями.
    """
    if not isinstance(plan, CalibrationPlan):
        raise CalibrationError("ожидается CalibrationPlan")

    samples = np.zeros(plan.total_samples, dtype=np.float64)
    for index, frequency in enumerate(plan.frequencies_hz):
        start = plan.tone_start_sample(index)
        stop = plan.tone_end_sample(index)
        time_axis = np.arange(stop - start, dtype=np.float64) / plan.sample_rate
        tone = plan.amplitude * np.sin(2.0 * math.pi * frequency * time_axis)
        samples[start:stop] = apply_ramp(tone, plan.ramp_samples)
    return samples


def analyze_capture(
    samples: np.ndarray,
    sample_rate: int,
    plan: CalibrationPlan,
    *,
    min_snr_db: float = DEFAULT_MIN_SNR_DB,
    max_offset_hz: float = DEFAULT_MAX_OFFSET_HZ,
) -> CalibrationAnalysis:
    """Найти тоны в записи и измерить уровень, SNR и смещение частоты.

    Для каждой тон-точки:

    1. По огибающей узкополосного сигнала на ожидаемой частоте выбирается
       временное окно с максимумом энергии (компенсирует задержку тракта).
    2. В окне ищется максимум спектра около ожидаемой частоты; положение
       уточняется параболической интерполяцией.
    3. Найденная частота используется для МНК-подгонки синуса. Мощность тона —
       `(a² + b²) / 2`, мощность шума — средний квадрат остатка.
    4. `SNR_dB = 10·log10(P_tone / P_noise)`; уровень —
       `10·log10(P_tone)` в dBFS.

    Тон считается обнаруженным при `SNR >= min_snr_db`, а поддерживаемым —
    если вдобавок `|f_found - f_expected| <= max_offset_hz` и в окне нет
    клиппинга. Порог не подбирается под результат: он задаётся заранее.
    """
    data = _validate_capture(samples)
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool):
        raise CalibrationError("sample_rate должен быть целым числом")
    if sample_rate != plan.sample_rate:
        raise CalibrationError(
            f"частота записи {sample_rate} Гц не совпадает с планом "
            f"{plan.sample_rate} Гц"
        )
    if not math.isfinite(min_snr_db):
        raise CalibrationError("min_snr_db должен быть конечным числом")
    if not math.isfinite(max_offset_hz) or max_offset_hz <= 0.0:
        raise CalibrationError("max_offset_hz должен быть положительным числом")

    ambient = audio.analyze_ambient(data, sample_rate)
    tones = tuple(
        _measure_tone(data, sample_rate, plan, index, min_snr_db, max_offset_hz)
        for index in range(len(plan.frequencies_hz))
    )

    supported = tuple(
        tone.frequency_hz for tone in tones if tone.supported
    )
    unsupported = tuple(
        tone.frequency_hz for tone in tones if not tone.supported
    )
    required_supported = all(
        frequency in supported for frequency in plan.required_frequencies_hz
    )
    verdict = "PASS" if required_supported else "FAIL"
    warnings = _build_warnings(
        tones=tones,
        ambient=ambient,
        plan=plan,
        capture_samples=int(data.size),
        min_snr_db=min_snr_db,
        max_offset_hz=max_offset_hz,
    )

    return CalibrationAnalysis(
        plan=plan,
        tones=tones,
        ambient=ambient,
        verdict=verdict,
        required_supported=required_supported,
        supported_frequencies_hz=supported,
        unsupported_frequencies_hz=unsupported,
        warnings=warnings,
        min_snr_db=min_snr_db,
        max_offset_hz=max_offset_hz,
        capture_samples=int(data.size),
        capture_duration_s=float(data.size / sample_rate),
    )


def _measure_tone(
    samples: np.ndarray,
    sample_rate: int,
    plan: CalibrationPlan,
    index: int,
    min_snr_db: float,
    max_offset_hz: float,
) -> ToneMeasurement:
    frequency = plan.frequencies_hz[index]
    expected_start = plan.tone_start_s(index)
    expected_end = plan.tone_end_s(index)
    search_start_sample, search_end_sample = plan.tone_boundaries(index)
    search_start_s = search_start_sample / sample_rate
    search_end_s = search_end_sample / sample_rate

    base = dict(
        index=index,
        frequency_hz=frequency,
        expected_start_s=expected_start,
        expected_end_s=expected_end,
        search_start_s=search_start_s,
        search_end_s=search_end_s,
    )

    if search_end_sample - search_start_sample < int(MIN_MEASUREMENT_S * sample_rate):
        return ToneMeasurement(
            **base,
            measured_start_s=None,
            measured_end_s=None,
            detected=False,
            supported=False,
            level_dbfs=None,
            noise_dbfs=None,
            snr_db=None,
            dominant_frequency_hz=None,
            frequency_offset_hz=None,
            clipped_samples=0,
            reason="capture_too_short",
        )

    region = samples[search_start_sample:search_end_sample]
    center = _best_window_center(region, sample_rate, frequency)
    measure_samples = min(
        int(round(MEASUREMENT_WINDOW_S * sample_rate)),
        plan.tone_samples,
    )
    measure_samples = max(
        measure_samples,
        int(round(MIN_MEASUREMENT_S * sample_rate)),
    )
    measure_start = search_start_sample + center - measure_samples // 2
    measure_start = max(search_start_sample, measure_start)
    measure_start = min(measure_start, search_end_sample - measure_samples)
    measure_end = measure_start + measure_samples
    segment = samples[measure_start:measure_end]

    if segment.size < int(MIN_MEASUREMENT_S * sample_rate):
        return ToneMeasurement(
            **base,
            measured_start_s=measure_start / sample_rate,
            measured_end_s=measure_end / sample_rate,
            detected=False,
            supported=False,
            level_dbfs=None,
            noise_dbfs=None,
            snr_db=None,
            dominant_frequency_hz=None,
            frequency_offset_hz=None,
            clipped_samples=0,
            reason="window_too_short",
        )

    dominant = _dominant_frequency(segment, sample_rate, frequency)
    offset = dominant - frequency

    # Подгонка синуса на найденной частоте. Края окна отбрасываются, чтобы
    # рампа тона и переходные процессы не завышали остаток.
    flat = _trim_edges(segment, 0.1)
    time_axis = np.arange(flat.size, dtype=np.float64) / sample_rate
    angle = 2.0 * math.pi * dominant * time_axis
    centered = flat - float(np.mean(flat))
    cosine = np.cos(angle)
    sine = np.sin(angle)
    a = 2.0 * float(np.dot(centered, cosine)) / flat.size
    b = 2.0 * float(np.dot(centered, sine)) / flat.size
    fitted = a * cosine + b * sine
    residual = centered - fitted

    tone_power = 0.5 * (a * a + b * b)
    noise_power = float(np.mean(np.square(residual, dtype=np.float64)))
    snr_db = _ratio_db(tone_power, noise_power)
    level_dbfs = _power_db(tone_power)
    noise_dbfs = _power_db(noise_power)
    clipped = int(audio.clipping_stats(segment)["clipped_samples"])

    detected = snr_db >= min_snr_db and tone_power > 0.0
    if not detected:
        reason = "snr_below_threshold"
    elif abs(offset) > max_offset_hz:
        reason = "frequency_offset"
    elif clipped > 0:
        reason = "clipping"
    else:
        reason = "ok"
    supported = reason == "ok"

    return ToneMeasurement(
        **base,
        measured_start_s=measure_start / sample_rate,
        measured_end_s=measure_end / sample_rate,
        detected=bool(detected),
        supported=bool(supported),
        level_dbfs=level_dbfs,
        noise_dbfs=noise_dbfs,
        snr_db=snr_db,
        dominant_frequency_hz=dominant,
        frequency_offset_hz=offset,
        clipped_samples=clipped,
        reason=reason,
    )


def _best_window_center(
    region: np.ndarray,
    sample_rate: int,
    frequency: float,
) -> int:
    """Центр окна 20 мс с максимальной энергией на ожидаемой частоте."""
    window = max(16, int(round(0.02 * sample_rate)))
    if region.size <= window:
        return region.size // 2

    time_axis = np.arange(region.size, dtype=np.float64)
    mixed = region * np.exp(-2j * math.pi * frequency * time_axis / sample_rate)
    cumulative = np.concatenate(
        (np.zeros(1, dtype=np.complex128), np.cumsum(mixed, dtype=np.complex128))
    )
    window_sum = cumulative[window:] - cumulative[:-window]
    energy = window_sum.real * window_sum.real + window_sum.imag * window_sum.imag

    # argmax попадает в произвольную точку плоской вершины тона. Чтобы окно
    # измерения не съезжало на паузу, берётся середина непрерывного участка
    # выше четверти максимума, содержащего максимум.
    peak = int(np.argmax(energy))
    threshold = 0.25 * float(energy[peak])
    left = peak
    while left > 0 and energy[left - 1] >= threshold:
        left -= 1
    right = peak
    while right + 1 < energy.size and energy[right + 1] >= threshold:
        right += 1
    center = (left + right) // 2 + window // 2
    return int(np.clip(center, window // 2, region.size - window // 2))


def _dominant_frequency(
    segment: np.ndarray,
    sample_rate: int,
    expected_hz: float,
) -> float:
    """Частота максимума спектра около ожидаемой с параболическим уточнением."""
    data = np.asarray(segment, dtype=np.float64)
    data = data - float(np.mean(data))
    windowed = data * np.hanning(data.size)
    spectrum = np.abs(np.fft.rfft(windowed))
    frequencies = np.fft.rfftfreq(data.size, d=1.0 / sample_rate)
    mask = np.abs(frequencies - expected_hz) <= PEAK_SEARCH_HZ
    if not np.any(mask):
        return float(expected_hz)
    indices = np.flatnonzero(mask)
    relative = int(np.argmax(spectrum[indices]))
    peak = int(indices[relative])

    magnitude = float(spectrum[peak])
    if peak <= 0 or peak >= spectrum.size - 1 or magnitude <= 0.0:
        return float(frequencies[peak])

    left = float(spectrum[peak - 1])
    right = float(spectrum[peak + 1])
    denominator = left - 2.0 * magnitude + right
    if abs(denominator) < 1e-18:
        return float(frequencies[peak])
    delta = 0.5 * (left - right) / denominator
    delta = float(np.clip(delta, -0.5, 0.5))
    return float(frequencies[peak] + delta * (frequencies[1] - frequencies[0]))


def _trim_edges(segment: np.ndarray, fraction: float) -> np.ndarray:
    trim = int(segment.size * fraction)
    if segment.size - 2 * trim < 8:
        return np.asarray(segment, dtype=np.float64)
    return np.asarray(segment[trim : segment.size - trim], dtype=np.float64)


def _build_warnings(
    *,
    tones: Sequence[ToneMeasurement],
    ambient: audio.AmbientAnalysis,
    plan: CalibrationPlan,
    capture_samples: int,
    min_snr_db: float,
    max_offset_hz: float,
) -> tuple[str, ...]:
    warnings: list[str] = []
    level = ambient.summary["level"]
    clipping = ambient.summary["clipping"]

    if capture_samples < plan.total_samples:
        warnings.append(
            f"запись короче плана: {capture_samples} из {plan.total_samples} "
            "отсчётов; часть тонов могла быть не передана"
        )
    if level["peak_linear"] == 0.0:
        warnings.append(
            "в записи цифровая тишина: проверьте, не отключён ли микрофон, "
            "усиление и разрешение на запись"
        )
    elif level["rms_dbfs"] < LOW_LEVEL_WARNING_DBFS:
        warnings.append(
            f"очень низкий уровень записи: RMS {level['rms_dbfs']:.1f} dBFS "
            f"(порог предупреждения {LOW_LEVEL_WARNING_DBFS:.0f} dBFS). "
            "Проверьте громкость динамика, усиление микрофона и обработку Windows"
        )
    if clipping["detected"]:
        warnings.append(
            f"клиппинг: {clipping['clipped_samples']} отсчётов; "
            "уровень записи или воспроизведения слишком высок"
        )

    low_snr = [
        tone.frequency_hz
        for tone in tones
        if tone.snr_db is None or tone.snr_db < min_snr_db
    ]
    if low_snr:
        text = ", ".join(f"{value:g}" for value in low_snr)
        warnings.append(f"ниже порога SNR {min_snr_db:g} дБ: {text} Гц")

    shifted = [
        tone.frequency_hz
        for tone in tones
        if tone.frequency_offset_hz is not None
        and abs(tone.frequency_offset_hz) > max_offset_hz
    ]
    if shifted:
        text = ", ".join(f"{value:g}" for value in shifted)
        warnings.append(
            f"смещение частоты больше {max_offset_hz:g} Гц: {text} Гц"
        )
    if not any(tone.supported for tone in tones):
        warnings.append("не обнаружен ни один тон: тракт не передаёт сигнал")
    return tuple(warnings)


def play_and_record(
    signal: np.ndarray,
    sample_rate: int,
    input_device: audio.DeviceInfo,
    output_device: audio.DeviceInfo,
    *,
    sd_module: Any | None = None,
    block_size: int = 4_096,
    concurrent_margin_s: float = 0.25,
) -> "PlaybackCapture":
    """Воспроизвести сигнал и одновременно записать микрофон.

    Сначала пробуется дуплексный поток (`sounddevice.playrec`), но только если
    вход и выход относятся к одному host API: PortAudio не открывает один
    дуплексный поток через разные API. Иначе используются два независимых
    потока: запись стартует раньше воспроизведения, поэтому тон-сетка
    попадает в запись с запасом.
    """
    data = np.asarray(signal, dtype=np.float32)
    if data.ndim != 1:
        raise PlaybackError("сигнал должен быть одномерным")
    if data.size == 0:
        raise PlaybackError("пустой сигнал: воспроизводить нечего")
    if not np.all(np.isfinite(data)):
        raise PlaybackError("сигнал содержит NaN или бесконечность")
    if not isinstance(sample_rate, int) or sample_rate <= 0:
        raise PlaybackError("sample_rate должен быть положительным целым числом")
    if block_size <= 0:
        raise PlaybackError("block_size должен быть положительным")
    if not isinstance(input_device, audio.DeviceInfo):
        raise PlaybackError("input_device должен быть DeviceInfo")
    if not isinstance(output_device, audio.DeviceInfo):
        raise PlaybackError("output_device должен быть DeviceInfo")

    sounddevice = audio.load_sounddevice(sd_module)
    started = time.monotonic()
    warnings: list[str] = []

    if input_device.hostapi == output_device.hostapi:
        try:
            recording = sounddevice.playrec(
                data,
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                device=(input_device.index, output_device.index),
                blocking=True,
            )
            samples = _as_mono_recording(recording)
            return PlaybackCapture(
                samples=samples,
                sample_rate=sample_rate,
                input_device_index=input_device.index,
                output_device_index=output_device.index,
                input_device_name=input_device.name,
                output_device_name=output_device.name,
                mode="duplex",
                overflows=0,
                wall_time_s=time.monotonic() - started,
                warnings=(),
            )
        except Exception as exc:  # noqa: BLE001 - PortAudio сообщает разные типы
            duplex_error = f"{type(exc).__name__}: {exc}"
            warnings.append(
                "дуплексный поток не открылся, используется раздельные "
                f"запись и воспроизведение ({duplex_error})"
            )
    else:
        duplex_error = (
            f"разные host API: вход {input_device.hostapi_name!r}, "
            f"выход {output_device.hostapi_name!r}"
        )
        warnings.append(
            "вход и выход в разных host API; используется раздельные "
            "запись и воспроизведение"
        )

    try:
        samples, overflows = _play_record_concurrent(
            sounddevice,
            data,
            sample_rate,
            input_device,
            output_device,
            block_size,
            concurrent_margin_s,
        )
    except Exception as exc:  # noqa: BLE001 - PortAudio сообщает разные типы
        raise PlaybackError(
            "не удалось воспроизвести и записать сигнал: "
            f"дуплекс: {duplex_error}; раздельные потоки: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if overflows:
        warnings.append(
            f"переполнений входного буфера: {overflows}; часть отсчётов потеряна"
        )
    return PlaybackCapture(
        samples=samples,
        sample_rate=sample_rate,
        input_device_index=input_device.index,
        output_device_index=output_device.index,
        input_device_name=input_device.name,
        output_device_name=output_device.name,
        mode="concurrent",
        overflows=overflows,
        wall_time_s=time.monotonic() - started,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True, slots=True)
class PlaybackCapture:
    """Запись, сделанная во время воспроизведения тон-сетки."""

    samples: np.ndarray
    sample_rate: int
    input_device_index: int
    output_device_index: int
    input_device_name: str
    output_device_name: str
    mode: str
    overflows: int
    wall_time_s: float
    warnings: tuple[str, ...]

    @property
    def duration_s(self) -> float:
        return self.samples.size / self.sample_rate

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "input_device_index": self.input_device_index,
            "input_device_name": self.input_device_name,
            "output_device_index": self.output_device_index,
            "output_device_name": self.output_device_name,
            "sample_rate_hz": self.sample_rate,
            "channels": 1,
            "frames": int(self.samples.size),
            "duration_s": self.duration_s,
            "wall_time_s": self.wall_time_s,
            "overflows": self.overflows,
            "warnings": list(self.warnings),
        }


def _play_record_concurrent(
    sounddevice: Any,
    signal: np.ndarray,
    sample_rate: int,
    input_device: audio.DeviceInfo,
    output_device: audio.DeviceInfo,
    block_size: int,
    margin_s: float,
) -> tuple[np.ndarray, int]:
    chunks: list[np.ndarray] = []
    state = {"overflows": 0}

    def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if status:
            state["overflows"] += 1
        chunks.append(np.array(indata, dtype=np.float32, copy=True))

    stream = sounddevice.InputStream(
        device=input_device.index,
        channels=1,
        samplerate=sample_rate,
        dtype="float32",
        blocksize=block_size,
        callback=callback,
    )
    with stream:
        time.sleep(0.1)
        sounddevice.play(
            signal,
            samplerate=sample_rate,
            device=output_device.index,
            blocking=True,
        )
        time.sleep(max(0.0, margin_s))

    if not chunks:
        raise PlaybackError("входной поток не вернул ни одного блока")
    samples = _as_mono_recording(np.concatenate(chunks, axis=0))
    return samples, state["overflows"]


def _as_mono_recording(recording: Any) -> np.ndarray:
    data = np.asarray(recording, dtype=np.float32)
    if data.ndim == 2:
        if data.shape[1] != 1:
            raise PlaybackError(
                f"ожидался моноканал, запись содержит {data.shape[1]} каналов"
            )
        data = data[:, 0]
    elif data.ndim != 1:
        raise PlaybackError("запись имеет неизвестную размерность")
    if data.size == 0:
        raise PlaybackError("запись пуста")
    return data


def write_plot(
    path: str | Path,
    analysis: CalibrationAnalysis,
    *,
    title: str = "Калибровка акустического тракта",
) -> Path:
    """Сохранить график: спектр записи и измеренные уровни тонов."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    frequencies = analysis.ambient.frequencies
    psd_db = 10.0 * np.log10(np.maximum(analysis.ambient.psd, 1e-30))
    tone_frequencies = [tone.frequency_hz for tone in analysis.tones]
    tone_levels = [
        tone.level_dbfs if tone.level_dbfs is not None else DB_FLOOR
        for tone in analysis.tones
    ]

    figure, axes = plt.subplots(figsize=(11, 6.5), dpi=120)
    axes.semilogx(frequencies[1:], psd_db[1:], linewidth=0.8, label="PSD записи")
    axes.plot(
        tone_frequencies,
        tone_levels,
        "o-",
        markersize=4,
        linewidth=1.0,
        label="уровень тона",
    )
    for frequency in analysis.plan.required_frequencies_hz:
        axes.axvline(
            frequency,
            linestyle="--",
            linewidth=0.9,
            color="tab:red",
            alpha=0.6,
        )
    axes.set_xlabel("Частота, Гц")
    axes.set_ylabel("dBFS / dBFS·Гц⁻¹")
    axes.set_title(
        f"{title}: {analysis.verdict} "
        f"({len(analysis.supported_frequencies_hz)}/"
        f"{len(analysis.tones)} тонов)"
    )
    axes.grid(True, which="both", alpha=0.3)
    axes.legend(loc="lower left")
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


def parse_frequency_list(text: str) -> tuple[float, ...]:
    """Разобрать список частот через запятую и проверить возрастание."""
    values: list[float] = []
    for part in text.split(","):
        token = part.strip()
        if not token:
            raise CalibrationError("пустое значение в списке частот")
        try:
            value = float(token)
        except ValueError:
            raise CalibrationError(f"не удалось разобрать частоту: {token!r}") from None
        if not math.isfinite(value):
            raise CalibrationError(f"частота должна быть конечной: {token!r}")
        values.append(value)
    if not values:
        raise CalibrationError("список частот пуст")
    if any(right <= left for left, right in zip(values, values[1:])):
        raise CalibrationError("частоты должны быть строго возрастающими")
    return tuple(values)


def _validate_capture(samples: np.ndarray) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 1:
        raise CalibrationError("запись должна быть одномерной")
    if data.size == 0:
        raise CalibrationError("пустая запись: анализировать нечего")
    if not np.all(np.isfinite(data)):
        raise CalibrationError("запись содержит NaN или бесконечность")
    return data


def _check_index(index: int, count: int) -> None:
    if not isinstance(index, int) or isinstance(index, bool):
        raise CalibrationError("индекс тона должен быть целым числом")
    if not 0 <= index < count:
        raise CalibrationError(f"индекс тона {index} вне диапазона 0..{count - 1}")


def _power_db(power: float) -> float:
    if power <= 0.0 or not math.isfinite(power):
        return DB_FLOOR
    return float(10.0 * math.log10(power))


def _ratio_db(numerator: float, denominator: float) -> float:
    if numerator <= 0.0:
        return DB_FLOOR
    return float(10.0 * math.log10(numerator / max(denominator, 1e-30)))
