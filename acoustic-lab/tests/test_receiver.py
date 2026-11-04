"""BFSK receiver SN-021: независимое декодирование WAV, синхронизация и отказы.

Приёмник получает только путь к WAV и профиль. Биты, кадр и позиции символов
из передатчика в декодер не передаются; они используются лишь для создания
повреждённых входных файлов в тестах.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

import receiver
import transmitter
from snlab.bfsk import modulate
from snlab.config import MAX_PAYLOAD_BYTES, PROFILE_BFSK_16K, PROFILE_BFSK_18K
from snlab.frame import (
    CRC_BITS,
    FRAME_OVERHEAD_BITS,
    LENGTH_BITS,
    PREAMBLE_BITS,
    SYNC_BITS,
    VERSION_BITS,
    build_frame,
)
from snlab.receiver import (
    CrcError,
    FrameLengthError,
    FrameStart,
    FrameVersionError,
    IncompleteFrame,
    ReceiverError,
    SignalNotFound,
    SyncWordError,
    bit_metric,
    decode_candidates,
    decode_samples,
    decode_wav,
    demodulate_samples,
    demodulate_wav,
    find_frame_candidates,
)
from snlab.wavio import WavFormatError, write_pcm16

PROFILE = PROFILE_BFSK_18K
PAYLOAD_OFFSET = PREAMBLE_BITS + SYNC_BITS + VERSION_BITS + LENGTH_BITS


def make_samples(
    payload: bytes,
    *,
    profile=PROFILE,
    leading: int = 0,
    trailing: int = 0,
):
    frame = build_frame(payload)
    samples = modulate(frame.bits, profile).samples
    return np.concatenate(
        (np.zeros(leading), samples, np.zeros(trailing))
    ), frame


def make_wav(
    tmp_path: Path,
    payload: bytes,
    *,
    name: str = "tx.wav",
    profile=PROFILE,
    leading: int = 0,
    trailing: int = 0,
):
    samples, frame = make_samples(
        payload, profile=profile, leading=leading, trailing=trailing
    )
    path = tmp_path / name
    write_pcm16(path, samples, profile.sample_rate)
    return path, frame


def write_bits(path: Path, bits: np.ndarray, *, profile=PROFILE, leading: int = 0) -> None:
    samples = modulate(np.asarray(bits, dtype=np.uint8), profile).samples
    if leading:
        samples = np.concatenate((np.zeros(leading), samples))
    write_pcm16(path, samples, profile.sample_rate)


def set_uint(bits: np.ndarray, offset: int, width: int, value: int) -> None:
    for index in range(width):
        bits[offset + index] = (value >> (width - 1 - index)) & 1


def payload_for(length: int) -> bytes:
    return bytes((index * 37 + 11) % 256 for index in range(length))


def test_short_text_roundtrip(tmp_path: Path) -> None:
    path, frame = make_wav(tmp_path, b"SILENT")

    decoded = decode_wav(path, PROFILE)

    assert decoded.payload == b"SILENT"
    assert decoded.version == 1
    assert decoded.crc == frame.crc
    assert decoded.preamble_matches == 32
    assert decoded.total_bits == frame.total_bits
    assert decoded.sample_rate == PROFILE.sample_rate


def test_utf8_roundtrip(tmp_path: Path) -> None:
    message = "привет, мир"
    path, _ = make_wav(tmp_path, message.encode("utf-8"))

    decoded = decode_wav(path, PROFILE)

    assert decoded.payload.decode("utf-8") == message


def test_binary_payload_roundtrip(tmp_path: Path) -> None:
    payload = bytes(range(256))
    path, _ = make_wav(tmp_path, payload)

    assert decode_wav(path, PROFILE).payload == payload


@pytest.mark.parametrize("length", [1, 2, 3, 7, 16, 32, 100, 255, 512])
def test_various_payload_lengths(tmp_path: Path, length: int) -> None:
    payload = payload_for(length)
    path, _ = make_wav(tmp_path, payload, name=f"tx-{length}.wav")

    decoded = decode_wav(path, PROFILE)

    assert decoded.payload == payload
    assert decoded.total_bits == FRAME_OVERHEAD_BITS + length * 8


@pytest.mark.parametrize("leading", [1, 159, 160, 1234, 4800])
def test_leading_silence_does_not_break_sync(tmp_path: Path, leading: int) -> None:
    path, _ = make_wav(tmp_path, b"LEAD", leading=leading)

    decoded = decode_wav(path, PROFILE)

    assert decoded.payload == b"LEAD"
    assert abs(decoded.start_sample - leading) <= PROFILE.samples_per_symbol


def test_trailing_data_is_ignored(tmp_path: Path) -> None:
    first, _ = make_samples(b"FIRST", trailing=2_000)
    second, _ = make_samples(b"SECOND")
    path = tmp_path / "two-frames.wav"
    write_pcm16(path, np.concatenate((first, second)), PROFILE.sample_rate)

    assert decode_wav(path, PROFILE).payload == b"FIRST"


def test_all_silence_has_no_signal(tmp_path: Path) -> None:
    path = tmp_path / "silence.wav"
    write_pcm16(path, np.zeros(PROFILE.sample_rate), PROFILE.sample_rate)

    with pytest.raises(SignalNotFound):
        decode_wav(path, PROFILE)


def test_empty_wav_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(PROFILE.sample_rate)
        wav_file.writeframes(b"")

    with pytest.raises(WavFormatError, match="не содержит"):
        decode_wav(path, PROFILE)


def test_corrupted_preamble_is_rejected(tmp_path: Path) -> None:
    frame = build_frame(b"PREAMBLE")
    bits = frame.bits.copy()
    bits[:8] = 1 - bits[:8]
    path = tmp_path / "bad-preamble.wav"
    write_bits(path, bits)

    with pytest.raises(SignalNotFound):
        decode_wav(path, PROFILE)


def test_corrupted_sync_word_is_rejected(tmp_path: Path) -> None:
    frame = build_frame(b"SYNC")
    bits = frame.bits.copy()
    bits[PREAMBLE_BITS] ^= 1
    path = tmp_path / "bad-sync.wav"
    write_bits(path, bits)

    with pytest.raises(SyncWordError, match="синхрослово"):
        decode_wav(path, PROFILE)


def test_incomplete_frame_is_rejected(tmp_path: Path) -> None:
    frame = build_frame(b"INCOMPLETE")
    samples = modulate(frame.bits, PROFILE).samples
    keep = (PAYLOAD_OFFSET + 2) * PROFILE.samples_per_symbol
    path = tmp_path / "incomplete.wav"
    write_pcm16(path, samples[:keep], PROFILE.sample_rate)

    with pytest.raises(IncompleteFrame, match="доступно"):
        decode_wav(path, PROFILE)


def test_truncated_wav_file_is_rejected(tmp_path: Path) -> None:
    path, _ = make_wav(tmp_path, b"TRUNCATED")
    path.write_bytes(path.read_bytes()[:-100])

    with pytest.raises(WavFormatError, match="обрезан|прочитать"):
        decode_wav(path, PROFILE)


def test_corrupted_payload_is_rejected_by_crc(tmp_path: Path) -> None:
    frame = build_frame(b"CORRUPT")
    bits = frame.bits.copy()
    bits[PAYLOAD_OFFSET + 3] ^= 1
    path = tmp_path / "bad-payload.wav"
    write_bits(path, bits)

    with pytest.raises(CrcError):
        decode_wav(path, PROFILE)


def test_corrupted_crc_is_rejected(tmp_path: Path) -> None:
    frame = build_frame(b"CRC")
    bits = frame.bits.copy()
    bits[-1] ^= 1
    path = tmp_path / "bad-crc.wav"
    write_bits(path, bits)

    with pytest.raises(CrcError):
        decode_wav(path, PROFILE)


def test_unsupported_version_is_rejected(tmp_path: Path) -> None:
    frame = build_frame(b"VERSION")
    bits = frame.bits.copy()
    set_uint(bits, PREAMBLE_BITS + SYNC_BITS, VERSION_BITS, 2)
    path = tmp_path / "bad-version.wav"
    write_bits(path, bits)

    with pytest.raises(FrameVersionError, match="версия"):
        decode_wav(path, PROFILE)


@pytest.mark.parametrize("length", [0, MAX_PAYLOAD_BYTES + 1, 0xFFFF])
def test_invalid_length_field_is_rejected(tmp_path: Path, length: int) -> None:
    frame = build_frame(b"LENGTH")
    bits = frame.bits.copy()
    set_uint(bits, PREAMBLE_BITS + SYNC_BITS + VERSION_BITS, LENGTH_BITS, length)
    path = tmp_path / f"bad-length-{length}.wav"
    write_bits(path, bits)

    with pytest.raises(FrameLengthError, match="длина"):
        decode_wav(path, PROFILE)


def test_wrong_but_plausible_length_fails_crc(tmp_path: Path) -> None:
    frame = build_frame(b"LENGTH")
    bits = frame.bits.copy()
    set_uint(
        bits,
        PREAMBLE_BITS + SYNC_BITS + VERSION_BITS,
        LENGTH_BITS,
        len(frame.payload) + 1,
    )
    samples = modulate(bits, PROFILE).samples
    # Объявлена длина на байт больше, чем передано. Добавляем тишину, чтобы
    # кадр формально помещался в запись: тогда ошибку должен поймать CRC,
    # а не проверка неполного кадра.
    samples = np.concatenate((samples, np.zeros(8 * PROFILE.samples_per_symbol)))
    path = tmp_path / "wrong-length.wav"
    write_pcm16(path, samples, PROFILE.sample_rate)

    with pytest.raises(CrcError):
        decode_wav(path, PROFILE)


def test_wrong_profile_does_not_decode(tmp_path: Path) -> None:
    path, _ = make_wav(tmp_path, b"PROFILE", profile=PROFILE_BFSK_18K)

    with pytest.raises(SignalNotFound):
        decode_wav(path, PROFILE_BFSK_16K)


def test_bit_metric_sign_follows_bits() -> None:
    bits = np.array([1, 0, 1, 1, 0, 0, 1, 0], dtype=np.uint8)
    metric = bit_metric(modulate(bits, PROFILE, ramp_ms=0.0).samples, PROFILE)
    for index, bit in enumerate(bits):
        start = index * PROFILE.samples_per_symbol
        assert (metric[start] > 0) == bool(bit)


def test_wrong_sample_rate_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "wrong-rate.wav"
    write_pcm16(path, np.zeros(1_000), 44_100)

    with pytest.raises(WavFormatError, match="частот"):
        decode_wav(path, PROFILE)


def test_stereo_and_24_bit_wav_are_rejected(tmp_path: Path) -> None:
    stereo = tmp_path / "stereo.wav"
    with wave.open(str(stereo), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(PROFILE.sample_rate)
        wav_file.writeframes(np.zeros(64, dtype="<i2").tobytes())

    with pytest.raises(WavFormatError, match="канал"):
        decode_wav(stereo, PROFILE)

    pcm24 = tmp_path / "pcm24.wav"
    with wave.open(str(pcm24), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(3)
        wav_file.setframerate(PROFILE.sample_rate)
        wav_file.writeframes(b"\x00" * 300)

    with pytest.raises(WavFormatError, match="16"):
        decode_wav(pcm24, PROFILE)


def test_non_wav_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "not-a-wav.txt"
    path.write_text("this is not a wav", encoding="utf-8")

    with pytest.raises(WavFormatError):
        decode_wav(path, PROFILE)


# --- CLI ---


def test_cli_roundtrip_and_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    message = "SN-021 CLI"
    wav_path = tmp_path / "tx.wav"
    out_path = tmp_path / "rx.bin"
    report_path = tmp_path / "rx.json"

    assert transmitter.main(["--message", message, "--out", str(wav_path)]) == 0
    assert (
        receiver.main(
            [
                "--in",
                str(wav_path),
                "--out",
                str(out_path),
                "--report",
                str(report_path),
            ]
        )
        == receiver.EXIT_OK
    )

    assert out_path.read_bytes() == message.encode("utf-8")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == receiver.SCHEMA
    assert report["task"] == "SN-021"
    assert report["payload"]["text"] == message
    assert report["payload"]["saved_to"] == str(out_path)
    assert report["frame"]["preamble_matches"] == 32
    assert report["input"]["sample_rate_hz"] == PROFILE.sample_rate
    assert report["output"]["report_path"] == str(report_path)
    assert "Текст:" in capsys.readouterr().out


def test_cli_binary_payload_writes_exact_bytes(tmp_path: Path) -> None:
    payload = bytes(range(32))
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)
    wav_path = tmp_path / "tx.wav"
    out_path = tmp_path / "rx.bin"

    assert transmitter.main(["--file", str(source), "--out", str(wav_path)]) == 0
    assert receiver.main(["--in", str(wav_path), "--out", str(out_path)]) == 0

    assert out_path.read_bytes() == payload
    report = json.loads(out_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["payload"]["text"] is None


def test_cli_missing_file_returns_3(tmp_path: Path) -> None:
    assert receiver.main(["--in", str(tmp_path / "nope.wav")]) == receiver.EXIT_INPUT


def test_cli_corrupted_frame_returns_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frame = build_frame(b"BROKEN")
    bits = frame.bits.copy()
    bits[-1] ^= 1
    wav_path = tmp_path / "bad.wav"
    write_bits(wav_path, bits)
    out_path = tmp_path / "rx.bin"

    assert (
        receiver.main(["--in", str(wav_path), "--out", str(out_path)])
        == receiver.EXIT_DECODE
    )
    assert not out_path.exists()
    assert "CRC" in capsys.readouterr().err


def test_cli_requires_input(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        receiver.main([])
    assert excinfo.value.code == receiver.EXIT_USAGE
    assert "--in" in capsys.readouterr().err


def test_cli_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        receiver.main(["--help"])
    assert excinfo.value.code == 0
    assert "usage:" in capsys.readouterr().out.lower()


def test_cli_unknown_profile_exits_2(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        receiver.main(["--in", str(tmp_path / "x.wav"), "--profile", "nope"])
    assert excinfo.value.code == receiver.EXIT_USAGE


def test_cli_unwritable_output_returns_3(tmp_path: Path) -> None:
    path, _ = make_wav(tmp_path, b"OUT")
    directory = tmp_path / "out-dir"
    directory.mkdir()

    assert (
        receiver.main(["--in", str(path), "--out", str(directory)])
        == receiver.EXIT_INPUT
    )


# --- SN-013: диагностическая демодуляция до CRC -------------------------


def test_demodulate_wav_returns_all_frame_bits(tmp_path: Path) -> None:
    path, frame = make_wav(tmp_path, b"DIAGNOSTIC")

    diagnostic = demodulate_wav(path, PROFILE)

    assert diagnostic.complete
    assert diagnostic.read_bits == frame.total_bits
    assert diagnostic.declared_payload_bytes == len(frame.payload)
    assert diagnostic.declared_length_valid
    assert diagnostic.preamble_matches == PREAMBLE_BITS
    np.testing.assert_array_equal(diagnostic.bits, frame.bits)


def test_demodulate_returns_bits_when_crc_is_broken(tmp_path: Path) -> None:
    frame = build_frame(b"CRC-BROKEN")
    bits = frame.bits.copy()
    bits[-1] ^= 1
    path = tmp_path / "bad-crc.wav"
    write_bits(path, bits)

    with pytest.raises(CrcError):
        decode_wav(path, PROFILE)

    diagnostic = demodulate_wav(path, PROFILE)
    assert diagnostic.read_bits == bits.size
    np.testing.assert_array_equal(diagnostic.bits, bits)


def test_demodulate_with_leading_silence_returns_frame_bits(tmp_path: Path) -> None:
    path, frame = make_wav(tmp_path, b"LEAD-DIAG", leading=4_800)

    diagnostic = demodulate_wav(path, PROFILE)

    assert diagnostic.start_sample == 4_800
    assert diagnostic.complete
    np.testing.assert_array_equal(diagnostic.bits, frame.bits)


def test_demodulate_silence_raises_signal_not_found(tmp_path: Path) -> None:
    path = tmp_path / "silence.wav"
    write_pcm16(path, np.zeros(PROFILE.sample_rate), PROFILE.sample_rate)

    with pytest.raises(SignalNotFound):
        demodulate_wav(path, PROFILE)


def test_demodulate_empty_wav_raises_wav_format(tmp_path: Path) -> None:
    path = tmp_path / "empty.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(PROFILE.sample_rate)
        wav_file.writeframes(b"")

    with pytest.raises(WavFormatError, match="не содержит"):
        demodulate_wav(path, PROFILE)


def test_demodulate_samples_does_not_accept_reference_bits() -> None:
    frame = build_frame(b"NO-REF")
    samples = modulate(frame.bits, PROFILE).samples

    diagnostic = demodulate_samples(samples, PROFILE)

    assert diagnostic.declared_payload_bytes == len(frame.payload)
    np.testing.assert_array_equal(diagnostic.bits, frame.bits)

# --- SN-035: устойчивость поиска начала кадра ---------------------------

def test_periodic_preamble_does_not_lock_two_symbols_early() -> None:
    """Преамбула `1010…` периодична: её продолжение не должно сдвигать кадр."""
    frame = build_frame(b"PERIODIC")
    # Два лишних символа преамбулы перед кадром дают ровно такое же
    # совпадение 32/32, но на два символа раньше истинного начала.
    bits = np.concatenate((np.array([1, 0], dtype=np.uint8), frame.bits))
    samples = np.concatenate(
        (np.zeros(1000), modulate(bits, PROFILE).samples, np.zeros(1000))
    )

    decoded = decode_samples(samples, PROFILE)

    assert decoded.payload == b"PERIODIC"
    assert decoded.crc == frame.crc


def test_crc_selects_start_inside_symbol_plateau() -> None:
    """Сдвиг начала на часть символа исправляется перебором до CRC."""
    samples, frame = make_samples(b"SHIFT")
    metric = bit_metric(samples, PROFILE)
    candidates = find_frame_candidates(metric, PROFILE)
    true_start = min(candidates, key=lambda item: item.sample).sample
    shifted = [
        FrameStart(
            sample=true_start + PROFILE.samples_per_symbol // 2,
            preamble_matches=PREAMBLE_BITS,
            coarse_sample=true_start,
        )
    ]

    decoded = decode_candidates(metric, PROFILE, shifted)

    assert decoded.payload == b"SHIFT"
    assert decoded.crc == frame.crc


def test_decodes_at_zero_snr_with_deterministic_noise() -> None:
    samples, _ = make_samples(b"NOISY", leading=4800, trailing=2400)
    tone_rms = float(np.sqrt(np.mean(samples**2)))
    rng = np.random.default_rng(20261104)

    for _ in range(5):
        received = samples + rng.normal(0.0, tone_rms, samples.size)
        decoded = decode_samples(received, PROFILE, min_preamble_matches=28)
        assert decoded.payload == b"NOISY"


def test_noise_only_is_never_accepted() -> None:
    rng = np.random.default_rng(777)

    for _ in range(5):
        noise = rng.normal(0.0, 0.01, 48000)
        with pytest.raises(ReceiverError):
            decode_samples(noise, PROFILE, min_preamble_matches=28)
