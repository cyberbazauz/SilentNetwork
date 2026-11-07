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


@dataclass(frozen=True, slots=True)
class ChatMode:
    """Пользовательский режим акустического чата.

    Режим — это не обещание слышимости или дальности, а набор параметров
    модуляции. Название «Неслышимый» означает только попытку уйти в верхний
    диапазон; неслышимость зависит от устройства, возраста слушателя и
    громкости и не гарантируется.
    """

    mode_id: str
    label: str
    summary: str
    profile: BfskProfile


PROFILE_BFSK_16K = BfskProfile(name="bfsk-16k", mark_hz=16_000.0, space_hz=17_000.0)
PROFILE_BFSK_18K = BfskProfile(name="bfsk-18k", mark_hz=18_000.0, space_hz=19_000.0)

# Профили первого реального обмена данными (SN-024). Слышимый диапазон
# выбран потому, что запись телефона уже подтвердила 1 кГц, а полоса
# 18–19 кГц в той же записи практически пустая. Это экспериментальные
# профили, а не обещание слышимости или дальности.
PROFILE_DATA_AUDIBLE = BfskProfile(
    name="data-audible",
    mark_hz=1_200.0,
    space_hz=2_200.0,
    symbol_rate=300.0,
    amplitude=0.30,
)
PROFILE_DATA_ROBUST = BfskProfile(
    name="data-robust",
    mark_hz=1_000.0,
    space_hz=2_000.0,
    symbol_rate=100.0,
    amplitude=0.30,
)

PROFILES: Mapping[str, BfskProfile] = {
    profile.name: profile
    for profile in (
        PROFILE_BFSK_16K,
        PROFILE_BFSK_18K,
        PROFILE_DATA_AUDIBLE,
        PROFILE_DATA_ROBUST,
    )
}

DATA_PROFILES: Mapping[str, BfskProfile] = {
    profile.name: profile
    for profile in (PROFILE_DATA_AUDIBLE, PROFILE_DATA_ROBUST)
}

# Режимы чата. Частоты выбраны по уже известным данным проекта, а не по
# «красивым» числам:
# * 1 кГц подтверждён реальной записью телефона, а 18–19 кГц в той же записи
#   был практически пустым (−103.18 dBFS), поэтому надёжный режим остаётся
#   в слышимом диапазоне;
# * 16–18 кГц — более совместимый из экспериментальных верхних диапазонов,
#   но многие динамики и микрофоны режут его фильтрами;
# * 20–22 кГц сознательно не используется: слишком высокая зависимость от
#   аппаратуры и почти нет запаса по Найквисту при 44.1 кГц.
PROFILE_CHAT_NORMAL = BfskProfile(
    name="chat-normal",
    mark_hz=1_200.0,
    space_hz=2_200.0,
    symbol_rate=300.0,
    amplitude=0.30,
)
PROFILE_CHAT_EXCELLENT = BfskProfile(
    name="chat-excellent",
    mark_hz=1_000.0,
    space_hz=2_000.0,
    symbol_rate=150.0,
    amplitude=0.30,
)
PROFILE_CHAT_HIGHFREQ = BfskProfile(
    name="chat-highfreq",
    mark_hz=16_000.0,
    space_hz=17_500.0,
    symbol_rate=100.0,
    amplitude=0.30,
)

CHAT_PROFILES: Mapping[str, BfskProfile] = {
    profile.name: profile
    for profile in (
        PROFILE_CHAT_NORMAL,
        PROFILE_CHAT_EXCELLENT,
        PROFILE_CHAT_HIGHFREQ,
    )
}

CHAT_MODE_NORMAL = ChatMode(
    mode_id="normal",
    label="Нормальный",
    summary=(
        "Слышимый диапазон 1200/2200 Гц, 300 бод. "
        "Основной режим: та же скорость, что у data-audible."
    ),
    profile=PROFILE_CHAT_NORMAL,
)
CHAT_MODE_EXCELLENT = ChatMode(
    mode_id="excellent",
    label="Отличный",
    summary=(
        "Слышимый диапазон 1000/2000 Гц, 150 бод. "
        "Медленнее, но с большим запасом по помехоустойчивости."
    ),
    profile=PROFILE_CHAT_EXCELLENT,
)
CHAT_MODE_HIGHFREQ = ChatMode(
    mode_id="highfreq",
    label="Неслышимый на высоких частотах",
    summary=(
        "Экспериментально: 16 000/17 500 Гц, 100 бод. "
        "Не гарантирует неслышимость: полосу должны поддерживать "
        "динамик и микрофон."
    ),
    profile=PROFILE_CHAT_HIGHFREQ,
)

CHAT_MODES: Mapping[str, ChatMode] = {
    mode.mode_id: mode
    for mode in (
        CHAT_MODE_NORMAL,
        CHAT_MODE_EXCELLENT,
        CHAT_MODE_HIGHFREQ,
    )
}

DEFAULT_CHAT_MODE = CHAT_MODE_NORMAL.mode_id

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


def get_chat_mode(key: str) -> ChatMode:
    """Вернуть режим чата по id или отображаемому названию."""
    if not isinstance(key, str) or not key.strip():
        raise ProfileError("режим чата должен быть непустой строкой")
    mode = CHAT_MODES.get(key)
    if mode is not None:
        return mode
    for candidate in CHAT_MODES.values():
        if candidate.label == key:
            return candidate
    available = ", ".join(f"{mode.mode_id} ({mode.label})" for mode in CHAT_MODES.values())
    raise ProfileError(f"неизвестный режим чата: {key!r}; доступны: {available}")
