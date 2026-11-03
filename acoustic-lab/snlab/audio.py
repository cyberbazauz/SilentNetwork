"""Аудиоустройства, запись фонового шума и спектральный анализ.

Модуль SN-010. Он отвечает только за измерение реального тракта:
перечисление устройств, проверку форматов, запись фона и расчёт уровней.

Чего здесь принципиально нет:

* SNR. Запись содержит только фоновый шум, эталонного сигнала нет, поэтому
  отношение сигнал/шум из неё не вычисляется (решение Product Owner, M0-4).
* Выводов о поддержке ультразвука. Список форматов, который сообщает драйвер,
  не доказывает, что микрофон и динамик физически работают на 16–22 кГц.

Зависимости: numpy, scipy (PSD и пики), sounddevice (только реальный ввод).
sounddevice импортируется лениво, чтобы тесты и анализ файлов работали без
подключённого аудиооборудования.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
from scipy.signal import find_peaks, welch

DB_FLOOR = -200.0
CLIP_THRESHOLD = 0.999
NOISE_FLOOR_FRAME_SECONDS = 0.1
NOISE_FLOOR_PERCENTILE = 10.0
PSD_MAX_SEGMENT = 8192
CANDIDATE_SAMPLE_RATES = (
    8_000,
    11_025,
    16_000,
    22_050,
    32_000,
    44_100,
    48_000,
    88_200,
    96_000,
    176_400,
    192_000,
)
CANDIDATE_DTYPES = ("float32", "int16", "int24", "int32")
CAPTURE_DTYPE = "float32"

#: Полосы, по которым считается накопленная энергия.
#: 16–18, 18–20 и 20–22 кГц — экспериментальные диапазоны Architecture v1.0 §4.1.
BANDS: tuple[tuple[str, float, float | None], ...] = (
    ("20_1000", 20.0, 1_000.0),
    ("1000_4000", 1_000.0, 4_000.0),
    ("4000_8000", 4_000.0, 8_000.0),
    ("8000_12000", 8_000.0, 12_000.0),
    ("12000_16000", 12_000.0, 16_000.0),
    ("16000_18000", 16_000.0, 18_000.0),
    ("18000_20000", 18_000.0, 20_000.0),
    ("20000_22000", 20_000.0, 22_000.0),
    ("22000_nyquist", 22_000.0, None),
)

FORMAT_NOTE = (
    "Список форматов отражает то, что принимает драйвер. Он не доказывает "
    "физическую работу тракта на 16–22 кГц: для этого нужно воспроизвести "
    "известный сигнал и измерить его уровень на приёме."
)


class AudioError(RuntimeError):
    """Ошибка аудиоподсистемы Acoustic Lab."""


class AudioBackendUnavailable(AudioError):
    """sounddevice/PortAudio недоступны или не отвечают."""


class NoAudioDevice(AudioError):
    """В системе нет устройства нужного направления."""


class DeviceNotFound(AudioError):
    """Запрошенное устройство не найдено или неоднозначно."""


class CaptureError(AudioError):
    """Запись не удалась: устройство занято, формат отклонён, поток прерван."""


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Описание аудиоустройства в терминах PortAudio."""

    index: int
    name: str
    hostapi: int
    hostapi_name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float
    default_low_input_latency: float
    default_low_output_latency: float
    is_default_input: bool = False
    is_default_output: bool = False

    @property
    def is_input(self) -> bool:
        return self.max_input_channels > 0

    @property
    def is_output(self) -> bool:
        return self.max_output_channels > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "hostapi": self.hostapi,
            "hostapi_name": self.hostapi_name,
            "max_input_channels": self.max_input_channels,
            "max_output_channels": self.max_output_channels,
            "default_samplerate": self.default_samplerate,
            "default_low_input_latency": self.default_low_input_latency,
            "default_low_output_latency": self.default_low_output_latency,
            "is_input": self.is_input,
            "is_output": self.is_output,
            "is_default_input": self.is_default_input,
            "is_default_output": self.is_default_output,
        }


@dataclass(frozen=True, slots=True)
class Recording:
    """Результат записи фонового шума."""

    samples: np.ndarray
    sample_rate: int
    device_index: int
    device_name: str
    frames_requested: int
    frames_captured: int
    overflows: int
    wall_time_s: float
    dtype: str = CAPTURE_DTYPE

    @property
    def duration_s(self) -> float:
        return self.frames_captured / self.sample_rate


@dataclass(frozen=True, slots=True)
class AmbientAnalysis:
    """Спектр и сводные уровни записи фонового шума."""

    frequencies: np.ndarray
    psd: np.ndarray
    nperseg: int
    resolution_hz: float
    summary: dict[str, Any]


def load_sounddevice(module: Any | None = None) -> Any:
    """Вернуть модуль sounddevice или поднять AudioBackendUnavailable."""
    if module is not None:
        return module
    try:
        import sounddevice
    except OSError as exc:  # pragma: no cover - зависит от PortAudio в системе
        raise AudioBackendUnavailable(
            f"PortAudio недоступен: {exc}. Установите sounddevice/PortAudio."
        ) from exc
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise AudioBackendUnavailable(
            "модуль sounddevice не установлен; выполните "
            "python -m pip install -r requirements.txt"
        ) from exc
    return sounddevice


def list_devices(sd_module: Any | None = None) -> list[DeviceInfo]:
    """Перечислить устройства ввода и вывода.

    Raises:
        AudioBackendUnavailable: PortAudio не отвечает или модуль недоступен.
    """
    sounddevice = load_sounddevice(sd_module)
    try:
        raw_devices = sounddevice.query_devices()
        hostapis = sounddevice.query_hostapis()
        default = sounddevice.default.device
    except Exception as exc:  # noqa: BLE001 - любые сбои PortAudio одинаковы для CLI
        raise AudioBackendUnavailable(
            f"не удалось получить список устройств: {type(exc).__name__}: {exc}"
        ) from exc

    default_input, default_output = _normalize_default(default)
    hostapi_names = {
        index: str(api.get("name", f"hostapi-{index}"))
        for index, api in enumerate(hostapis)
    }

    devices: list[DeviceInfo] = []
    for position, entry in enumerate(raw_devices):
        index = int(entry.get("index", position))
        hostapi = int(entry.get("hostapi", -1))
        devices.append(
            DeviceInfo(
                index=index,
                name=str(entry.get("name", f"device-{index}")),
                hostapi=hostapi,
                hostapi_name=hostapi_names.get(hostapi, f"hostapi-{hostapi}"),
                max_input_channels=int(entry.get("max_input_channels", 0)),
                max_output_channels=int(entry.get("max_output_channels", 0)),
                default_samplerate=float(entry.get("default_samplerate", 0.0)),
                default_low_input_latency=float(
                    entry.get("default_low_input_latency", 0.0)
                ),
                default_low_output_latency=float(
                    entry.get("default_low_output_latency", 0.0)
                ),
                is_default_input=index == default_input,
                is_default_output=index == default_output,
            )
        )
    return devices


BLUETOOTH_NAME_PATTERN = re.compile(
    r"hands[- ]?free|bthhfenum|bluetooth|airpods|\bheadset\b|wireless|\bbt600\b",
    re.IGNORECASE,
)
HEADPHONE_NAME_PATTERN = re.compile(r"наушники|headphones", re.IGNORECASE)
VIRTUAL_NAME_PATTERN = re.compile(
    r"переназначение|первичный драйвер|первичный звуковой|стерео микшер|"
    r"virtual|voicemeeter|vb-audio|cable output|loopback|sound mapper|what u hear",
    re.IGNORECASE,
)
BUILTIN_NAME_PATTERN = re.compile(
    r"realtek|amd|microphone array|speaker|динамик|микрофон|headphones 1 \(realtek",
    re.IGNORECASE,
)


def is_bluetooth_device(name: str) -> bool:
    """Похоже ли имя устройства на Bluetooth-маршрут или гарнитуру.

    Это эвристика по имени: Windows не отдаёт транспорт через PortAudio.
    «Наушники»/«Headphones» без признаков встроенного устройства считаются
    подозрительными, потому что так Windows показывает и Bluetooth-гарнитуру.
    Ошибка в сторону осторожности: такое устройство не выбирается автоматически,
    но остаётся доступным для ручного выбора.
    """
    if BLUETOOTH_NAME_PATTERN.search(name):
        return True
    return bool(HEADPHONE_NAME_PATTERN.search(name)) and not is_builtin_device(name)


def is_virtual_device(name: str) -> bool:
    """Виртуальное или перенаправленное устройство (микшер, кабель, mapper)."""
    return bool(VIRTUAL_NAME_PATTERN.search(name))


def is_builtin_device(name: str) -> bool:
    """Похоже ли имя на встроенный динамик/микрофон ноутбука."""
    return bool(BUILTIN_NAME_PATTERN.search(name))


def device_warning(device: DeviceInfo) -> str | None:
    """Предупреждение для UI или None, если маршрут допустим для измерения."""
    if is_bluetooth_device(device.name):
        return (
            "похоже на Bluetooth-устройство или гарнитуру: нельзя использовать "
            "как основной измерительный маршрут без проверки фактического маршрута."
        )
    if is_virtual_device(device.name):
        return (
            "Виртуальное или перенаправленное устройство: не подходит "
            "для акустического теста через воздух."
        )
    return None


def select_preferred_device(
    devices: Iterable[DeviceInfo], direction: str
) -> DeviceInfo | None:
    """Выбрать встроенное устройство для автовыбора.

    Порядок: встроенное → системное по умолчанию → первое допустимое.
    Bluetooth и виртуальные устройства не выбираются автоматически. Если
    допустимых устройств нет, возвращается None: UI обязан оставить выбор
    пустым и предупредить пользователя, а не подставлять Bluetooth молча.
    """
    if direction not in ("input", "output"):
        raise ValueError("direction должен быть 'input' или 'output'")
    available = [
        device
        for device in devices
        if (device.is_output if direction == "output" else device.is_input)
    ]
    safe = [
        device
        for device in available
        if not is_bluetooth_device(device.name) and not is_virtual_device(device.name)
    ]
    if not safe:
        return None
    builtin = next((device for device in safe if is_builtin_device(device.name)), None)
    if builtin is not None:
        return builtin
    default_flag = "is_default_output" if direction == "output" else "is_default_input"
    return next((device for device in safe if getattr(device, default_flag, False)), safe[0])


def probe_formats(
    device: DeviceInfo,
    *,
    direction: str,
    sd_module: Any | None = None,
    channels: int = 1,
) -> dict[str, Any]:
    """Проверить, какие частоты и типы отсчётов принимает драйвер.

    Проверка выполняется через `check_input_settings`/`check_output_settings`,
    то есть без открытия потока. Неудачи не считаются ошибкой программы:
    они и есть результат измерения.
    """
    if direction not in ("input", "output"):
        raise ValueError("direction должен быть 'input' или 'output'")
    sounddevice = load_sounddevice(sd_module)
    check = (
        sounddevice.check_input_settings
        if direction == "input"
        else sounddevice.check_output_settings
    )

    supported_rates: list[int] = []
    rejected_rates: dict[str, str] = {}
    for rate in CANDIDATE_SAMPLE_RATES:
        ok, error = _try_check(
            check, device=device.index, channels=channels, samplerate=rate, dtype=CAPTURE_DTYPE
        )
        if ok:
            supported_rates.append(rate)
        else:
            rejected_rates[str(rate)] = error or "отклонено драйвером"

    preferred_rate = _preferred_rate(device, supported_rates)
    supported_dtypes: list[str] = []
    rejected_dtypes: dict[str, str] = {}
    for dtype in CANDIDATE_DTYPES:
        ok, error = _try_check(
            check,
            device=device.index,
            channels=channels,
            samplerate=preferred_rate,
            dtype=dtype,
        )
        if ok:
            supported_dtypes.append(dtype)
        else:
            rejected_dtypes[dtype] = error or "отклонено драйвером"

    return {
        "direction": direction,
        "channels": channels,
        "supported_sample_rates": supported_rates,
        "rejected_sample_rates": rejected_rates,
        "dtypes_probed_at_hz": preferred_rate,
        "supported_dtypes": supported_dtypes,
        "rejected_dtypes": rejected_dtypes,
        "note": FORMAT_NOTE,
    }


def select_device(
    requested: str | int | None,
    direction: str,
    devices: Iterable[DeviceInfo],
) -> DeviceInfo:
    """Выбрать устройство по индексу, части имени или умолчанию.

    Raises:
        NoAudioDevice: нет устройств нужного направления.
        DeviceNotFound: индекс/имя не найдены или имя неоднозначно.
    """
    if direction not in ("input", "output"):
        raise ValueError("direction должен быть 'input' или 'output'")

    candidates = [
        device
        for device in devices
        if (device.is_input if direction == "input" else device.is_output)
    ]
    if not candidates:
        what = "входных" if direction == "input" else "выходных"
        raise NoAudioDevice(f"в системе не найдено {what} аудиоустройств")

    if requested is None or (isinstance(requested, str) and not requested.strip()):
        is_default = (
            (lambda device: device.is_default_input)
            if direction == "input"
            else (lambda device: device.is_default_output)
        )
        for device in candidates:
            if is_default(device):
                return device
        return candidates[0]

    text = str(requested).strip()
    if text.lstrip("-").isdigit():
        index = int(text)
        for device in candidates:
            if device.index == index:
                return device
        raise DeviceNotFound(
            f"устройство с индексом {index} не найдено среди "
            f"{'входных' if direction == 'input' else 'выходных'} устройств"
        )

    needle = text.casefold()
    matches = [device for device in candidates if needle in device.name.casefold()]
    if not matches:
        raise DeviceNotFound(f"устройство по запросу {text!r} не найдено")
    if len(matches) > 1:
        listing = "; ".join(f"[{d.index}] {d.name}" for d in matches)
        raise DeviceNotFound(
            f"запрос {text!r} неоднозначен, подходят: {listing}. Укажите индекс."
        )
    return matches[0]


def record_ambient(
    *,
    duration_s: float,
    device: DeviceInfo | None = None,
    sample_rate: int = 48_000,
    block_size: int = 4_096,
    sd_module: Any | None = None,
    startup_margin_s: float = 30.0,
) -> Recording:
    """Записать фоновый шум с микрофона.

    Запись прерывается, только если поток не отдал данные за отведённое время
    или драйвер выбросил исключение. Запас `startup_margin_s` покрывает
    холодный старт драйвера: на этой машине первый захват занял на ~13 с
    больше длительности записи, последующие шли в реальном времени.
    """
    if not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("длительность записи должна быть положительным числом")
    if duration_s > 300.0:
        raise ValueError("длительность записи ограничена 300 с")
    if not isinstance(sample_rate, int) or not 8_000 <= sample_rate <= 192_000:
        raise ValueError("sample_rate должен быть целым в диапазоне 8000..192000")
    if not isinstance(block_size, int) or block_size <= 0:
        raise ValueError("block_size должен быть положительным целым")
    if not math.isfinite(startup_margin_s) or startup_margin_s < 0:
        raise ValueError("startup_margin_s не может быть отрицательным")

    sounddevice = load_sounddevice(sd_module)
    frames = int(round(duration_s * sample_rate))
    if frames <= 0:
        raise ValueError("длительность записи короче одного отсчёта")

    device_index = device.index if device is not None else None
    device_name = device.name if device is not None else "default"

    chunks: list[np.ndarray] = []
    collected = 0
    overflows = 0
    started = time.monotonic()
    deadline = started + duration_s + startup_margin_s

    try:
        with sounddevice.InputStream(
            device=device_index,
            channels=1,
            samplerate=sample_rate,
            dtype=CAPTURE_DTYPE,
            blocksize=block_size,
        ) as stream:
            while collected < frames:
                if time.monotonic() > deadline:
                    raise CaptureError(
                        "поток не отдал данные за отведённое время: "
                        f"записано {collected} из {frames} отсчётов"
                    )
                block, overflowed = stream.read(min(block_size, frames - collected))
                if overflowed:
                    overflows += 1
                data = _as_mono(block)
                if data.size == 0:
                    raise CaptureError("драйвер вернул пустой блок данных")
                chunks.append(data)
                collected += int(data.size)
    except CaptureError:
        raise
    except Exception as exc:  # noqa: BLE001 - PortAudio сообщает разные типы
        raise CaptureError(
            f"ошибка записи с устройства [{device_index}] {device_name!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    samples = np.concatenate(chunks)[:frames].astype(np.float32, copy=False)
    return Recording(
        samples=samples,
        sample_rate=sample_rate,
        device_index=int(device_index) if device_index is not None else -1,
        device_name=device_name,
        frames_requested=frames,
        frames_captured=int(samples.size),
        overflows=overflows,
        wall_time_s=time.monotonic() - started,
    )


def compute_psd(
    samples: np.ndarray,
    sample_rate: int,
    *,
    nperseg: int | None = None,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Односторонняя PSD (Велч) в единицах полной шкалы²/Гц.

    Returns:
        (частоты, psd, фактическое nperseg)

    Raises:
        ValueError: пустой/многомерный сигнал или неверная частота.
    """
    data = _validate_signal(samples)
    if not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("sample_rate должен быть положительным целым числом")

    segment = nperseg if nperseg is not None else min(PSD_MAX_SEGMENT, data.size)
    if segment < 8:
        raise ValueError("запись слишком коротка для спектрального анализа")
    segment = int(min(segment, data.size))

    frequencies, psd = welch(
        data,
        fs=sample_rate,
        window="hann",
        nperseg=segment,
        noverlap=segment // 2,
        detrend="constant",
        scaling="density",
        average="mean",
    )
    return frequencies, np.asarray(psd, dtype=np.float64), segment


def noise_floor(
    samples: np.ndarray,
    sample_rate: int,
    *,
    frame_seconds: float = NOISE_FLOOR_FRAME_SECONDS,
    percentile: float = NOISE_FLOOR_PERCENTILE,
) -> dict[str, Any]:
    """Оценить шумовой пол как низкий процентиль RMS кадров.

    Методика: сигнал режется на неперекрывающиеся кадры по 100 мс, для каждого
    считается RMS, шумовой пол — 10-й процентиль этих значений. Это устойчивая
    к коротким выбросам оценка «тихого» уровня, а не минимум и не среднее.
    """
    data = _validate_signal(samples)
    if not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("sample_rate должен быть положительным целым числом")
    if not 0.0 <= percentile <= 100.0:
        raise ValueError("percentile должен быть в диапазоне 0..100")

    frame_length = int(round(frame_seconds * sample_rate))
    if frame_length < 1:
        raise ValueError("frame_seconds короче одного отсчёта")
    frame_count = data.size // frame_length
    if frame_count < 1:
        raise ValueError(
            "запись короче одного кадра шумового пола "
            f"({frame_seconds * 1000:.0f} мс)"
        )

    frames = data[: frame_count * frame_length].reshape(frame_count, frame_length)
    frame_rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
    value = float(np.percentile(frame_rms, percentile))
    return {
        "method": "percentile_of_frame_rms",
        "frame_seconds": frame_seconds,
        "percentile": percentile,
        "frames": frame_count,
        "linear": value,
        "dbfs": _db(value),
        "frames_db_min": _db(float(np.min(frame_rms))),
        "frames_db_max": _db(float(np.max(frame_rms))),
    }


def clipping_stats(
    samples: np.ndarray,
    *,
    threshold: float = CLIP_THRESHOLD,
) -> dict[str, Any]:
    """Найти отсчёты, упёршиеся в предел шкалы."""
    data = _validate_signal(samples)
    if not 0.0 < threshold <= 1.0:
        raise ValueError("threshold должен быть в диапазоне (0, 1]")

    magnitude = np.abs(data)
    indices = np.flatnonzero(magnitude >= threshold)
    longest_run = 0
    if indices.size:
        breaks = np.flatnonzero(np.diff(indices) > 1)
        runs = np.split(indices, breaks + 1)
        longest_run = int(max(run.size for run in runs))

    return {
        "threshold": threshold,
        "detected": bool(indices.size),
        "clipped_samples": int(indices.size),
        "clipped_ratio": float(indices.size / data.size),
        "longest_run": longest_run,
        "first_index": int(indices[0]) if indices.size else None,
        "max_abs": float(np.max(magnitude)),
    }


def band_levels(
    frequencies: np.ndarray,
    psd: np.ndarray,
    *,
    bands: tuple[tuple[str, float, float | None], ...] = BANDS,
) -> list[dict[str, Any]]:
    """Накопленная энергия по полосам в dBFS.

    Границы полос включаются в интервал. Это измерительные корзины, а не
    кирпичная стена: тон ровно на границе теряет часть главного лепестка окна,
    поэтому для проверки уровня выбирайте сигнал внутри полосы.

    Полоса, выходящая за пределы записанного спектра, помечается `null`:
    отсутствие данных — не то же самое, что нулевой уровень.
    """
    freqs = np.asarray(frequencies, dtype=np.float64)
    density = np.asarray(psd, dtype=np.float64)
    if freqs.shape != density.shape:
        raise ValueError("частоты и PSD должны совпадать по размеру")
    nyquist = float(freqs[-1]) if freqs.size else 0.0

    result: list[dict[str, Any]] = []
    for name, low, high in bands:
        upper = nyquist if high is None else float(high)
        mask = (freqs >= low) & (freqs <= upper)
        entry: dict[str, Any] = {
            "name": name,
            "low_hz": float(low),
            "high_hz": high,
            "level_dbfs": None,
        }
        if np.count_nonzero(mask) < 2 or upper <= low:
            entry["note"] = "полоса вне записанного диапазона"
            result.append(entry)
            continue
        power = float(_integrate(density[mask], freqs[mask]))
        entry["level_dbfs"] = max(_db(math.sqrt(power)), DB_FLOOR) if power > 0.0 else DB_FLOOR
        result.append(entry)
    return result


def spectral_peaks(
    frequencies: np.ndarray,
    psd: np.ndarray,
    *,
    count: int = 10,
    prominence_db: float = 6.0,
) -> list[dict[str, Any]]:
    """Найти самые заметные узкополосные составляющие фона."""
    freqs = np.asarray(frequencies, dtype=np.float64)
    density = np.asarray(psd, dtype=np.float64)
    if freqs.shape != density.shape:
        raise ValueError("частоты и PSD должны совпадать по размеру")
    if not 1 <= count <= 50:
        raise ValueError("count должен быть в диапазоне 1..50")

    db = _psd_db(density)
    peak_indices, properties = find_peaks(db, prominence=prominence_db)
    if peak_indices.size == 0:
        return []
    order = np.argsort(db[peak_indices])[::-1][:count]
    prominences = properties.get("prominences")
    result: list[dict[str, Any]] = []
    for position in order:
        peak = int(peak_indices[position])
        result.append(
            {
                "frequency_hz": float(freqs[peak]),
                "level_dbfs_per_hz": float(db[peak]),
                "prominence_db": float(prominences[position])
                if prominences is not None
                else None,
            }
        )
    return result


def analyze_ambient(
    samples: np.ndarray,
    sample_rate: int,
    *,
    nperseg: int | None = None,
) -> AmbientAnalysis:
    """Рассчитать уровни и спектр записи фонового шума.

    SNR здесь не вычисляется: в записи нет известного сигнала.
    """
    data = _validate_signal(samples)
    if not isinstance(sample_rate, int) or sample_rate <= 0:
        raise ValueError("sample_rate должен быть положительным целым числом")

    frequencies, psd, segment = compute_psd(data, sample_rate, nperseg=nperseg)

    rms_linear = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
    peak_linear = float(np.max(np.abs(data)))
    dc_offset = float(np.mean(data))
    spectrum_floor = float(np.median(_psd_db(psd)))
    total_power = float(_integrate(psd, frequencies))
    flatness = _spectral_flatness(psd)
    centroid = _spectral_centroid(frequencies, psd)

    summary: dict[str, Any] = {
        "sample_rate_hz": sample_rate,
        "samples": int(data.size),
        "duration_s": float(data.size / sample_rate),
        "level": {
            "rms_linear": rms_linear,
            "rms_dbfs": _db(rms_linear),
            "peak_linear": peak_linear,
            "peak_dbfs": _db(peak_linear),
            "crest_factor_db": _db(peak_linear) - _db(rms_linear),
            "dc_offset": dc_offset,
        },
        "noise_floor": noise_floor(data, sample_rate),
        "clipping": clipping_stats(data),
        "psd": {
            "method": "scipy.signal.welch",
            "window": "hann",
            "detrend": "constant",
            "scaling": "density",
            "nperseg": segment,
            "resolution_hz": segment and sample_rate / segment,
            "bins": int(frequencies.size),
            "max_frequency_hz": float(frequencies[-1]),
            "median_level_dbfs_per_hz": spectrum_floor,
            "integrated_power_linear": total_power,
            "integrated_level_dbfs": max(_db(math.sqrt(total_power)), DB_FLOOR)
            if total_power > 0.0
            else DB_FLOOR,
            "spectral_centroid_hz": centroid,
            "spectral_flatness": flatness,
        },
        "bands": band_levels(frequencies, psd),
        "peaks": spectral_peaks(frequencies, psd),
        "snr": None,
        "snr_note": (
            "SNR не вычисляется из записи фонового шума: нет известного "
            "тестового сигнала. Требуется отдельная методика с эталоном."
        ),
    }
    return AmbientAnalysis(
        frequencies=frequencies,
        psd=psd,
        nperseg=segment,
        resolution_hz=sample_rate / segment,
        summary=summary,
    )


def write_psd_csv(path: str, analysis: AmbientAnalysis) -> None:
    """Сохранить PSD в CSV: frequency_hz,psd_linear,psd_dbfs_per_hz."""
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = np.column_stack(
        [
            analysis.frequencies,
            analysis.psd,
            _psd_db(analysis.psd),
        ]
    )
    np.savetxt(
        target,
        rows,
        delimiter=",",
        header="frequency_hz,psd_linear,psd_dbfs_per_hz",
        comments="",
        fmt=["%.6f", "%.12e", "%.6f"],
    )


def write_spectrum_plot(
    path: str,
    analysis: AmbientAnalysis,
    *,
    title: str = "Фоновый шум: спектральная плотность мощности",
) -> None:
    """Построить график PSD с отметками экспериментальных полос."""
    import matplotlib

    matplotlib.use("Agg")
    from pathlib import Path

    import matplotlib.pyplot as plt

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    figure, axes = plt.subplots(figsize=(10, 6), dpi=120)
    axes.semilogx(
        analysis.frequencies[1:],
        _psd_db(analysis.psd)[1:],
        linewidth=0.9,
    )
    axes.set_xlabel("Частота, Гц")
    axes.set_ylabel("PSD, dBFS/Гц")
    axes.set_title(title)
    axes.grid(True, which="both", alpha=0.3)

    for frequency, label in (
        (16_000, "16 кГц"),
        (18_000, "18 кГц"),
        (20_000, "20 кГц"),
        (22_000, "22 кГц"),
    ):
        axes.axvline(frequency, linestyle="--", linewidth=0.8, color="tab:orange")
        axes.annotate(
            label,
            xy=(frequency, 1.0),
            xycoords=("data", "axes fraction"),
            xytext=(2, -12),
            textcoords="offset points",
            fontsize=8,
            color="tab:orange",
        )

    axes.set_xlim(max(20.0, float(analysis.frequencies[1])), analysis.frequencies[-1])
    figure.tight_layout()
    figure.savefig(target)
    plt.close(figure)


def _try_check(check: Any, **kwargs: Any) -> tuple[bool, str | None]:
    try:
        check(**kwargs)
    except Exception as exc:  # noqa: BLE001 - отказ драйвера это результат проверки
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def _preferred_rate(device: DeviceInfo, supported: list[int]) -> int:
    if supported:
        default = device.default_samplerate
        if default > 0:
            closest = min(supported, key=lambda rate: abs(rate - default))
            if abs(closest - default) <= 1.0:
                return closest
        if 48_000 in supported:
            return 48_000
        return supported[0]
    return int(round(device.default_samplerate)) or 48_000


def _normalize_default(default: Any) -> tuple[int, int]:
    """Разобрать `sd.default.device`, не полагаясь на тип.

    В sounddevice это `_InputOutputPair` — итерируемый объект, но не tuple/list,
    поэтому проверка через isinstance здесь неприменима.
    """
    if default is None:
        return -1, -1
    try:
        values = list(default)
    except TypeError:
        value = int(default)
        return value, value
    values = (values + [-1, -1])[:2]
    return int(values[0]), int(values[1])


def _as_mono(block: Any) -> np.ndarray:
    data = np.asarray(block, dtype=np.float32)
    if data.ndim == 2:
        if data.shape[1] != 1:
            raise CaptureError("ожидался моноканал, драйвер вернул больше каналов")
        data = data[:, 0]
    elif data.ndim != 1:
        raise CaptureError("драйвер вернул блок неизвестной размерности")
    return data


def _validate_signal(samples: np.ndarray) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 1:
        raise ValueError("ожидается одномерный сигнал")
    if data.size == 0:
        raise ValueError("пустой сигнал: анализ невозможен")
    if not np.all(np.isfinite(data)):
        raise ValueError("сигнал содержит NaN или бесконечность")
    return data


def _integrate(values: np.ndarray, coordinates: np.ndarray) -> float:
    """Интеграл по трапециям без зависимости от версии NumPy."""
    if values.size < 2 or coordinates.size != values.size:
        return 0.0
    steps = np.diff(coordinates)
    total = np.sum((values[1:] + values[:-1]) * steps / 2.0)
    return float(total)


def _db(value: float) -> float:
    if value <= 0.0 or not math.isfinite(value):
        return DB_FLOOR
    return float(20.0 * math.log10(value))


def _psd_db(psd: np.ndarray) -> np.ndarray:
    density = np.asarray(psd, dtype=np.float64)
    with np.errstate(divide="ignore"):
        db = 10.0 * np.log10(density)
    return np.where(np.isfinite(db), db, DB_FLOOR)


def _spectral_flatness(psd: np.ndarray) -> float:
    density = np.asarray(psd, dtype=np.float64)
    mean = float(np.mean(density))
    if mean <= 0.0:
        return 0.0
    positive = density[density > 0.0]
    if positive.size == 0:
        return 0.0
    geometric = float(np.exp(np.mean(np.log(positive))))
    return geometric / mean


def _spectral_centroid(frequencies: np.ndarray, psd: np.ndarray) -> float:
    density = np.asarray(psd, dtype=np.float64)
    total = float(np.sum(density))
    if total <= 0.0:
        return 0.0
    return float(np.sum(np.asarray(frequencies, dtype=np.float64) * density) / total)
