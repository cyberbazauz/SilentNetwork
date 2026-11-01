"""Тесты частотной характеристики TX→RX (SN-014, часть D).

Сравниваются только синтетические WAV; устройства не открываются.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import response as response_cli
from snlab import calibration, response
from snlab.bfsk import apply_ramp
from snlab.reporting import write_json
from snlab.wavio import write_pcm16, write_pcm16_channels

SAMPLE_RATE = 48_000


def make_plan(
    *,
    frequencies_hz: tuple[float, ...] = (8_000.0, 18_000.0, 19_000.0),
    required_frequencies_hz: tuple[float, ...] = (18_000.0, 19_000.0),
) -> calibration.CalibrationPlan:
    return calibration.CalibrationPlan(
        sample_rate=SAMPLE_RATE,
        frequencies_hz=frequencies_hz,
        required_frequencies_hz=required_frequencies_hz,
        tone_ms=300.0,
        gap_ms=150.0,
        lead_ms=150.0,
        tail_ms=150.0,
        amplitude=0.4,
        ramp_ms=10.0,
    )


def shifted(plan: calibration.CalibrationPlan, shift_hz: float) -> np.ndarray:
    signal = np.zeros(plan.total_samples, dtype=np.float64)
    for index, frequency in enumerate(plan.frequencies_hz):
        start = plan.tone_start_sample(index)
        stop = plan.tone_end_sample(index)
        time = np.arange(stop - start, dtype=np.float64) / plan.sample_rate
        signal[start:stop] = apply_ramp(
            plan.amplitude
            * np.sin(2.0 * math.pi * (frequency + shift_hz) * time),
            plan.ramp_samples,
        )
    return signal


def muted(plan: calibration.CalibrationPlan, frequency: float) -> np.ndarray:
    signal = calibration.generate_signal(plan)
    for index, value in enumerate(plan.frequencies_hz):
        if value == frequency:
            signal[plan.tone_start_sample(index) : plan.tone_end_sample(index)] = 0.0
    rng = np.random.default_rng(11)
    return signal + rng.normal(0.0, 1e-3, signal.size)


def plan_payload(plan: calibration.CalibrationPlan) -> dict[str, object]:
    return {"plan": {**plan.as_dict(), "channels": 1}}


def write_plan(path: Path, plan: calibration.CalibrationPlan) -> Path:
    write_json(path, plan_payload(plan))
    return path


def test_identical_files_pass_with_zero_attenuation() -> None:
    plan = make_plan()
    signal = calibration.generate_signal(plan)

    result = response.analyze_response(
        signal, SAMPLE_RATE, signal, SAMPLE_RATE, plan
    )

    assert result.verdict == "PASS"
    assert result.required_supported is True
    assert len(result.points) == 3
    for point in result.points:
        assert point.supported is True
        assert point.reason == "ok"
        assert point.attenuation_db == pytest.approx(0.0, abs=0.1)
        assert point.frequency_shift_hz == pytest.approx(0.0, abs=1.0)


def test_constant_gain_is_measured_as_attenuation() -> None:
    plan = make_plan()
    tx = calibration.generate_signal(plan)
    rx = tx * 0.1

    result = response.analyze_response(tx, SAMPLE_RATE, rx, SAMPLE_RATE, plan)

    assert result.verdict == "PASS"
    for point in result.points:
        assert point.attenuation_db == pytest.approx(20.0, abs=0.2)
        assert point.supported is True


def test_large_attenuation_fails_even_with_clean_signal() -> None:
    plan = make_plan()
    tx = calibration.generate_signal(plan)
    rx = tx * 0.001

    result = response.analyze_response(
        tx,
        SAMPLE_RATE,
        rx,
        SAMPLE_RATE,
        plan,
        max_attenuation_db=50.0,
    )

    assert result.verdict == "FAIL"
    assert result.required_supported is False
    for point in result.points:
        assert point.attenuation_db == pytest.approx(60.0, abs=0.5)
        assert point.supported is False
        assert point.reason == "attenuation_exceeded"
    assert any("ослабление" in warning for warning in result.warnings)


def test_frequency_shift_is_detected() -> None:
    plan = make_plan()
    tx = calibration.generate_signal(plan)
    rx = shifted(plan, 100.0)

    result = response.analyze_response(
        tx,
        SAMPLE_RATE,
        rx,
        SAMPLE_RATE,
        plan,
        max_shift_hz=50.0,
    )

    assert result.verdict == "FAIL"
    for point in result.points:
        assert point.frequency_shift_hz == pytest.approx(100.0, abs=2.0)
        assert point.supported is False
        assert point.reason == "frequency_shift_exceeded"


def test_missing_required_tone_fails() -> None:
    plan = make_plan()
    tx = calibration.generate_signal(plan)
    rx = muted(plan, 18_000.0)

    result = response.analyze_response(tx, SAMPLE_RATE, rx, SAMPLE_RATE, plan)

    assert result.verdict == "FAIL"
    assert 18_000.0 in result.unsupported_frequencies_hz
    point = next(item for item in result.points if item.frequency_hz == 18_000.0)
    assert point.rx_detected is False
    assert point.reason == "rx_snr_below_threshold"


def test_clipping_in_receive_is_detected() -> None:
    plan = make_plan()
    tx = calibration.generate_signal(plan)
    rx = np.clip(tx * 4.0, -1.0, 1.0)

    result = response.analyze_response(tx, SAMPLE_RATE, rx, SAMPLE_RATE, plan)

    assert result.verdict == "FAIL"
    point = next(item for item in result.points if item.frequency_hz == 18_000.0)
    assert point.supported is False
    assert point.reason == "rx_clipping"


def test_tx_shorter_than_plan_warns() -> None:
    plan = make_plan()
    tx = calibration.generate_signal(plan)[: plan.tone_end_sample(0)]

    result = response.analyze_response(tx, SAMPLE_RATE, tx, SAMPLE_RATE, plan)

    assert result.verdict == "FAIL"
    assert any("TX:" in warning for warning in result.warnings)


def test_analyze_files_reads_wav_and_plan(tmp_path: Path) -> None:
    plan = make_plan()
    tx = calibration.generate_signal(plan)
    tx_path = write_pcm16(tmp_path / "tx.wav", tx, SAMPLE_RATE)
    rx_path = write_pcm16(tmp_path / "rx.wav", tx * 0.5, SAMPLE_RATE)
    plan_path = write_plan(tmp_path / "tx.json", plan)

    loaded = response.load_plan_json(plan_path)
    assert loaded.frequencies_hz == plan.frequencies_hz
    assert loaded.required_frequencies_hz == plan.required_frequencies_hz

    result = response.analyze_files(tx_path, rx_path, loaded)
    assert result.verdict == "PASS"
    assert result.tx["path"] == str(tx_path)
    assert result.rx["path"] == str(rx_path)
    assert result.tx["frames"] == tx.size
    assert result.points[0].attenuation_db == pytest.approx(6.0, abs=0.2)


def test_multichannel_uses_selected_channel(tmp_path: Path) -> None:
    plan = make_plan()
    signal = calibration.generate_signal(plan)
    stereo = np.column_stack([signal, np.zeros_like(signal)])
    tx_path = write_pcm16_channels(tmp_path / "tx.wav", stereo, SAMPLE_RATE)
    rx_path = write_pcm16_channels(tmp_path / "rx.wav", stereo, SAMPLE_RATE)

    first = response.analyze_files(tx_path, rx_path, plan, channel=0)
    second = response.analyze_files(tx_path, rx_path, plan, channel=1)

    assert first.verdict == "PASS"
    assert second.verdict == "FAIL"
    assert second.channel == 1
    assert any("канал 0" in warning for warning in first.warnings)
    with pytest.raises(response.ResponseError, match="канал 2"):
        response.analyze_files(tx_path, rx_path, plan, channel=2)


def test_load_plan_accepts_bare_plan(tmp_path: Path) -> None:
    plan = make_plan()
    path = tmp_path / "bare.json"
    write_json(path, plan.as_dict())

    loaded = response.load_plan_json(path)
    assert loaded.sample_rate == plan.sample_rate
    assert loaded.tone_ms == plan.tone_ms


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"plan": {}},
        {"plan": {"sample_rate_hz": 48_000}},
        {"plan": {"frequencies_hz": [8_000], "sample_rate_hz": 48_000,
                  "required_frequencies_hz": []}},
        {"plan": {"frequencies_hz": [8_000], "sample_rate_hz": "abc"}},
    ],
)
def test_load_plan_rejects_bad_payload(tmp_path: Path, payload: dict[str, object]) -> None:
    path = tmp_path / "bad.json"
    write_json(path, payload)
    with pytest.raises(response.ResponseError):
        response.load_plan_json(path)


def test_load_plan_rejects_missing_or_invalid_json(tmp_path: Path) -> None:
    with pytest.raises(response.ResponseError, match="не найден"):
        response.load_plan_json(tmp_path / "missing.json")

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(response.ResponseError, match="прочитать"):
        response.load_plan_json(broken)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"min_rx_snr_db": float("nan")}, "min_rx_snr_db"),
        ({"max_attenuation_db": 0.0}, "max_attenuation_db"),
        ({"max_shift_hz": -1.0}, "max_shift_hz"),
    ],
)
def test_invalid_thresholds_rejected(kwargs: dict[str, object], message: str) -> None:
    plan = make_plan()
    signal = calibration.generate_signal(plan)
    with pytest.raises(response.ResponseError, match=message):
        response.analyze_response(
            signal, SAMPLE_RATE, signal, SAMPLE_RATE, plan, **kwargs  # type: ignore[arg-type]
        )


def test_invalid_signals_and_rates_rejected() -> None:
    plan = make_plan()
    signal = calibration.generate_signal(plan)

    with pytest.raises(response.ResponseError, match="одноканальным"):
        response.analyze_response(
            np.column_stack([signal, signal]), SAMPLE_RATE, signal, SAMPLE_RATE, plan
        )
    with pytest.raises(response.ResponseError, match="пуст"):
        response.analyze_response(
            np.array([]), SAMPLE_RATE, signal, SAMPLE_RATE, plan
        )
    with pytest.raises(response.ResponseError, match="NaN"):
        response.analyze_response(
            np.array([np.nan]), SAMPLE_RATE, signal, SAMPLE_RATE, plan
        )
    with pytest.raises(response.ResponseError, match="не совпадает"):
        response.analyze_response(
            signal, 44_100, signal, SAMPLE_RATE, plan
        )
    with pytest.raises(response.ResponseError, match="CalibrationPlan"):
        response.analyze_response(
            signal, SAMPLE_RATE, signal, SAMPLE_RATE, object()  # type: ignore[arg-type]
        )


def test_csv_and_plot_created(tmp_path: Path) -> None:
    plan = make_plan()
    signal = calibration.generate_signal(plan)
    result = response.analyze_response(signal, SAMPLE_RATE, signal, SAMPLE_RATE, plan)

    csv_path = response.write_csv(tmp_path / "response.csv", result)
    plot_path = response.write_plot(tmp_path / "response.png", result)

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert rows[0]["frequency_hz"] == "8000.0"
    assert rows[0]["supported"] == "1"
    assert rows[0]["reason"] == "ok"
    assert plot_path.is_file() and plot_path.stat().st_size > 1_000


def test_required_override_changes_verdict(tmp_path: Path) -> None:
    plan = make_plan(required_frequencies_hz=(8_000.0,))
    tx = calibration.generate_signal(plan)
    rx = muted(plan, 8_000.0)

    result = response.analyze_response(tx, SAMPLE_RATE, rx, SAMPLE_RATE, plan)
    assert result.verdict == "FAIL"

    relaxed = replace(
        plan,
        required_frequencies_hz=(18_000.0,),
    )
    result = response.analyze_response(tx, SAMPLE_RATE, rx, SAMPLE_RATE, relaxed)
    assert result.verdict == "PASS"


def test_cli_pass_and_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = make_plan()
    tx = calibration.generate_signal(plan)
    tx_path = write_pcm16(tmp_path / "tx.wav", tx, SAMPLE_RATE)
    rx_path = write_pcm16(tmp_path / "rx.wav", tx * 0.5, SAMPLE_RATE)
    plan_path = write_plan(tmp_path / "tx.json", plan)
    json_path = tmp_path / "report.json"
    csv_path = tmp_path / "report.csv"

    code = response_cli.main(
        [
            "--tx",
            str(tx_path),
            "--rx",
            str(rx_path),
            "--plan",
            str(plan_path),
            "--json",
            str(json_path),
            "--csv",
            str(csv_path),
            "--no-plot",
        ]
    )

    assert code == response_cli.EXIT_OK
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema"] == response_cli.SCHEMA
    assert payload["task"] == "SN-014"
    assert payload["summary"]["verdict"] == "PASS"
    assert payload["method"]["thresholds"]["channel"] == 0
    assert csv_path.is_file()
    assert "PASS" in capsys.readouterr().out

    loud_loss = write_pcm16(tmp_path / "rx-loss.wav", tx * 0.001, SAMPLE_RATE)
    code = response_cli.main(
        [
            "--tx",
            str(tx_path),
            "--rx",
            str(loud_loss),
            "--plan",
            str(plan_path),
            "--json",
            str(tmp_path / "fail.json"),
            "--csv",
            str(tmp_path / "fail.csv"),
            "--no-plot",
        ]
    )
    assert code == response_cli.EXIT_FAIL
    payload = json.loads((tmp_path / "fail.json").read_text(encoding="utf-8"))
    assert payload["summary"]["verdict"] == "FAIL"


def test_cli_errors(tmp_path: Path) -> None:
    plan = make_plan()
    tx = calibration.generate_signal(plan)
    tx_path = write_pcm16(tmp_path / "tx.wav", tx, SAMPLE_RATE)
    plan_path = write_plan(tmp_path / "tx.json", plan)

    code = response_cli.main(
        [
            "--tx",
            str(tx_path),
            "--rx",
            str(tmp_path / "missing.wav"),
            "--plan",
            str(plan_path),
            "--no-plot",
        ]
    )
    assert code == response_cli.EXIT_IO

    bad_plan = tmp_path / "bad.json"
    write_json(bad_plan, {"plan": {}})
    code = response_cli.main(
        [
            "--tx",
            str(tx_path),
            "--rx",
            str(tx_path),
            "--plan",
            str(bad_plan),
            "--no-plot",
        ]
    )
    assert code == response_cli.EXIT_USAGE
