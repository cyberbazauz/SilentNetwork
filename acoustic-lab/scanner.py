"""scanner.py — аудиоустройства, форматы и фоновый шум.

Задача SN-010. Инструмент отвечает на вопросы «что есть в системе», «какие
форматы принимает драйвер» и «что слышно в помещении», и сохраняет сырые
данные для последующего анализа.

Границы:

* SNR не вычисляется: запись содержит только фон, эталонного сигнала нет.
* Поддержка 16–22 кГц не выводится из списка форматов: нужен реальный сигнал.
* Скрипт не передаёт данные через воздух и не является доказательством
  работоспособности акустического канала.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from snlab import audio
from snlab.wavio import write_pcm16

TOOL_NAME = "scanner.py"
TASK_ID = "SN-010"
SCHEMA = "silentnetwork.acoustic-lab.scanner/1"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3
EXIT_CAPTURE_FAILED = 4

LIMITATIONS = [
    "SNR не вычисляется: в записи только фоновый шум, эталонного сигнала нет.",
    "Список форматов драйвера не доказывает физическую работу тракта "
    "на 16–22 кГц.",
    "RMS и peak зависят от усиления микрофона и обработки Windows "
    "(AGC, шумоподавление, «улучшения»).",
    "Одна запись не характеризует помехи в другое время и в другом помещении.",
    "Запись не подтверждает передачу данных через воздух.",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Перечисление аудиоустройств и форматов, запись фонового шума "
            "и спектральный анализ. SNR из записи фона не вычисляется."
        ),
        epilog=(
            "Коды возврата: 0 — успех, 2 — неверные аргументы, "
            "3 — нет доступного устройства/бэкенда, 4 — ошибка записи или файла."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--list",
        action="store_true",
        help="перечислить устройства и поддерживаемые параметры",
    )
    mode.add_argument(
        "--capture",
        type=float,
        metavar="SECONDS",
        help="записать фоновый шум заданной длительности",
    )
    parser.add_argument(
        "--device",
        help="индекс или часть имени устройства (по умолчанию системное)",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=48_000,
        metavar="HZ",
        help="частота дискретизации записи (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help=(
            "для --list: JSON-отчёт; для --capture: WAV-файл "
            "(по умолчанию results/ambient-<время>.wav)"
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="путь JSON-отчёта для --capture (по умолчанию рядом с WAV)",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="не строить PNG со спектром",
    )
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="для --list: не проверять форматы через драйвер (быстрее)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.list and args.capture is None:
        parser.error("укажите --list или --capture SECONDS")
    if args.capture is not None and not 0.0 < args.capture <= 300.0:
        parser.error("--capture должен быть в диапазоне (0, 300] секунд")

    try:
        if args.list:
            return run_list(args)
        return run_capture(args)
    except audio.NoAudioDevice as exc:
        _fail(f"нет устройства: {exc}", "проверьте подключение микрофона и --list")
        return EXIT_UNAVAILABLE
    except audio.DeviceNotFound as exc:
        _fail(f"устройство не выбрано: {exc}", "запустите --list и укажите --device")
        return EXIT_UNAVAILABLE
    except audio.AudioBackendUnavailable as exc:
        _fail(f"аудиобэкенд недоступен: {exc}", None)
        return EXIT_UNAVAILABLE
    except audio.CaptureError as exc:
        _fail(f"ошибка захвата: {exc}", "устройство занято или формат не поддержан")
        return EXIT_CAPTURE_FAILED
    except (ValueError, OSError) as exc:
        _fail(f"ошибка: {exc}", None)
        return EXIT_USAGE


def run_list(args: argparse.Namespace) -> int:
    report = build_device_report(probe=not args.no_probe)
    print_device_table(report)

    if args.out is not None:
        _write_json(args.out, report)
        print(f"\nJSON: {args.out}")
    return EXIT_OK


def run_capture(args: argparse.Namespace) -> int:
    devices = audio.list_devices()
    device = audio.select_device(args.device, "input", devices)
    print(
        f"Запись {args.capture:.1f} с: [{device.index}] {device.name} "
        f"({device.hostapi_name}), {args.sample_rate} Гц, mono"
    )

    recording = audio.record_ambient(
        duration_s=args.capture,
        device=device,
        sample_rate=args.sample_rate,
    )
    wav_path = args.out or _default_capture_path()
    write_pcm16(wav_path, recording.samples.astype(np.float64), recording.sample_rate)

    analysis = audio.analyze_ambient(recording.samples, recording.sample_rate)
    psd_path = wav_path.with_suffix(".psd.csv")
    audio.write_psd_csv(psd_path, analysis)

    plot_path: Path | None = None
    if not args.no_plot:
        plot_path = wav_path.with_suffix(".spectrum.png")
        audio.write_spectrum_plot(plot_path, analysis)

    report = build_capture_report(
        recording=recording,
        analysis=analysis,
        files={
            "wav": str(wav_path),
            "psd_csv": str(psd_path),
            "spectrum_png": str(plot_path) if plot_path is not None else None,
        },
    )
    report_path = args.report or wav_path.with_suffix(".json")
    _write_json(report_path, report)

    print_capture_summary(report)
    print(f"\nWAV: {wav_path}")
    print(f"PSD: {psd_path}")
    if plot_path is not None:
        print(f"PNG: {plot_path}")
    print(f"JSON: {report_path}")
    return EXIT_OK


def build_device_report(
    *,
    probe: bool = True,
    sd_module: Any | None = None,
) -> dict[str, Any]:
    """Собрать отчёт о доступных устройствах и форматах."""
    devices = audio.list_devices(sd_module)
    entries: list[dict[str, Any]] = []
    for device in devices:
        entry = device.as_dict()
        if probe:
            if device.is_input:
                entry["input_formats"] = audio.probe_formats(
                    device, direction="input", sd_module=sd_module
                )
            if device.is_output:
                entry["output_formats"] = audio.probe_formats(
                    device, direction="output", sd_module=sd_module
                )
        entries.append(entry)

    inputs = [entry for entry in entries if entry["is_input"]]
    outputs = [entry for entry in entries if entry["is_output"]]
    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": _utc_now(),
        "environment": _environment(),
        "probed": probe,
        "summary": {
            "devices_total": len(entries),
            "input_devices": len(inputs),
            "output_devices": len(outputs),
            "default_input": _first(entries, "is_default_input"),
            "default_output": _first(entries, "is_default_output"),
        },
        "devices": entries,
        "limitations": LIMITATIONS,
    }


def build_capture_report(
    *,
    recording: audio.Recording,
    analysis: audio.AmbientAnalysis,
    files: dict[str, str | None],
) -> dict[str, Any]:
    """Собрать отчёт о записи фона: параметры, уровни, спектр, файлы."""
    warnings: list[str] = []
    if recording.overflows:
        warnings.append(
            f"переполнений буфера: {recording.overflows}; часть отсчётов потеряна"
        )
    if recording.frames_captured != recording.frames_requested:
        warnings.append(
            "записано "
            f"{recording.frames_captured} из {recording.frames_requested} отсчётов"
        )
    if analysis.summary["level"]["peak_linear"] == 0.0:
        warnings.append(
            "запись содержит цифровую тишину: проверьте, не отключён ли микрофон"
        )

    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": _utc_now(),
        "environment": _environment(),
        "capture": {
            "device_index": recording.device_index,
            "device_name": recording.device_name,
            "sample_rate_hz": recording.sample_rate,
            "channels": 1,
            "dtype": recording.dtype,
            "frames_requested": recording.frames_requested,
            "frames_captured": recording.frames_captured,
            "duration_s": recording.duration_s,
            "wall_time_s": recording.wall_time_s,
            "overflows": recording.overflows,
        },
        "analysis": analysis.summary,
        "files": files,
        "warnings": warnings,
        "limitations": LIMITATIONS,
    }


def print_device_table(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(
        f"Устройств: {summary['devices_total']} "
        f"(вход: {summary['input_devices']}, выход: {summary['output_devices']})"
    )
    print(f"По умолчанию: вход=[{summary['default_input']}], "
          f"выход=[{summary['default_output']}]")
    print()
    header = f"{'ID':>3}  {'IN':>3} {'OUT':>3}  {'Fs, Гц':>8}  {'Host API':<12}  Имя"
    print(header)
    print("-" * len(header))
    for entry in report["devices"]:
        print(
            f"{entry['index']:>3}  {entry['max_input_channels']:>3} "
            f"{entry['max_output_channels']:>3}  "
            f"{entry['default_samplerate']:>8.0f}  "
            f"{entry['hostapi_name'][:12]:<12}  {entry['name']}"
        )
        if "input_formats" in entry:
            _print_formats("вход", entry["input_formats"])
        if "output_formats" in entry:
            _print_formats("выход", entry["output_formats"])

    print("\nПоддержка 16–22 кГц этим списком не подтверждается: " + audio.FORMAT_NOTE)


def print_capture_summary(report: dict[str, Any]) -> None:
    capture = report["capture"]
    level = report["analysis"]["level"]
    floor = report["analysis"]["noise_floor"]
    clipping = report["analysis"]["clipping"]

    print()
    print(
        f"Записано {capture['frames_captured']} отсчётов "
        f"({capture['duration_s']:.3f} с) за {capture['wall_time_s']:.2f} с"
    )
    print(f"RMS: {level['rms_dbfs']:.1f} dBFS (linear {level['rms_linear']:.6f})")
    print(f"Peak: {level['peak_dbfs']:.1f} dBFS (linear {level['peak_linear']:.6f})")
    print(
        f"Шумовой пол ({floor['percentile']:.0f}-й процентиль RMS кадров "
        f"{floor['frame_seconds'] * 1000:.0f} мс): {floor['dbfs']:.1f} dBFS"
    )
    print(
        "Клиппинг: "
        f"{'да' if clipping['detected'] else 'нет'} "
        f"({clipping['clipped_samples']} отсчётов)"
    )
    print("Полосы 16–22 кГц (dBFS):")
    for band in report["analysis"]["bands"]:
        if band["low_hz"] < 16_000:
            continue
        level_text = (
            f"{band['level_dbfs']:.1f}" if band["level_dbfs"] is not None else "нет данных"
        )
        print(f"  {band['name']}: {level_text}")
    peaks = report["analysis"]["peaks"][:3]
    if peaks:
        text = ", ".join(
            f"{peak['frequency_hz']:.0f} Гц ({peak['level_dbfs_per_hz']:.1f} dBFS/Гц)"
            for peak in peaks
        )
        print(f"Заметные пики: {text}")
    print("SNR не вычисляется: " + report["analysis"]["snr_note"])
    for warning in report["warnings"]:
        print(f"ВНИМАНИЕ: {warning}")


def _print_formats(direction: str, formats: dict[str, Any]) -> None:
    rates = formats.get("supported_sample_rates") or []
    dtypes = formats.get("supported_dtypes") or []
    rates_text = ", ".join(str(rate) for rate in rates) or "нет"
    dtypes_text = ", ".join(dtypes) or "нет"
    print(f"      {direction}: Fs [{rates_text}]; типы [{dtypes_text}]")


def _first(entries: list[dict[str, Any]], key: str) -> int | None:
    for entry in entries:
        if entry.get(key):
            return int(entry["index"])
    return None


def _environment() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "libraries": {
            "numpy": np.__version__,
            "scipy": _module_version("scipy"),
            "sounddevice": _module_version("sounddevice"),
            "matplotlib": _module_version("matplotlib"),
        },
    }


def _module_version(name: str) -> str | None:
    try:
        module = __import__(name)
    except ImportError:  # pragma: no cover - зависит от окружения
        return None
    return getattr(module, "__version__", None)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _default_capture_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("results") / f"ambient-{stamp}.wav"


def _fail(message: str, hint: str | None) -> None:
    print(message, file=sys.stderr)
    if hint:
        print(f"Подсказка: {hint}", file=sys.stderr)


def _configure_console() -> None:
    """Не падать на символах, которые не кодируются в текущей консоли."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):  # pragma: no cover - экзотические потоки
                pass


if __name__ == "__main__":
    sys.exit(main())
