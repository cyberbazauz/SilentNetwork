"""analyze.py — анализ WAV без аудиоустройств (SN-014, часть B).

Инструмент читает PCM16 WAV, считает уровни, шумовой пол, клиппинг, Welch PSD,
полосы и спектральные пики, затем сохраняет JSON, длинный CSV и графики PSD и
спектрограммы.

Он не открывает микрофон и динамик. Это осознанное ограничение: анализатор
отвечает только за содержимое файла и не выдаёт его за измерение тракта.

Примеры (PowerShell, из каталога acoustic-lab):

    python analyze.py results/18k.wav
    python analyze.py results/capture.wav --json results/capture.analysis.json ^
        --csv results/capture.analysis.csv --plot results/capture.psd.png

Коды возврата:
    0 — анализ выполнен;
    2 — неверные аргументы;
    4 — ошибка чтения WAV или записи результата.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from snlab import analysis as wav_analysis
from snlab.reporting import configure_console, environment, utc_now, write_json
from snlab.wavio import WavFormatError

TOOL_NAME = "analyze.py"
TASK_ID = "SN-014"
SCHEMA = "silentnetwork.acoustic-lab.wav-analysis/1"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_IO = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Анализ PCM16 WAV: RMS/peak, шумовой пол, клиппинг, Welch PSD, "
            "полосы, пики, JSON/CSV и графики. Устройства не открываются."
        ),
        epilog="Коды возврата: 0 — успех, 2 — аргументы, 4 — файл/запись.",
    )
    parser.add_argument("wav", type=Path, metavar="WAV", help="входной PCM16 WAV")
    parser.add_argument(
        "--json",
        type=Path,
        metavar="JSON",
        help="JSON-отчёт (по умолчанию рядом с WAV)",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        metavar="CSV",
        help="длинный CSV с метриками (по умолчанию рядом с WAV)",
    )
    parser.add_argument(
        "--plot",
        type=Path,
        metavar="PNG",
        help="PNG с PSD (по умолчанию рядом с WAV)",
    )
    parser.add_argument(
        "--spectrogram",
        type=Path,
        metavar="PNG",
        help="PNG со спектрограммой (по умолчанию рядом с WAV)",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="не строить PNG (полезно без matplotlib)",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="не создавать CSV",
    )
    parser.add_argument(
        "--top-peaks",
        type=int,
        default=wav_analysis.DEFAULT_TOP_PEAKS,
        metavar="N",
        help="сколько пиков сохранять, 1..50 (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--nperseg",
        type=int,
        metavar="N",
        help="размер сегмента Welch/спектрограммы (по умолчанию авто)",
    )
    parser.add_argument(
        "--spectrogram-channel",
        type=int,
        default=0,
        metavar="N",
        help="канал для спектрограммы (по умолчанию %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except wav_analysis.AnalysisError as exc:
        _fail(f"ошибка анализа: {exc}")
        return EXIT_USAGE
    except (WavFormatError, FileNotFoundError, OSError) as exc:
        _fail(f"ошибка файла: {exc}")
        return EXIT_IO


def run(args: argparse.Namespace) -> int:
    source = Path(args.wav)
    analysis = wav_analysis.analyze_file(
        source,
        top_peaks=args.top_peaks,
        nperseg=args.nperseg,
    )

    stem = source.with_suffix("")
    json_path = args.json or stem.with_name(stem.name + ".analysis.json")
    csv_path = None if args.no_csv else (args.csv or stem.with_name(stem.name + ".analysis.csv"))
    plot_path = None
    spectrogram_path = None
    if not args.no_plots:
        plot_path = args.plot or stem.with_name(stem.name + ".psd.png")
        spectrogram_path = (
            args.spectrogram or stem.with_name(stem.name + ".spectrogram.png")
        )

    report = build_report(
        analysis=analysis,
        files={
            "wav": str(source),
            "json": str(json_path),
            "csv": str(csv_path) if csv_path is not None else None,
            "plot": str(plot_path) if plot_path is not None else None,
            "spectrogram": (
                str(spectrogram_path) if spectrogram_path is not None else None
            ),
        },
        command=format_command(args),
    )
    write_json(json_path, report)
    if csv_path is not None:
        wav_analysis.write_csv(csv_path, analysis)
    if plot_path is not None:
        wav_analysis.write_psd_plot(plot_path, analysis)
    if spectrogram_path is not None:
        wav_analysis.write_spectrogram_plot(
            spectrogram_path,
            analysis,
            channel=args.spectrogram_channel,
            nperseg=args.nperseg,
        )

    print_analysis_summary(analysis)
    print(f"\nJSON: {json_path}")
    if csv_path is not None:
        print(f"CSV: {csv_path}")
    if plot_path is not None:
        print(f"PSD PNG: {plot_path}")
    if spectrogram_path is not None:
        print(f"Спектрограмма PNG: {spectrogram_path}")
    print("Аудиоустройства не открывались: это анализ файла, не запись.")
    return EXIT_OK


def build_report(
    *,
    analysis: wav_analysis.WavAnalysis,
    files: dict[str, str | None],
    command: str,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "task": TASK_ID,
        "generated_at_utc": utc_now(),
        "environment": environment(),
        "command": command,
        "mode": "file_only",
        **analysis.as_dict(),
        "files": files,
        "limitations": list(wav_analysis.LIMITATIONS),
    }


def print_analysis_summary(analysis: wav_analysis.WavAnalysis) -> None:
    print(f"Файл: {analysis.path}")
    print(
        f"Формат: PCM16, {analysis.channels} канал(ов), "
        f"Fs={analysis.sample_rate} Гц, {analysis.frames} отсчётов "
        f"({analysis.duration_s:.3f} с)"
    )
    print(f"SHA-256: {analysis.sha256}")
    print()
    for channel in analysis.channel_analyses:
        summary = channel.summary
        level = summary["level"]
        floor = summary["noise_floor"]
        psd = summary.get("psd") or {}
        title = "моно" if analysis.channels == 1 else f"канал {channel.index}"
        print(f"[{title}]")
        print(
            f"  RMS {level['rms_dbfs']:.1f} dBFS; peak {level['peak_dbfs']:.1f} dBFS; "
            f"crest {level['crest_factor_db']:.1f} дБ"
        )
        if floor is not None:
            print(f"  Шумовой пол: {floor['dbfs']:.1f} dBFS")
        else:
            print("  Шумовой пол: n/a (запись короче 100 мс)")
        clipping = summary["clipping"]
        print(
            f"  Клиппинг: {'да' if clipping['detected'] else 'нет'} "
            f"({clipping['clipped_samples']} отсчётов)"
        )
        if psd:
            print(
                f"  PSD: nperseg={psd['nperseg']}, "
                f"разрешение {psd['resolution_hz']:.2f} Гц"
            )
        peaks = summary.get("peaks") or []
        if peaks:
            text = ", ".join(
                f"{peak['frequency_hz']:.0f} Гц ({peak['level_dbfs_per_hz']:.1f} dBFS/Гц)"
                for peak in peaks[:5]
            )
            print(f"  Пики: {text}")
        for warning in channel.warnings:
            print(f"  ВНИМАНИЕ: {warning}")
    for warning in analysis.warnings:
        print(f"ВНИМАНИЕ: {warning}")


def format_command(args: argparse.Namespace) -> str:
    parts = ["python analyze.py", str(args.wav)]
    if args.json is not None:
        parts.append(f"--json {args.json}")
    if args.csv is not None:
        parts.append(f"--csv {args.csv}")
    if args.plot is not None:
        parts.append(f"--plot {args.plot}")
    if args.spectrogram is not None:
        parts.append(f"--spectrogram {args.spectrogram}")
    if args.no_plots:
        parts.append("--no-plots")
    if args.no_csv:
        parts.append("--no-csv")
    parts.append(f"--top-peaks {args.top_peaks}")
    if args.nperseg is not None:
        parts.append(f"--nperseg {args.nperseg}")
    parts.append(f"--spectrogram-channel {args.spectrogram_channel}")
    return " ".join(parts)


def _fail(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
