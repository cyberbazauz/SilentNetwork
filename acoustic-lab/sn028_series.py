"""SN-028: разбор экспортированных WAV серии Laptop → Phone.

Пример:

    python sn028_series.py --dir C:\\Temp\\sn028-30cm --distance 30cm
    python sn028_series.py --dir C:\\Temp\\sn028-noise --distance noise

Файлы должны называться ``sn028-w2p-<дистанция>-<M1..M4>-<номер>.wav``.
Отчёт CSV/JSON/Markdown сохраняется в ``results/sn028/series``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from snlab import sn028_series
from snlab.config import DATA_PROFILES
from snlab.reporting import write_json

EXIT_PASS = 0
EXIT_FAIL = 2
EXIT_INCOMPLETE = 3
EXIT_USAGE = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Разобрать WAV серии SN-028 и проверить текст и CRC",
    )
    parser.add_argument("--dir", required=True, help="каталог с WAV-записями")
    parser.add_argument(
        "--distance",
        required=True,
        help="30cm, 1m, 3m или noise",
    )
    parser.add_argument(
        "--profile",
        default="data-audible",
        choices=sorted(DATA_PROFILES),
        help="профиль передачи (по умолчанию data-audible)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="каталог отчётов (по умолчанию results/sn028/series)",
    )
    parser.add_argument(
        "--min-preamble",
        type=int,
        default=sn028_series.ACOUSTIC_MIN_PREAMBLE_MATCHES,
        help="минимум совпавших бит преамбулы (для воздуха 28)",
    )
    return parser


def default_out_dir() -> Path:
    return Path(__file__).resolve().parent / "results" / "sn028" / "series"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = sn028_series.analyze_directory(
            args.dir,
            distance=args.distance,
            profile=DATA_PROFILES[args.profile],
            min_preamble_matches=args.min_preamble,
        )
    except sn028_series.SeriesError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_USAGE

    out_dir = Path(args.out) if args.out else default_out_dir()
    paths = sn028_series.write_report(report, out_dir)
    print(
        f"Серия {report.distance}: {report.passed}/{len(report.attempts)} "
        f"успешно, вердикт {report.verdict} ({report.threshold})"
    )
    for note in report.notes:
        print(f"  - {note}")
    print(f"Отчёт: {paths['md']}")
    print(f"CSV:   {paths['csv']}")
    print(f"JSON:  {paths['json']}")

    # Сводка в машинно-читаемом виде рядом с отчётами.
    write_json(
        paths["json"].with_name(paths["json"].stem + "-summary.json"),
        {
            "distance": report.distance,
            "profile": report.profile_name,
            "attempts": len(report.attempts),
            "passed": report.passed,
            "failed": report.failed,
            "verdict": report.verdict,
            "threshold": report.threshold,
        },
    )

    if report.verdict == "FAIL":
        return EXIT_FAIL
    if report.verdict == "INCOMPLETE":
        return EXIT_INCOMPLETE
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
