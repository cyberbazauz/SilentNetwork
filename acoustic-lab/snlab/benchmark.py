"""Воспроизводимый BER/PER benchmark файлового BFSK-канала (SN-013).

Эксперимент работает только с файлами и смоделированным каналом AWGN:

    payload → Frame v1 → CPFSK → PCM16 WAV → +AWGN → WAV → receiver

Шум добавляется к аудиоотсчётам, а не к битам или payload. Мощность полезного
сигнала считается по активной области, поэтому начальная/конечная тишина не
занижает SNR. Результаты AWGN **не** являются доказательством передачи через
воздух.

PER включает все неудачные испытания: потерю преамбулы, ошибку sync word,
неполный кадр, неверную версию/длину, CRC и несовпадение payload. BER
разделяется на BER по доступным демодулированным битам и BER, в котором
недоступные биты считаются ошибками.
"""

from __future__ import annotations

import csv
import hashlib
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from snlab.bfsk import DEFAULT_RAMP_MS, modulate
from snlab.channel import (
    DEFAULT_ACTIVE_THRESHOLD_DB,
    active_region_bounds,
    active_signal_power,
    add_awgn,
)
from snlab.config import MAX_PAYLOAD_BYTES, BfskProfile
from snlab.frame import build_frame
from snlab.receiver import (
    CrcError,
    FrameLengthError,
    FrameVersionError,
    IncompleteFrame,
    ReceiverError,
    SignalNotFound,
    SyncWordError,
    decode_wav,
    demodulate_wav,
)
from snlab.reporting import environment, utc_now, write_json
from snlab.wavio import WavFormatError, read_pcm16, write_pcm16

SCHEMA = "silentnetwork.acoustic-lab.benchmark/1"
TOOL_NAME = "benchmark.py"
TASK_ID = "SN-013"

REQUIRED_SNR_DB = (0.0, 5.0, 10.0, 15.0, 20.0)
DEFAULT_SEED = 20261101
M0_SNR_DB = 15.0
M0_TRIALS = 100
M0_PAYLOAD_BYTES = 32

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_IO = 4

LIMITATIONS = [
    "AWGN — это симуляция, а не реальный акустический канал. Результаты не "
    "подтверждают работу через динамик и микрофон.",
    "Модель не учитывает реверберацию, многолучёвость, дрейф частоты, "
    "нелинейные искажения динамика и обработку Windows (AGC, шумоподавление).",
    "Сигнал не масштабируется; если шум выводит отсчёты за ±1, PCM16 "
    "клиппируется. Число таких отсчётов фиксируется, но не корректируется.",
    "BER по доступным битам не определён, если приёмник не нашёл преамбулу "
    "или не демодулировал ни одного бита; в этом случае он равен null, а не 0.",
    "BER с пропущенными битами считает недоступные биты ошибками: это верхняя "
    "оценка, а не BER конкретного демодулятора на принятых битах.",
    "CRC-16 обнаруживает повреждение, но не аутентифицирует payload и не "
    "заменяет AEAD.",
    "Один профиль, один sample rate и одна платформа не описывают всё "
    "оборудование; для других профилей эксперимент нужно повторить.",
]

METHODOLOGY = {
    "channel": (
        "AWGN добавляется к PCM16 аудиоотсчётам после записи и повторного "
        "чтения WAV текущего передатчика; биты и payload шумом не изменяются."
    ),
    "snr_definition": "SNR_dB = 10 * log10(P_signal_active / P_noise)",
    "signal_power": (
        "P_signal_active = mean(x^2) по активной области от -40 dB "
        "относительно пика; начальная и конечная тишина исключены."
    ),
    "noise_power": (
        "P_noise = sigma^2; sigma = sqrt(P_signal_active / 10^(SNR_dB/10)). "
        "Шум добавляется ко всей записи, включая тишину."
    ),
    "realized_snr": (
        "Фактический SNR считается по той же P_signal_active и измеренной "
        "дисперсии добавленного шума; он фиксируется для каждой точки."
    ),
    "level_policy": (
        "Уровень сигнала, sample rate и длина записи не меняются; "
        "ресемплирование и автоматическое масштабирование отсутствуют."
    ),
    "per_definition": (
        "PER = число испытаний без точного совпадения payload / все "
        "испытания. Потеря синхронизации, sync word, версия, длина, "
        "неполный кадр, CRC и несовпадение payload учитываются как ошибки."
    ),
    "ber_available": (
        "BER_available = ошибочные доступные биты / сравнённые доступные "
        "биты. null, если не демодулировано ни одного бита."
    ),
    "ber_effective": (
        "BER_effective = (ошибочные биты + недоступные биты) / биты "
        "эталонного кадра; пропущенные биты считаются ошибками."
    ),
    "seed_scheme": (
        "Каждый payload и каждый шум получают seed из "
        "numpy.random.SeedSequence([base_seed, role, snr_index, trial_index]); "
        "payload не зависит от SNR, шум независим для каждой точки."
    ),
}


class BenchmarkError(ValueError):
    """Параметры benchmark некорректны."""


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Параметры воспроизводимого эксперимента."""

    snr_db: tuple[float, ...]
    trials: int
    payload_bytes: int
    seed: int
    profile: BfskProfile
    ramp_ms: float = DEFAULT_RAMP_MS
    m0_snr_db: float = M0_SNR_DB
    m0_trials: int = M0_TRIALS
    m0_seed: int | None = None

    def __post_init__(self) -> None:
        if not self.snr_db:
            raise BenchmarkError("нужен хотя бы один SNR")
        if len(set(self.snr_db)) != len(self.snr_db):
            raise BenchmarkError("значения SNR не должны повторяться")
        for value in self.snr_db:
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise BenchmarkError("SNR должен быть конечным числом")
        if not isinstance(self.trials, int) or isinstance(self.trials, bool):
            raise BenchmarkError("trials должен быть целым числом")
        if self.trials < 1:
            raise BenchmarkError("trials должен быть не меньше 1")
        if not isinstance(self.payload_bytes, int) or isinstance(self.payload_bytes, bool):
            raise BenchmarkError("payload_bytes должен быть целым числом")
        if not 1 <= self.payload_bytes <= MAX_PAYLOAD_BYTES:
            raise BenchmarkError(
                f"payload_bytes должен быть в диапазоне 1..{MAX_PAYLOAD_BYTES}"
            )
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0:
            raise BenchmarkError("seed должен быть неотрицательным целым числом")
        if not isinstance(self.profile, BfskProfile):
            raise BenchmarkError("profile должен быть BfskProfile")
        if (
            not isinstance(self.ramp_ms, (int, float))
            or isinstance(self.ramp_ms, bool)
            or not math.isfinite(self.ramp_ms)
            or not 0.0 <= self.ramp_ms <= 500.0
        ):
            raise BenchmarkError("ramp_ms должен быть в диапазоне 0..500")
        if (
            not isinstance(self.m0_snr_db, (int, float))
            or isinstance(self.m0_snr_db, bool)
            or not math.isfinite(self.m0_snr_db)
        ):
            raise BenchmarkError("m0_snr_db должен быть конечным числом")
        if not isinstance(self.m0_trials, int) or isinstance(self.m0_trials, bool):
            raise BenchmarkError("m0_trials должен быть целым числом")
        if self.m0_trials < 0:
            raise BenchmarkError("m0_trials не может быть отрицательным")
        if self.m0_seed is not None and (
            not isinstance(self.m0_seed, int)
            or isinstance(self.m0_seed, bool)
            or self.m0_seed < 0
        ):
            raise BenchmarkError("m0_seed должен быть неотрицательным целым числом")


@dataclass(frozen=True, slots=True)
class TrialResult:
    """Одно испытание: один payload, один шум, одно декодирование."""

    snr_db: float
    trial: int
    payload_seed: int
    noise_seed: int
    payload_bytes: int
    payload_sha256: str
    frame_bits: int
    success: bool
    failure: str | None
    failure_class: str | None
    demodulated: bool
    crc_ok: bool
    payload_match: bool
    available_bits: int
    compared_bits: int
    bit_errors: int
    missing_bits: int
    ber_available: float | None
    ber_effective: float
    active_signal_power: float
    active_samples: int
    noise_std_expected: float
    noise_std_measured: float
    realized_snr_db: float
    clipped_samples: int
    clean_wav_sha256: str
    noisy_wav_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "snr_db": _round(self.snr_db),
            "trial": self.trial,
            "payload_seed": self.payload_seed,
            "noise_seed": self.noise_seed,
            "payload_bytes": self.payload_bytes,
            "payload_sha256": self.payload_sha256,
            "frame_bits": self.frame_bits,
            "success": self.success,
            "failure": self.failure,
            "failure_class": self.failure_class,
            "demodulated": self.demodulated,
            "crc_ok": self.crc_ok,
            "payload_match": self.payload_match,
            "available_bits": self.available_bits,
            "compared_bits": self.compared_bits,
            "bit_errors": self.bit_errors,
            "missing_bits": self.missing_bits,
            "ber_available": _round_optional(self.ber_available),
            "ber_effective": _round(self.ber_effective),
            "active_signal_power": _round(self.active_signal_power, 12),
            "active_samples": self.active_samples,
            "noise_std_expected": _round(self.noise_std_expected, 12),
            "noise_std_measured": _round(self.noise_std_measured, 12),
            "realized_snr_db": _round(self.realized_snr_db),
            "clipped_samples": self.clipped_samples,
            "clean_wav_sha256": self.clean_wav_sha256,
            "noisy_wav_sha256": self.noisy_wav_sha256,
        }


@dataclass(frozen=True, slots=True)
class SnrSummary:
    """Агрегат по одной точке SNR; неудачи не исключаются."""

    snr_db: float
    trials: int
    successes: int
    failures: int
    per: float
    ber_available: float | None
    ber_effective: float
    bit_errors: int
    bits_compared: int
    bits_missing: int
    total_reference_bits: int
    demodulation_unavailable: int
    synchronization_errors: int
    sync_word_errors: int
    crc_errors: int
    incomplete_errors: int
    version_errors: int
    length_errors: int
    payload_mismatches: int
    wav_errors: int
    other_errors: int
    clipped_samples: int
    active_signal_power_mean: float
    mean_realized_snr_db: float

    @property
    def demodulation_unavailable_fraction(self) -> float:
        """Доля испытаний, в которых демодуляция бит невозможна."""
        return self.demodulation_unavailable / self.trials

    @property
    def missing_bit_fraction(self) -> float:
        """Доля бит эталонных кадров, для которых нет принятой пары."""
        return self.bits_missing / self.total_reference_bits

    def as_dict(self) -> dict[str, Any]:
        return {
            "snr_db": _round(self.snr_db),
            "trials": self.trials,
            "successes": self.successes,
            "failures": self.failures,
            "per": _round(self.per),
            "ber_available": _round_optional(self.ber_available),
            "ber_effective": _round(self.ber_effective),
            "bit_errors": self.bit_errors,
            "bits_compared": self.bits_compared,
            "bits_missing": self.bits_missing,
            "total_reference_bits": self.total_reference_bits,
            "demodulation_unavailable": self.demodulation_unavailable,
            "demodulation_unavailable_fraction": _round(
                self.demodulation_unavailable_fraction
            ),
            "missing_bit_fraction": _round(self.missing_bit_fraction),
            "synchronization_errors": self.synchronization_errors,
            "sync_word_errors": self.sync_word_errors,
            "crc_errors": self.crc_errors,
            "incomplete_errors": self.incomplete_errors,
            "version_errors": self.version_errors,
            "length_errors": self.length_errors,
            "payload_mismatches": self.payload_mismatches,
            "wav_errors": self.wav_errors,
            "other_errors": self.other_errors,
            "clipped_samples": self.clipped_samples,
            "active_signal_power_mean": _round(self.active_signal_power_mean, 12),
            "mean_realized_snr_db": _round(self.mean_realized_snr_db),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Полный результат эксперимента, включая отдельную проверку M0-6."""

    config: BenchmarkConfig
    summaries: tuple[SnrSummary, ...]
    trials: tuple[TrialResult, ...]
    m0_summary: SnrSummary | None
    m0_trials: tuple[TrialResult, ...]
    generated_at_utc: str
    command: str | None
    environment: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "tool": TOOL_NAME,
            "task": TASK_ID,
            "generated_at_utc": self.generated_at_utc,
            "command": self.command,
            "environment": self.environment,
            "experiment": {
                "profile": self.config.profile.as_dict(),
                "snr_db": [_round(value) for value in self.config.snr_db],
                "required_snr_db": list(REQUIRED_SNR_DB),
                "trials_per_point": self.config.trials,
                "payload_bytes": self.config.payload_bytes,
                "seed": self.config.seed,
                "ramp_ms": self.config.ramp_ms,
                "m0": {
                    "snr_db": _round(self.config.m0_snr_db),
                    "trials": self.config.m0_trials,
                    "payload_bytes": M0_PAYLOAD_BYTES,
                    "seed": self.config.m0_seed,
                    "enabled": self.config.m0_trials > 0,
                },
            },
            "methodology": METHODOLOGY,
            "results": [summary.as_dict() for summary in self.summaries],
            "m0_6": self.m0_summary.as_dict() if self.m0_summary is not None else None,
            "trials": [trial.as_dict() for trial in self.trials],
            "m0_6_trials": [trial.as_dict() for trial in self.m0_trials],
            "limitations": LIMITATIONS,
        }


def derive_seed(base_seed: int, *path: int) -> int:
    """Детерминированно вывести 32-битный seed из базового seed и пути.

    Одинаковые аргументы дают одинаковый seed на разных запусках. Разные
    роли (payload/шум), точки SNR и номера испытаний дают разные seed.
    """
    sequence = np.random.SeedSequence([int(base_seed), *(int(item) for item in path)])
    return int(sequence.generate_state(1, dtype=np.uint32)[0])


def compare_bits(
    reference: np.ndarray | Sequence[int],
    received: np.ndarray | Sequence[int] | None,
) -> tuple[int, int, int, float | None, float]:
    """Сравнить эталонные биты с диагностически демодулированными.

    Args:
        reference: биты эталонного кадра (передатчик).
        received: доступные демодулированные биты; None — демодуляция
            невозможна (например, преамбула не найдена).

    Returns:
        ``(bit_errors, compared, missing, ber_available, ber_effective)``.
        ``missing`` — биты эталона, которые не с чем сравнить. В
        ``ber_effective`` они считаются ошибками, поэтому при полностью
        недоступной демодуляции получается 1.0, а не 0.0.

    Raises:
        ValueError: эталон пуст или содержит не 0/1.
    """
    expected = np.asarray(reference, dtype=np.uint8).ravel()
    if expected.size == 0:
        raise ValueError("пустая эталонная последовательность бит")
    if np.any(expected > 1):
        raise ValueError("эталонные биты должны принимать значения 0 или 1")

    if received is None:
        compared = 0
        errors = 0
    else:
        actual = np.asarray(received, dtype=np.uint8).ravel()
        if actual.size and np.any(actual > 1):
            raise ValueError("принятые биты должны принимать значения 0 или 1")
        compared = min(expected.size, actual.size)
        errors = int(np.count_nonzero(expected[:compared] != actual[:compared]))

    missing = expected.size - compared
    ber_available = errors / compared if compared else None
    ber_effective = (errors + missing) / expected.size
    return errors, compared, missing, ber_available, ber_effective


def summarize(snr_db: float, trials: Sequence[TrialResult]) -> SnrSummary:
    """Собрать BER/PER и причины отказов по одной точке SNR."""
    if not trials:
        raise ValueError("нет испытаний для суммаризации")

    successes = sum(1 for trial in trials if trial.success)
    failures = len(trials) - successes
    bit_errors = sum(trial.bit_errors for trial in trials)
    bits_compared = sum(trial.compared_bits for trial in trials)
    bits_missing = sum(trial.missing_bits for trial in trials)
    total_reference_bits = sum(trial.frame_bits for trial in trials)
    if total_reference_bits <= 0:  # pragma: no cover - защита от пустого кадра
        raise ValueError("эталонные кадры не содержат бит")

    class_counts = {
        "synchronization": 0,
        "sync_word": 0,
        "crc": 0,
        "incomplete": 0,
        "version": 0,
        "length": 0,
        "payload_mismatch": 0,
        "wav_or_io": 0,
    }
    other_errors = 0
    for trial in trials:
        if trial.failure_class in class_counts:
            class_counts[trial.failure_class] += 1
        elif trial.failure_class is not None:
            other_errors += 1

    return SnrSummary(
        snr_db=float(snr_db),
        trials=len(trials),
        successes=successes,
        failures=failures,
        per=failures / len(trials),
        ber_available=bit_errors / bits_compared if bits_compared else None,
        ber_effective=(bit_errors + bits_missing) / total_reference_bits,
        bit_errors=bit_errors,
        bits_compared=bits_compared,
        bits_missing=bits_missing,
        total_reference_bits=total_reference_bits,
        demodulation_unavailable=sum(1 for trial in trials if not trial.demodulated),
        synchronization_errors=class_counts["synchronization"],
        sync_word_errors=class_counts["sync_word"],
        crc_errors=class_counts["crc"],
        incomplete_errors=class_counts["incomplete"],
        version_errors=class_counts["version"],
        length_errors=class_counts["length"],
        payload_mismatches=class_counts["payload_mismatch"],
        wav_errors=class_counts["wav_or_io"],
        other_errors=other_errors,
        clipped_samples=sum(trial.clipped_samples for trial in trials),
        active_signal_power_mean=float(
            np.mean([trial.active_signal_power for trial in trials])
        ),
        mean_realized_snr_db=float(np.mean([trial.realized_snr_db for trial in trials])),
    )


def run_benchmark(
    config: BenchmarkConfig,
    *,
    keep_wav: bool = False,
    workdir: str | Path | None = None,
    progress: Callable[[str], None] | None = None,
    command: str | None = None,
) -> BenchmarkResult:
    """Выполнить полный эксперимент, включая отдельную проверку M0-6.

    Если `workdir` не задан, используется временный каталог, который
    удаляется после завершения. WAV можно сохранить через ``keep_wav=True``.
    """
    if workdir is None:
        with tempfile.TemporaryDirectory(prefix="sn-benchmark-") as temp:
            return _run_in_directory(
                config,
                Path(temp),
                keep_wav=keep_wav,
                progress=progress,
                command=command,
            )
    directory = Path(workdir)
    directory.mkdir(parents=True, exist_ok=True)
    return _run_in_directory(
        config,
        directory,
        keep_wav=keep_wav,
        progress=progress,
        command=command,
    )


def _run_in_directory(
    config: BenchmarkConfig,
    directory: Path,
    *,
    keep_wav: bool,
    progress: Callable[[str], None] | None,
    command: str | None,
) -> BenchmarkResult:
    trials: list[TrialResult] = []
    summaries: list[SnrSummary] = []

    for snr_index, snr_db in enumerate(config.snr_db):
        point_trials: list[TrialResult] = []
        for trial_index in range(config.trials):
            point_trials.append(
                _run_trial(
                    config=config,
                    snr_db=snr_db,
                    snr_index=snr_index,
                    trial_index=trial_index,
                    payload_bytes=config.payload_bytes,
                    base_seed=config.seed,
                    directory=directory,
                    keep_wav=keep_wav,
                )
            )
        summary = summarize(snr_db, point_trials)
        summaries.append(summary)
        trials.extend(point_trials)
        if progress is not None:
            progress(
                f"SNR {snr_db:+g} дБ: {summary.successes}/{summary.trials} успешно, "
                f"PER={summary.per:.3f}, "
                f"BER_eff={summary.ber_effective:.3e}"
            )

    m0_trials: list[TrialResult] = []
    m0_summary: SnrSummary | None = None
    if config.m0_trials > 0:
        m0_base_seed = (
            config.m0_seed if config.m0_seed is not None else derive_seed(config.seed, 2)
        )
        for trial_index in range(config.m0_trials):
            m0_trials.append(
                _run_trial(
                    config=config,
                    snr_db=config.m0_snr_db,
                    snr_index=0,
                    trial_index=trial_index,
                    payload_bytes=M0_PAYLOAD_BYTES,
                    base_seed=m0_base_seed,
                    directory=directory,
                    keep_wav=keep_wav,
                    name_prefix="m0_6",
                )
            )
        m0_summary = summarize(config.m0_snr_db, m0_trials)
        if progress is not None:
            progress(
                f"M0-6 {config.m0_snr_db:+g} дБ, {M0_PAYLOAD_BYTES} байт: "
                f"{m0_summary.successes}/{m0_summary.trials} успешно, "
                f"PER={m0_summary.per:.3f}"
            )

    return BenchmarkResult(
        config=config,
        summaries=tuple(summaries),
        trials=tuple(trials),
        m0_summary=m0_summary,
        m0_trials=tuple(m0_trials),
        generated_at_utc=utc_now(),
        command=command,
        environment=environment(),
    )


def _run_trial(
    *,
    config: BenchmarkConfig,
    snr_db: float,
    snr_index: int,
    trial_index: int,
    payload_bytes: int,
    base_seed: int,
    directory: Path,
    keep_wav: bool,
    name_prefix: str | None = None,
) -> TrialResult:
    payload_seed = derive_seed(base_seed, 0, trial_index)
    noise_seed = derive_seed(base_seed, 1, snr_index, trial_index)

    payload_rng = np.random.default_rng(payload_seed)
    payload = payload_rng.integers(
        0, 256, size=payload_bytes, dtype=np.uint8
    ).tobytes()

    frame = build_frame(payload)
    modulation = modulate(frame.bits, config.profile, ramp_ms=config.ramp_ms)

    prefix = f"{name_prefix}_" if name_prefix else ""
    if keep_wav:
        stem = (
            f"{prefix}snr_{_snr_tag(snr_db)}_trial_{trial_index:04d}"
        )
        clean_path = directory / f"{stem}-clean.wav"
        noisy_path = directory / f"{stem}-noisy.wav"
    else:
        clean_path = directory / f"{prefix}clean.wav"
        noisy_path = directory / f"{prefix}noisy.wav"

    write_pcm16(clean_path, modulation.samples, config.profile.sample_rate)
    clean_sha256 = _sha256_file(clean_path)
    clean_samples, _ = read_pcm16(
        clean_path, expected_sample_rate=config.profile.sample_rate
    )

    region_start, region_end = active_region_bounds(clean_samples)
    signal_power = active_signal_power(clean_samples)
    noise_std_expected = math.sqrt(
        signal_power / (10.0 ** (snr_db / 10.0))
    )
    noisy = add_awgn(
        clean_samples,
        snr_db,
        seed=noise_seed,
        signal_power=signal_power,
    )
    noise = noisy - clean_samples
    noise_power = float(np.mean(noise**2))
    realized_snr_db = (
        10.0 * math.log10(signal_power / noise_power)
        if noise_power > 0.0
        else float("inf")
    )
    clipped_samples = int(np.count_nonzero(np.abs(noisy) > 1.0))

    write_pcm16(noisy_path, noisy, config.profile.sample_rate)
    noisy_sha256 = _sha256_file(noisy_path)

    decoded = None
    failure: str | None = None
    failure_class: str | None = None
    try:
        decoded = decode_wav(noisy_path, config.profile)
    except ReceiverError as exc:
        failure = str(exc)
        failure_class = _classify_failure(exc)
    except (WavFormatError, FileNotFoundError, OSError) as exc:
        failure = str(exc)
        failure_class = "wav_or_io"

    diagnostic = None
    try:
        diagnostic = demodulate_wav(noisy_path, config.profile)
    except (ReceiverError, WavFormatError, FileNotFoundError, OSError):
        # Диагностика до CRC тоже может быть невозможна; это фиксируется как
        # отсутствие доступных бит, а не как нулевой BER.
        diagnostic = None

    payload_match = decoded is not None and decoded.payload == payload
    success = bool(payload_match)
    if success:
        failure = None
        failure_class = None
    elif decoded is not None and failure is None:
        failure = (
            f"payload не совпал: получено {len(decoded.payload)} байт "
            f"вместо {len(payload)}"
        )
        failure_class = "payload_mismatch"

    errors, compared, missing, ber_available, ber_effective = compare_bits(
        frame.bits, diagnostic.bits if diagnostic is not None else None
    )

    return TrialResult(
        snr_db=float(snr_db),
        trial=trial_index,
        payload_seed=payload_seed,
        noise_seed=noise_seed,
        payload_bytes=payload_bytes,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        frame_bits=int(frame.bits.size),
        success=success,
        failure=failure,
        failure_class=failure_class,
        demodulated=diagnostic is not None and diagnostic.read_bits > 0,
        crc_ok=decoded is not None,
        payload_match=payload_match,
        available_bits=diagnostic.read_bits if diagnostic is not None else 0,
        compared_bits=compared,
        bit_errors=errors,
        missing_bits=missing,
        ber_available=ber_available,
        ber_effective=ber_effective,
        active_signal_power=signal_power,
        active_samples=region_end - region_start,
        noise_std_expected=noise_std_expected,
        noise_std_measured=float(np.std(noise)),
        realized_snr_db=realized_snr_db,
        clipped_samples=clipped_samples,
        clean_wav_sha256=clean_sha256,
        noisy_wav_sha256=noisy_sha256,
    )


def _classify_failure(exc: ReceiverError) -> str:
    mapping: tuple[tuple[type[ReceiverError], str], ...] = (
        (SignalNotFound, "synchronization"),
        (SyncWordError, "sync_word"),
        (FrameVersionError, "version"),
        (FrameLengthError, "length"),
        (IncompleteFrame, "incomplete"),
        (CrcError, "crc"),
    )
    for error_type, name in mapping:
        if isinstance(exc, error_type):
            return name
    return "other"


def write_summary_csv(path: str | Path, summaries: Sequence[SnrSummary]) -> Path:
    """Записать агрегаты по SNR в CSV с фиксированным порядком колонок."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "snr_db",
        "trials",
        "successes",
        "failures",
        "per",
        "ber_available",
        "ber_effective",
        "bit_errors",
        "bits_compared",
        "bits_missing",
        "total_reference_bits",
        "demodulation_unavailable",
        "demodulation_unavailable_fraction",
        "missing_bit_fraction",
        "synchronization_errors",
        "sync_word_errors",
        "crc_errors",
        "incomplete_errors",
        "version_errors",
        "length_errors",
        "payload_mismatches",
        "wav_errors",
        "other_errors",
        "clipped_samples",
        "active_signal_power_mean",
        "mean_realized_snr_db",
    ]
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for summary in summaries:
            writer.writerow(_csv_row(summary.as_dict(), fields))
    return target


def write_report(path: str | Path, result: BenchmarkResult) -> Path:
    """Записать JSON с параметрами, методикой, агрегатами и испытаниями."""
    return write_json(path, result.as_dict())


def write_ber_plot(path: str | Path, summaries: Sequence[SnrSummary]) -> Path:
    """Построить BER vs SNR: доступные биты и пропущенные как ошибки."""
    plt = _pyplot()
    ordered = sorted(summaries, key=lambda item: item.snr_db)
    snr = [item.snr_db for item in ordered]
    available = [
        item.ber_available if item.ber_available is not None else math.nan
        for item in ordered
    ]
    effective = [item.ber_effective for item in ordered]

    figure, axis = plt.subplots(figsize=(7.2, 4.6))
    axis.plot(
        snr,
        effective,
        marker="o",
        linewidth=1.8,
        label="BER, missing bits as errors",
    )
    axis.plot(
        snr,
        available,
        marker="s",
        linestyle="--",
        linewidth=1.4,
        label="BER, available bits only",
    )
    axis.set_yscale("symlog", linthresh=1e-4)
    axis.set_ylim(bottom=0.0)
    axis.set_xlabel("SNR, dB (active signal power / noise power)")
    axis.set_ylabel("BER")
    axis.set_title("BFSK file channel over AWGN: BER")
    axis.grid(True, which="both", alpha=0.3)
    axis.legend(fontsize=8)
    for item in ordered:
        if item.ber_available == 0.0:
            axis.annotate(
                "0 errors",
                (item.snr_db, 0.0),
                textcoords="offset points",
                xytext=(0, 6),
                ha="center",
                fontsize=7,
            )
    figure.tight_layout()
    return _save_figure(figure, path)


def write_per_plot(path: str | Path, summaries: Sequence[SnrSummary]) -> Path:
    """Построить PER vs SNR; все неудачные испытания входят в PER."""
    plt = _pyplot()
    ordered = sorted(summaries, key=lambda item: item.snr_db)
    snr = [item.snr_db for item in ordered]
    per = [item.per for item in ordered]

    figure, axis = plt.subplots(figsize=(7.2, 4.6))
    axis.plot(snr, per, marker="o", linewidth=1.8, color="#b03a2e")
    axis.set_ylim(-0.02, 1.02)
    axis.set_xlabel("SNR, dB (active signal power / noise power)")
    axis.set_ylabel("PER")
    axis.set_title("BFSK file channel over AWGN: PER")
    axis.grid(True, alpha=0.3)
    for item in ordered:
        axis.annotate(
            f"{item.failures}/{item.trials}",
            (item.snr_db, item.per),
            textcoords="offset points",
            xytext=(0, 7),
            ha="center",
            fontsize=7,
        )
    figure.tight_layout()
    return _save_figure(figure, path)


def write_m0_report(path: str | Path, result: BenchmarkResult) -> Path:
    """Сгенерировать Markdown-отчёт M0-6/SN-013 с фактическими числами."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    profile = result.config.profile
    lines: list[str] = [
        "# M0-6 / SN-013 — BER/PER на AWGN",
        "",
        f"- Сгенерировано (UTC): {result.generated_at_utc}",
        f"- Команда: `{result.command or 'benchmark.py'}`",
        f"- Профиль: `{profile.name}`, Fs={profile.sample_rate} Гц, "
        f"mark={profile.mark_hz:g} Гц, space={profile.space_hz:g} Гц, "
        f"{profile.symbol_rate:g} симв/с, {profile.samples_per_symbol} "
        f"отсчётов/символ",
        f"- Payload основного эксперимента: {result.config.payload_bytes} байт; "
        f"испытаний на точку: {result.config.trials}",
        f"- Seed: {result.config.seed}; ramp: {result.config.ramp_ms:g} мс",
        f"- Python: {result.environment.get('python')}; "
        f"NumPy: {result.environment.get('libraries', {}).get('numpy')}; "
        f"Matplotlib: {result.environment.get('libraries', {}).get('matplotlib')}",
        f"- Платформа: {result.environment.get('platform')}",
        "",
        "## Методика",
        "",
        f"- Канал: {METHODOLOGY['channel']}",
        f"- SNR: `{METHODOLOGY['snr_definition']}`",
        f"- Мощность сигнала: {METHODOLOGY['signal_power']}",
        f"- Мощность шума: {METHODOLOGY['noise_power']}",
        f"- Уровень: {METHODOLOGY['level_policy']}",
        f"- PER: {METHODOLOGY['per_definition']}",
        f"- BER по доступным битам: {METHODOLOGY['ber_available']}",
        f"- BER с пропущенными битами: {METHODOLOGY['ber_effective']}",
        f"- Seeds: {METHODOLOGY['seed_scheme']}",
        "",
        "## Результаты по SNR",
        "",
        "`BER_avail` — по доступным демодулированным битам; `BER_eff` — "
        "недоступные биты считаются ошибками. `n/a` означает, что биты не "
        "демодулированы, а не нулевой BER. `нет демод.` — число и доля "
        "испытаний без демодулированных бит; `пропущено бит` — доля бит "
        "эталонных кадров без принятой пары.",
        "",
        "| SNR, дБ | обязательная | испытаний | успешно | ошибок | PER | "
        "BER_avail | BER_eff | нет демод. | пропущено бит | потеря sync | "
        "sync word | CRC | неполный | версия | длина | payload | WAV/IO | "
        "прочее | clipped |",
        "|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for summary in sorted(result.summaries, key=lambda item: item.snr_db):
        mandatory = (
            summary.snr_db in REQUIRED_SNR_DB and summary.trials >= 100
        )
        lines.append(
            "| {snr:+g} | {mandatory} | {trials} | {successes} | {failures} | "
            "{per:.3f} | {ber_avail} | {ber_eff} | {no_demod} | {missing} | "
            "{sync} | {sync_word} | {crc} | {incomplete} | {version} | "
            "{length} | {payload} | {wav} | {other} | {clipped} |".format(
                snr=summary.snr_db,
                mandatory="да" if mandatory else "нет",
                trials=summary.trials,
                successes=summary.successes,
                failures=summary.failures,
                per=summary.per,
                ber_avail=_fmt_ber(summary.ber_available),
                ber_eff=_fmt_ber(summary.ber_effective),
                no_demod=(
                    f"{summary.demodulation_unavailable} "
                    f"({summary.demodulation_unavailable_fraction:.1%})"
                ),
                missing=f"{summary.missing_bit_fraction:.2%}",
                sync=summary.synchronization_errors,
                sync_word=summary.sync_word_errors,
                crc=summary.crc_errors,
                incomplete=summary.incomplete_errors,
                version=summary.version_errors,
                length=summary.length_errors,
                payload=summary.payload_mismatches,
                wav=summary.wav_errors,
                other=summary.other_errors,
                clipped=summary.clipped_samples,
            )
        )

    lines.extend(
        [
            "",
            "Обязательные точки M0/SN-013 — 0, 5, 10, 15 и 20 дБ. Если в "
            "команде были дополнительные (например, отрицательные) SNR, они "
            "показаны в таблице как характеризация и не заменяют обязательные.",
            "",
            "## M0-6 — 100 payload × 32 байта при 15 дБ",
            "",
        ]
    )

    if result.m0_summary is None:
        lines.extend(
            [
                "**M0-6 не выполнялась:** `--m0-trials 0`.",
                "",
            ]
        )
    else:
        m0 = result.m0_summary
        target_zero = m0.failures == 0
        lines.extend(
            [
                f"- Испытаний: {m0.trials}",
                f"- Успешно восстановлено payload: {m0.successes}",
                f"- Ошибок: {m0.failures}",
                f"- PER: {m0.per:.3f}",
                f"- BER_avail: {_fmt_ber(m0.ber_available)}",
                f"- BER_eff: {_fmt_ber(m0.ber_effective)}",
                f"- Потеря синхронизации: {m0.synchronization_errors}; "
                f"sync word: {m0.sync_word_errors}; CRC: {m0.crc_errors}; "
                f"неполный кадр: {m0.incomplete_errors}; "
                f"payload mismatch: {m0.payload_mismatches}; "
                f"прочее: {m0.other_errors}",
                f"- Клиппировано отсчётов: {m0.clipped_samples}",
                f"- Итог относительно цели 0 ошибок: "
                f"**{'PASS' if target_zero else 'FAIL'}** "
                f"({m0.failures} ошибок). Цель — гипотеза, а не повод менять "
                f"пороги приёмника.",
                "",
            ]
        )

    lines.extend(
        [
            "## Воспроизводимость",
            "",
            "Payload и шум получают seed из `SeedSequence` по схеме "
            "`[base_seed, role, snr_index, trial_index]`. Повторный запуск с "
            "теми же аргументами даёт те же payload, шум и агрегаты; это "
            "проверяется автоматическим тестом. SHA-256 clean/noisy WAV для "
            "каждого испытания записаны в JSON.",
            "",
            "## Что не проверено",
            "",
            "- Передача через динамик и микрофон (L2) не выполнялась.",
            "- Реальный фон помещения, реверберация, доплер и дрейф частоты не "
            "моделировались.",
            "- FEC, эквалайзер, оценка канала и адаптивный порог отсутствуют.",
            "- Другие профили, sample rate, платформы и аудиоустройства не "
            "проверялись этим прогоном.",
            "- CRC-16 не является аутентификацией и не заменяет AEAD.",
            "",
            "## Ограничения",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in LIMITATIONS)
    lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _pyplot():
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    return plt


def _save_figure(figure, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target, dpi=150, bbox_inches="tight")
    figure.clf()
    return target


def _csv_row(data: dict[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for field in fields:
        value = data[field]
        if value is None:
            row[field] = ""
        elif isinstance(value, float):
            row[field] = f"{value:.12g}"
        else:
            row[field] = value
    return row


def _round(value: float, digits: int = 9) -> float:
    if value is None:  # pragma: no cover - защита от ошибочного вызова
        return value
    if not math.isfinite(value):
        return value
    return float(round(value, digits))


def _round_optional(value: float | None, digits: int = 9) -> float | None:
    return None if value is None else _round(value, digits)


def _fmt_ber(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3e}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snr_tag(snr_db: float) -> str:
    tag = f"{snr_db:+.1f}"
    return tag.replace("+", "p").replace("-", "m").replace(".", "_")


__all__ = [
    "BenchmarkConfig",
    "BenchmarkError",
    "BenchmarkResult",
    "METHODOLOGY",
    "REQUIRED_SNR_DB",
    "SnrSummary",
    "TrialResult",
    "compare_bits",
    "derive_seed",
    "run_benchmark",
    "summarize",
    "write_ber_plot",
    "write_m0_report",
    "write_per_plot",
    "write_report",
    "write_summary_csv",
]
