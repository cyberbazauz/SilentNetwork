"""SN-028: пакетный разбор серии WAV.

Тесты не открывают микрофон и динамик. WAV создаются тем же Python-кодеком,
поэтому проверяется логика отчёта, а не акустическая передача.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from snlab import datalink, sn028_series
from snlab.config import DATA_PROFILES
from snlab.sn028_series import AttemptOutcome, SeriesError
from snlab.wavio import write_pcm16

PROFILE = DATA_PROFILES["data-audible"]
TEXTS = sn028_series.FIXED_TEXTS


def _write_message(path: Path, text: str) -> Path:
    datalink.encode_text_to_wav(
        text,
        PROFILE,
        path,
        lead_silence_s=0.05,
        tail_silence_s=0.05,
    )
    return path


def _write_silence(path: Path, seconds: float = 1.0) -> Path:
    samples = np.zeros(int(PROFILE.sample_rate * seconds), dtype=np.float64)
    return write_pcm16(path, samples, PROFILE.sample_rate)


def _attempt(
    *,
    passed: bool,
    crc_ok: bool | None = None,
    expected: str | None = "SN-028 link check",
    error_type: str | None = None,
) -> AttemptOutcome:
    return AttemptOutcome(
        path=Path("sn028-w2p-30cm-M1-01.wav"),
        distance="30cm",
        text_id="M1",
        expected_text=expected,
        decoded_text="SN-028 link check" if crc_ok else None,
        passed=passed,
        error_type=error_type,
        message="test",
        preamble_matches=32 if crc_ok else None,
        start_time_s=0.05 if crc_ok else None,
        rms_dbfs=-20.0,
        peak_dbfs=-6.0,
        clipped_samples=0,
        duration_s=2.0,
        sample_rate=48000,
        sha256="0" * 64,
    )


def test_parse_filename_known_distance_and_text() -> None:
    distance, text_id = sn028_series.parse_filename("sn028-w2p-30cm-M2-03.wav")
    assert distance == "30cm"
    assert text_id == "M2"


def test_parse_filename_accepts_russian_noise_and_unknown_names() -> None:
    assert sn028_series.parse_filename("sn028-w2p-noise-M1-01.wav") == (
        "noise",
        "M1",
    )
    assert sn028_series.parse_filename("datalink_rx_data-audible.wav") == (
        None,
        None,
    )
    with pytest.raises(SeriesError):
        sn028_series.normalize_distance("10m")


def test_analyze_attempt_decodes_cyrillic_and_matches_text(tmp_path: Path) -> None:
    path = _write_message(tmp_path / "sn028-w2p-30cm-M2-01.wav", TEXTS["M2"])
    outcome = sn028_series.analyze_attempt(
        path, distance="30cm", profile=PROFILE, text_id="M2", expected=TEXTS["M2"]
    )
    assert outcome.passed is True
    assert outcome.crc_ok is True
    assert outcome.decoded_text == TEXTS["M2"]
    assert outcome.preamble_matches == 32
    assert outcome.error_type is None
    assert outcome.duration_s > 0
    assert outcome.peak_dbfs < 0.0


def test_analyze_attempt_reports_text_mismatch(tmp_path: Path) -> None:
    path = _write_message(tmp_path / "sn028-w2p-30cm-M1-01.wav", TEXTS["M2"])
    outcome = sn028_series.analyze_attempt(
        path, distance="30cm", profile=PROFILE, text_id="M1", expected=TEXTS["M1"]
    )
    assert outcome.passed is False
    assert outcome.crc_ok is True
    assert outcome.error_type == "text_mismatch"
    assert outcome.decoded_text == TEXTS["M2"]


def test_analyze_attempt_silence_is_not_a_false_frame(tmp_path: Path) -> None:
    path = _write_silence(tmp_path / "sn028-w2p-noise-01.wav")
    outcome = sn028_series.analyze_attempt(
        path, distance="noise", profile=PROFILE
    )
    assert outcome.passed is False
    assert outcome.crc_ok is False
    assert outcome.error_type == "signal_not_found"


def test_build_report_30cm_requires_ten_of_ten() -> None:
    all_pass = [_attempt(passed=True, crc_ok=True) for _ in range(10)]
    assert (
        sn028_series.build_report(all_pass, distance="30cm", profile=PROFILE).verdict
        == "PASS"
    )

    nine = [_attempt(passed=True, crc_ok=True) for _ in range(9)]
    nine.append(_attempt(passed=False, crc_ok=False, error_type="signal_not_found"))
    report = sn028_series.build_report(nine, distance="30cm", profile=PROFILE)
    assert report.verdict == "FAIL"
    assert report.passed == 9

    short = [_attempt(passed=True, crc_ok=True) for _ in range(9)]
    assert (
        sn028_series.build_report(short, distance="30cm", profile=PROFILE).verdict
        == "INCOMPLETE"
    )


def test_build_report_1m_threshold_is_eight_of_ten() -> None:
    eight = [_attempt(passed=True, crc_ok=True) for _ in range(8)]
    eight += [
        _attempt(passed=False, crc_ok=False, error_type="crc_mismatch")
        for _ in range(2)
    ]
    assert (
        sn028_series.build_report(eight, distance="1m", profile=PROFILE).verdict
        == "PASS"
    )

    seven = [_attempt(passed=True, crc_ok=True) for _ in range(7)]
    seven += [
        _attempt(passed=False, crc_ok=False, error_type="crc_mismatch")
        for _ in range(3)
    ]
    assert (
        sn028_series.build_report(seven, distance="1m", profile=PROFILE).verdict
        == "FAIL"
    )


def test_build_report_3m_is_research_only() -> None:
    attempts = [_attempt(passed=False, crc_ok=False, error_type="signal_not_found")]
    report = sn028_series.build_report(attempts, distance="3m", profile=PROFILE)
    assert report.verdict == "RESEARCH ONLY"


def test_build_report_noise_requires_no_false_frames() -> None:
    clean = [_attempt(passed=False, crc_ok=False, error_type="signal_not_found")] * 10
    report = sn028_series.build_report(clean, distance="noise", profile=PROFILE)
    assert report.verdict == "PASS"
    assert report.false_frames == 0

    one_false = clean[:9] + [_attempt(passed=True, crc_ok=True)]
    assert (
        sn028_series.build_report(one_false, distance="noise", profile=PROFILE).verdict
        == "FAIL"
    )

    short = clean[:5]
    assert (
        sn028_series.build_report(short, distance="noise", profile=PROFILE).verdict
        == "INCOMPLETE"
    )


def test_analyze_directory_filters_other_distances(tmp_path: Path) -> None:
    _write_message(tmp_path / "sn028-w2p-30cm-M1-01.wav", TEXTS["M1"])
    _write_message(tmp_path / "sn028-w2p-1m-M1-01.wav", TEXTS["M1"])

    report = sn028_series.analyze_directory(
        tmp_path, distance="30cm", profile=PROFILE
    )
    assert len(report.attempts) == 1
    assert report.attempts[0].path.name == "sn028-w2p-30cm-M1-01.wav"


def test_write_report_creates_csv_json_and_markdown(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wav"
    wav_dir.mkdir()
    _write_message(wav_dir / "sn028-w2p-30cm-M1-01.wav", TEXTS["M1"])
    report = sn028_series.analyze_directory(
        wav_dir, distance="30cm", profile=PROFILE
    )
    paths = sn028_series.write_report(report, tmp_path / "out")

    assert paths["csv"].exists()
    assert paths["md"].exists()
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["schema"] == sn028_series.REPORT_SCHEMA
    assert payload["verdict"] == "INCOMPLETE"
    assert payload["attempts"][0]["crc_ok"] is True
    markdown = paths["md"].read_text(encoding="utf-8")
    assert "SN-028 — серия 30cm" in markdown
    assert "CRC-16 не является аутентификацией" in markdown
