"""SN-028 preflight: инвентаризация аудиоустройств и проверка тракта ноутбука.

Скрипт не запускает цифровой протокол и НЕ заменяет обязательную контрольную
проверку «динамик ноутбука → микрофон Android». Он проверяет только сторону
ноутбука: какие устройства доступны, какие форматы принимает драйвер, каков
уровень фона и слышен ли тон 1 кГц в собственной петле динамик → микрофон.

Режимы:
    python sn028_preflight.py --inventory
    python sn028_preflight.py --noise 3
    python sn028_preflight.py --tone-check

Все результаты — измерения, а не приёмка. PASS SN-028 требует Android-устройства.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from snlab import audio
from snlab.wavio import write_pcm16

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "sn028"


def classify(name: str) -> dict[str, bool]:
    return {
        "bluetooth": audio.is_bluetooth_device(name),
        "virtual": audio.is_virtual_device(name),
        "builtin": audio.is_builtin_device(name),
    }


def describe(device: audio.DeviceInfo) -> dict[str, Any]:
    return {"device": device.as_dict(), "classification": classify(device.name)}


def pick_preferred(devices: list[audio.DeviceInfo], direction: str) -> audio.DeviceInfo | None:
    """Выбрать встроенное устройство, не Bluetooth и не виртуальное."""
    return audio.select_preferred_device(devices, direction)


def probe(device: audio.DeviceInfo, direction: str) -> dict[str, Any]:
    try:
        formats = audio.probe_formats(device, direction=direction, channels=1)
    except Exception as exc:  # noqa: BLE001 - отказ драйвера и есть результат
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, **formats}


def analyze(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    values = np.asarray(samples, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return {"ok": False, "error": "пустая запись"}
    peak = float(np.max(np.abs(values)))
    rms = float(np.sqrt(np.mean(np.square(values))))
    clipped = int(np.count_nonzero(np.abs(values) >= 0.999))
    window = np.hanning(values.size)
    spectrum = np.abs(np.fft.rfft(values * window))
    frequencies = np.fft.rfftfreq(values.size, d=1.0 / sample_rate)
    band = (frequencies >= 300.0) & (frequencies <= 3000.0)
    if not np.any(band):
        return {"ok": False, "error": "полоса 300–3000 Гц вне диапазона"}
    index = int(np.argmax(spectrum[band]))
    band_freqs = frequencies[band]
    band_values = spectrum[band]
    dominant_hz = float(band_freqs[index])
    if 0 < index < band_values.size - 1:
        left, center, right = band_values[index - 1], band_values[index], band_values[index + 1]
        denominator = left - 2.0 * center + right
        if denominator != 0.0:
            shift = 0.5 * (left - right) / denominator
            dominant_hz += float(shift) * float(band_freqs[1] - band_freqs[0])
    tone_band = (frequencies >= 950.0) & (frequencies <= 1050.0)
    tone_energy = float(np.sum(np.square(spectrum[tone_band])))
    total_energy = float(np.sum(np.square(spectrum[band])))
    tone_share = tone_energy / total_energy if total_energy > 0 else 0.0
    return {
        "ok": True,
        "samples": int(values.size),
        "duration_s": values.size / sample_rate,
        "sample_rate_hz": sample_rate,
        "rms_dbfs": _dbfs(rms),
        "peak_dbfs": _dbfs(peak),
        "clipped_samples": clipped,
        "dominant_hz": dominant_hz,
        "tone_1khz_share": tone_share,
        "tone_share_dbfs": _dbfs(math.sqrt(tone_share)) if tone_share > 0 else None,
    }


def _dbfs(value: float) -> float | None:
    if value <= 0.0:
        return None
    return round(20.0 * math.log10(value), 2)


def record_noise(device: audio.DeviceInfo, seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    recording = audio.record_ambient(
        duration_s=seconds, device=device, sample_rate=48000
    )
    samples = np.asarray(recording.samples, dtype=np.float64)
    result = {
        "device": describe(device),
        "requested_s": seconds,
        "captured_s": round(recording.duration_s, 3),
        "frames_captured": recording.frames_captured,
        "overflows": recording.overflows,
        "wall_time_s": round(time.monotonic() - started, 3),
        **analyze(samples, recording.sample_rate),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    wav_path = RESULTS_DIR / f"preflight-noise-{stamp}.wav"
    write_pcm16(wav_path, samples, recording.sample_rate)
    result["wav"] = str(wav_path)
    return result


def tone_loopback(
    input_candidates: list[audio.DeviceInfo],
    output_candidates: list[audio.DeviceInfo],
    *,
    tone_hz: float = 1000.0,
    seconds: float = 1.5,
    amplitude: float = 0.15,
) -> dict[str, Any]:
    """Короткий тон 1 кГц в собственной петле ноутбука (не проверка Android)."""
    import sounddevice as sd

    sample_rate = 48000
    ramp = int(0.01 * sample_rate)
    count = int(seconds * sample_rate)
    t = np.arange(count) / sample_rate
    signal = amplitude * np.sin(2.0 * np.pi * tone_hz * t)
    envelope = np.ones(count)
    envelope[:ramp] = np.linspace(0.0, 1.0, ramp)
    envelope[-ramp:] = np.linspace(1.0, 0.0, ramp)
    signal *= envelope
    blocks = np.concatenate(
        [np.zeros(int(0.3 * sample_rate)), signal, np.zeros(int(0.3 * sample_rate))]
    ).astype(np.float32)

    attempts: list[dict[str, Any]] = []
    captured: np.ndarray | None = None
    used: dict[str, Any] | None = None
    for input_device, output_device in _candidate_pairs(input_candidates, output_candidates):
        try:
            data = sd.playrec(
                blocks,
                samplerate=sample_rate,
                channels=1,
                device=(input_device.index, output_device.index),
                dtype="float32",
                blocking=True,
            )
        except Exception as exc:  # noqa: BLE001 - отказ драйвера фиксируем
            attempts.append(
                {
                    "input": input_device.index,
                    "output": output_device.index,
                    "hostapi": input_device.hostapi_name,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        attempts.append(
            {
                "input": input_device.index,
                "output": output_device.index,
                "hostapi": input_device.hostapi_name,
                "ok": True,
            }
        )
        captured = np.asarray(data, dtype=np.float64).reshape(-1)
        used = {"input": describe(input_device), "output": describe(output_device)}
        break
    if captured is None or used is None:
        return {
            "ok": False,
            "attempts": attempts,
            "error": "не удалось открыть дуплексный поток; тон не проверен",
        }

    results = analyze(captured, sample_rate)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    wav_path = RESULTS_DIR / f"preflight-loopback-{stamp}.wav"
    write_pcm16(wav_path, captured, sample_rate)
    ok = bool(
        results.get("ok")
        and results.get("dominant_hz") is not None
        and abs(results["dominant_hz"] - tone_hz) <= 15.0
        and results.get("clipped_samples") == 0
    )
    return {
        "ok": ok,
        "tone_hz": tone_hz,
        "emitted_amplitude": amplitude,
        **used,
        "attempts": attempts,
        "measurement": results,
        "wav": str(wav_path),
        "note": (
            "Петля динамик → микрофон того же ноутбука. Подтверждает только "
            "воспроизведение, запись и анализ; не подтверждает приём Android."
        ),
    }


def _candidate_pairs(
    inputs: list[audio.DeviceInfo], outputs: list[audio.DeviceInfo]
) -> list[tuple[audio.DeviceInfo, audio.DeviceInfo]]:
    """Пары одного host API; сначала встроенные, без Bluetooth и виртуальных."""
    pairs: list[tuple[audio.DeviceInfo, audio.DeviceInfo]] = []
    for input_device in inputs:
        if classify(input_device.name)["bluetooth"] or classify(input_device.name)["virtual"]:
            continue
        for output_device in outputs:
            if input_device.hostapi_name != output_device.hostapi_name:
                continue
            if classify(output_device.name)["bluetooth"] or classify(output_device.name)["virtual"]:
                continue
            pairs.append((input_device, output_device))
    pairs.sort(
        key=lambda pair: (
            not classify(pair[0].name)["builtin"],
            not classify(pair[1].name)["builtin"],
            pair[0].hostapi_name != "MME",
        )
    )
    return pairs


def build_inventory(devices: list[audio.DeviceInfo] | None = None) -> dict[str, Any]:
    devices = audio.list_devices() if devices is None else devices
    inputs = [d for d in devices if d.is_input]
    outputs = [d for d in devices if d.is_output]
    preferred_input = pick_preferred(inputs, "input")
    preferred_output = pick_preferred(outputs, "output")
    return {
        "schema": "silentnetwork.sn028.preflight/1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "portaudio": getattr(audio.load_sounddevice(), "__version__", "unknown"),
        "counts": {"devices": len(devices), "inputs": len(inputs), "outputs": len(outputs)},
        "default_input": describe(next(d for d in inputs if d.is_default_input)) if any(d.is_default_input for d in inputs) else None,
        "default_output": describe(next(d for d in outputs if d.is_default_output)) if any(d.is_default_output for d in outputs) else None,
        "preferred_input": describe(preferred_input) if preferred_input else None,
        "preferred_output": describe(preferred_output) if preferred_output else None,
        "inputs": [describe(d) for d in inputs],
        "outputs": [describe(d) for d in outputs],
        "format_probe_input": probe(preferred_input, "input") if preferred_input else None,
        "format_probe_output": probe(preferred_output, "output") if preferred_output else None,
        "bluetooth_as_default_output": bool(
            next((d for d in outputs if d.is_default_output), None)
            and classify(next(d for d in outputs if d.is_default_output).name)["bluetooth"]
        ),
        "note": (
            "Инвентаризация ноутбука. Не подтверждает передачу через воздух "
            "и не заменяет контроль Laptop speaker → Android microphone."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SN-028 preflight (ноутбук)")
    parser.add_argument("--inventory", action="store_true", help="только список устройств и форматов")
    parser.add_argument("--noise", type=float, default=0.0, metavar="SECONDS", help="записать фон")
    parser.add_argument("--tone-check", action="store_true", help="тон 1 кГц в петле ноутбука")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.inventory and args.noise <= 0 and not args.tone_check:
        args.inventory = True

    devices = audio.list_devices()

    report: dict[str, Any] = {"inventory": build_inventory(devices)}
    inputs = [d for d in devices if d.is_input]
    outputs = [d for d in devices if d.is_output]
    preferred_input = pick_preferred(inputs, "input")
    preferred_output = pick_preferred(outputs, "output")

    if args.noise > 0:
        if preferred_input is None:
            report["noise"] = {"ok": False, "error": "нет входного устройства"}
        else:
            report["noise"] = record_noise(preferred_input, args.noise)
    if args.tone_check:
        if preferred_input is None or preferred_output is None:
            report["tone_check"] = {"ok": False, "error": "нет входного или выходного устройства"}
        else:
            report["tone_check"] = tone_loopback(inputs, outputs)

    target = args.json_out or (
        RESULTS_DIR / f"preflight-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    inventory = report["inventory"]
    print(f"Устройств: {inventory['counts']}")
    for label in ("default_output", "preferred_output", "default_input", "preferred_input"):
        entry = inventory[label]
        if entry:
            flags = ", ".join(k for k, v in entry["classification"].items() if v) or "—"
            print(f"{label}: [{entry['device']['index']}] {entry['device']['name']} ({flags})")
    print(f"Bluetooth выбран системным выходом: {inventory['bluetooth_as_default_output']}")
    if report.get("noise"):
        noise = report["noise"]
        print(f"Фон: RMS {noise.get('rms_dbfs')} dBFS, пик {noise.get('peak_dbfs')} dBFS, "
              f"клипинг {noise.get('clipped_samples')}")
    if report.get("tone_check"):
        tone = report["tone_check"]
        measurement = tone.get("measurement") or {}
        print(f"Тон 1 кГц (петля ноутбука): ok={tone['ok']}, "
              f"доминанта {measurement.get('dominant_hz')} Гц, "
              f"RMS {measurement.get('rms_dbfs')} dBFS, клипинг {measurement.get('clipped_samples')}")
    print(f"JSON: {target}")
    failed = bool(report.get("tone_check") and not report["tone_check"].get("ok"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
