"""benchmark.py — BER/PER файлового BFSK-канала на AWGN (SN-013).

Инструмент создаёт **измерительные артефакты**: CSV, JSON, PNG и отчёт
M0-report.md. Он не открывает динамик и микрофон и не доказывает передачу
через воздух. Шум добавляется к PCM16-аудио текущего передатчика.

Коды возврата:
    0 — измерения выполнены, файлы созданы;
    2 — неверные аргументы, SNR, профиль или размер payload;
    4 — не удалось записать результат или прочитать/записать WAV.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from snlab.benchmark import (
    DEFAULT_SEED,
    M0_SNR_DB,
    M0_TRIALS,
    BenchmarkConfig,
    BenchmarkError,
    run_benchmark,
    write_ber_plot,
    write_m0_report,
    write_per_plot,
    write_report,
    write_summary_csv,
)
from snlab.bfsk import DEFAULT_RAMP_MS
from snlab.config import DEFAULT_PROFILE, MAX_PAYLOAD_BYTES, PROFILES, ProfileError, get_profile
from snlab.reporting import configure_console

TOOL_NAME = "benchmark.py"
TASK_ID = "SN-013"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_IO = 4

DEFAULT_OUT = Path("results") / "ber.csv"
DEFAULT_REPORT = Path("results") / "benchmark.json"
DEFAULT_BER_PLOT = Path("results") / "ber.png"
DEFAULT_PER_PLOT = Path("results") / "per.png"
DEFAULT_M0_REPORT = Path("results") / "M0-report.md"
DEFAULT_WAV_DIR = Path("results") / "benchmark-wav"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Измерение BER/PER файлового BFSK-канала на смоделированном AWGN. "
            "Результаты AWGN не являются подтверждением передачи через воздух."
        ),
        epilog=(
            "Коды возврата: 0 — успех, 2 — неверные аргументы, "
            "4 — ошибка записи/чтения файла."
        ),
    )
    parser.add_argument(
        "--snr",
        default="0,5,10,15,20",
        help=(
            "список значений SNR в дБ через запятую; обязательные точки M0 — "
            "0,5,10,15,20 (по умолчанию %(default)s)"
        ),
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=100,
        help="число испытаний на каждое значение SNR (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=32,
        help=f"размер payload основного эксперимента, 1..{MAX_PAYLOAD_BYTES} "
        "(по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="базовый seed для payload и шума (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default=DEFAULT_PROFILE.name,
        help="профиль передачи (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--ramp-ms",
        type=float,
        default=DEFAULT_RAMP_MS,
        metavar="MS",
        help="длительность рампы передатчика, мс (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="путь CSV с агрегатами (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help="путь JSON с параметрами и испытаниями (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--ber-plot",
        type=Path,
        default=DEFAULT_BER_PLOT,
        help="путь PNG BER vs SNR (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--per-plot",
        type=Path,
        default=DEFAULT_PER_PLOT,
        help="путь PNG PER vs SNR (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--m0-report",
        type=Path,
        default=DEFAULT_M0_REPORT,
        help="путь Markdown-отчёта M0 (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--m0-snr",
        type=float,
        default=M0_SNR_DB,
        help="SNR для отдельной проверки M0-6 (по умолчанию %(default)s дБ)",
    )
    parser.add_argument(
        "--m0-trials",
        type=int,
        default=M0_TRIALS,
        help="число испытаний M0-6; 0 отключает проверку (по умолчанию %(default)s)",
    )
    parser.add_argument(
        "--m0-seed",
        type=int,
        default=None,
        help="отдельный базовый seed M0-6 (по умолчанию производный от --seed)",
    )
    parser.add_argument(
        "--keep-wav",
        action="store_true",
        help=(
            "сохранить clean/noisy WAV каждого испытания (много файлов); "
            f"без --workdir используется {DEFAULT_WAV_DIR}"
        ),
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help=(
            "каталог для WAV; по умолчанию временный и удаляется, "
            f"а с --keep-wav — {DEFAULT_WAV_DIR}"
        ),
    )
    return parser


def parse_snr_list(text: str) -> tuple[float, ...]:
    """Разобрать список SNR через запятую и отвергнуть пустые/нечисловые."""
    values: list[float] = []
    for part in text.split(","):
        token = part.strip()
        if not token:
            raise BenchmarkError("пустое значение в списке --snr")
        try:
            value = float(token)
        except ValueError:
            raise BenchmarkError(f"не удалось разобрать SNR: {token!r}") from None
        if not math.isfinite(value):
            raise BenchmarkError(f"SNR должен быть конечным числом: {token!r}")
        values.append(value)
    if not values:
        raise BenchmarkError("список --snr пуст")
    return tuple(values)


def build_config(args: argparse.Namespace) -> BenchmarkConfig:
    return BenchmarkConfig(
        snr_db=parse_snr_list(args.snr),
        trials=args.trials,
        payload_bytes=args.payload_bytes,
        seed=args.seed,
        profile=get_profile(args.profile),
        ramp_ms=args.ramp_ms,
        m0_snr_db=args.m0_snr,
        m0_trials=args.m0_trials,
        m0_seed=args.m0_seed,
    )


def main(argv: list[str] | None = None) -> int:
    configure_console()
    args = build_parser().parse_args(argv)
    try:
        config = build_config(args)
        result = run_benchmark(
            config,
            keep_wav=args.keep_wav,
            workdir=resolve_workdir(args),
            progress=print,
            command=format_command(config),
        )
        write_summary_csv(args.out, result.summaries)
        write_report(args.report, result)
        write_ber_plot(args.ber_plot, result.summaries)
        write_per_plot(args.per_plot, result.summaries)
        write_m0_report(args.m0_report, result)
    except (BenchmarkError, ProfileError, ValueError) as exc:
        _fail(f"ошибка параметров: {exc}", "проверьте --snr, --trials и --payload-bytes")
        return EXIT_USAGE
    except OSError as exc:
        _fail(f"ошибка файла: {exc}", "проверьте пути и права на запись")
        return EXIT_IO

    print_results(result, args)
    return EXIT_OK


def resolve_workdir(args: argparse.Namespace) -> Path | None:
    """Выбрать каталог WAV так, чтобы `--keep-wav` не терял файлы.

    Без `--keep-wav` и `--workdir` используется временный каталог, который
    удаляется. С `--keep-wav` без явного каталога WAV сохраняются в
    `results/benchmark-wav`, иначе они были бы удалены вместе с временным.
    """
    if args.workdir is not None:
        return args.workdir
    if args.keep_wav:
        return DEFAULT_WAV_DIR
    return None


def format_command(config: BenchmarkConfig) -> str:
    """Каноническая команда для отчёта; не зависит от sys.argv тестов."""
    snr = ",".join(f"{value:g}" for value in config.snr_db)
    parts = [
        "python benchmark.py",
        f"--snr={snr}",
        f"--trials {config.trials}",
        f"--payload-bytes {config.payload_bytes}",
        f"--seed {config.seed}",
        f"--profile {config.profile.name}",
        f"--ramp-ms {config.ramp_ms:g}",
        f"--m0-snr {config.m0_snr_db:g}",
        f"--m0-trials {config.m0_trials}",
    ]
    if config.m0_seed is not None:
        parts.append(f"--m0-seed {config.m0_seed}")
    return " ".join(parts)


def print_results(result, args: argparse.Namespace) -> None:
    print()
    print("BER/PER на AWGN — файловый канал, не передача через воздух")
    print(
        f"{'SNR, дБ':>9} {'trial':>7} {'OK':>6} {'fail':>6} {'PER':>8} "
        f"{'BER_avail':>11} {'BER_eff':>11} {'sync':>6} {'CRC':>6}"
    )
    for summary in result.summaries:
        ber_available = (
            "n/a" if summary.ber_available is None else f"{summary.ber_available:.3e}"
        )
        print(
            f"{summary.snr_db:>+9g} {summary.trials:>7} {summary.successes:>6} "
            f"{summary.failures:>6} {summary.per:>8.3f} {ber_available:>11} "
            f"{summary.ber_effective:>11.3e} {summary.synchronization_errors:>6} "
            f"{summary.crc_errors:>6}"
        )
    if result.m0_summary is not None:
        m0 = result.m0_summary
        verdict = "PASS" if m0.failures == 0 else "FAIL"
        print(
            f"M0-6: {m0.successes}/{m0.trials} успешно, PER={m0.per:.3f}, "
            f"BER_eff={m0.ber_effective:.3e} → {verdict}"
        )
    print()
    print(f"CSV: {args.out}")
    print(f"JSON: {args.report}")
    print(f"BER PNG: {args.ber_plot}")
    print(f"PER PNG: {args.per_plot}")
    print(f"M0 report: {args.m0_report}")


def _fail(message: str, hint: str | None) -> None:
    print(message, file=sys.stderr)
    if hint:
        print(f"Подсказка: {hint}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
