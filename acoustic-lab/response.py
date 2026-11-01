"""response.py — частотная характеристика тракта по двум WAV (SN-014, часть D).

Инструмент сравнивает переданный (TX) и принятый (RX) файлы:

* уровень на каждой частоте и ослабление TX→RX;
* SNR в принятом файле;
* фактическую частоту тона до и после тракта;
* список частот, которые проходят плохо.

Он не открывает устройства и не воспроизводит звук. План берётся из JSON,
который создал `tonegen.py`: без знания таймингов нельзя корректно нарезать
запись на тон-точки.

Пример (PowerShell, из каталога acoustic-lab):

    python response.py --tx results/mark-space.wav ^
        --rx results/mark-space-rx.wav --plan results/mark-space.json ^
        --json results/mark-space-response.json

Коды возврата:
    0 — PASS: обязательные частоты прошли;
    2 — неверные аргументы или план;
    4 — ошибка чтения/записи файла;
    5 — FAIL: обязательные частоты не прошли.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from snlab import calibration, response
from snlab.reporting import configure_console, environment, utc_now, write_json
from snlab.wavio import WavFormatError

TOOL_NAME = "response.py"
TASK_ID = "SN-014"
SCHEMA = "silentnetwork.acoustic-lab.frequency-response/1"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_IO = 4
EXIT_FAIL = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Сравнить переданный и принятый WAV: ослабление, сдвиг частоты, "
            "SNR и список плохо проходящих частот. Устройства не открываются."
        ),
        epilog=(
            "Коды возврата: 0 — PASS, 2 — аргументы/план, 4 — файл, 5 — FAIL."
        ),
    )
    parser.add_argument("--tx", type=Path, required=True, metavar="WAV", help="переданный WAV")
    parser.add_argument("--rx", type=Path, required=True, metavar="WAV", help="принятый WAV")
    parser.add_argument(
        "--plan",
        type=Path,
        required=True,
        metavar="JSON",
        help="JSON-план от tonegen.py с таймингами и частотами",
    )
    parser.add_argument(
        "--required",
        metavar="LIST",
        help="переопределить обязательные частоты для PASS",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=response.DEFAULT_CHANNEL,
        metavar="N",
        help="канал для анализа (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--min-rx-snr-db",
        type=float,
        default=response.DEFAULT_MIN_RX_SNR_DB,
        metavar="DB",
        help="минимальный SNR в RX (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--max-attenuation-db",
        type=float,
        default=response.DEFAULT_MAX_ATTENUATION_DB,
        metavar="DB",
        help="допустимое ослабление TX→RX (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--max-shift-hz",
        type=float,
        default=response.DEFAULT_MAX_SHIFT_HZ,
        metavar="HZ",
        help="допустимый сдвиг частоты TX→RX (по умолчанию %(default)s)",
    )
    parser.add_argument("--json", type=Path, metavar="JSON", help="JSON-отчёт")
    parser.add_argument("--csv", type=Path, metavar="CSV", help="CSV по точкам")
    plot = parser.add_mutually_exclusive_group()
    plot.add_argument("--plot", type=Path, metavar="PNG", help="PNG с АЧХ")
    plot.add_argument("--no-plot", action="store_true", help="не строить PNG")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (response.ResponseError, calibration.CalibrationError) as exc:
        _fail(f"ошибка сравнения: {exc}")
        return EXIT_USAGE
    except (WavFormatError, FileNotFoundError, OSError) as exc:
        _fail(f"ошибка файла: {exc}")
        return EXIT_IO


def run(args: argparse.Namespace) -> int:
    plan = response.load_plan_json(args.plan)
    if args.required is not None:
        required = calibration.parse_frequency_list(args.required)
        plan = replace(plan, required_frequencies_hz=required)

    analysis = response.analyze_files(
        args.tx,
        args.rx,
        plan,
        channel=args.channel,
        min_rx_snr_db=args.min_rx_snr_db,
        max_attenuation_db=args.max_attenuation_db,
        max_shift_hz=args.max_shift_hz,
    )

    stem = Path(args.rx).with_suffix("")
    json_path = args.json or stem.with_name(stem.name + ".response.json")
    csv_path = args.csv or stem.with_name(stem.name + ".response.csv")
    plot_path = None if args.no_plot else (
        args.plot or stem.with_name(stem.name + ".response.png")
    )

    report = build_report(
        analysis=analysis,
        plan=plan,
        files={
            "tx": str(args.tx),
            "rx": str(args.rx),
            "plan": str(args.plan),
            "json": str(json_path),
            "csv": str(csv_path),
            "plot": str(plot_path) if plot_path is not None else None,
        },
        command=format_command(args),
    )
    write_json(json_path, report)
    response.write_csv(csv_path, analysis)
    if plot_path is not None:
        response.write_plot(plot_path, analysis)

    print_response_summary(analysis)
    print(f"\nJSON: {json_path}")
    print(f"CSV: {csv_path}")
    if plot_path is not None:
        print(f"PNG: {plot_path}")
    print("Аудиоустройства не открывались: это сравнение файлов, не запись.")
    return EXIT_OK if analysis.verdict == "PASS" else EXIT_FAIL


def build_report(
    *,
    analysis: response.ResponseAnalysis,
    plan: calibration.CalibrationPlan,
    files: dict[str, str | None],
    command: str,
) -> dict[str, Any]:
    fields = analysis.as_dict()
    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": utc_now(),
        "environment": environment(),
        "command": command,
        "mode": "file_only",
        "method": {
            "tone_measurement": (
                "Один и тот же анализ `calibration.analyze_capture` применяется "
                "к TX и RX: окно по максимуму огибающей, МНК-подгонка синуса, "
                "SNR по остатку."
            ),
            "attenuation": "attenuation_db = TX level_dbfs - RX level_dbfs",
            "frequency_shift": (
                "frequency_shift_hz = RX dominant frequency - TX dominant frequency"
            ),
            "supported": (
                "TX тон обнаружен; RX тон обнаружен без клиппинга и смещения; "
                "ослабление <= max_attenuation_db; сдвиг <= max_shift_hz"
            ),
            "pass": (
                "все обязательные частоты поддержаны; остальные частоты "
                "остаются в отчёте как характеризация"
            ),
            "thresholds": {
                "min_rx_snr_db": analysis.min_rx_snr_db,
                "max_attenuation_db": analysis.max_attenuation_db,
                "max_shift_hz": analysis.max_shift_hz,
                "channel": analysis.channel,
            },
        },
        "plan": plan.as_dict(),
        "summary": fields["summary"],
        "tx": fields["tx"],
        "rx": fields["rx"],
        "points": fields["points"],
        "files": files,
        "limitations": list(response.LIMITATIONS),
    }


def print_response_summary(analysis: response.ResponseAnalysis) -> None:
    summary = analysis.summary_dict()
    print(f"TX: {analysis.tx.get('path', 'n/a')}")
    print(f"RX: {analysis.rx.get('path', 'n/a')}")
    print(
        f"Порог: SNR RX >= {analysis.min_rx_snr_db:g} дБ, "
        f"ослабление <= {analysis.max_attenuation_db:g} дБ, "
        f"сдвиг <= {analysis.max_shift_hz:g} Гц"
    )
    print()
    header = (
        f"{'Гц':>7}  {'TX dBFS':>8}  {'RX dBFS':>8}  {'ослаб.':>7}  "
        f"{'SNR RX':>7}  {'сдвиг':>6}  статус"
    )
    print(header)
    print("-" * len(header))
    for point in analysis.points:
        tx_text = f"{point.tx_level_dbfs:.1f}" if point.tx_level_dbfs is not None else "n/a"
        rx_text = f"{point.rx_level_dbfs:.1f}" if point.rx_level_dbfs is not None else "n/a"
        att_text = f"{point.attenuation_db:.1f}" if point.attenuation_db is not None else "n/a"
        snr_text = f"{point.rx_snr_db:.1f}" if point.rx_snr_db is not None else "n/a"
        shift_text = (
            f"{point.frequency_shift_hz:+.1f}"
            if point.frequency_shift_hz is not None
            else "n/a"
        )
        status = "OK" if point.supported else point.reason
        print(
            f"{point.frequency_hz:>7.0f}  {tx_text:>8}  {rx_text:>8}  "
            f"{att_text:>7}  {snr_text:>7}  {shift_text:>6}  {status}"
        )
    print()
    print(
        f"Прошло {summary['tones_supported']} из {summary['tones_total']} точек; "
        f"обязательные "
        f"{', '.join(f'{value:g}' for value in summary['required_frequencies_hz'])} Гц: "
        f"{'да' if summary['required_supported'] else 'НЕТ'}"
    )
    print(f"Вердикт: {summary['verdict']}")
    for warning in summary["warnings"]:
        print(f"ВНИМАНИЕ: {warning}")


def format_command(args: argparse.Namespace) -> str:
    parts = [
        "python response.py",
        f"--tx {args.tx}",
        f"--rx {args.rx}",
        f"--plan {args.plan}",
        f"--channel {args.channel}",
        f"--min-rx-snr-db {args.min_rx_snr_db:g}",
        f"--max-attenuation-db {args.max_attenuation_db:g}",
        f"--max-shift-hz {args.max_shift_hz:g}",
    ]
    if args.required is not None:
        parts.append(f"--required {args.required}")
    if args.json is not None:
        parts.append(f"--json {args.json}")
    if args.csv is not None:
        parts.append(f"--csv {args.csv}")
    if args.plot is not None:
        parts.append(f"--plot {args.plot}")
    if args.no_plot:
        parts.append("--no-plot")
    return " ".join(parts)


def _fail(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
