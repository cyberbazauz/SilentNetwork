"""tonegen.py — генератор тестовых тонов и тон-сеток (SN-014, часть A).

Инструмент только создаёт PCM16 WAV и JSON-план. Он **не воспроизводит
звук**: здесь нет ни sounddevice, ни OutputStream, ни вызова play(). Поэтому
его безопасно запускать без аудиооборудования и без флага --hardware-test.

Примеры (PowerShell, из каталога acoustic-lab):

    python tonegen.py --frequency 18000 --duration-ms 1000 --out results/18k.wav
    python tonegen.py --frequencies 1000,8000,16000,18000,19000 --channels 2
    python tonegen.py --frequencies 18000,19000 --out results/mark-space.wav

Коды возврата:
    0 — WAV и JSON созданы;
    2 — неверные аргументы или параметры плана;
    4 — ошибка записи файла.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from snlab import calibration, tones
from snlab.reporting import configure_console, environment, utc_now, write_json

TOOL_NAME = "tonegen.py"
TASK_ID = "SN-014"
SCHEMA = "silentnetwork.acoustic-lab.tone-generator/1"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_IO = 4

LIMITATIONS = [
    "Генератор создаёт только файл и никогда не воспроизводит его.",
    "Наличие WAV не доказывает поддержку частот динамиком или микрофоном.",
    "Одинаковый сигнал во всех каналах — тестовый стимул, а не стерео.",
    "Windows может изменять сигнал при воспроизведении и записи.",
    "Для проверки тракта нужен отдельный физический тест с --hardware-test.",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Создать тестовый WAV с синусоидами и паузами. Звук не "
            "воспроизводится, аудиоустройства не открываются."
        ),
        epilog=(
            "Коды возврата: 0 — файлы созданы, 2 — аргументы, 4 — запись файла. "
            "Для физического теста используйте calibrate.py --hardware-test."
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--frequency",
        type=float,
        metavar="HZ",
        help="одна частота (эквивалент --frequencies с одним значением)",
    )
    source.add_argument(
        "--frequencies",
        metavar="LIST",
        help="частоты через запятую, Гц (по умолчанию 1000,8000,16000,18000,19000)",
    )
    parser.add_argument(
        "--duration-ms",
        type=float,
        default=tones.DEFAULT_TONE_MS,
        metavar="MS",
        help="длительность каждого тона (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=48_000,
        metavar="HZ",
        help="частота дискретизации (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--amplitude",
        type=float,
        default=tones.DEFAULT_AMPLITUDE,
        metavar="LEVEL",
        help="амплитуда в долях полной шкалы (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--channels",
        type=int,
        default=tones.DEFAULT_CHANNELS,
        metavar="N",
        help="число каналов WAV, 1..8 (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--gap-ms",
        type=float,
        default=tones.DEFAULT_GAP_MS,
        metavar="MS",
        help="пауза между тонами (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--lead-ms",
        type=float,
        default=tones.DEFAULT_LEAD_MS,
        metavar="MS",
        help="тишина до первого тона (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--tail-ms",
        type=float,
        default=tones.DEFAULT_TAIL_MS,
        metavar="MS",
        help="тишина после последнего тона (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--ramp-ms",
        type=float,
        default=tones.DEFAULT_RAMP_MS,
        metavar="MS",
        help="рампа каждого тона (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--required",
        metavar="LIST",
        help=(
            "частоты, обязательные для PASS при сравнении; по умолчанию "
            "18000,19000, если они есть в наборе, иначе все частоты"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        metavar="WAV",
        help="выходной WAV (по умолчанию results/tones-<время>.wav)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        metavar="JSON",
        help="JSON-план (по умолчанию рядом с WAV)",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="не создавать JSON-план",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except calibration.CalibrationError as exc:
        _fail(f"ошибка параметров: {exc}")
        return EXIT_USAGE
    except (OSError, ValueError) as exc:
        _fail(f"ошибка файла: {exc}")
        return EXIT_IO


def run(args: argparse.Namespace) -> int:
    plan, calibration_plan = build_plan(args)
    wav_path = args.out or _default_stem().with_suffix(".wav")
    report_path = None if args.no_report else (args.report or wav_path.with_suffix(".json"))

    signal = tones.generate_signal(plan)
    written, digest = tones.write_tone_wav(wav_path, plan, samples=signal)

    print("Генерация тонов: только файл, воспроизведения нет.")
    print(
        f"План: {len(plan.frequencies_hz)} тонов, {plan.duration_s:.2f} с, "
        f"Fs={plan.sample_rate} Гц, каналов {plan.channels}, "
        f"амплитуда {plan.amplitude:g}"
    )
    print("Частоты, Гц: " + ", ".join(f"{value:g}" for value in plan.frequencies_hz))
    print(f"WAV: {written}")
    print(f"Размер: {written.stat().st_size} байт; SHA-256: {digest}")

    if report_path is not None:
        report = build_report(
            plan=plan,
            calibration_plan=calibration_plan,
            wav_path=written,
            wav_sha256=digest,
            command=format_command(args),
        )
        write_json(report_path, report)
        print(f"JSON: {report_path}")

    print("Аудиоустройства не открывались, звук не воспроизводился.")
    return EXIT_OK


def build_plan(args: argparse.Namespace) -> tuple[tones.TonePlan, calibration.CalibrationPlan]:
    """Собрать тон-план и план для анализа с проверкой всех ограничений."""
    if args.frequency is not None:
        frequencies = (float(args.frequency),)
    elif args.frequencies is not None:
        frequencies = calibration.parse_frequency_list(args.frequencies)
    else:
        frequencies = tones.DEFAULT_FREQUENCIES_HZ

    if args.required is not None:
        required = calibration.parse_frequency_list(args.required)
    else:
        preferred = tuple(
            value
            for value in calibration.REQUIRED_FREQUENCIES_HZ
            if value in frequencies
        )
        required = preferred or frequencies

    plan = tones.TonePlan(
        sample_rate=args.sample_rate,
        channels=args.channels,
        frequencies_hz=frequencies,
        tone_ms=args.duration_ms,
        gap_ms=args.gap_ms,
        lead_ms=args.lead_ms,
        tail_ms=args.tail_ms,
        amplitude=args.amplitude,
        ramp_ms=args.ramp_ms,
    )
    return plan, plan.as_calibration_plan(required=required)


def build_report(
    *,
    plan: tones.TonePlan,
    calibration_plan: calibration.CalibrationPlan,
    wav_path: Path,
    wav_sha256: str,
    command: str,
) -> dict:
    plan_payload = calibration_plan.as_dict()
    plan_payload["channels"] = plan.channels
    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": utc_now(),
        "environment": environment(),
        "command": command,
        "mode": "file_only",
        "plan": plan_payload,
        "schedule": plan.schedule(),
        "file": {
            "path": str(wav_path),
            "sha256": wav_sha256,
            "format": "pcm_s16le",
            "sample_rate_hz": plan.sample_rate,
            "channels": plan.channels,
            "frames": plan.total_samples,
            "duration_s": plan.duration_s,
            "bytes": wav_path.stat().st_size,
        },
        "playback": {
            "performed": False,
            "note": "Генератор не воспроизводит звук и не открывает устройства.",
        },
        "warnings": [
            "Это синтетический стимул, а не измерение реального оборудования.",
            "Поддержка частот проверяется только физическим тестом.",
        ],
        "limitations": LIMITATIONS,
    }


def format_command(args: argparse.Namespace) -> str:
    parts = ["python tonegen.py"]
    if args.frequency is not None:
        parts.append(f"--frequency {args.frequency:g}")
    if args.frequencies is not None:
        parts.append(f"--frequencies {args.frequencies}")
    parts.extend(
        [
            f"--duration-ms {args.duration_ms:g}",
            f"--sample-rate {args.sample_rate}",
            f"--amplitude {args.amplitude:g}",
            f"--channels {args.channels}",
            f"--gap-ms {args.gap_ms:g}",
            f"--lead-ms {args.lead_ms:g}",
            f"--tail-ms {args.tail_ms:g}",
            f"--ramp-ms {args.ramp_ms:g}",
        ]
    )
    if args.required is not None:
        parts.append(f"--required {args.required}")
    if args.out is not None:
        parts.append(f"--out {args.out}")
    if args.report is not None:
        parts.append(f"--report {args.report}")
    if args.no_report:
        parts.append("--no-report")
    return " ".join(parts)


def _default_stem() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("results") / f"tones-{stamp}"


def _fail(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
