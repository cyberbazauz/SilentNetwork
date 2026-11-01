"""Генерация тестовых тонов и тон-сеток (SN-014, часть A).

Модуль строит **файл**: синусоиды с известными частотами, длительностями и
паузами, PCM16 WAV. Он не открывает динамик, не воспроизводит звук и не
обращается к sounddevice.

Тон-план переиспользует проверенную логику `snlab.calibration.CalibrationPlan`:
те же ограничения частот, длительностей и амплитуды. Дополнительно здесь есть
число каналов: один и тот же сигнал записывается во все каналы WAV.

Границы:

* генератор не доказывает, что динамик или микрофон работают;
* одинаковый сигнал во всех каналах — это тестовый стимул, а не стерео;
* файл детерминирован: те же параметры дают те же отсчёты и SHA-256.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from snlab import calibration
from snlab.wavio import MAX_CHANNELS, write_pcm16_channels

DEFAULT_FREQUENCIES_HZ: tuple[float, ...] = (1_000.0, 8_000.0, 16_000.0, 18_000.0, 19_000.0)
DEFAULT_TONE_MS = 500.0
DEFAULT_GAP_MS = 200.0
DEFAULT_LEAD_MS = 500.0
DEFAULT_TAIL_MS = 500.0
DEFAULT_AMPLITUDE = 0.5
DEFAULT_RAMP_MS = 10.0
DEFAULT_CHANNELS = 1


class ToneError(calibration.CalibrationError):
    """Некорректный тон-план или параметры генерации."""


@dataclass(frozen=True, slots=True)
class TonePlan:
    """План тон-сетки: частоты, тайминги, амплитуда и число каналов.

    Длительность тона и амплитуда общие для всех точек. Пауза `gap_ms`
    вставляется только между тонами; `lead_ms` и `tail_ms` — тишина до и после.
    """

    sample_rate: int = 48_000
    channels: int = DEFAULT_CHANNELS
    frequencies_hz: tuple[float, ...] = DEFAULT_FREQUENCIES_HZ
    tone_ms: float = DEFAULT_TONE_MS
    gap_ms: float = DEFAULT_GAP_MS
    lead_ms: float = DEFAULT_LEAD_MS
    tail_ms: float = DEFAULT_TAIL_MS
    amplitude: float = DEFAULT_AMPLITUDE
    ramp_ms: float = DEFAULT_RAMP_MS

    def __post_init__(self) -> None:
        if (
            not isinstance(self.channels, int)
            or isinstance(self.channels, bool)
            or not 1 <= self.channels <= MAX_CHANNELS
        ):
            raise ToneError(
                f"channels должен быть целым в диапазоне 1..{MAX_CHANNELS}"
            )
        # Валидация частот, длительностей и амплитуды — в CalibrationPlan.
        self.as_calibration_plan()

    def as_calibration_plan(
        self,
        required: Sequence[float] | None = None,
    ) -> calibration.CalibrationPlan:
        """Представить план как `CalibrationPlan` для генерации и анализа.

        `required` по умолчанию — все частоты: генератору нечего требовать,
        а анализ отдельно задаёт обязательные частоты.
        """
        required_values = (
            tuple(float(value) for value in required)
            if required is not None
            else self.frequencies_hz
        )
        try:
            return calibration.CalibrationPlan(
                sample_rate=self.sample_rate,
                frequencies_hz=self.frequencies_hz,
                required_frequencies_hz=required_values,
                tone_ms=self.tone_ms,
                gap_ms=self.gap_ms,
                lead_ms=self.lead_ms,
                tail_ms=self.tail_ms,
                amplitude=self.amplitude,
                ramp_ms=self.ramp_ms,
            )
        except calibration.CalibrationError as exc:
            raise ToneError(str(exc)) from exc

    @property
    def tone_samples(self) -> int:
        return self.as_calibration_plan().tone_samples

    @property
    def gap_samples(self) -> int:
        return self.as_calibration_plan().gap_samples

    @property
    def lead_samples(self) -> int:
        return self.as_calibration_plan().lead_samples

    @property
    def tail_samples(self) -> int:
        return self.as_calibration_plan().tail_samples

    @property
    def ramp_samples(self) -> int:
        return self.as_calibration_plan().ramp_samples

    @property
    def total_samples(self) -> int:
        return self.as_calibration_plan().total_samples

    @property
    def duration_s(self) -> float:
        return self.as_calibration_plan().duration_s

    def tone_start_s(self, index: int) -> float:
        return self.as_calibration_plan().tone_start_s(index)

    def tone_end_s(self, index: int) -> float:
        return self.as_calibration_plan().tone_end_s(index)

    def tone_start_sample(self, index: int) -> int:
        return self.as_calibration_plan().tone_start_sample(index)

    def tone_end_sample(self, index: int) -> int:
        return self.as_calibration_plan().tone_end_sample(index)

    def tone_boundaries(self, index: int) -> tuple[int, int]:
        return self.as_calibration_plan().tone_boundaries(index)

    def schedule(self) -> list[dict[str, Any]]:
        """Расписание тонов: индекс, частота, начало и конец в секундах."""
        return [
            {
                "index": index,
                "frequency_hz": float(frequency),
                "start_s": self.tone_start_s(index),
                "end_s": self.tone_end_s(index),
                "duration_s": self.tone_end_s(index) - self.tone_start_s(index),
            }
            for index, frequency in enumerate(self.frequencies_hz)
        ]

    def as_dict(self) -> dict[str, Any]:
        base = self.as_calibration_plan().as_dict()
        base.pop("required_frequencies_hz", None)
        base.pop("tones", None)
        base["channels"] = self.channels
        base["tones"] = len(self.frequencies_hz)
        return base


def generate_signal(plan: TonePlan) -> np.ndarray:
    """Построить тон-сетку формы `(frames, channels)` в долях полной шкалы.

    Один и тот же моно-сигнал копируется во все каналы. Повторный вызов с теми
    же параметрами даёт побитово тот же массив.
    """
    if not isinstance(plan, TonePlan):
        raise ToneError("ожидается TonePlan")
    mono = calibration.generate_signal(plan.as_calibration_plan())
    if plan.channels == 1:
        return mono[:, None].copy()
    return np.repeat(mono[:, None], plan.channels, axis=1)


def write_tone_wav(
    path: str | Path,
    plan: TonePlan,
    *,
    samples: np.ndarray | None = None,
) -> tuple[Path, str]:
    """Записать тон-сетку в PCM16 WAV и вернуть (путь, SHA-256 файла).

    SHA-256 считается по записанному файлу, а не по массиву в памяти: он
    должен совпадать у всех, кто получил тот же WAV.
    """
    data = generate_signal(plan) if samples is None else np.asarray(samples)
    target = write_pcm16_channels(path, data, plan.sample_rate)
    return target, sha256_file(target)


def generate_silence(
    plan: TonePlan,
    *,
    seconds: float,
) -> np.ndarray:
    """Тишина той же формы и частоты, что и план (для отрицательного теста)."""
    if not math.isfinite(seconds) or seconds <= 0:
        raise ToneError("длительность тишины должна быть положительным числом")
    frames = int(round(seconds * plan.sample_rate))
    if frames <= 0:
        raise ToneError("длительность тишины короче одного отсчёта")
    return np.zeros((frames, plan.channels), dtype=np.float64)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
