"""SN-024: контракт Acoustic Data Link v0, векторы и отчёты.

Тесты проверяют только Python-ядро. Реальная передача через воздух здесь не
имитируется и не объявляется успешной: WAV — это файловый канал L0.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from snlab.bfsk import modulate
from snlab.config import (
    DATA_PROFILES,
    PROFILE_DATA_AUDIBLE,
    PROFILE_DATA_ROBUST,
)
from snlab.datalink import (
    ACOUSTIC_MIN_PREAMBLE_MATCHES,
    MAX_DATA_PAYLOAD_BYTES,
    REPORT_SCHEMA,
    DataLinkError,
    DataLinkPayloadError,
    DataLinkProfileError,
    decode_report_ok,
    decode_wav_to_text,
    encode_text_to_wav,
    payload_to_text,
    text_to_payload,
)
from snlab.frame import build_frame
from snlab.receiver import ReceiverError
from snlab.wavio import write_pcm16

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "data-link-vectors.json"


def load_fixture() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


FIXTURE = load_fixture()
POSITIVE_VECTORS = [v for v in FIXTURE["vectors"] if v["expect_ok"]]
NEGATIVE_DECODE = {v["id"]: v for v in FIXTURE["negative_decode"]}


def vector_by_id(vector_id: str) -> dict:
    for vector in FIXTURE["vectors"]:
        if vector["id"] == vector_id:
            return vector
    raise AssertionError(f"вектор {vector_id} отсутствует")


def write_bits_wav(
    path: Path,
    bits_hex: str,
    profile,
    *,
    leading_s: float = 0.2,
    trailing_s: float = 0.2,
) -> Path:
    bits = np.frombuffer(bytes.fromhex(bits_hex), dtype=np.uint8)
    bits = np.unpackbits(bits.astype(np.uint8))  # bytearray не несёт биты
    samples = modulate(bits, profile).samples
    leading = np.zeros(int(round(leading_s * profile.sample_rate)))
    trailing = np.zeros(int(round(trailing_s * profile.sample_rate)))
    write_pcm16(path, np.concatenate((leading, samples, trailing)), profile.sample_rate)
    return path


def bits_from_hex(bits_hex: str) -> np.ndarray:
    raw = np.frombuffer(bytes.fromhex(bits_hex), dtype=np.uint8)
    return np.unpackbits(raw)


# --- фикстура и профили ---------------------------------------------------


def test_fixture_schema_and_profiles() -> None:
    assert FIXTURE["schema"] == "silentnetwork.acoustic-lab.data-link-vectors/1"
    assert FIXTURE["crc_algorithm"] == "CRC-16/CCITT-FALSE"
    assert FIXTURE["default_profile"] == "data-audible"
    assert set(FIXTURE["profiles"]) == set(DATA_PROFILES)
    for name, described in FIXTURE["profiles"].items():
        assert described == DATA_PROFILES[name].as_dict()


def test_data_profiles_cover_fixture() -> None:
    assert PROFILE_DATA_AUDIBLE.name in DATA_PROFILES
    assert PROFILE_DATA_ROBUST.name in DATA_PROFILES
    assert ACOUSTIC_MIN_PREAMBLE_MATCHES < 32


# --- построение кадра совпадает с векторами -------------------------------


@pytest.mark.parametrize("vector", POSITIVE_VECTORS, ids=lambda v: v["id"])
def test_positive_vector_matches_frame_contract(vector: dict) -> None:
    payload = text_to_payload(vector["text"], max_bytes=vector["max_bytes"])
    frame = build_frame(payload, max_payload_bytes=vector["max_bytes"])

    assert payload.hex() == vector["payload_hex"]
    assert len(payload) == vector["payload_bytes"]
    assert frame.total_bits == vector["frame"]["total_bits"]
    assert f"0x{frame.crc:04X}" == vector["frame"]["crc16"]
    assert np.packbits(frame.bits).tobytes().hex() == vector["bits_hex"]


def test_max64_vector_is_exactly_64_bytes() -> None:
    vector = vector_by_id("max64")
    assert len(vector["text"].encode("utf-8")) == MAX_DATA_PAYLOAD_BYTES


def test_negative_construction_vectors() -> None:
    for vector in FIXTURE["vectors"]:
        if vector["expect_ok"]:
            continue
        with pytest.raises(DataLinkPayloadError) as error:
            text_to_payload(vector["text"], max_bytes=vector["max_bytes"])
        assert error.value.error_type == vector["error_type"]


# --- сквозной файловый round-trip -----------------------------------------


@pytest.mark.parametrize("profile", [PROFILE_DATA_AUDIBLE, PROFILE_DATA_ROBUST], ids=["audible", "robust"])
@pytest.mark.parametrize("vector", POSITIVE_VECTORS, ids=lambda v: v["id"])
def test_text_round_trip_through_wav(tmp_path: Path, vector: dict, profile) -> None:
    path = tmp_path / f"{vector['id']}-{profile.name}.wav"
    report = encode_text_to_wav(
        vector["text"],
        profile,
        path,
        max_bytes=vector["max_bytes"],
        lead_silence_s=0.25,
        tail_silence_s=0.1,
    )

    assert report["schema"] == REPORT_SCHEMA
    assert report["direction"] == "tx"
    assert report["result"] == "ok"
    assert report["payload_hex"] == vector["payload_hex"]
    assert report["payload_sha256"] == vector["payload_sha256"]
    assert report["frame"]["crc16"] == vector["frame"]["crc16"]
    assert report["crc_is_authentication"] is False
    assert Path(report["wav_path"]).is_file()

    decoded = decode_wav_to_text(path, profile, max_bytes=vector["max_bytes"])
    assert decode_report_ok(decoded)
    assert decoded["result"] == "ok"
    assert decoded["text"] == vector["text"]
    assert decoded["payload_hex"] == vector["payload_hex"]
    assert decoded["crc_ok"] is True
    assert decoded["error_type"] is None
    assert decoded["preamble_matches"] == 32
    assert decoded["start_time_s"] == pytest.approx(0.25, abs=0.02)
    assert decoded["recording"]["rms_dbfs"] < 0.0


def test_encode_report_is_json_serializable(tmp_path: Path) -> None:
    report = encode_text_to_wav("JSON", PROFILE_DATA_AUDIBLE, tmp_path / "json.wav")
    restored = json.loads(json.dumps(report, ensure_ascii=False))
    assert restored["text"] == "JSON"
    assert restored["direction"] == "tx"


def test_encode_rejects_bad_inputs(tmp_path: Path) -> None:
    with pytest.raises(DataLinkPayloadError):
        encode_text_to_wav("", PROFILE_DATA_AUDIBLE, tmp_path / "empty.wav")
    with pytest.raises(DataLinkPayloadError):
        encode_text_to_wav("A" * 65, PROFILE_DATA_AUDIBLE, tmp_path / "long.wav")
    with pytest.raises(DataLinkProfileError):
        encode_text_to_wav("A", "data-audible", tmp_path / "bad.wav")  # type: ignore[arg-type]
    with pytest.raises(DataLinkError):
        encode_text_to_wav(
            "A", PROFILE_DATA_AUDIBLE, tmp_path / "silence.wav", lead_silence_s=-1.0
        )


def test_payload_to_text_rejects_invalid_utf8() -> None:
    with pytest.raises(DataLinkPayloadError) as error:
        payload_to_text(b"\xff\xfe")
    assert error.value.error_type == "payload_invalid"
    assert "UTF-8" in str(error.value)


# --- отказы приёма --------------------------------------------------------


def test_corrupted_crc_is_reported(tmp_path: Path) -> None:
    vector = NEGATIVE_DECODE["corrupted_crc"]
    path = write_bits_wav(tmp_path / "crc.wav", vector["bits_hex"], PROFILE_DATA_AUDIBLE)
    report = decode_wav_to_text(path, PROFILE_DATA_AUDIBLE)
    assert report["result"] == "error"
    assert report["error_type"] == "crc_mismatch"
    assert report["crc_ok"] is False
    assert report["text"] is None


def test_truncated_frame_is_reported(tmp_path: Path) -> None:
    vector = NEGATIVE_DECODE["truncated"]
    path = write_bits_wav(tmp_path / "cut.wav", vector["bits_hex"], PROFILE_DATA_AUDIBLE)
    report = decode_wav_to_text(path, PROFILE_DATA_AUDIBLE)
    assert report["result"] == "error"
    assert report["error_type"] == "incomplete_frame"


def test_noise_only_does_not_decode(tmp_path: Path) -> None:
    vector = NEGATIVE_DECODE["noise_only"]
    settings = vector["noise"]
    rng = np.random.default_rng(settings["seed"])
    samples = rng.normal(
        0.0, settings["amplitude"], settings["samples"]
    ).astype(np.float64)
    path = tmp_path / "noise.wav"
    write_pcm16(path, samples, PROFILE_DATA_AUDIBLE.sample_rate)

    report = decode_wav_to_text(path, PROFILE_DATA_AUDIBLE)
    assert report["result"] == "error"
    assert report["error_type"] == "signal_not_found"
    assert report["text"] is None


def test_acoustic_threshold_accepts_28_of_32(tmp_path: Path) -> None:
    vector = vector_by_id("ascii-silentnetwork")
    bits = bits_from_hex(vector["bits_hex"]).copy()
    bits[[0, 8, 16, 24]] ^= 1  # 28/32 совпадений преамбулы, payload и CRC целы
    samples = modulate(bits, PROFILE_DATA_AUDIBLE).samples
    leading = np.zeros(int(0.25 * PROFILE_DATA_AUDIBLE.sample_rate))
    path = tmp_path / "weak-preamble.wav"
    write_pcm16(
        path,
        np.concatenate((leading, samples)),
        PROFILE_DATA_AUDIBLE.sample_rate,
    )

    strict = decode_wav_to_text(path, PROFILE_DATA_AUDIBLE)
    assert strict["error_type"] == "signal_not_found"

    acoustic = decode_wav_to_text(
        path,
        PROFILE_DATA_AUDIBLE,
        min_preamble_matches=ACOUSTIC_MIN_PREAMBLE_MATCHES,
    )
    assert decode_report_ok(acoustic)
    assert acoustic["text"] == vector["text"]
    assert acoustic["preamble_matches"] == 28


def test_invalid_min_preamble_matches_is_rejected() -> None:
    samples = np.zeros(4800, dtype=np.float64)
    with pytest.raises(ReceiverError):
        decode_wav_to_text(
            "does-not-matter.wav",
            PROFILE_DATA_AUDIBLE,
            min_preamble_matches=0,
        )
    # Проверяем и сам приёмник: параметр валидируется до чтения файла.
    from snlab.receiver import decode_samples

    with pytest.raises(ReceiverError):
        decode_samples(samples, PROFILE_DATA_AUDIBLE, min_preamble_matches=33)


def test_missing_and_broken_wav_are_reported(tmp_path: Path) -> None:
    missing = decode_wav_to_text(tmp_path / "missing.wav", PROFILE_DATA_AUDIBLE)
    assert missing["error_type"] == "wav_not_found"

    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    broken = decode_wav_to_text(empty, PROFILE_DATA_AUDIBLE)
    assert broken["error_type"] == "wav_format_error"


def test_wrong_sample_rate_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "rate.wav"
    write_pcm16(path, np.zeros(4410, dtype=np.float64), 44_100)
    report = decode_wav_to_text(path, PROFILE_DATA_AUDIBLE)
    assert report["result"] == "error"
    assert report["error_type"] == "wav_format_error"
    assert "48000" in report["message"]


def test_non_utf8_payload_reports_crc_ok(tmp_path: Path) -> None:
    payload = b"\xff\xfe\x00"
    frame = build_frame(payload, max_payload_bytes=MAX_DATA_PAYLOAD_BYTES)
    path = tmp_path / "binary.wav"
    write_pcm16(
        path,
        modulate(frame.bits, PROFILE_DATA_AUDIBLE).samples,
        PROFILE_DATA_AUDIBLE.sample_rate,
    )

    report = decode_wav_to_text(path, PROFILE_DATA_AUDIBLE)
    assert report["result"] == "error"
    assert report["error_type"] == "payload_utf8_error"
    assert report["crc_ok"] is True
    assert report["payload_hex"] == payload.hex()


def test_decode_report_is_human_readable_on_success(tmp_path: Path) -> None:
    path = tmp_path / "ok.wav"
    encode_text_to_wav("PING", PROFILE_DATA_AUDIBLE, path, lead_silence_s=0.1)
    report = decode_wav_to_text(path, PROFILE_DATA_AUDIBLE)
    assert report["message"] == "Сообщение принято, CRC верный"
    assert report["frame"]["crc_algorithm"] == "CRC-16/CCITT-FALSE"
    assert report["crc_is_authentication"] is False
    assert report["recording"]["clipping"]["detected"] is False


def test_invalid_max_bytes_is_rejected() -> None:
    with pytest.raises(DataLinkPayloadError) as error:
        decode_wav_to_text("any.wav", PROFILE_DATA_AUDIBLE, max_bytes=0)
    assert error.value.error_type == "payload_invalid"
