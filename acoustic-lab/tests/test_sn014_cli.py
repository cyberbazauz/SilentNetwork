"""CLI SN-014: tonegen.py и analyze.py, безопасный режим без устройств."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import analyze as analyze_cli
import response as response_cli
import tonegen as tonegen_cli
from snlab import audio, calibration, tones
from snlab.reporting import write_json
from snlab.wavio import read_wav


def tone_args(tmp_path: Path, name: str = "tx") -> list[str]:
    return [
        "--frequencies",
        "8000,18000,19000",
        "--duration-ms",
        "300",
        "--gap-ms",
        "150",
        "--lead-ms",
        "150",
        "--tail-ms",
        "150",
        "--amplitude",
        "0.4",
        "--out",
        str(tmp_path / f"{name}.wav"),
        "--report",
        str(tmp_path / f"{name}.json"),
    ]


def test_tonegen_writes_wav_and_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = tonegen_cli.main(tone_args(tmp_path))

    assert code == tonegen_cli.EXIT_OK
    wav_path = tmp_path / "tx.wav"
    plan_path = tmp_path / "tx.json"
    assert wav_path.is_file() and plan_path.is_file()

    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert payload["schema"] == tonegen_cli.SCHEMA
    assert payload["task"] == "SN-014"
    assert payload["mode"] == "file_only"
    assert payload["playback"]["performed"] is False
    assert payload["plan"]["frequencies_hz"] == [8_000.0, 18_000.0, 19_000.0]
    assert payload["plan"]["required_frequencies_hz"] == [18_000.0, 19_000.0]
    assert payload["file"]["channels"] == 1
    assert payload["file"]["sha256"] == tones.sha256_file(wav_path)
    assert len(payload["schedule"]) == 3
    assert "воспроизведения нет" in capsys.readouterr().out


def test_tonegen_is_deterministic(tmp_path: Path) -> None:
    first = tonegen_cli.main(tone_args(tmp_path, "a"))
    second = tonegen_cli.main(tone_args(tmp_path, "b"))

    assert first == second == tonegen_cli.EXIT_OK
    assert (tmp_path / "a.wav").read_bytes() == (tmp_path / "b.wav").read_bytes()


def test_tonegen_single_frequency_and_channels(tmp_path: Path) -> None:
    code = tonegen_cli.main(
        [
            "--frequency",
            "18000",
            "--duration-ms",
            "250",
            "--channels",
            "2",
            "--out",
            str(tmp_path / "single.wav"),
            "--report",
            str(tmp_path / "single.json"),
        ]
    )

    assert code == tonegen_cli.EXIT_OK
    data, rate, channels = read_wav(tmp_path / "single.wav")
    assert rate == 48_000
    assert channels == 2
    assert data.shape[0] == int(round((0.5 + 0.25 + 0.5) * 48_000))

    payload = json.loads((tmp_path / "single.json").read_text(encoding="utf-8"))
    assert payload["plan"]["required_frequencies_hz"] == [18_000.0]
    assert payload["schedule"][0]["frequency_hz"] == 18_000.0


def test_tonegen_no_report(tmp_path: Path) -> None:
    code = tonegen_cli.main(
        [
            "--frequency",
            "1000",
            "--out",
            str(tmp_path / "one.wav"),
            "--no-report",
        ]
    )

    assert code == tonegen_cli.EXIT_OK
    assert (tmp_path / "one.wav").is_file()
    assert not (tmp_path / "one.json").exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--frequencies", "19000,18000"],
        ["--frequencies", "8000", "--required", "18000"],
        ["--frequency", "30000"],
        ["--amplitude", "0"],
        ["--channels", "9"],
        ["--duration-ms", "0"],
    ],
)
def test_tonegen_invalid_arguments_return_2(
    tmp_path: Path, extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code = tonegen_cli.main(
        [*extra, "--out", str(tmp_path / "bad.wav"), "--no-report"]
    )

    assert code == tonegen_cli.EXIT_USAGE
    assert "ошибка" in capsys.readouterr().err


def test_tonegen_write_error_returns_4(tmp_path: Path) -> None:
    directory = tmp_path / "as-directory.wav"
    directory.mkdir()

    code = tonegen_cli.main(
        ["--frequency", "1000", "--out", str(directory), "--no-report"]
    )
    assert code == tonegen_cli.EXIT_IO


def test_analyze_cli_creates_all_outputs(tmp_path: Path) -> None:
    assert tonegen_cli.main(tone_args(tmp_path)) == tonegen_cli.EXIT_OK

    code = analyze_cli.main(
        [
            str(tmp_path / "tx.wav"),
            "--json",
            str(tmp_path / "analysis.json"),
            "--csv",
            str(tmp_path / "analysis.csv"),
            "--plot",
            str(tmp_path / "psd.png"),
            "--spectrogram",
            str(tmp_path / "spectrogram.png"),
        ]
    )

    assert code == analyze_cli.EXIT_OK
    for name in ("analysis.json", "analysis.csv", "psd.png", "spectrogram.png"):
        assert (tmp_path / name).is_file()
    payload = json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8"))
    assert payload["schema"] == analyze_cli.SCHEMA
    assert payload["task"] == "SN-014"
    assert payload["mode"] == "file_only"
    assert payload["file"]["channels"] == 1
    assert payload["channels"][0]["level"]["rms_dbfs"] < 0.0


def test_analyze_cli_default_paths_and_no_plots(tmp_path: Path) -> None:
    assert tonegen_cli.main(tone_args(tmp_path)) == tonegen_cli.EXIT_OK

    code = analyze_cli.main([str(tmp_path / "tx.wav"), "--no-plots"])

    assert code == analyze_cli.EXIT_OK
    assert (tmp_path / "tx.analysis.json").is_file()
    assert (tmp_path / "tx.analysis.csv").is_file()
    assert not (tmp_path / "tx.psd.png").exists()


def test_analyze_cli_errors(tmp_path: Path) -> None:
    assert analyze_cli.main([str(tmp_path / "missing.wav")]) == analyze_cli.EXIT_IO

    assert tonegen_cli.main(tone_args(tmp_path)) == tonegen_cli.EXIT_OK
    code = analyze_cli.main(
        [
            str(tmp_path / "tx.wav"),
            "--nperseg",
            "4",
            "--no-plots",
            "--no-csv",
        ]
    )
    assert code == analyze_cli.EXIT_USAGE


def test_safe_tools_never_load_audio_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert tonegen_cli.main(tone_args(tmp_path)) == tonegen_cli.EXIT_OK
    tx_path = tmp_path / "tx.wav"
    plan_path = tmp_path / "tx.json"

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("аудиобэкенд не должен загружаться")

    monkeypatch.setattr(audio, "load_sounddevice", forbidden)
    monkeypatch.setattr(audio, "list_devices", forbidden)
    monkeypatch.setattr(calibration, "play_and_record", forbidden)

    assert analyze_cli.main([str(tx_path), "--no-plots"]) == analyze_cli.EXIT_OK
    assert (
        response_cli.main(
            [
                "--tx",
                str(tx_path),
                "--rx",
                str(tx_path),
                "--plan",
                str(plan_path),
                "--json",
                str(tmp_path / "response.json"),
                "--csv",
                str(tmp_path / "response.csv"),
                "--no-plot",
            ]
        )
        == response_cli.EXIT_OK
    )


def test_response_cli_requires_plan(tmp_path: Path) -> None:
    assert tonegen_cli.main(tone_args(tmp_path)) == tonegen_cli.EXIT_OK
    tx_path = tmp_path / "tx.wav"
    write_json(tmp_path / "wrong.json", {"plan": {"sample_rate_hz": 48_000}})

    code = response_cli.main(
        [
            "--tx",
            str(tx_path),
            "--rx",
            str(tx_path),
            "--plan",
            str(tmp_path / "wrong.json"),
            "--no-plot",
        ]
    )
    assert code == response_cli.EXIT_USAGE
