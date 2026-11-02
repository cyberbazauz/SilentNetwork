"""Воспроизведение PCM16 WAV через явно выбранное устройство (SN-015).

Модуль отделяет физическое воспроизведение от GUI и анализа. Реальный
движок использует PortAudio только в момент вызова `play()`; при импорте
модуля sounddevice не загружается. `FakePlaybackEngine` позволяет проверять
оркестрацию, STOP и отчёты без аудиоустройств.

Правила, которые нельзя обходить:

* устройство всегда задаётся явно, неявный default output запрещён;
* параметры проверяются через `check_output_settings`; при отказе драйвера
  выбрасывается ошибка, а не выполняется скрытое ресемплирование;
* поток закрывается в `finally`, в том числе после ошибки или STOP;
* системная громкость Windows не изменяется.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np

from snlab import audio
from snlab.wavio import to_pcm16_bytes


class PlaybackError(RuntimeError):
    """Воспроизведение невозможно или прервано ошибкой драйвера."""


@dataclass(frozen=True, slots=True)
class PlaybackOutcome:
    """Фактический результат одного вызова `play()`."""

    status: str
    frames_written: int
    total_frames: int
    elapsed_s: float
    error: str | None = None

    @property
    def performed(self) -> bool:
        """Полное воспроизведение, а не попытка или частичный STOP."""
        return self.status == "completed" and self.frames_written == self.total_frames

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "performed": self.performed,
            "frames_written": self.frames_written,
            "frames_total": self.total_frames,
            "elapsed_s": self.elapsed_s,
            "error": self.error,
        }


@runtime_checkable
class PlaybackEngine(Protocol):
    """Интерфейс, который GUI использует вместо конкретного аудиобэкенда."""

    def play(
        self,
        samples: np.ndarray,
        sample_rate: int,
        device: audio.DeviceInfo,
    ) -> PlaybackOutcome:
        ...

    def stop(self) -> None:
        ...

    @property
    def is_playing(self) -> bool:
        ...


class SoundDevicePlaybackEngine:
    """PortAudio-движок с явным устройством и управляемым STOP."""

    def __init__(self, sd_module: Any | None = None, *, block_size: int = 4_096) -> None:
        if not isinstance(block_size, int) or isinstance(block_size, bool) or block_size <= 0:
            raise PlaybackError("block_size должен быть положительным целым числом")
        self._sd_module = sd_module
        self._block_size = block_size
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._stream: Any | None = None
        self._playing = False

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def stop(self) -> None:
        """Запросить остановку и разбудить заблокированный поток."""
        self._stop.set()
        with self._lock:
            stream = self._stream
        if stream is None:
            return
        for method_name in ("abort", "stop"):
            method = getattr(stream, method_name, None)
            if method is None:
                continue
            try:
                method()
            except Exception:  # noqa: BLE001 - поток мог уже закрыться
                pass

    def play(
        self,
        samples: np.ndarray,
        sample_rate: int,
        device: audio.DeviceInfo,
    ) -> PlaybackOutcome:
        data = _validate_request(samples, sample_rate, device)
        sounddevice = audio.load_sounddevice(self._sd_module)

        with self._lock:
            if self._playing:
                raise PlaybackError("движок уже воспроизводит другой сигнал")
            self._playing = True
        self._stop.clear()

        started = time.monotonic()
        pcm = np.frombuffer(to_pcm16_bytes(data), dtype="<i2").copy()
        total = int(pcm.size)
        written = 0
        status = "completed"
        error: str | None = None
        stream: Any | None = None

        try:
            try:
                sounddevice.check_output_settings(
                    device=device.index,
                    channels=1,
                    samplerate=sample_rate,
                    dtype="int16",
                )
            except Exception as exc:  # noqa: BLE001 - отказ драйвера это результат
                raise PlaybackError(
                    f"устройство [{device.index}] {device.name!r} "
                    f"({device.hostapi_name}) не принимает PCM16 mono "
                    f"{sample_rate} Гц; скрытое ресемплирование не выполняется: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            stream = sounddevice.OutputStream(
                device=device.index,
                channels=1,
                samplerate=sample_rate,
                dtype="int16",
                blocksize=self._block_size,
            )
            with self._lock:
                self._stream = stream
            with stream:
                while written < total:
                    if self._stop.is_set():
                        status = "stopped"
                        break
                    end = min(total, written + self._block_size)
                    stream.write(pcm[written:end])
                    written = end
        except PlaybackError as exc:
            status = "failed"
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - PortAudio сообщает разные типы
            if self._stop.is_set():
                status = "stopped"
            else:
                status = "failed"
                error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._stream = None
                self._playing = False

        if status == "completed" and written < total:
            status = "failed"
            error = f"записано {written} из {total} отсчётов"

        return PlaybackOutcome(
            status=status,
            frames_written=written,
            total_frames=total,
            elapsed_s=time.monotonic() - started,
            error=error,
        )


class FakePlaybackEngine:
    """Детерминированный движок для тестов; звук не воспроизводится.

    `wait_for_stop=True` нужен для проверки STOP: `play()` блокируется до
    вызова `stop()` (или до таймаута), не создавая аудиопоток.
    """

    def __init__(
        self,
        *,
        wait_for_stop: bool = False,
        fail_with: BaseException | None = None,
        stop_timeout_s: float = 5.0,
    ) -> None:
        self.wait_for_stop = bool(wait_for_stop)
        self.fail_with = fail_with
        self.stop_timeout_s = float(stop_timeout_s)
        self.calls: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._playing = False

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def stop(self) -> None:
        self._stop.set()

    def play(
        self,
        samples: np.ndarray,
        sample_rate: int,
        device: audio.DeviceInfo,
    ) -> PlaybackOutcome:
        data = _validate_request(samples, sample_rate, device)
        with self._lock:
            if self._playing:
                raise PlaybackError("fake-движок уже воспроизводит другой сигнал")
            self._playing = True
            self.calls.append(
                {
                    "device": device,
                    "sample_rate": sample_rate,
                    "samples": data.copy(),
                }
            )
        self._stop.clear()
        started = time.monotonic()
        total = int(data.size)
        try:
            if self.fail_with is not None:
                raise self.fail_with
            if self.wait_for_stop:
                stopped = self._stop.wait(timeout=self.stop_timeout_s)
                if not stopped:
                    return PlaybackOutcome(
                        status="failed",
                        frames_written=0,
                        total_frames=total,
                        elapsed_s=time.monotonic() - started,
                        error="fake playback timeout: stop() не был вызван",
                    )
                return PlaybackOutcome(
                    status="stopped",
                    frames_written=total // 2,
                    total_frames=total,
                    elapsed_s=time.monotonic() - started,
                    error=None,
                )
            return PlaybackOutcome(
                status="completed",
                frames_written=total,
                total_frames=total,
                elapsed_s=time.monotonic() - started,
                error=None,
            )
        except Exception as exc:  # noqa: BLE001 - тестовый отказ
            return PlaybackOutcome(
                status="failed",
                frames_written=0,
                total_frames=total,
                elapsed_s=time.monotonic() - started,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            with self._lock:
                self._playing = False


def probe_output_formats(
    device: audio.DeviceInfo,
    *,
    rates: tuple[int, ...] = (48_000, 44_100),
    sd_module: Any | None = None,
) -> dict[int, dict[str, Any]]:
    """Проверить PCM16 mono на заданных частотах без открытия потока.

    Возвращает словарь `rate -> {"supported": bool, "error": str | None}`.
    Отказ драйвера — это результат проверки, а не исключение.
    """
    if not isinstance(device, audio.DeviceInfo):
        raise PlaybackError("device должен быть audio.DeviceInfo")
    if not device.is_output:
        raise PlaybackError(f"устройство [{device.index}] не является выходным")
    sounddevice = audio.load_sounddevice(sd_module)
    result: dict[int, dict[str, Any]] = {}
    for rate in rates:
        if not isinstance(rate, int) or isinstance(rate, bool) or rate <= 0:
            raise PlaybackError("частоты должны быть положительными целыми числами")
        try:
            sounddevice.check_output_settings(
                device=device.index,
                channels=1,
                samplerate=rate,
                dtype="int16",
            )
        except Exception as exc:  # noqa: BLE001 - отказ драйвера это результат
            result[rate] = {
                "supported": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        else:
            result[rate] = {"supported": True, "error": None}
    return result


def _validate_request(
    samples: np.ndarray,
    sample_rate: int,
    device: audio.DeviceInfo,
) -> np.ndarray:
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 1:
        raise PlaybackError("сигнал должен быть одномерным")
    if data.size == 0:
        raise PlaybackError("пустой сигнал: воспроизводить нечего")
    if not np.all(np.isfinite(data)):
        raise PlaybackError("сигнал содержит NaN или бесконечность")
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool):
        raise PlaybackError("sample_rate должен быть целым числом")
    if sample_rate <= 0:
        raise PlaybackError("sample_rate должен быть положительным")
    if not isinstance(device, audio.DeviceInfo):
        raise PlaybackError("device должен быть audio.DeviceInfo")
    if not device.is_output or device.max_output_channels < 1:
        raise PlaybackError(f"устройство [{device.index}] не является выходным")
    return data
