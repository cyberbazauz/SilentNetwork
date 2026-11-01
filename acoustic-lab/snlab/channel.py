"""Модели канала для воспроизводимых экспериментов.

AWGN — это симуляция, а не реальный акустический канал. Результаты на AWGN
не подтверждают работу через воздух и не должны так интерпретироваться.
"""

from __future__ import annotations

import math

import numpy as np


def add_awgn(
    signal: np.ndarray,
    snr_db: float,
    *,
    seed: int | None = None,
) -> np.ndarray:
    """Добавить аддитивный белый гауссов шум с заданным SNR.

    Мощность шума вычисляется относительно средней мощности сигнала.
    Фиксированный seed делает эксперимент воспроизводимым.

    Args:
        signal: одномерный сигнал в долях полной шкалы.
        snr_db: отношение сигнал/шум в дБ (может быть отрицательным).
        seed: зерно генератора; None — невоспроизводимый шум.

    Returns:
        Сигнал с шумом, тип float64.

    Raises:
        ValueError: пустой/многомерный сигнал, NaN/бесконечность,
            нулевая мощность или нечисловой SNR.
    """
    data = np.asarray(signal, dtype=np.float64)
    if data.ndim != 1:
        raise ValueError("ожидается одномерный сигнал")
    if data.size == 0:
        raise ValueError("пустой сигнал: SNR не определён")
    if not np.all(np.isfinite(data)):
        raise ValueError("сигнал содержит NaN или бесконечность")
    if not isinstance(snr_db, (int, float)) or not math.isfinite(snr_db):
        raise ValueError("snr_db должен быть конечным числом")

    power = float(np.mean(data**2))
    if power <= 0.0:
        raise ValueError("нулевая мощность сигнала: SNR не определён")

    noise_std = math.sqrt(power / (10.0 ** (snr_db / 10.0)))
    rng = np.random.default_rng(seed)
    noise = rng.normal(loc=0.0, scale=noise_std, size=data.size)
    return data + noise
