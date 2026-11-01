"""Модели канала для воспроизводимых экспериментов.

AWGN — это симуляция, а не реальный акустический канал. Результаты на AWGN
не подтверждают работу через воздух и не должны так интерпретироваться.

Методика SNR в этом модуле:

* мощность полезного сигнала задаёт вызывающая сторона либо она считается по
  всей записи;
* для файлового benchmark мощность считается по **активной области**
  (`active_signal_power`), чтобы начальная/конечная тишина не занижала SNR;
* шум добавляется ко всей записи, а не только к активной области;
* ``SNR_dB = 10 * log10(P_signal / P_noise)``, где ``P_noise`` — дисперсия
  гауссова шума.
"""

from __future__ import annotations

import math

import numpy as np

DEFAULT_ACTIVE_THRESHOLD_DB = -40.0


def add_awgn(
    signal: np.ndarray,
    snr_db: float | None,
    *,
    seed: int | None = None,
    signal_power: float | None = None,
) -> np.ndarray:
    """Добавить аддитивный белый гауссов шум с заданным SNR.

    По умолчанию мощность шума вычисляется относительно средней мощности
    всего переданного массива. Для сигнала с тишиной на краях нужно передать
    `signal_power`, измеренную по активной области: иначе тишина искусственно
    занизит мощность сигнала и фактический SNR будет выше заданного.

    Args:
        signal: одномерный сигнал в долях полной шкалы.
        snr_db: отношение сигнал/шум в дБ (может быть отрицательным);
            None — точный pass-through без генерации шума.
        seed: зерно генератора; None — невоспроизводимый шум.
        signal_power: мощность полезного сигнала для расчёта шума; None —
            средняя мощность всего массива.

    Returns:
        Сигнал с шумом, тип float64. При ``snr_db is None`` возвращается
        копия входного сигнала без изменений.

    Raises:
        ValueError: пустой/многомерный сигнал, NaN/бесконечность,
            нулевая мощность, нечисловой SNR или некорректный `signal_power`.
    """
    data = np.asarray(signal, dtype=np.float64)
    if data.ndim != 1:
        raise ValueError("ожидается одномерный сигнал")
    if data.size == 0:
        raise ValueError("пустой сигнал: SNR не определён")
    if not np.all(np.isfinite(data)):
        raise ValueError("сигнал содержит NaN или бесконечность")

    if signal_power is not None:
        if (
            not isinstance(signal_power, (int, float))
            or isinstance(signal_power, bool)
            or not math.isfinite(signal_power)
        ):
            raise ValueError("signal_power должен быть конечным числом")
        if signal_power <= 0.0:
            raise ValueError("signal_power должен быть положительным")

    if snr_db is None:
        if signal_power is not None:
            raise ValueError(
                "signal_power не используется при отключённом шуме (snr_db=None)"
            )
        return data.copy()

    if (
        not isinstance(snr_db, (int, float))
        or isinstance(snr_db, bool)
        or not math.isfinite(snr_db)
    ):
        raise ValueError("snr_db должен быть конечным числом")

    power = float(signal_power) if signal_power is not None else float(np.mean(data**2))
    if power <= 0.0:
        raise ValueError("нулевая мощность сигнала: SNR не определён")

    noise_std = math.sqrt(power / (10.0 ** (snr_db / 10.0)))
    rng = np.random.default_rng(seed)
    noise = rng.normal(loc=0.0, scale=noise_std, size=data.size)
    return data + noise


def active_region_bounds(
    signal: np.ndarray,
    *,
    threshold_db: float = DEFAULT_ACTIVE_THRESHOLD_DB,
) -> tuple[int, int]:
    """Найти границы активной передачи по уровню относительно пика.

    Порог задаётся в дБ относительно пикового абсолютного значения. Тишина и
    хвосты рампы ниже порога в активную область не входят. Возвращается
    полуинтервал ``[start, end)``, пригодный для среза NumPy.

    Args:
        signal: одномерный сигнал.
        threshold_db: порог относительно пика, дБ; должен быть отрицательным.

    Raises:
        ValueError: пустой/многомерный сигнал, NaN/бесконечность, нулевой пик,
            нечисловой или неотрицательный порог, либо активная область пуста.
    """
    data = np.asarray(signal, dtype=np.float64)
    if data.ndim != 1:
        raise ValueError("ожидается одномерный сигнал")
    if data.size == 0:
        raise ValueError("пустой сигнал: активная область не определена")
    if not np.all(np.isfinite(data)):
        raise ValueError("сигнал содержит NaN или бесконечность")
    if (
        not isinstance(threshold_db, (int, float))
        or isinstance(threshold_db, bool)
        or not math.isfinite(threshold_db)
    ):
        raise ValueError("threshold_db должен быть конечным числом")
    if threshold_db >= 0.0:
        raise ValueError("threshold_db должен быть отрицательным")

    peak = float(np.max(np.abs(data)))
    if peak <= 0.0:
        raise ValueError("нулевой сигнал: активная область не определена")

    threshold = peak * (10.0 ** (threshold_db / 20.0))
    above = np.flatnonzero(np.abs(data) > threshold)
    if above.size == 0:  # pragma: no cover - защита от арифметической ошибки
        raise ValueError("активная область не найдена")
    return int(above[0]), int(above[-1]) + 1


def active_signal_power(
    signal: np.ndarray,
    *,
    threshold_db: float = DEFAULT_ACTIVE_THRESHOLD_DB,
) -> float:
    """Средняя мощность сигнала в активной области, без тишины на краях.

    Это мощность, относительно которой задаётся SNR в benchmark. Шум при
    этом всё равно добавляется ко всей записи.
    """
    data = np.asarray(signal, dtype=np.float64)
    start, end = active_region_bounds(data, threshold_db=threshold_db)
    return float(np.mean(data[start:end] ** 2))
