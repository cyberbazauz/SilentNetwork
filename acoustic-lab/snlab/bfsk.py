"""BFSK-модулятор: биты → звуковые отсчёты (SN-011).

Модуляция — непрерывная по фазе (CPFSK): фаза не сбрасывается на границе
символа, поэтому спектр не расползается от разрывов, а переход между mark и
space остаётся плавным. Один символ — один бит: 1 → `mark_hz`, 0 → `space_hz`.

Детерминизм:

* число отсчётов жёстко равно `bits.size * samples_per_symbol`;
* нет случайных чисел, генераторов шума и времени;
* арифметика — float64 и NumPy; повторный запуск с теми же входными данными
  на той же платформе и версиях библиотек даёт побайтово тот же PCM.

Побитовая идентичность между разными ОС не гарантируется: `np.sin` и libm
могут отличаться в последнем разряде. Это ограничение, а не обещание.

Здесь нет канального кодирования, FEC, синхронизации приёмника и работы с
реальным звуком. Модулятор только строит файл.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from snlab.config import BfskProfile
from snlab.wavio import to_pcm16_bytes

DEFAULT_RAMP_MS = 5.0
MAX_RAMP_MS = 500.0
TWO_PI = 2.0 * math.pi


class ModulationError(ValueError):
    """Биты или параметры нельзя превратить в корректный BFSK-сигнал."""


@dataclass(frozen=True, slots=True)
class Modulation:
    """Результат модуляции: биты, отсчёты и параметры сигнала."""

    bits: np.ndarray
    samples: np.ndarray
    profile: BfskProfile
    ramp_ms: float
    ramp_samples: int

    @property
    def sample_count(self) -> int:
        """Число отсчётов; для BFSK всегда `bits * samples_per_symbol`."""
        return int(self.samples.size)

    @property
    def duration_s(self) -> float:
        return self.sample_count / self.profile.sample_rate

    @property
    def peak_amplitude(self) -> float:
        return float(np.max(np.abs(self.samples))) if self.samples.size else 0.0

    def pcm16_bytes(self) -> bytes:
        """PCM16 mono little-endian — то же, что попадёт в WAV."""
        return to_pcm16_bytes(self.samples)

    def pcm_sha256(self) -> str:
        """SHA-256 PCM-данных без заголовка WAV."""
        return hashlib.sha256(self.pcm16_bytes()).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        """Описание сигнала для JSON-отчёта."""
        return {
            "scheme": "continuous-phase BFSK",
            "bits": int(self.bits.size),
            "samples_per_symbol": self.profile.samples_per_symbol,
            "samples": self.sample_count,
            "duration_s": self.duration_s,
            "ramp_ms": self.ramp_ms,
            "ramp_samples": self.ramp_samples,
            "peak_amplitude": self.peak_amplitude,
            "pcm_format": "PCM16 mono little-endian",
            "pcm_bytes": self.sample_count * 2,
            "pcm_sha256": self.pcm_sha256(),
        }


def bits_to_samples(
    bits: np.ndarray | list[int] | tuple[int, ...],
    profile: BfskProfile,
    *,
    ramp_ms: float = DEFAULT_RAMP_MS,
    phase0: float = 0.0,
) -> np.ndarray:
    """Преобразовать биты в отсчёты BFSK в долях полной шкалы.

    Args:
        bits: последовательность 0/1; пустая недопустима.
        profile: проверенный BFSK-профиль.
        ramp_ms: длительность линейного нарастания/спада, мс; 0 отключает.
        phase0: начальная фаза, радианы.

    Returns:
        `float64` массив длиной ровно `bits.size * profile.samples_per_symbol`.

    Raises:
        ModulationError: биты не 0/1, не одномерные или пустые; `ramp_ms`
            вне диапазона; `phase0` не конечен.
    """
    array = _validate_bits(bits)
    ramp_samples_total = _validate_ramp(ramp_ms, profile.sample_rate)
    if not math.isfinite(phase0):
        raise ModulationError("phase0 должен быть конечным числом")

    samples_per_symbol = profile.samples_per_symbol
    frequencies = np.where(array == 1, profile.mark_hz, profile.space_hz)
    # Шаг фазы на отсчёт, повторённый для каждого символа.
    step_per_sample = np.repeat(TWO_PI * frequencies / profile.sample_rate, samples_per_symbol)

    # Непрерывная фаза: первый отсчёт имеет phase0, каждый следующий
    # продвигается на шаг текущего символа.
    phase = phase0 + np.concatenate(
        (np.zeros(1, dtype=np.float64), np.cumsum(step_per_sample, dtype=np.float64)[:-1])
    )
    samples = profile.amplitude * np.sin(phase)
    return apply_ramp(samples, ramp_samples_total)


def modulate(
    bits: np.ndarray | list[int] | tuple[int, ...],
    profile: BfskProfile,
    *,
    ramp_ms: float = DEFAULT_RAMP_MS,
    phase0: float = 0.0,
) -> Modulation:
    """Построить модуляцию и сохранить параметры вместе с отсчётами."""
    array = _validate_bits(bits)
    samples = bits_to_samples(array, profile, ramp_ms=ramp_ms, phase0=phase0)
    ramp_samples = _effective_ramp(_validate_ramp(ramp_ms, profile.sample_rate), samples.size)
    return Modulation(
        bits=array,
        samples=samples,
        profile=profile,
        ramp_ms=float(ramp_ms),
        ramp_samples=ramp_samples,
    )


def apply_ramp(samples: np.ndarray, ramp_samples: int) -> np.ndarray:
    """Линейно поднять и опустить амплитуду, не меняя число отсчётов.

    Рампы нужны, чтобы динамик и запись не получали щелчок на старте и в
    конце. При коротком сигнале рампа укорачивается до половины длины.
    """
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 1:
        raise ModulationError("ожидается одномерный сигнал")
    if data.size == 0:
        raise ModulationError("нельзя обработать пустой сигнал")
    if not isinstance(ramp_samples, int) or isinstance(ramp_samples, bool):
        raise ModulationError("ramp_samples должен быть целым числом")
    if ramp_samples < 0:
        raise ModulationError("ramp_samples не может быть отрицательным")

    effective = _effective_ramp(ramp_samples, data.size)
    if effective == 0:
        return data.copy()

    faded = data.copy()
    fade_in = np.linspace(0.0, 1.0, effective, endpoint=False, dtype=np.float64)
    faded[:effective] *= fade_in
    faded[-effective:] *= fade_in[::-1]
    return faded


def _effective_ramp(ramp_samples: int, total: int) -> int:
    if ramp_samples == 0 or total < 3:
        return 0
    return min(ramp_samples, total // 2)


def _validate_bits(bits: np.ndarray | list[int] | tuple[int, ...]) -> np.ndarray:
    array = np.asarray(bits)
    if array.ndim != 1:
        raise ModulationError("ожидается одномерная последовательность бит")
    if array.size == 0:
        raise ModulationError("пустая последовательность бит")
    if not np.issubdtype(array.dtype, np.integer):
        raise ModulationError("биты должны быть целыми числами 0 или 1")
    if np.any((array != 0) & (array != 1)):
        raise ModulationError("биты должны принимать значения 0 или 1")
    return array.astype(np.uint8, copy=True)


def _validate_ramp(ramp_ms: float, sample_rate: int) -> int:
    if not isinstance(ramp_ms, (int, float)) or isinstance(ramp_ms, bool):
        raise ModulationError("ramp_ms должен быть числом")
    if not math.isfinite(ramp_ms):
        raise ModulationError("ramp_ms должен быть конечным числом")
    if not 0.0 <= ramp_ms <= MAX_RAMP_MS:
        raise ModulationError(
            f"ramp_ms должен быть в диапазоне 0..{MAX_RAMP_MS:g}"
        )
    return int(round(ramp_ms / 1000.0 * sample_rate))
