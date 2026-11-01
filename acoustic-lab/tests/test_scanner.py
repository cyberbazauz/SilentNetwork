"""CLI scanner.py (SN-010): коды возврата, отчёты и обработка ошибок.

Тесты подменяют аудиобэкенд: реальный микрофон не открывается. Проверяется,
что отсутствие устройства, занятое устройство и неверные аргументы дают
понятную ошибку и код возврата, а не трассировку.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import scanner
from snlab import audio
from snlab.wavio import read_pcm16


def make_device(
    index: int = 1,
    *,
    name: str = "Fake Mic",
    inputs: int = 1,
    outputs: int = 0,
    default_input: bool = True,
) -> audio.DeviceInfo:
    return audio.DeviceInfo(
        index=index,
        name=name,
        hostapi=0,
        hostapi_name="MME",
        max_input_channels=inputs,
        max_output_channels=outputs,
        default_samplerate=48_000.0,
        default_low_input_latency=0.01,
        default_low_output_latency=0.01,
        is_default_input=default_input,
        is_default_output=False,
    )


def fake_recording(sample_rate: int = 8_000, seconds: float = 1.0) -> audio.Recording:
    samples = np.full(int(sample_rate * seconds), 0.05, dtype=np.float32)
    return audio.Recording(
        samples=samples,
        sample_rate=sample_rate,
        device_index=1,
        device_name="Fake Mic",
        frames_requested=samples.size,
        frames_captured=samples.size,
        overflows=0,
        wall_time_s=seconds + 0.1,
    )


def test_no_mode_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        scanner.main([])
    assert excinfo.value.code == scanner.EXIT_USAGE
    assert "--list" in capsys.readouterr().err


def test_list_and_capture_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        scanner.main(["--list", "--capture", "1", "--out", str(tmp_path / "x.json")])
    assert excinfo.value.code == scanner.EXIT_USAGE


@pytest.mark.parametrize("duration", ["0", "-5", "301"])
def test_capture_duration_is_validated(
    duration: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        scanner.main(["--capture", duration])
    assert excinfo.value.code == scanner.EXIT_USAGE
    assert "--capture" in capsys.readouterr().err


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        scanner.main(["--help"])
    assert excinfo.value.code == 0
    assert "usage:" in capsys.readouterr().out.lower()


def test_list_writes_json_without_probing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    devices = [
        make_device(0, name="Output", inputs=0, outputs=2, default_input=False),
        make_device(1, name="Input"),
    ]
    monkeypatch.setattr(scanner.audio, "list_devices", lambda sd_module=None: devices)
    target = tmp_path / "nested" / "devices.json"

    code = scanner.main(["--list", "--no-probe", "--out", str(target)])

    assert code == scanner.EXIT_OK
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["schema"] == scanner.SCHEMA
    assert report["summary"]["devices_total"] == 2
    assert report["summary"]["input_devices"] == 1
    assert report["summary"]["output_devices"] == 1
    assert report["probed"] is False
    assert "input_formats" not in report["devices"][1]
    assert "Устройств: 2" in capsys.readouterr().out


def test_list_probes_formats_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = make_device(1, inputs=1, outputs=1)
    calls: list[str] = []

    def fake_probe(target, *, direction, sd_module=None, channels=1):
        calls.append(direction)
        return {
            "direction": direction,
            "channels": channels,
            "supported_sample_rates": [48_000],
            "rejected_sample_rates": {},
            "dtypes_probed_at_hz": 48_000,
            "supported_dtypes": ["float32"],
            "rejected_dtypes": {},
            "note": audio.FORMAT_NOTE,
        }

    monkeypatch.setattr(scanner.audio, "list_devices", lambda sd_module=None: [device])
    monkeypatch.setattr(scanner.audio, "probe_formats", fake_probe)
    target = tmp_path / "devices.json"

    assert scanner.main(["--list", "--out", str(target)]) == scanner.EXIT_OK

    report = json.loads(target.read_text(encoding="utf-8"))
    assert calls == ["input", "output"]
    assert report["devices"][0]["input_formats"]["supported_sample_rates"] == [48_000]
    assert report["devices"][0]["output_formats"]["direction"] == "output"


def test_capture_writes_wav_csv_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        scanner.audio, "list_devices", lambda sd_module=None: [make_device()]
    )
    monkeypatch.setattr(
        scanner.audio,
        "record_ambient",
        lambda **kwargs: fake_recording(),
    )
    wav_path = tmp_path / "ambient.wav"

    code = scanner.main(
        [
            "--capture",
            "1",
            "--sample-rate",
            "8000",
            "--out",
            str(wav_path),
            "--no-plot",
        ]
    )

    assert code == scanner.EXIT_OK

    samples, rate = read_pcm16(wav_path, expected_sample_rate=8_000)
    assert rate == 8_000
    assert samples.size == 8_000

    csv_path = wav_path.with_suffix(".psd.csv")
    assert csv_path.is_file()
    assert csv_path.read_text(encoding="utf-8").startswith("frequency_hz,")

    report_path = wav_path.with_suffix(".json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["capture"]["device_name"] == "Fake Mic"
    assert report["capture"]["frames_captured"] == 8_000
    assert report["analysis"]["snr"] is None
    assert report["files"]["wav"] == str(wav_path)
    assert report["files"]["spectrum_png"] is None
    assert not wav_path.with_suffix(".spectrum.png").exists()
    assert "SNR не вычисляется" in capsys.readouterr().out


def test_capture_warns_about_digital_silence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    silent = audio.Recording(
        samples=np.zeros(8_000, dtype=np.float32),
        sample_rate=8_000,
        device_index=1,
        device_name="Muted Mic",
        frames_requested=8_000,
        frames_captured=8_000,
        overflows=0,
        wall_time_s=1.0,
    )
    monkeypatch.setattr(
        scanner.audio, "list_devices", lambda sd_module=None: [make_device()]
    )
    monkeypatch.setattr(scanner.audio, "record_ambient", lambda **kwargs: silent)
    wav_path = tmp_path / "silent.wav"

    assert (
        scanner.main(
            ["--capture", "1", "--sample-rate", "8000", "--out", str(wav_path), "--no-plot"]
        )
        == scanner.EXIT_OK
    )

    report = json.loads(wav_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert any("цифровую тишину" in warning for warning in report["warnings"])


def test_capture_without_input_devices_returns_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(scanner.audio, "list_devices", lambda sd_module=None: [])

    assert scanner.main(["--capture", "1"]) == scanner.EXIT_UNAVAILABLE
    assert "нет устройства" in capsys.readouterr().err


def test_capture_with_unknown_device_returns_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        scanner.audio, "list_devices", lambda sd_module=None: [make_device()]
    )

    assert scanner.main(["--capture", "1", "--device", "99"]) == scanner.EXIT_UNAVAILABLE
    assert "устройство не выбрано" in capsys.readouterr().err


def test_capture_driver_failure_returns_4(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        scanner.audio, "list_devices", lambda sd_module=None: [make_device()]
    )

    def broken(**kwargs):
        raise audio.CaptureError("Device unavailable")

    monkeypatch.setattr(scanner.audio, "record_ambient", broken)

    assert scanner.main(["--capture", "1"]) == scanner.EXIT_CAPTURE_FAILED
    assert "ошибка захвата" in capsys.readouterr().err


def test_backend_unavailable_returns_3(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(sd_module=None):
        raise audio.AudioBackendUnavailable("PortAudio недоступен")

    monkeypatch.setattr(scanner.audio, "list_devices", broken)

    assert scanner.main(["--list"]) == scanner.EXIT_UNAVAILABLE
    assert "аудиобэкенд недоступен" in capsys.readouterr().err
