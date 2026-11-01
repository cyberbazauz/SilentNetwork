"""CLI calibrate.py (SN-012): PASS/FAIL, коды возврата, WAV и JSON.

Аудиобэкенд подменяется: реальные динамик и микрофон не открываются.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import calibrate
from snlab import audio, calibration
from snlab.wavio import read_pcm16, write_pcm16

SAMPLE_RATE = 48_000
TONE_ARGS = [
    "--frequencies",
    "8000,18000,19000",
    "--required",
    "18000,19000",
    "--tone-ms",
    "300",
    "--gap-ms",
    "150",
    "--lead-ms",
    "150",
    "--tail-ms",
    "150",
]


def make_device(
    index: int,
    *,
    name: str,
    inputs: int,
    outputs: int,
    hostapi: int = 0,
    hostapi_name: str = "MME",
) -> audio.DeviceInfo:
    return audio.DeviceInfo(
        index=index,
        name=name,
        hostapi=hostapi,
        hostapi_name=hostapi_name,
        max_input_channels=inputs,
        max_output_channels=outputs,
        default_samplerate=48_000.0,
        default_low_input_latency=0.01,
        default_low_output_latency=0.01,
        is_default_input=bool(inputs),
        is_default_output=bool(outputs),
    )


def plan_from_args() -> calibration.CalibrationPlan:
    args = calibrate.build_parser().parse_args(TONE_ARGS)
    return calibrate.build_plan(args)


def capture_for_plan(
    plan: calibration.CalibrationPlan,
    *,
    latency_samples: int = 480,
    noise_rms: float = 1e-3,
    mute: frozenset[float] = frozenset(),
) -> np.ndarray:
    signal = calibration.generate_signal(plan)
    if mute:
        for index, frequency in enumerate(plan.frequencies_hz):
            if frequency in mute:
                signal[
                    plan.tone_start_sample(index) : plan.tone_end_sample(index)
                ] = 0.0
    padded = np.concatenate(
        (np.zeros(latency_samples), signal, np.zeros(latency_samples))
    )
    rng = np.random.default_rng(7)
    return (padded + rng.normal(0.0, noise_rms, padded.size)).astype(np.float64)


def write_capture(
    path: Path,
    plan: calibration.CalibrationPlan,
    *,
    mute: frozenset[float] = frozenset(),
) -> None:
    samples = capture_for_plan(plan, mute=mute)
    write_pcm16(path, samples, plan.sample_rate)


def test_analyze_pass_writes_report_and_plot(tmp_path: Path) -> None:
    plan = plan_from_args()
    wav = tmp_path / "capture.wav"
    write_capture(wav, plan)
    report = tmp_path / "report.json"
    plot = tmp_path / "spectrum.png"

    code = calibrate.main(
        [
            "--analyze",
            str(wav),
            *TONE_ARGS,
            "--report",
            str(report),
            "--plot",
            str(plot),
        ]
    )

    assert code == calibrate.EXIT_OK
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["schema"] == calibrate.SCHEMA
    assert payload["task"] == "SN-012"
    assert payload["summary"]["verdict"] == "PASS"
    assert payload["summary"]["required_supported"] is True
    assert payload["summary"]["tones_supported"] == 3
    assert payload["capture"]["mode"] == "file"
    assert payload["capture"]["frames"] == plan.total_samples + 960
    assert payload["devices"] is None
    assert payload["files"]["wav"] == str(wav)
    assert payload["files"]["plot"] == str(plot)
    assert payload["plan"]["frequencies_hz"] == [8_000.0, 18_000.0, 19_000.0]
    assert "snr" in payload["method"]
    assert plot.is_file()


def test_analyze_fail_returns_5(tmp_path: Path) -> None:
    plan = plan_from_args()
    wav = tmp_path / "silence.wav"
    write_pcm16(wav, np.zeros(plan.total_samples), plan.sample_rate)
    report = tmp_path / "report.json"

    code = calibrate.main(
        [
            "--analyze",
            str(wav),
            *TONE_ARGS,
            "--report",
            str(report),
            "--no-plot",
        ]
    )

    assert code == calibrate.EXIT_FAIL
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["summary"]["verdict"] == "FAIL"
    assert payload["summary"]["required_supported"] is False
    assert payload["files"]["plot"] is None


def test_analyze_missing_file_returns_4(tmp_path: Path) -> None:
    code = calibrate.main(
        [
            "--analyze",
            str(tmp_path / "absent.wav"),
            *TONE_ARGS,
            "--report",
            str(tmp_path / "report.json"),
            "--no-plot",
        ]
    )

    assert code == calibrate.EXIT_IO


def test_analyze_invalid_wav_returns_4(tmp_path: Path) -> None:
    broken = tmp_path / "broken.wav"
    broken.write_text("not a wave file", encoding="utf-8")

    code = calibrate.main(
        [
            "--analyze",
            str(broken),
            *TONE_ARGS,
            "--report",
            str(tmp_path / "report.json"),
            "--no-plot",
        ]
    )

    assert code == calibrate.EXIT_IO


def test_analyze_wrong_sample_rate_returns_4(tmp_path: Path) -> None:
    wav = tmp_path / "capture.wav"
    write_pcm16(wav, np.zeros(4_800), 44_100)

    code = calibrate.main(
        [
            "--analyze",
            str(wav),
            *TONE_ARGS,
            "--report",
            str(tmp_path / "report.json"),
            "--no-plot",
        ]
    )

    assert code == calibrate.EXIT_IO


def test_live_capture_writes_wav_report_and_plot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = plan_from_args()
    devices = [
        make_device(1, name="Fake Mic", inputs=1, outputs=0),
        make_device(2, name="Fake Speaker", inputs=0, outputs=2),
    ]
    monkeypatch.setattr(
        calibrate.audio, "list_devices", lambda sd_module=None: devices
    )

    def fake_play_and_record(
        signal: np.ndarray,
        sample_rate: int,
        input_device: audio.DeviceInfo,
        output_device: audio.DeviceInfo,
        **kwargs: Any,
    ) -> calibration.PlaybackCapture:
        assert sample_rate == SAMPLE_RATE
        assert input_device.index == 1
        assert output_device.index == 2
        samples = np.concatenate(
            (np.zeros(480), np.asarray(signal, dtype=np.float32), np.zeros(480))
        )
        return calibration.PlaybackCapture(
            samples=samples,
            sample_rate=sample_rate,
            input_device_index=input_device.index,
            output_device_index=output_device.index,
            input_device_name=input_device.name,
            output_device_name=output_device.name,
            mode="duplex",
            overflows=0,
            wall_time_s=0.5,
            warnings=(),
        )

    monkeypatch.setattr(
        calibrate.calibration, "play_and_record", fake_play_and_record
    )

    wav = tmp_path / "live.wav"
    report = tmp_path / "live.json"
    plot = tmp_path / "live.png"
    code = calibrate.main(
        [
            *TONE_ARGS,
            "--out",
            str(wav),
            "--report",
            str(report),
            "--plot",
            str(plot),
        ]
    )

    assert code == calibrate.EXIT_OK
    samples, rate = read_pcm16(wav, expected_sample_rate=SAMPLE_RATE)
    assert rate == SAMPLE_RATE
    assert samples.size > plan.total_samples
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["capture"]["mode"] == "duplex"
    assert payload["devices"]["input"]["index"] == 1
    assert payload["devices"]["output"]["index"] == 2
    assert payload["summary"]["verdict"] == "PASS"
    assert payload["files"]["wav_sha256"]
    assert plot.is_file()


def test_live_capture_without_input_device_returns_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        calibrate.audio,
        "list_devices",
        lambda sd_module=None: [make_device(2, name="Speaker", inputs=0, outputs=2)],
    )

    code = calibrate.main(
        [
            *TONE_ARGS,
            "--out",
            str(tmp_path / "x.wav"),
            "--report",
            str(tmp_path / "x.json"),
            "--no-plot",
        ]
    )

    assert code == calibrate.EXIT_UNAVAILABLE


def test_playback_error_returns_4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    devices = [
        make_device(1, name="Fake Mic", inputs=1, outputs=0),
        make_device(2, name="Fake Speaker", inputs=0, outputs=2),
    ]
    monkeypatch.setattr(
        calibrate.audio, "list_devices", lambda sd_module=None: devices
    )

    def broken(*args: Any, **kwargs: Any) -> calibration.PlaybackCapture:
        raise calibration.PlaybackError("устройство занято")

    monkeypatch.setattr(calibrate.calibration, "play_and_record", broken)

    code = calibrate.main(
        [
            *TONE_ARGS,
            "--out",
            str(tmp_path / "x.wav"),
            "--report",
            str(tmp_path / "x.json"),
            "--no-plot",
        ]
    )

    assert code == calibrate.EXIT_IO


def test_audio_backend_unavailable_returns_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(sd_module: Any = None) -> list[audio.DeviceInfo]:
        raise audio.AudioBackendUnavailable("PortAudio недоступен")

    monkeypatch.setattr(calibrate.audio, "list_devices", broken)

    code = calibrate.main(
        [
            *TONE_ARGS,
            "--out",
            str(tmp_path / "x.wav"),
            "--report",
            str(tmp_path / "x.json"),
            "--no-plot",
        ]
    )

    assert code == calibrate.EXIT_UNAVAILABLE


@pytest.mark.parametrize(
    "extra",
    [
        ["--frequencies", "18000,8000"],
        ["--frequencies", "8000,18000"],
        ["--required", "17000"],
        ["--amplitude", "0"],
        ["--amplitude", "1.5"],
        ["--tone-ms", "0"],
        ["--min-snr-db", "-1"],
        ["--max-offset-hz", "0"],
    ],
)
def test_invalid_parameters_return_2(
    tmp_path: Path, extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    base = [
        "--out",
        str(tmp_path / "x.wav"),
        "--report",
        str(tmp_path / "x.json"),
        "--no-plot",
    ]
    code = calibrate.main([*TONE_ARGS, *extra, *base])

    assert code == calibrate.EXIT_USAGE
    assert "ошибка" in capsys.readouterr().err.lower()


def test_plot_and_no_plot_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        calibrate.main(
            [
                *TONE_ARGS,
                "--plot",
                str(tmp_path / "x.png"),
                "--no-plot",
            ]
        )
    assert excinfo.value.code == calibrate.EXIT_USAGE
