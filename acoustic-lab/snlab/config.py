"""Профили передачи Acoustic Lab и их валидация.

Модуль не выполняет DSP. Он только описывает параметры сигнала и проверяет,
что конфигурация внутренне непротиворечива.

Частоты в профилях — экспериментальные гипотезы (Architecture v1.0 §4.1),
а не подтверждённая поддержка оборудования. Итоговые рабочие частоты должны
определяться измерениями, а не значением по умолчанию.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 192_000
MIN_FREQ_HZ = 100.0
MAX_FREQ_HZ = 23_500.0
MIN_SYMBOL_RATE = 10.0
MAX_SYMBOL_RATE = 4_000.0
MAX_PAYLOAD_BYTES = 512
MAX_AMPLITUDE = 1.0


class ProfileError(ValueError):
    """Некорректная конфигурация профиля передачи."""


@dataclass(frozen=True, slots=True)
class BfskProfile:
    """Параметры BFSK-профиля.

    Attributes:
        name: имя профиля, используется в CLI и отчётах.
        sample_rate: частота дискретизации, Гц.
        mark_hz: частота символа «1», Гц.
        space_hz: частота символа «0», Гц.
        symbol_rate: символьная скорость, симв/с (1 бит на символ).
        amplitude: амплитуда несущей в долях полной шкалы (0..1).
    """

    name: str
    sample_rate: int = 48_000
    mark_hz: float = 18_000.0
    space_hz: float = 19_000.0
    symbol_rate: float = 300.0
    amplitude: float = 0.25

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ProfileError("имя профиля не может быть пустым")

        if not isinstance(self.sample_rate, int) or isinstance(self.sample_rate, bool):
            raise ProfileError("sample_rate должен быть целым числом")
        if not MIN_SAMPLE_RATE <= self.sample_rate <= MAX_SAMPLE_RATE:
            raise ProfileError(
                f"sample_rate должен быть в диапазоне "
                f"{MIN_SAMPLE_RATE}..{MAX_SAMPLE_RATE} Гц"
            )

        for field_name, value in (("mark_hz", self.mark_hz), ("space_hz", self.space_hz)):
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ProfileError(f"{field_name} должен быть конечным числом")
            if not MIN_FREQ_HZ <= value <= MAX_FREQ_HZ:
                raise ProfileError(
                    f"{field_name}={value} Гц вне исследуемого диапазона "
                    f"{MIN_FREQ_HZ}..{MAX_FREQ_HZ} Гц"
                )

        if self.mark_hz == self.space_hz:
            raise ProfileError("частоты mark и space должны различаться")

        nyquist = self.sample_rate / 2.0
        if max(self.mark_hz, self.space_hz) >= nyquist:
            raise ProfileError(
                "частоты BFSK должны быть ниже частоты Найквиста "
                f"({nyquist:.0f} Гц при sample_rate={self.sample_rate})"
            )

        if (
            not isinstance(self.symbol_rate, (int, float))
            or not math.isfinite(self.symbol_rate)
            or not MIN_SYMBOL_RATE <= self.symbol_rate <= MAX_SYMBOL_RATE
        ):
            raise ProfileError(
                f"symbol_rate должен быть в диапазоне "
                f"{MIN_SYMBOL_RATE}..{MAX_SYMBOL_RATE} симв/с"
            )
        if self.symbol_rate >= nyquist:
            raise ProfileError("symbol_rate должен быть ниже частоты Найквиста")

        separation = abs(self.mark_hz - self.space_hz)
        if separation < self.symbol_rate:
            raise ProfileError(
                "разнос частот меньше символьной скорости: "
                "символы будут неразличимы"
            )

        samples_per_symbol = self.sample_rate / self.symbol_rate
        if not math.isclose(
            samples_per_symbol, round(samples_per_symbol), rel_tol=0.0, abs_tol=1e-9
        ):
            raise ProfileError(
                "sample_rate должен делиться на symbol_rate нацело: "
                f"{self.sample_rate}/{self.symbol_rate} = {samples_per_symbol:.3f}"
            )

        if (
            not isinstance(self.amplitude, (int, float))
            or not math.isfinite(self.amplitude)
            or not 0.0 < self.amplitude <= MAX_AMPLITUDE
        ):
            raise ProfileError(
                f"amplitude должен быть в диапазоне (0, {MAX_AMPLITUDE}]"
            )

    @property
    def samples_per_symbol(self) -> int:
        """Число отсчётов на символ."""
        return round(self.sample_rate / self.symbol_rate)

    @property
    def bit_rate_bps(self) -> float:
        """Битовая скорость BFSK: один бит на символ."""
        return float(self.symbol_rate)

    def as_dict(self) -> dict[str, object]:
        """Представление профиля для отчётов и JSON."""
        return {
            "name": self.name,
            "sample_rate": self.sample_rate,
            "mark_hz": self.mark_hz,
            "space_hz": self.space_hz,
            "symbol_rate": self.symbol_rate,
            "amplitude": self.amplitude,
            "samples_per_symbol": self.samples_per_symbol,
            "bit_rate_bps": self.bit_rate_bps,
        }


PROFILE_BFSK_16K = BfskProfile(name="bfsk-16k", mark_hz=16_000.0, space_hz=17_000.0)
PROFILE_BFSK_18K = BfskProfile(name="bfsk-18k", mark_hz=18_000.0, space_hz=19_000.0)

PROFILES: Mapping[str, BfskProfile] = {
    profile.name: profile for profile in (PROFILE_BFSK_16K, PROFILE_BFSK_18K)
}

DEFAULT_PROFILE = PROFILE_BFSK_18K


def get_profile(name: str) -> BfskProfile:
    """Вернуть профиль по имени или выбросить ProfileError."""
    try:
        return PROFILES[name]
    except KeyError:
        available = ", ".join(sorted(PROFILES))
        raise ProfileError(
            f"неизвестный профиль: {name!r}; доступны: {available}"
        ) from None
