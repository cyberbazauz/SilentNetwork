"""calibrate.py — калибровка тракта динамик → воздух → микрофон (SN-012).

Инструмент передаёт тон-сетку через выбранный динамик, одновременно пишет
микрофон, измеряет уровень/SNR/смещение частоты и сохраняет WAV + JSON.

Он не передаёт сообщения и не доказывает работу модема. PASS означает только
то, что обязательные частоты (по умолчанию 18 и 19 кГц) обнаружены в записи
с заданным SNR и допустимым смещением.

Коды возврата:
    0 — калибровка выполнена, обязательные частоты поддержаны;
    2 — неверные аргументы или параметры плана;
    3 — нет аудиобэкенда, входного или выходного устройства;
    4 — ошибка воспроизведения, записи, чтения WAV или отчёта;
    5 — калибровка выполнена, но обязательные частоты не поддержаны.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from snlab import audio, calibration
from snlab.reporting import configure_console, environment, utc_now, write_json
from snlab.wavio import WavFormatError, read_pcm16, write_pcm16

TOOL_NAME = "calibrate.py"
TASK_ID = "SN-012"
SCHEMA = "silentnetwork.acoustic-lab.calibration/1"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3
EXIT_IO = 4
EXIT_FAIL = 5

LIMITATIONS = [
    "PASS означает обнаружение тонов, а не подтверждённую передачу данных.",
    "Результат относится к текущему положению устройств, громкости и настройкам "
    "Windows; он не переносится на другое помещение или оборудование.",
    "Windows может применять AGC, шумоподавление и «улучшения» микрофона; "
    "инструмент их не отключает и не компенсирует.",
    "Тон-сетка не измеряет задержку, реверберацию, многолучёвость и BER.",
    "Одна машина с общим динамиком и микрофоном не заменяет передачу "
    "Компьютер A → Компьютер B.",
    "Порог SNR задаётся заранее и не подбирается под полученный результат.",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Калибровка акустического тракта: тон-сетка через динамик, запись "
            "микрофоном, уровень/SNR/смещение частоты, WAV и JSON."
        ),
        epilog=(
            "Коды возврата: 0 — PASS, 2 — аргументы, 3 — нет устройства, "
            "4 — ошибка ввода-вывода, 5 — обязательные частоты не поддержаны."
        ),
    )
    parser.add_argument(
        "--analyze",
        type=Path,
        metavar="WAV",
        help="анализировать существующую запись вместо воспроизведения",
    )
    parser.add_argument(
        "--input-device",
        help="индекс или часть имени микрофона (по умолчанию системный)",
    )
    parser.add_argument(
        "--output-device",
        help="индекс или часть имени динамика (по умолчанию системный)",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=48_000,
        metavar="HZ",
        help="частота дискретизации (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--frequencies",
        default=",".join(f"{value:g}" for value in calibration.DEFAULT_FREQUENCIES_HZ),
        metavar="LIST",
        help="тон-сетка через запятую, Гц (по умолчанию встроенная 0.5–22 кГц)",
    )
    parser.add_argument(
        "--required",
        default=",".join(
            f"{value:g}" for value in calibration.REQUIRED_FREQUENCIES_HZ
        ),
        metavar="LIST",
        help="частоты, обязательные для PASS (по умолчанию 18000,19000)",
    )
    parser.add_argument(
        "--tone-ms",
        type=float,
        default=calibration.DEFAULT_TONE_MS,
        metavar="MS",
        help="длительность одного тона (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--gap-ms",
        type=float,
        default=calibration.DEFAULT_GAP_MS,
        metavar="MS",
        help="пауза между тонами (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--lead-ms",
        type=float,
        default=calibration.DEFAULT_LEAD_MS,
        metavar="MS",
        help="тишина до первого тона (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--tail-ms",
        type=float,
        default=calibration.DEFAULT_TAIL_MS,
        metavar="MS",
        help="тишина после последнего тона (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--amplitude",
        type=float,
        default=calibration.DEFAULT_AMPLITUDE,
        metavar="LEVEL",
        help="амплитуда тонов в долях полной шкалы (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--ramp-ms",
        type=float,
        default=calibration.DEFAULT_RAMP_MS,
        metavar="MS",
        help="рампа каждого тона (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--min-snr-db",
        type=float,
        default=calibration.DEFAULT_MIN_SNR_DB,
        metavar="DB",
        help="минимальный SNR для обнаружения тона (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--max-offset-hz",
        type=float,
        default=calibration.DEFAULT_MAX_OFFSET_HZ,
        metavar="HZ",
        help="допустимое смещение частоты (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="WAV записи (по умолчанию results/hw-calibration-<время>.wav)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="JSON-отчёт (по умолчанию рядом с WAV)",
    )
    plot = parser.add_mutually_exclusive_group()
    plot.add_argument(
        "--plot",
        type=Path,
        help="PNG со спектром (по умолчанию рядом с JSON)",
    )
    plot.add_argument(
        "--no-plot",
        action="store_true",
        help="не строить PNG",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except calibration.CalibrationError as exc:
        _fail(f"ошибка параметров: {exc}", None)
        return EXIT_USAGE
    except (audio.NoAudioDevice, audio.DeviceNotFound) as exc:
        _fail(f"устройство не выбрано: {exc}", "проверьте подключение и --help")
        return EXIT_UNAVAILABLE
    except audio.AudioBackendUnavailable as exc:
        _fail(f"аудиобэкенд недоступен: {exc}", None)
        return EXIT_UNAVAILABLE
    except (calibration.PlaybackError, audio.CaptureError) as exc:
        _fail(f"ошибка воспроизведения/записи: {exc}", "проверьте устройства и громкость")
        return EXIT_IO
    except WavFormatError as exc:
        _fail(f"ошибка WAV: {exc}", None)
        return EXIT_IO
    except OSError as exc:
        _fail(f"ошибка файла: {exc}", None)
        return EXIT_IO


def run(args: argparse.Namespace) -> int:
    plan = build_plan(args)
    if not 0.0 <= args.min_snr_db <= 200.0:
        raise calibration.CalibrationError("--min-snr-db должен быть в 0..200")
    if not 0.0 < args.max_offset_hz <= 1_000.0:
        raise calibration.CalibrationError("--max-offset-hz должен быть в (0, 1000]")

    stem = _default_stem()
    report_path = args.report or (
        args.out.with_suffix(".json") if args.out is not None else stem.with_suffix(".json")
    )
    plot_path = None if args.no_plot else (args.plot or report_path.with_suffix(".png"))

    if args.analyze is not None:
        return _run_analysis(args, plan, report_path, plot_path)

    return _run_capture(args, plan, report_path, plot_path)


def _run_capture(
    args: argparse.Namespace,
    plan: calibration.CalibrationPlan,
    report_path: Path,
    plot_path: Path | None,
) -> int:
    signal = calibration.generate_signal(plan)
    devices = audio.list_devices()
    input_device = audio.select_device(args.input_device, "input", devices)
    output_device = audio.select_device(args.output_device, "output", devices)

    print(
        f"Вход: [{input_device.index}] {input_device.name} "
        f"({input_device.hostapi_name})"
    )
    print(
        f"Выход: [{output_device.index}] {output_device.name} "
        f"({output_device.hostapi_name})"
    )
    print(
        f"План: {len(plan.frequencies_hz)} тонов, {plan.duration_s:.2f} с, "
        f"Fs={plan.sample_rate} Гц, амплитуда {plan.amplitude:g}"
    )
    print("Воспроизведение и запись...", flush=True)

    capture = calibration.play_and_record(
        signal,
        plan.sample_rate,
        input_device,
        output_device,
    )

    wav_path = args.out or report_path.with_suffix(".wav")
    write_pcm16(wav_path, capture.samples, plan.sample_rate)
    analysis = calibration.analyze_capture(
        capture.samples,
        plan.sample_rate,
        plan,
        min_snr_db=args.min_snr_db,
        max_offset_hz=args.max_offset_hz,
    )

    files = {
        "wav": str(wav_path),
        "wav_sha256": calibration.sha256_file(wav_path),
        "report": str(report_path),
        "plot": str(plot_path) if plot_path is not None else None,
    }
    report = build_report(
        plan=plan,
        analysis=analysis,
        capture=capture.as_dict(),
        devices={
            "input": input_device.as_dict(),
            "output": output_device.as_dict(),
        },
        files=files,
        command=format_command(args),
    )
    write_json(report_path, report)
    if plot_path is not None:
        calibration.write_plot(plot_path, analysis)

    print_calibration_summary(analysis)
    print(f"\nWAV: {wav_path}")
    print(f"JSON: {report_path}")
    if plot_path is not None:
        print(f"PNG: {plot_path}")
    return EXIT_OK if analysis.verdict == "PASS" else EXIT_FAIL


def _run_analysis(
    args: argparse.Namespace,
    plan: calibration.CalibrationPlan,
    report_path: Path,
    plot_path: Path | None,
) -> int:
    source = Path(args.analyze)
    samples, sample_rate = read_pcm16(
        source,
        expected_sample_rate=plan.sample_rate,
    )
    analysis = calibration.analyze_capture(
        samples,
        sample_rate,
        plan,
        min_snr_db=args.min_snr_db,
        max_offset_hz=args.max_offset_hz,
    )
    files = {
        "wav": str(source),
        "wav_sha256": calibration.sha256_file(source),
        "report": str(report_path),
        "plot": str(plot_path) if plot_path is not None else None,
    }
    report = build_report(
        plan=plan,
        analysis=analysis,
        capture={
            "mode": "file",
            "source_wav": str(source),
            "sample_rate_hz": sample_rate,
            "channels": 1,
            "frames": int(samples.size),
            "duration_s": float(samples.size / sample_rate),
            "wall_time_s": None,
            "overflows": 0,
            "warnings": [],
        },
        devices=None,
        files=files,
        command=format_command(args),
    )
    write_json(report_path, report)
    if plot_path is not None:
        calibration.write_plot(plot_path, analysis)

    print(f"Анализ файла: {source}")
    print_calibration_summary(analysis)
    print(f"\nJSON: {report_path}")
    if plot_path is not None:
        print(f"PNG: {plot_path}")
    return EXIT_OK if analysis.verdict == "PASS" else EXIT_FAIL


def build_plan(args: argparse.Namespace) -> calibration.CalibrationPlan:
    """Собрать план из аргументов CLI с понятными ошибками."""
    frequencies = calibration.parse_frequency_list(args.frequencies)
    required = calibration.parse_frequency_list(args.required)
    return calibration.CalibrationPlan(
        sample_rate=args.sample_rate,
        frequencies_hz=frequencies,
        required_frequencies_hz=required,
        tone_ms=args.tone_ms,
        gap_ms=args.gap_ms,
        lead_ms=args.lead_ms,
        tail_ms=args.tail_ms,
        amplitude=args.amplitude,
        ramp_ms=args.ramp_ms,
    )


def build_report(
    *,
    plan: calibration.CalibrationPlan,
    analysis: calibration.CalibrationAnalysis,
    capture: dict[str, Any],
    devices: dict[str, Any] | None,
    files: dict[str, str | None],
    command: str,
) -> dict[str, Any]:
    """Собрать JSON-отчёт: план, методика, измерения, пороги, файлы."""
    analysis_fields = analysis.as_dict()
    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": utc_now(),
        "environment": environment(),
        "command": command,
        "method": {
            "tone_generation": (
                "Синусоиды с рампой; каждая в своём временном окне, между "
                "тонами тишина. PCM16 WAV на выходе, запись микрофоном."
            ),
            "level": "level_dbfs = 10·log10(P_tone), P_tone = (a² + b²)/2",
            "noise": "P_noise = mean((x - a·cos - b·sin)²) в том же окне",
            "snr": "SNR_dB = 10·log10(P_tone / P_noise)",
            "frequency": (
                "Максимум спектра около ожидаемой частоты; положение "
                "уточняется параболической интерполяцией по трём бинам"
            ),
            "window": (
                f"Окно {calibration.MEASUREMENT_WINDOW_S:g} с (не длиннее тона), "
                "края 10% отбрасываются; время окна выбирается по максимуму "
                "огибающей на ожидаемой частоте"
            ),
            "thresholds": {
                "min_snr_db": analysis.min_snr_db,
                "max_offset_hz": analysis.max_offset_hz,
                "supported": (
                    "SNR >= min_snr_db и |смещение| <= max_offset_hz и нет "
                    "клиппинга в окне"
                ),
                "pass": (
                    "все обязательные частоты поддержаны; остальные частоты "
                    "остаются в отчёте как характеризация"
                ),
            },
        },
        "plan": plan.as_dict(),
        "capture": capture,
        "devices": devices,
        "summary": analysis_fields["summary"],
        "tones": analysis_fields["tones"],
        "spectrum": {
            **analysis_fields["psd"],
            "bands": analysis_fields["bands"],
            "peaks": analysis_fields["peaks"][:10],
        },
        "files": files,
        "warnings": list(analysis.warnings),
        "limitations": LIMITATIONS,
    }


def print_calibration_summary(analysis: calibration.CalibrationAnalysis) -> None:
    summary = analysis.summary_dict()
    print()
    print(
        f"Записано {summary['capture_samples']} отсчётов "
        f"({summary['capture_duration_s']:.2f} с)"
    )
    level = summary["level"]
    floor = summary["noise_floor"]
    print(f"RMS: {level['rms_dbfs']:.1f} dBFS; peak: {level['peak_dbfs']:.1f} dBFS")
    print(
        f"Шумовой пол: {floor['dbfs']:.1f} dBFS; "
        f"клиппинг: {'да' if summary['clipping']['detected'] else 'нет'}"
    )
    print()
    header = (
        f"{'Гц':>7}  {'уровень':>9}  {'SNR':>8}  {'смещ.':>8}  "
        f"{'клип':>5}  статус"
    )
    print(header)
    print("-" * len(header))
    for tone in analysis.tones:
        level_text = f"{tone.level_dbfs:.1f}" if tone.level_dbfs is not None else "n/a"
        snr_text = f"{tone.snr_db:.1f}" if tone.snr_db is not None else "n/a"
        offset_text = (
            f"{tone.frequency_offset_hz:+.1f}"
            if tone.frequency_offset_hz is not None
            else "n/a"
        )
        status = "OK" if tone.supported else tone.reason
        print(
            f"{tone.frequency_hz:>7.0f}  {level_text:>9}  {snr_text:>8}  "
            f"{offset_text:>8}  {tone.clipped_samples:>5}  {status}"
        )
    print()
    print(
        f"Поддержано {summary['tones_supported']} из "
        f"{summary['tones_total']} тонов; обязательные "
        f"{', '.join(f'{value:g}' for value in summary['required_frequencies_hz'])} Гц: "
        f"{'да' if summary['required_supported'] else 'НЕТ'}"
    )
    print(f"Вердикт: {summary['verdict']}")
    for warning in summary["warnings"]:
        print(f"ВНИМАНИЕ: {warning}")


def format_command(args: argparse.Namespace) -> str:
    parts = [
        "python calibrate.py",
        f"--sample-rate {args.sample_rate}",
        f"--frequencies {args.frequencies}",
        f"--required {args.required}",
        f"--tone-ms {args.tone_ms:g}",
        f"--gap-ms {args.gap_ms:g}",
        f"--lead-ms {args.lead_ms:g}",
        f"--tail-ms {args.tail_ms:g}",
        f"--amplitude {args.amplitude:g}",
        f"--ramp-ms {args.ramp_ms:g}",
        f"--min-snr-db {args.min_snr_db:g}",
        f"--max-offset-hz {args.max_offset_hz:g}",
    ]
    if args.analyze is not None:
        parts.append(f"--analyze {args.analyze}")
    if args.input_device:
        parts.append(f"--input-device {args.input_device!r}")
    if args.output_device:
        parts.append(f"--output-device {args.output_device!r}")
    return " ".join(parts)


def _default_stem() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("results") / f"hw-calibration-{stamp}"


def _fail(message: str, hint: str | None) -> None:
    print(message, file=sys.stderr)
    if hint:
        print(f"Подсказка: {hint}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
