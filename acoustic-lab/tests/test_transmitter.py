"""CLI transmitter.py (SN-011): WAV, отчёт, детерминизм и отказы.

Тесты проверяют файловый результат. Ни один из них не открывает динамик и не
доказывает акустическую передачу.
"""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import numpy as np
import pytest

import transmitter
from snlab.config import MAX_PAYLOAD_BYTES, PROFILE_BFSK_18K


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        transmitter.main(["--help"])
    assert excinfo.value.code == 0
    assert "usage:" in capsys.readouterr().out.lower()


def test_message_creates_wav_and_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wav_path = tmp_path / "nested" / "tx.wav"

    code = transmitter.main(
        ["--message", "SILENTNETWORK", "--out", str(wav_path)]
    )

    assert code == transmitter.EXIT_OK
    assert wav_path.is_file()
    report_path = wav_path.with_suffix(".json")
    assert report_path.is_file()

    report = read_report(report_path)
    assert report["schema"] == transmitter.SCHEMA
    assert report["task"] == "SN-011"
    assert report["input"]["kind"] == "message"
    assert report["input"]["payload_bytes"] == len("SILENTNETWORK")
    assert report["input"]["payload_text"] == "SILENTNETWORK"
    assert report["profile"]["name"] == PROFILE_BFSK_18K.name
    assert report["frame"]["preamble_bits"] == 32
    assert report["frame"]["payload_bits"] == len("SILENTNETWORK") * 8
    assert report["output"]["wav_bytes"] == wav_path.stat().st_size
    assert report["output"]["wav_sha256"] == sha256_file(wav_path)
    assert report["determinism"]["pcm_sha256"] == report["modulation"]["pcm_sha256"]
    assert len(report["limitations"]) >= 4
    assert "WAV SHA-256" in capsys.readouterr().out


def test_wav_format_and_length_are_exact(tmp_path: Path) -> None:
    wav_path = tmp_path / "tx.wav"
    message = "SN-011"

    assert transmitter.main(["--message", message, "--out", str(wav_path)]) == 0

    report = read_report(wav_path.with_suffix(".json"))
    with wave.open(str(wav_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == PROFILE_BFSK_18K.sample_rate
        assert wav_file.getnframes() == report["modulation"]["samples"]

    expected_samples = (
        report["frame"]["total_bits"] * PROFILE_BFSK_18K.samples_per_symbol
    )
    assert report["modulation"]["samples"] == expected_samples
    assert report["modulation"]["duration_s"] == pytest.approx(
        expected_samples / PROFILE_BFSK_18K.sample_rate
    )


def test_twenty_runs_produce_identical_pcm_and_wav(tmp_path: Path) -> None:
    wav_path = tmp_path / "tx.wav"
    pcm_hashes: list[str] = []
    wav_hashes: list[str] = []

    for _ in range(20):
        assert transmitter.main(
            [
                "--message",
                "DETERMINISM",
                "--profile",
                "bfsk-18k",
                "--ramp-ms",
                "5",
                "--out",
                str(wav_path),
            ]
        ) == transmitter.EXIT_OK
        report = read_report(wav_path.with_suffix(".json"))
        pcm_hashes.append(report["modulation"]["pcm_sha256"])
        wav_hashes.append(report["output"]["wav_sha256"])

    assert len(set(pcm_hashes)) == 1
    assert len(set(wav_hashes)) == 1
    assert pcm_hashes[0] == report["determinism"]["pcm_sha256"]
    assert wav_hashes[0] == sha256_file(wav_path)


def test_file_payload_is_read_as_bytes(tmp_path: Path) -> None:
    payload = bytes(range(32))
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)
    wav_path = tmp_path / "tx.wav"

    assert transmitter.main(["--file", str(source), "--out", str(wav_path)]) == 0

    report = read_report(wav_path.with_suffix(".json"))
    assert report["input"]["kind"] == "file"
    assert report["input"]["path"] == str(source)
    assert report["input"]["payload_bytes"] == 32
    assert report["input"]["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert report["input"]["payload_text"] is None


def test_max_payload_is_accepted(tmp_path: Path) -> None:
    source = tmp_path / "max.bin"
    source.write_bytes(b"\x5a" * MAX_PAYLOAD_BYTES)
    wav_path = tmp_path / "tx.wav"

    assert transmitter.main(["--file", str(source), "--out", str(wav_path)]) == 0

    report = read_report(wav_path.with_suffix(".json"))
    assert report["frame"]["payload_bytes"] == MAX_PAYLOAD_BYTES
    assert report["frame"]["total_bits"] == 88 + MAX_PAYLOAD_BYTES * 8


def test_utf8_message_records_text_and_bytes(tmp_path: Path) -> None:
    wav_path = tmp_path / "tx.wav"
    message = "привет"

    assert transmitter.main(["--message", message, "--out", str(wav_path)]) == 0

    report = read_report(wav_path.with_suffix(".json"))
    encoded = message.encode("utf-8")
    assert report["input"]["payload_bytes"] == len(encoded)
    assert report["input"]["payload_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert report["input"]["payload_text"] == message


def test_custom_report_path(tmp_path: Path) -> None:
    wav_path = tmp_path / "tx.wav"
    report_path = tmp_path / "reports" / "custom.json"

    assert (
        transmitter.main(
            ["--message", "x", "--out", str(wav_path), "--report", str(report_path)]
        )
        == 0
    )

    assert report_path.is_file()
    assert read_report(report_path)["output"]["report_path"] == str(report_path)


def test_message_and_file_are_mutually_exclusive(tmp_path: Path) -> None:
    source = tmp_path / "payload.bin"
    source.write_bytes(b"x")
    with pytest.raises(SystemExit) as excinfo:
        transmitter.main(["--message", "x", "--file", str(source)])
    assert excinfo.value.code == transmitter.EXIT_USAGE


def test_source_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        transmitter.main([])
    assert excinfo.value.code == transmitter.EXIT_USAGE
    assert "--message" in capsys.readouterr().err


def test_empty_message_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wav_path = tmp_path / "tx.wav"
    assert transmitter.main(["--message", "", "--out", str(wav_path)]) == transmitter.EXIT_USAGE
    assert not wav_path.exists()
    assert "пустое" in capsys.readouterr().err


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "empty.bin"
    source.write_bytes(b"")
    assert transmitter.main(["--file", str(source), "--out", str(tmp_path / "tx.wav")]) == 2


def test_missing_file_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert transmitter.main(
        ["--file", str(tmp_path / "nope.bin"), "--out", str(tmp_path / "tx.wav")]
    ) == transmitter.EXIT_USAGE
    assert "не найден" in capsys.readouterr().err


def test_oversized_file_is_rejected_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "big.bin"
    source.write_bytes(b"x" * (MAX_PAYLOAD_BYTES + 1))
    wav_path = tmp_path / "tx.wav"

    assert transmitter.main(["--file", str(source), "--out", str(wav_path)]) == 2
    assert not wav_path.exists()
    assert "превышает" in capsys.readouterr().err


def test_invalid_ramp_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for ramp in ("-1", "501"):
        assert (
            transmitter.main(
                ["--message", "x", "--ramp-ms", ramp, "--out", str(tmp_path / "tx.wav")]
            )
            == transmitter.EXIT_USAGE
        )
    assert not (tmp_path / "tx.wav").exists()
    assert "ramp_ms" in capsys.readouterr().err


def test_unknown_profile_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        transmitter.main(
            ["--message", "x", "--profile", "nope", "--out", str(tmp_path / "tx.wav")]
        )
    assert excinfo.value.code == transmitter.EXIT_USAGE


def test_unwritable_output_returns_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert transmitter.main(["--message", "x", "--out", str(tmp_path)]) == transmitter.EXIT_IO
    assert "ошибка файла" in capsys.readouterr().err


def test_profile_changes_signal(tmp_path: Path) -> None:
    first = tmp_path / "tx16.wav"
    second = tmp_path / "tx18.wav"

    assert transmitter.main(["--message", "x", "--profile", "bfsk-16k", "--out", str(first)]) == 0
    assert transmitter.main(["--message", "x", "--profile", "bfsk-18k", "--out", str(second)]) == 0

    report_16 = read_report(first.with_suffix(".json"))
    report_18 = read_report(second.with_suffix(".json"))
    assert report_16["profile"]["mark_hz"] == 16_000.0
    assert report_18["profile"]["mark_hz"] == 18_000.0
    assert report_16["modulation"]["pcm_sha256"] != report_18["modulation"]["pcm_sha256"]


def test_pcm_samples_are_not_silent(tmp_path: Path) -> None:
    wav_path = tmp_path / "tx.wav"
    assert transmitter.main(["--message", "A", "--out", str(wav_path)]) == 0

    with wave.open(str(wav_path), "rb") as wav_file:
        data = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype="<i2")
    assert data.size > 0
    assert int(np.max(np.abs(data.astype(np.int32)))) > 0
