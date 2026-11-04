"""SN-034: файловый слой v0 над Frame v1, векторы, отказы и CLI.

Тесты проверяют только Python-ядро и файловый канал L0. Успешный round-trip
через WAV не означает передачу через воздух и не подтверждает дальность.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

import file_transfer as cli
from snlab.config import (
    PROFILE_DATA_AUDIBLE,
    PROFILE_DATA_ROBUST,
)
from snlab.datalink import MAX_DATA_PAYLOAD_BYTES, encode_text_to_wav
from snlab.file_transfer import (
    CHUNK_DATA_BYTES,
    MAX_CHUNKS,
    MAX_FILE_BYTES,
    MAX_FILENAME_BYTES,
    PACKET_DATA,
    PACKET_END,
    PACKET_MANIFEST,
    FileAssembler,
    FilePacket,
    FileTooLargeError,
    FileTransferError,
    FilenameError,
    HashMismatchError,
    IncompleteTransferError,
    ManifestError,
    NotFileTransferError,
    PacketError,
    TransferConflictError,
    build_transfer_plan,
    decode_packets_from_samples,
    decode_report_ok,
    decode_wav_to_file,
    encode_file_to_wav,
    expected_chunks,
    parse_packet,
    report_to_json,
    validate_filename,
)
from snlab.frame import Frame, build_frame
from snlab.wavio import read_pcm16, write_pcm16

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "file-transfer-vectors.json"


def load_fixture() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


FIXTURE = load_fixture()
VECTORS = {vector["name"]: vector for vector in FIXTURE["vectors"]}


def raw_packet(
    packet_type: int = PACKET_DATA,
    *,
    magic: bytes = b"SF",
    version: int = 1,
    transfer_id: int = 7,
    seq: int = 0,
    total_chunks: int = 1,
    payload: bytes = b"",
    payload_len: int | None = None,
) -> bytes:
    length = len(payload) if payload_len is None else payload_len
    return (
        magic
        + bytes((version, packet_type, transfer_id))
        + seq.to_bytes(2, "big")
        + total_chunks.to_bytes(2, "big")
        + bytes((length,))
        + payload
    )


def manifest_payload(
    name: str = "note.txt", size: int = 12, chunk: int = CHUNK_DATA_BYTES
) -> bytes:
    name_bytes = name.encode("utf-8")
    return (
        bytes((len(name_bytes),))
        + name_bytes
        + size.to_bytes(4, "big")
        + bytes((chunk,))
    )


def write_file(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def round_trip(
    tmp_path: Path,
    data: bytes,
    *,
    name: str = "payload.bin",
    profile=PROFILE_DATA_AUDIBLE,
    transfer_id: int = 42,
) -> tuple[dict, dict, Path]:
    source = write_file(tmp_path / name, data)
    wav = tmp_path / f"{name}.wav"
    tx = encode_file_to_wav(
        source,
        profile,
        wav,
        transfer_id=transfer_id,
        inter_frame_gap_s=0.1,
        lead_silence_s=0.1,
        tail_silence_s=0.05,
    )
    out_dir = tmp_path / "out"
    rx = decode_wav_to_file(wav, profile, out_dir)
    return tx, rx, out_dir / name


def load_samples(path: Path, profile) -> np.ndarray:
    samples, _ = read_pcm16(path, expected_sample_rate=profile.sample_rate)
    return samples


def mask_packet(samples: np.ndarray, profile, decoded) -> np.ndarray:
    """Занулить кадр начиная с decoded: имитация потери без CRC-мусора."""
    frame: Frame = build_frame(
        decoded.packet.to_bytes(), max_payload_bytes=MAX_DATA_PAYLOAD_BYTES
    )
    frame_samples = frame.total_bits * profile.samples_per_symbol
    start = decoded.start_sample
    stop = start + frame_samples
    masked = samples.copy()
    masked[start:stop] = 0.0
    return masked


# --- фикстура и точные wire-векторы --------------------------------------


def test_fixture_schema_and_limits() -> None:
    assert FIXTURE["schema"] == "silentnetwork.acoustic-lab.file-transfer-vectors/1"
    assert FIXTURE["ft_version"] == 1
    assert FIXTURE["chunk_data_bytes"] == CHUNK_DATA_BYTES
    assert FIXTURE["max_file_bytes"] == MAX_FILE_BYTES
    assert FIXTURE["max_filename_bytes"] == MAX_FILENAME_BYTES
    assert MAX_CHUNKS == 152


def test_fixture_matches_transfer_plan() -> None:
    plan = build_transfer_plan(b"Hello, world", "note.txt", 7)
    manifest, data, end, end_repeat = plan.packets

    assert manifest.to_bytes().hex() == VECTORS["manifest_note_txt"]["packet_hex"]
    assert data.to_bytes().hex() == VECTORS["data_note_txt"]["packet_hex"]
    assert end.to_bytes().hex() == VECTORS["end_note_txt"]["packet_hex"]
    assert end_repeat == end
    assert plan.manifest.file_size == VECTORS["manifest_note_txt"]["file_size"]
    assert hashlib.sha256(b"Hello, world").hexdigest() == VECTORS["end_note_txt"]["sha256"]


def test_fixture_parses_back_to_same_packets() -> None:
    for vector in FIXTURE["vectors"]:
        packet = parse_packet(bytes.fromhex(vector["packet_hex"]))
        assert packet.type_name == vector["packet_type"]
        assert packet.transfer_id == vector["transfer_id"]
        assert packet.seq == vector["seq"]
        assert packet.total_chunks == vector["total_chunks"]
        assert packet.to_bytes().hex() == vector["packet_hex"]


# --- план передачи --------------------------------------------------------


def test_expected_chunks_boundaries() -> None:
    assert expected_chunks(0) == 0
    assert expected_chunks(1) == 1
    assert expected_chunks(53) == 1
    assert expected_chunks(54) == 1
    assert expected_chunks(55) == 2
    assert expected_chunks(4096) == 76
    assert expected_chunks(8192) == 152


def test_plan_for_empty_file_has_manifest_and_end_only() -> None:
    plan = build_transfer_plan(b"", "empty.bin", 0)
    assert plan.total_chunks == 0
    assert [p.packet_type for p in plan.packets] == [
        PACKET_MANIFEST,
        PACKET_END,
        PACKET_END,
    ]


def test_plan_repeats_manifest_every_16_chunks() -> None:
    plan = build_transfer_plan(b"A" * 1000, "big.bin", 3)
    assert plan.total_chunks == 19
    assert sum(p.packet_type == PACKET_MANIFEST for p in plan.packets) == 2
    assert sum(p.packet_type == PACKET_DATA for p in plan.packets) == 19
    assert sum(p.packet_type == PACKET_END for p in plan.packets) == 2
    assert plan.packet_count == 23

    big = build_transfer_plan(bytes(MAX_FILE_BYTES), "max.bin", 4)
    assert big.total_chunks == MAX_CHUNKS
    assert big.packet_count == 1 + MAX_CHUNKS + (MAX_CHUNKS - 1) // 16 + 2


def test_plan_rejects_bad_parameters() -> None:
    with pytest.raises(FileTooLargeError):
        build_transfer_plan(bytes(MAX_FILE_BYTES + 1), "big.bin", 0)
    for transfer_id in (-1, 256, True):
        with pytest.raises(FileTransferError):
            build_transfer_plan(b"x", "a.bin", transfer_id)  # type: ignore[arg-type]
    with pytest.raises(FileTransferError):
        build_transfer_plan(b"x", "a.bin", 0, manifest_repeat_every=0)
    with pytest.raises(FileTransferError):
        build_transfer_plan(b"x", "a.bin", 0, end_repeats=0)


# --- имена файлов ---------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["note.txt", "a", "a" * MAX_FILENAME_BYTES, "заметка.txt", "image.PNG"],
)
def test_valid_filenames(name: str) -> None:
    assert validate_filename(name) == name.encode("utf-8")


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "a/b.txt",
        "a\\b.txt",
        "bad\x01.txt",
        " leading.txt",
        "trailing.txt ",
        "trailing.",
        "CON",
        "con.txt",
        "NUL.bin",
        "a" * (MAX_FILENAME_BYTES + 1),
    ],
)
def test_invalid_filenames(name: str) -> None:
    with pytest.raises(FilenameError):
        validate_filename(name)


def test_manifest_with_unsafe_filename_is_rejected() -> None:
    payload = manifest_payload(name="../evil.txt")
    raw = raw_packet(
        PACKET_MANIFEST, total_chunks=1, payload=payload
    )
    with pytest.raises(ManifestError):
        parse_packet(raw)


# --- разбор и валидация пакетов -----------------------------------------


def test_packet_round_trip_at_54_bytes() -> None:
    data = bytes(range(CHUNK_DATA_BYTES))
    packet = FilePacket(PACKET_DATA, 7, 0, 1, data)
    parsed = parse_packet(packet.to_bytes())
    assert parsed == packet
    assert len(parsed.payload) == CHUNK_DATA_BYTES


def test_short_packet_is_rejected() -> None:
    with pytest.raises(PacketError):
        parse_packet(b"SF\x01")


def test_wrong_magic_is_not_file_transfer() -> None:
    with pytest.raises(NotFileTransferError):
        parse_packet(raw_packet(magic=b"XX"))


def test_wrong_version_is_rejected() -> None:
    with pytest.raises(PacketError):
        parse_packet(raw_packet(version=2))


def test_unknown_packet_type_is_rejected() -> None:
    with pytest.raises(PacketError):
        parse_packet(raw_packet(packet_type=9))


def test_payload_len_mismatch_is_rejected() -> None:
    with pytest.raises(PacketError):
        parse_packet(raw_packet(payload=b"abc", payload_len=4))


def test_payload_over_54_bytes_is_rejected() -> None:
    with pytest.raises(PacketError):
        parse_packet(raw_packet(payload=b"x" * 55, payload_len=55))


def test_total_chunks_over_limit_is_rejected() -> None:
    with pytest.raises(PacketError):
        parse_packet(raw_packet(total_chunks=MAX_CHUNKS + 1))


def test_data_packet_requires_chunks_and_payload() -> None:
    with pytest.raises(PacketError):
        parse_packet(raw_packet(total_chunks=0))
    with pytest.raises(PacketError):
        parse_packet(raw_packet(seq=1, total_chunks=1, payload=b"x"))
    with pytest.raises(PacketError):
        parse_packet(raw_packet(seq=0, total_chunks=1, payload=b""))


def test_manifest_seq_must_be_zero() -> None:
    with pytest.raises(ManifestError):
        parse_packet(
            raw_packet(PACKET_MANIFEST, seq=1, payload=manifest_payload())
        )


def test_manifest_size_and_chunk_count_are_checked() -> None:
    over = manifest_payload(size=MAX_FILE_BYTES + 1)
    with pytest.raises(ManifestError):
        parse_packet(raw_packet(PACKET_MANIFEST, payload=over))

    mismatch = manifest_payload(size=12)
    with pytest.raises(ManifestError):
        parse_packet(raw_packet(PACKET_MANIFEST, total_chunks=2, payload=mismatch))

    bad_chunk = manifest_payload(chunk=53)
    with pytest.raises(ManifestError):
        parse_packet(raw_packet(PACKET_MANIFEST, payload=bad_chunk))


def test_end_packet_length_and_fields_are_checked() -> None:
    with pytest.raises(PacketError):
        parse_packet(raw_packet(PACKET_END, payload=b"\x00" * 37))
    with pytest.raises(PacketError):
        parse_packet(raw_packet(PACKET_END, seq=1, payload=b"\x00" * 38))


def test_empty_file_manifest_and_end_are_valid() -> None:
    plan = build_transfer_plan(b"", "empty.bin", 1)
    for packet in plan.packets:
        assert parse_packet(packet.to_bytes()) == packet


# --- сборка файла ---------------------------------------------------------


def test_assembler_rejects_missing_chunks() -> None:
    plan = build_transfer_plan(b"A" * 100, "two.bin", 5)
    assembler = FileAssembler()
    for packet in plan.packets:
        if packet.packet_type == PACKET_DATA and packet.seq == 1:
            continue
        assembler.add(packet)

    assert assembler.state().missing_chunks == (1,)
    assert assembler.state().complete is False
    with pytest.raises(IncompleteTransferError):
        assembler.assemble()


def test_assembler_accepts_duplicates_and_reordering() -> None:
    plan = build_transfer_plan(b"A" * 100, "two.bin", 5)
    assembler = FileAssembler()
    data = [p for p in plan.packets if p.packet_type == PACKET_DATA]
    others = [p for p in plan.packets if p.packet_type != PACKET_DATA]
    for packet in reversed(data):
        assembler.add(packet)
    for packet in others:
        assembler.add(packet)
    for packet in others:
        assembler.add(packet)
    for packet in data:
        assembler.add(packet)

    assert assembler.state().duplicate_chunks == len(data)
    assert assembler.assemble() == b"A" * 100


def test_assembler_rejects_conflicting_chunk() -> None:
    plan = build_transfer_plan(b"A" * 100, "two.bin", 5)
    assembler = FileAssembler()
    for packet in plan.packets:
        if packet.packet_type == PACKET_DATA and packet.seq == 0:
            assembler.add(packet)
            break
    conflict = FilePacket(PACKET_DATA, 5, 0, 2, b"B" * CHUNK_DATA_BYTES)
    with pytest.raises(TransferConflictError):
        assembler.add(conflict)


def test_assembler_rejects_transfer_id_mismatch() -> None:
    plan = build_transfer_plan(b"A" * 100, "two.bin", 5)
    assembler = FileAssembler()
    assembler.add(plan.packets[0])
    wrong = FilePacket(PACKET_DATA, 6, 0, 2, b"A" * CHUNK_DATA_BYTES)
    with pytest.raises(TransferConflictError):
        assembler.add(wrong)


def test_assembler_rejects_different_manifest_repeat() -> None:
    plan = build_transfer_plan(b"A" * 100, "two.bin", 5)
    assembler = FileAssembler()
    assembler.add(plan.packets[0])
    other = FilePacket(
        PACKET_MANIFEST,
        5,
        0,
        2,
        manifest_payload(name="other.bin", size=100),
    )
    with pytest.raises(TransferConflictError):
        assembler.add(other)


def test_assembler_detects_sha256_mismatch() -> None:
    plan = build_transfer_plan(b"Hello, world", "note.txt", 7)
    assembler = FileAssembler()
    end = next(p for p in plan.packets if p.packet_type == PACKET_END)
    corrupt_payload = bytearray(end.payload)
    corrupt_payload[-1] ^= 0x01
    corrupt_end = FilePacket(PACKET_END, 7, 0, 1, bytes(corrupt_payload))
    for packet in plan.packets:
        if packet.packet_type != PACKET_END:
            assembler.add(packet)
    assembler.add(corrupt_end)

    with pytest.raises(HashMismatchError):
        assembler.assemble()


# --- сквозной файловый round-trip ----------------------------------------


@pytest.mark.parametrize(
    "size", [0, 1, 53, 54, 55, 100, 1000, 4096, 8192], ids=lambda value: f"{value}B"
)
def test_file_round_trip_through_wav(tmp_path: Path, size: int) -> None:
    data = bytes((index * 37 + 11) % 256 for index in range(size))
    tx, rx, output = round_trip(tmp_path, data, name="payload.bin")

    assert tx["schema"] == "silentnetwork.acoustic-lab.file-transfer/1"
    assert tx["result"] == "ok"
    assert tx["file"]["size"] == size
    assert tx["file"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert tx["transfer"]["total_chunks"] == expected_chunks(size)
    assert tx["crc_is_authentication"] is False
    assert Path(tx["wav"]["path"]).is_file()

    assert decode_report_ok(rx), rx.get("message")
    assert rx["result"] == "ok"
    assert rx["transfer"]["complete"] is True
    assert rx["transfer"]["hash_ok"] is True
    assert rx["file"]["size"] == size
    assert rx["file"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert output.read_bytes() == data
    assert rx["stream"]["frames_found"] == tx["transfer"]["packet_count"]


def test_robust_profile_round_trip(tmp_path: Path) -> None:
    data = b"ROBUST" * 17
    tx, rx, output = round_trip(
        tmp_path, data, name="robust.bin", profile=PROFILE_DATA_ROBUST
    )
    assert decode_report_ok(rx)
    assert output.read_bytes() == data
    assert tx["profile"]["name"] == "data-robust"


def test_tx_report_is_json_serializable(tmp_path: Path) -> None:
    source = write_file(tmp_path / "note.txt", b"Hello, world")
    report = encode_file_to_wav(
        source, PROFILE_DATA_AUDIBLE, tmp_path / "note.wav", transfer_id=7
    )
    restored = json.loads(report_to_json(report))
    assert restored["file"]["filename"] == "note.txt"
    assert restored["transfer"]["packet_types"]["data"] == 1
    assert restored["wav"]["sha256"] == hashlib.sha256(
        Path(restored["wav"]["path"]).read_bytes()
    ).hexdigest()


def test_encode_rejects_oversize_and_bad_name(tmp_path: Path) -> None:
    big = write_file(tmp_path / "big.bin", bytes(MAX_FILE_BYTES + 1))
    with pytest.raises(FileTooLargeError):
        encode_file_to_wav(big, PROFILE_DATA_AUDIBLE, tmp_path / "big.wav")

    long_name = write_file(tmp_path / ("n" * 41), b"x")
    with pytest.raises(FilenameError):
        encode_file_to_wav(long_name, PROFILE_DATA_AUDIBLE, tmp_path / "long.wav")


# --- потеря кадров и ресинхронизация ------------------------------------


def test_lost_data_frame_reports_incomplete_without_output(tmp_path: Path) -> None:
    data = b"A" * 100
    source = write_file(tmp_path / "two.bin", data)
    wav = tmp_path / "two.wav"
    encode_file_to_wav(
        source,
        PROFILE_DATA_AUDIBLE,
        wav,
        transfer_id=13,
        inter_frame_gap_s=0.1,
        lead_silence_s=0.1,
        tail_silence_s=0.05,
    )
    samples = load_samples(wav, PROFILE_DATA_AUDIBLE)
    stream = decode_packets_from_samples(samples, PROFILE_DATA_AUDIBLE)
    lost = next(
        item
        for item in stream.packets
        if item.packet.packet_type == PACKET_DATA and item.packet.seq == 0
    )
    masked = mask_packet(samples, PROFILE_DATA_AUDIBLE, lost)
    write_pcm16(wav, masked, PROFILE_DATA_AUDIBLE.sample_rate)

    out_dir = tmp_path / "out"
    report = decode_wav_to_file(wav, PROFILE_DATA_AUDIBLE, out_dir)

    assert report["result"] == "error"
    assert report["error_type"] == "incomplete_transfer"
    assert report["transfer"]["missing_chunks"] == [0]
    assert report["transfer"]["hash_ok"] is False
    assert not (out_dir / "two.bin").exists()
    assert report["stream"]["frames_found"] == len(stream.packets) - 1


def test_lost_duplicate_manifest_still_succeeds(tmp_path: Path) -> None:
    data = b"M" * 1000
    source = write_file(tmp_path / "big.bin", data)
    wav = tmp_path / "big.wav"
    encode_file_to_wav(
        source,
        PROFILE_DATA_AUDIBLE,
        wav,
        transfer_id=14,
        inter_frame_gap_s=0.1,
        lead_silence_s=0.1,
        tail_silence_s=0.05,
    )
    samples = load_samples(wav, PROFILE_DATA_AUDIBLE)
    stream = decode_packets_from_samples(samples, PROFILE_DATA_AUDIBLE)
    manifests = [
        item for item in stream.packets if item.packet.packet_type == PACKET_MANIFEST
    ]
    assert len(manifests) == 2
    masked = mask_packet(samples, PROFILE_DATA_AUDIBLE, manifests[1])
    write_pcm16(wav, masked, PROFILE_DATA_AUDIBLE.sample_rate)

    out_dir = tmp_path / "out"
    report = decode_wav_to_file(wav, PROFILE_DATA_AUDIBLE, out_dir)

    assert decode_report_ok(report), report.get("message")
    assert (out_dir / "big.bin").read_bytes() == data


def test_lost_end_frames_report_incomplete(tmp_path: Path) -> None:
    source = write_file(tmp_path / "one.bin", b"END?")
    wav = tmp_path / "one.wav"
    encode_file_to_wav(
        source,
        PROFILE_DATA_AUDIBLE,
        wav,
        transfer_id=15,
        inter_frame_gap_s=0.1,
        lead_silence_s=0.1,
        tail_silence_s=0.05,
    )
    samples = load_samples(wav, PROFILE_DATA_AUDIBLE)
    stream = decode_packets_from_samples(samples, PROFILE_DATA_AUDIBLE)
    ends = [item for item in stream.packets if item.packet.packet_type == PACKET_END]
    assert len(ends) == 2
    masked = mask_packet(samples, PROFILE_DATA_AUDIBLE, ends[0])
    masked = mask_packet(masked, PROFILE_DATA_AUDIBLE, ends[1])
    write_pcm16(wav, masked, PROFILE_DATA_AUDIBLE.sample_rate)

    out_dir = tmp_path / "out"
    report = decode_wav_to_file(wav, PROFILE_DATA_AUDIBLE, out_dir)

    assert report["result"] == "error"
    assert report["error_type"] == "incomplete_transfer"
    assert report["transfer"]["end_seen"] is False
    assert not (out_dir / "one.bin").exists()


def test_conflicting_second_transfer_is_rejected(tmp_path: Path) -> None:
    """Полный файл A + другой файл B с тем же transfer_id не должен пройти."""
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    write_file(first / "data.bin", b"A" * 100)
    write_file(second / "data.bin", b"B" * 100)
    wav_a = tmp_path / "a.wav"
    wav_b = tmp_path / "b.wav"
    for source, target in ((first / "data.bin", wav_a), (second / "data.bin", wav_b)):
        encode_file_to_wav(
            source,
            PROFILE_DATA_AUDIBLE,
            target,
            transfer_id=21,
            inter_frame_gap_s=0.1,
            lead_silence_s=0.1,
            tail_silence_s=0.05,
        )

    samples_a = load_samples(wav_a, PROFILE_DATA_AUDIBLE)
    samples_b = load_samples(wav_b, PROFILE_DATA_AUDIBLE)
    gap = np.zeros(int(0.2 * PROFILE_DATA_AUDIBLE.sample_rate))
    wav = tmp_path / "conflict.wav"
    write_pcm16(
        wav,
        np.concatenate((samples_a, gap, samples_b)),
        PROFILE_DATA_AUDIBLE.sample_rate,
    )

    out_dir = tmp_path / "out"
    report = decode_wav_to_file(wav, PROFILE_DATA_AUDIBLE, out_dir)

    assert report["result"] == "error"
    assert report["error_type"] == "transfer_conflict"
    assert report["transfer"]["complete"] is True
    assert report["transfer"]["conflicts"]
    assert not (out_dir / "data.bin").exists()


# --- отказы приёма --------------------------------------------------------


def test_missing_and_broken_wav_are_reported(tmp_path: Path) -> None:
    missing = decode_wav_to_file(
        tmp_path / "missing.wav", PROFILE_DATA_AUDIBLE, tmp_path / "out"
    )
    assert missing["error_type"] == "wav_not_found"

    empty = write_file(tmp_path / "empty.wav", b"")
    broken = decode_wav_to_file(empty, PROFILE_DATA_AUDIBLE, tmp_path / "out")
    assert broken["error_type"] == "wav_format_error"


def test_wrong_sample_rate_is_reported(tmp_path: Path) -> None:
    wav = tmp_path / "rate.wav"
    write_pcm16(wav, np.zeros(4410), 44_100)
    report = decode_wav_to_file(wav, PROFILE_DATA_AUDIBLE, tmp_path / "out")
    assert report["error_type"] == "wav_format_error"
    assert "48000" in report["message"]


def test_silence_has_no_signal(tmp_path: Path) -> None:
    wav = tmp_path / "silence.wav"
    write_pcm16(wav, np.zeros(24_000), PROFILE_DATA_AUDIBLE.sample_rate)
    report = decode_wav_to_file(wav, PROFILE_DATA_AUDIBLE, tmp_path / "out")
    assert report["error_type"] == "signal_not_found"


def test_non_file_frame_is_not_file_transfer(tmp_path: Path) -> None:
    wav = tmp_path / "datalink.wav"
    encode_text_to_wav(
        "HELLO",
        PROFILE_DATA_AUDIBLE,
        wav,
        lead_silence_s=0.1,
        tail_silence_s=0.05,
    )
    report = decode_wav_to_file(wav, PROFILE_DATA_AUDIBLE, tmp_path / "out")
    assert report["error_type"] == "not_file_transfer"
    assert report["stream"]["frames_found"] == 1


# --- CLI ------------------------------------------------------------------


def test_cli_help_and_missing_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    assert "usage:" in capsys.readouterr().out.lower()

    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2


def test_cli_send_then_receive(tmp_path: Path) -> None:
    data = bytes(range(100))
    source = write_file(tmp_path / "sample.bin", data)
    wav = tmp_path / "sample.wav"
    tx_report = tmp_path / "tx.json"
    rx_report = tmp_path / "rx.json"
    out_dir = tmp_path / "received"

    assert (
        cli.main(
            [
                "send",
                "--file",
                str(source),
                "--out",
                str(wav),
                "--report",
                str(tx_report),
                "--transfer-id",
                "9",
                "--gap-ms",
                "100",
                "--lead-s",
                "0.1",
                "--tail-s",
                "0.05",
            ]
        )
        == 0
    )
    assert wav.is_file()
    tx = json.loads(tx_report.read_text(encoding="utf-8"))
    assert tx["tool"] == "file_transfer.py"
    assert tx["transfer"]["transfer_id"] == 9
    assert tx["file"]["sha256"] == hashlib.sha256(data).hexdigest()

    assert (
        cli.main(
            [
                "receive",
                "--wav",
                str(wav),
                "--out-dir",
                str(out_dir),
                "--report",
                str(rx_report),
            ]
        )
        == 0
    )
    rx = json.loads(rx_report.read_text(encoding="utf-8"))
    assert rx["result"] == "ok"
    assert rx["transfer"]["hash_ok"] is True
    assert (out_dir / "sample.bin").read_bytes() == data


def test_cli_send_uses_default_report_path(tmp_path: Path) -> None:
    source = write_file(tmp_path / "a.bin", b"A")
    wav = tmp_path / "a.wav"
    assert cli.main(["send", "--file", str(source), "--out", str(wav)]) == 0
    assert wav.with_suffix(".tx.json").is_file()


def test_cli_default_reports_do_not_overwrite_source_or_each_other(
    tmp_path: Path,
) -> None:
    data = b'{"node":"laptop"}'
    source = write_file(tmp_path / "status.json", data)
    wav = tmp_path / "status.wav"
    assert cli.main(["send", "--file", str(source), "--out", str(wav)]) == 0
    assert source.read_bytes() == data
    tx_report = wav.with_suffix(".tx.json")
    assert tx_report.is_file()
    tx_bytes = tx_report.read_bytes()

    out_dir = tmp_path / "out"
    assert cli.main(["receive", "--wav", str(wav), "--out-dir", str(out_dir)]) == 0
    assert tx_report.read_bytes() == tx_bytes
    assert wav.with_suffix(".rx.json").is_file()
    assert (out_dir / "status.json").read_bytes() == data


def test_cli_send_rejects_missing_and_oversize(tmp_path: Path) -> None:
    assert (
        cli.main(["send", "--file", str(tmp_path / "missing.bin")])
        == cli.EXIT_IO
    )

    big = write_file(tmp_path / "big.bin", bytes(MAX_FILE_BYTES + 1))
    assert (
        cli.main(
            [
                "send",
                "--file",
                str(big),
                "--out",
                str(tmp_path / "big.wav"),
                "--report",
                str(tmp_path / "big.json"),
            ]
        )
        == cli.EXIT_USAGE
    )
    assert not (tmp_path / "big.wav").exists()


def test_cli_receive_failure_exit_codes(tmp_path: Path) -> None:
    missing_report = tmp_path / "missing.json"
    assert (
        cli.main(
            [
                "receive",
                "--wav",
                str(tmp_path / "missing.wav"),
                "--report",
                str(missing_report),
            ]
        )
        == cli.EXIT_IO
    )
    assert json.loads(missing_report.read_text(encoding="utf-8"))["error_type"] == (
        "wav_not_found"
    )

    wav = tmp_path / "datalink.wav"
    encode_text_to_wav(
        "HELLO",
        PROFILE_DATA_AUDIBLE,
        wav,
        lead_silence_s=0.1,
        tail_silence_s=0.05,
    )
    assert (
        cli.main(
            [
                "receive",
                "--wav",
                str(wav),
                "--out-dir",
                str(tmp_path / "out"),
                "--report",
                str(tmp_path / "rx.json"),
            ]
        )
        == cli.EXIT_DECODE
    )


def test_cli_rejects_invalid_preamble_matches(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "receive",
                "--wav",
                str(tmp_path / "x.wav"),
                "--min-preamble-matches",
                "0",
            ]
        )
    assert excinfo.value.code == 2
