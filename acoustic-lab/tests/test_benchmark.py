"""SN-013: воспроизводимый BER/PER benchmark на AWGN."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

import benchmark
from snlab.benchmark import (
    BenchmarkConfig,
    BenchmarkError,
    SnrSummary,
    TrialResult,
    compare_bits,
    run_benchmark,
    summarize,
    write_ber_plot,
    write_m0_report,
    write_per_plot,
    write_report,
    write_summary_csv,
)
from snlab.config import PROFILE_BFSK_18K
from snlab.receiver import SignalNotFound, demodulate_wav
from snlab.wavio import WavFormatError, write_pcm16


def make_config(
    *,
    snr_db: tuple[float, ...] = (15.0,),
    trials: int = 2,
    payload_bytes: int = 8,
    seed: int = 42,
    m0_trials: int = 0,
) -> BenchmarkConfig:
    return BenchmarkConfig(
        snr_db=snr_db,
        trials=trials,
        payload_bytes=payload_bytes,
        seed=seed,
        profile=PROFILE_BFSK_18K,
        m0_trials=m0_trials,
        m0_snr_db=15.0,
    )


def make_trial(**overrides) -> TrialResult:
    base = {
        "snr_db": 15.0,
        "trial": 0,
        "payload_seed": 1,
        "noise_seed": 2,
        "payload_bytes": 32,
        "payload_sha256": "a" * 64,
        "frame_bits": 100,
        "success": True,
        "failure": None,
        "failure_class": None,
        "demodulated": True,
        "crc_ok": True,
        "payload_match": True,
        "available_bits": 100,
        "compared_bits": 100,
        "bit_errors": 0,
        "missing_bits": 0,
        "ber_available": 0.0,
        "ber_effective": 0.0,
        "active_signal_power": 0.03,
        "active_samples": 1_000,
        "noise_std_expected": 0.005,
        "noise_std_measured": 0.005,
        "realized_snr_db": 15.0,
        "clipped_samples": 0,
        "clean_wav_sha256": "b" * 64,
        "noisy_wav_sha256": "c" * 64,
    }
    base.update(overrides)
    return TrialResult(**base)


def test_compare_bits_exact_match() -> None:
    errors, compared, missing, available, effective = compare_bits(
        [0, 1, 1, 0], [0, 1, 1, 0]
    )
    assert (errors, compared, missing) == (0, 4, 0)
    assert available == 0.0
    assert effective == 0.0


def test_compare_bits_counts_flips() -> None:
    errors, compared, missing, available, effective = compare_bits(
        [0, 0, 0, 0], [0, 1, 0, 1]
    )
    assert (errors, compared, missing) == (2, 4, 0)
    assert available == pytest.approx(0.5)
    assert effective == pytest.approx(0.5)


def test_compare_bits_treats_missing_as_errors() -> None:
    errors, compared, missing, available, effective = compare_bits(
        [1, 0, 1, 0], [1, 0]
    )
    assert (errors, compared, missing) == (0, 2, 2)
    assert available == 0.0
    assert effective == pytest.approx(0.5)


def test_compare_bits_without_demodulation_is_not_zero() -> None:
    errors, compared, missing, available, effective = compare_bits(
        [1, 0, 1, 0], None
    )
    assert (errors, compared, missing) == (0, 0, 4)
    assert available is None
    assert effective == 1.0


def test_compare_bits_rejects_empty_reference() -> None:
    with pytest.raises(ValueError, match="пустая"):
        compare_bits([], None)


def test_summarize_counts_all_failures_and_ber() -> None:
    trials = [
        make_trial(trial=0),
        make_trial(trial=1),
        make_trial(
            trial=2,
            success=False,
            failure="CRC",
            failure_class="crc",
            crc_ok=False,
            payload_match=False,
            compared_bits=90,
            bit_errors=5,
            missing_bits=10,
            ber_available=5 / 90,
            ber_effective=15 / 100,
        ),
        make_trial(
            trial=3,
            success=False,
            failure="preamble",
            failure_class="synchronization",
            demodulated=False,
            crc_ok=False,
            payload_match=False,
            available_bits=0,
            compared_bits=0,
            bit_errors=0,
            missing_bits=100,
            ber_available=None,
            ber_effective=1.0,
        ),
        make_trial(
            trial=4,
            success=False,
            failure="mismatch",
            failure_class="payload_mismatch",
            crc_ok=True,
            payload_match=False,
        ),
    ]

    summary = summarize(15.0, trials)

    assert summary.trials == 5
    assert summary.successes == 2
    assert summary.failures == 3
    assert summary.per == pytest.approx(0.6)
    assert summary.bit_errors == 5
    assert summary.bits_compared == 390
    assert summary.bits_missing == 110
    assert summary.total_reference_bits == 500
    assert summary.ber_available == pytest.approx(5 / 390)
    assert summary.ber_effective == pytest.approx(115 / 500)
    assert summary.demodulation_unavailable == 1
    assert summary.demodulation_unavailable_fraction == pytest.approx(0.2)
    assert summary.missing_bit_fraction == pytest.approx(110 / 500)
    assert summary.synchronization_errors == 1
    assert summary.crc_errors == 1
    assert summary.payload_mismatches == 1
    assert summary.other_errors == 0


def test_run_benchmark_success_at_15db(tmp_path: Path) -> None:
    result = run_benchmark(make_config(trials=2), workdir=tmp_path / "work")

    summary = result.summaries[0]
    assert summary.trials == 2
    assert summary.successes == 2
    assert summary.failures == 0
    assert summary.per == 0.0
    assert summary.ber_available == 0.0
    assert summary.ber_effective == 0.0
    assert summary.demodulation_unavailable == 0
    assert all(trial.payload_match for trial in result.trials)
    assert all(trial.crc_ok for trial in result.trials)
    assert all(trial.bit_errors == 0 for trial in result.trials)


def test_run_benchmark_is_reproducible(tmp_path: Path) -> None:
    config = make_config(trials=2, seed=1234)
    first = run_benchmark(config, workdir=tmp_path / "first")
    second = run_benchmark(config, workdir=tmp_path / "second")

    assert [item.as_dict() for item in first.summaries] == [
        item.as_dict() for item in second.summaries
    ]
    assert [item.as_dict() for item in first.trials] == [
        item.as_dict() for item in second.trials
    ]

    first_csv = write_summary_csv(tmp_path / "first.csv", first.summaries)
    second_csv = write_summary_csv(tmp_path / "second.csv", second.summaries)
    assert first_csv.read_bytes() == second_csv.read_bytes()


def test_run_benchmark_different_seed_changes_payload_and_noise(tmp_path: Path) -> None:
    first = run_benchmark(make_config(trials=1, seed=1), workdir=tmp_path / "a")
    second = run_benchmark(make_config(trials=1, seed=2), workdir=tmp_path / "b")

    assert first.trials[0].payload_sha256 != second.trials[0].payload_sha256
    assert first.trials[0].noise_seed != second.trials[0].noise_seed


def test_run_benchmark_writes_csv_and_json(tmp_path: Path) -> None:
    result = run_benchmark(make_config(trials=1), workdir=tmp_path / "work")
    csv_path = write_summary_csv(tmp_path / "ber.csv", result.summaries)
    json_path = write_report(tmp_path / "benchmark.json", result)

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["snr_db"] == "15"
    assert rows[0]["trials"] == "1"
    assert rows[0]["successes"] == "1"
    assert rows[0]["per"] == "0"
    assert rows[0]["ber_available"] == "0"
    assert rows[0]["demodulation_unavailable_fraction"] == "0"
    assert rows[0]["missing_bit_fraction"] == "0"
    assert "synchronization_errors" in rows[0]

    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["schema"] == "silentnetwork.acoustic-lab.benchmark/1"
    assert report["task"] == "SN-013"
    assert report["experiment"]["profile"]["name"] == "bfsk-18k"
    assert report["experiment"]["trials_per_point"] == 1
    assert len(report["results"]) == 1
    assert len(report["trials"]) == 1
    assert report["trials"][0]["payload_bytes"] == 8
    assert report["results"][0]["demodulation_unavailable_fraction"] == 0.0
    assert report["results"][0]["missing_bit_fraction"] == 0.0
    assert "snr_definition" in report["methodology"]
    assert report["m0_6"] is None


def test_m0_report_contains_actual_numbers(tmp_path: Path) -> None:
    result = run_benchmark(
        make_config(trials=1, m0_trials=2),
        workdir=tmp_path / "work",
    )
    path = write_m0_report(tmp_path / "M0-report.md", result)
    text = path.read_text(encoding="utf-8")

    assert "# M0-6 / SN-013" in text
    assert "## Методика" in text
    assert "## Результаты по SNR" in text
    assert "нет демод." in text
    assert "пропущено бит" in text
    assert "## M0-6" in text
    assert f"- Испытаний: {result.m0_summary.trials}" in text
    assert "PASS" in text or "FAIL" in text
    assert "Передача через динамик и микрофон (L2) не выполнялась." in text


def test_plots_are_written(tmp_path: Path) -> None:
    result = run_benchmark(make_config(trials=1), workdir=tmp_path / "work")

    ber_path = write_ber_plot(tmp_path / "ber.png", result.summaries)
    per_path = write_per_plot(tmp_path / "per.png", result.summaries)

    assert ber_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert per_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_invalid_wav_is_rejected_by_diagnostics(tmp_path: Path) -> None:
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")

    with pytest.raises(WavFormatError):
        demodulate_wav(empty, PROFILE_BFSK_18K)

    silence = tmp_path / "silence.wav"
    write_pcm16(silence, np.zeros(1_000), PROFILE_BFSK_18K.sample_rate)
    with pytest.raises(SignalNotFound):
        demodulate_wav(silence, PROFILE_BFSK_18K)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"trials": 0},
        {"payload_bytes": 0},
        {"payload_bytes": 513},
        {"seed": -1},
        {"snr_db": ()},
        {"snr_db": (5.0, 5.0)},
        {"m0_trials": -1},
    ],
)
def test_config_rejects_invalid_values(kwargs) -> None:
    config = {
        "snr_db": (15.0,),
        "trials": 1,
        "payload_bytes": 8,
        "seed": 1,
        "profile": PROFILE_BFSK_18K,
    }
    config.update(kwargs)
    with pytest.raises(BenchmarkError):
        BenchmarkConfig(**config)


def test_keep_wav_without_workdir_uses_persistent_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wav_dir = tmp_path / "default-wav"
    monkeypatch.setattr(benchmark, "DEFAULT_WAV_DIR", wav_dir)

    code = benchmark.main(
        [
            "--snr",
            "15",
            "--trials",
            "1",
            "--payload-bytes",
            "8",
            "--m0-trials",
            "0",
            "--keep-wav",
            "--out",
            str(tmp_path / "ber.csv"),
            "--report",
            str(tmp_path / "benchmark.json"),
            "--ber-plot",
            str(tmp_path / "ber.png"),
            "--per-plot",
            str(tmp_path / "per.png"),
            "--m0-report",
            str(tmp_path / "m0.md"),
        ]
    )

    assert code == benchmark.EXIT_OK
    assert list(wav_dir.glob("*-clean.wav"))
    assert list(wav_dir.glob("*-noisy.wav"))


def test_format_command_is_copy_pasteable_for_negative_snr() -> None:
    config = make_config(snr_db=(-10.0, -5.0, 0.0, 15.0), trials=100)

    command = benchmark.format_command(config)

    assert "--snr=-10,-5,0,15" in command
    assert "--trials 100" in command
    assert "--m0-trials 0" in command


def test_cli_creates_all_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "ber.csv"
    report = tmp_path / "benchmark.json"
    ber_plot = tmp_path / "ber.png"
    per_plot = tmp_path / "per.png"
    m0_report = tmp_path / "M0-report.md"

    code = benchmark.main(
        [
            "--snr",
            "15",
            "--trials",
            "1",
            "--payload-bytes",
            "8",
            "--m0-trials",
            "1",
            "--out",
            str(out),
            "--report",
            str(report),
            "--ber-plot",
            str(ber_plot),
            "--per-plot",
            str(per_plot),
            "--m0-report",
            str(m0_report),
            "--workdir",
            str(tmp_path / "wav"),
        ]
    )

    assert code == benchmark.EXIT_OK
    for path in (out, report, ber_plot, per_plot, m0_report):
        assert path.is_file()
        assert path.stat().st_size > 0
    assert "M0-6" in capsys.readouterr().out


@pytest.mark.parametrize(
    "extra",
    [
        ["--trials", "0"],
        ["--payload-bytes", "0"],
        ["--snr", "abc"],
        ["--snr", ""],
    ],
)
def test_cli_invalid_arguments_return_2(
    tmp_path: Path, extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code = benchmark.main(
        [
            "--out",
            str(tmp_path / "ber.csv"),
            "--report",
            str(tmp_path / "benchmark.json"),
            "--ber-plot",
            str(tmp_path / "ber.png"),
            "--per-plot",
            str(tmp_path / "per.png"),
            "--m0-report",
            str(tmp_path / "m0.md"),
            *extra,
        ]
    )
    assert code == benchmark.EXIT_USAGE
    assert "ошибка параметров" in capsys.readouterr().err


def test_cli_unwritable_output_returns_4(tmp_path: Path) -> None:
    directory = tmp_path / "as-directory"
    directory.mkdir()

    code = benchmark.main(
        [
            "--snr",
            "15",
            "--trials",
            "1",
            "--payload-bytes",
            "8",
            "--m0-trials",
            "0",
            "--out",
            str(directory),
            "--report",
            str(tmp_path / "benchmark.json"),
            "--ber-plot",
            str(tmp_path / "ber.png"),
            "--per-plot",
            str(tmp_path / "per.png"),
            "--m0-report",
            str(tmp_path / "m0.md"),
            "--workdir",
            str(tmp_path / "wav"),
        ]
    )
    assert code == benchmark.EXIT_IO
