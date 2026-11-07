package org.silentnetwork.app.audiolab.datalink

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Continuous-phase BFSK modulator matching `snlab/bfsk.py` in layout (not
 * guaranteed bit-identical PCM across platforms because `sin`/libm may differ
 * in the last place).
 *
 * One symbol is one bit: 1 -> mark frequency, 0 -> space frequency. The phase is
 * never reset at a symbol boundary. A short linear ramp suppresses clicks.
 */
object BfskModulator {
    const val DEFAULT_RAMP_MS = 5.0
    const val MAX_RAMP_MS = 500.0
    private const val TWO_PI = 2.0 * PI

    class ModulationError(message: String) : IllegalArgumentException(message)

    /** Convert 0/1 bits into normalized samples in [-1, 1]. */
    fun bitsToSamples(
        bits: ByteArray,
        profile: BfskProfile,
        rampMs: Double = DEFAULT_RAMP_MS,
        phase0: Double = 0.0
    ): FloatArray {
        if (bits.isEmpty()) throw ModulationError("пустая последовательность бит")
        val rampSamples = validateRampMs(rampMs, profile.sampleRate)
        require(phase0.isFinite()) { "phase0 должен быть конечным" }

        val samplesPerSymbol = profile.samplesPerSymbol
        val totalSamples = bits.size * samplesPerSymbol
        val samples = FloatArray(totalSamples)

        // Continuous phase: sample 0 has phase0, then each sample advances by
        // the step of the bit it belongs to (matches snlab/bfsk.py). The
        // oscillator is advanced by multiplying a unit phasor with a cached
        // per-frequency step instead of calling sin() for every sample: a
        // 64-byte message has ~96k samples, and on a phone per-sample libm
        // calls dominate the time between pressing "send" and hearing sound.
        val markStep = TWO_PI * profile.markHz / profile.sampleRate
        val spaceStep = TWO_PI * profile.spaceHz / profile.sampleRate
        val markCos = cos(markStep)
        val markSin = sin(markStep)
        val spaceCos = cos(spaceStep)
        val spaceSin = sin(spaceStep)

        val phase = phase0 % TWO_PI
        var phaseSin = sin(phase)
        var phaseCos = cos(phase)
        var cursor = 0
        for (bit in bits) {
            val value = bit.toInt()
            if (value != 0 && value != 1) {
                throw ModulationError("биты должны принимать значения 0 или 1")
            }
            val stepCos = if (value == 1) markCos else spaceCos
            val stepSin = if (value == 1) markSin else spaceSin
            for (offset in 0 until samplesPerSymbol) {
                samples[cursor++] = (profile.amplitude * phaseSin).toFloat()
                val nextSin = phaseSin * stepCos + phaseCos * stepSin
                val nextCos = phaseCos * stepCos - phaseSin * stepSin
                phaseSin = nextSin
                phaseCos = nextCos
            }
            // Multiplication drift over one symbol is tiny, but renormalising
            // keeps a long file's phase from slowly losing amplitude.
            val magnitude = sqrt(phaseSin * phaseSin + phaseCos * phaseCos)
            if (magnitude > 0.0) {
                phaseSin /= magnitude
                phaseCos /= magnitude
            }
        }

        applyRampInPlace(samples, rampSamples)
        return samples
    }

    /** Linear fade-in/fade-out in place; the sample count is unchanged. */
    fun applyRampInPlace(samples: FloatArray, rampSamples: Int) {
        require(rampSamples >= 0) { "rampSamples не может быть отрицательным" }
        if (samples.isEmpty()) throw ModulationError("нельзя обработать пустой сигнал")
        val effective = effectiveRamp(rampSamples, samples.size)
        if (effective == 0) return
        for (index in 0 until effective) {
            samples[index] *= index.toFloat() / effective
            samples[samples.size - effective + index] *=
                (effective - 1 - index).toFloat() / effective
        }
    }

    /** Linear fade-in/fade-out in place; the sample count is unchanged. */
    fun applyRampInPlace(samples: DoubleArray, rampSamples: Int) {
        require(rampSamples >= 0) { "rampSamples не может быть отрицательным" }
        if (samples.isEmpty()) throw ModulationError("нельзя обработать пустой сигнал")
        val effective = effectiveRamp(rampSamples, samples.size)
        if (effective == 0) return
        for (index in 0 until effective) {
            samples[index] *= index.toDouble() / effective
            samples[samples.size - effective + index] *=
                (effective - 1 - index).toDouble() / effective
        }
    }

    fun effectiveRamp(rampSamples: Int, total: Int): Int {
        if (rampSamples == 0 || total < 3) return 0
        return min(rampSamples, total / 2)
    }

    fun validateRampMs(rampMs: Double, sampleRate: Int): Int {
        require(rampMs.isFinite()) { "ramp_ms должен быть конечным" }
        require(rampMs in 0.0..MAX_RAMP_MS) { "ramp_ms вне диапазона 0..$MAX_RAMP_MS" }
        return (rampMs / 1000.0 * sampleRate).roundToInt()
    }

    /** Sign of a sample with the same convention as numpy.sign. */
    internal fun sign(value: Double): Double = when {
        value > 0.0 -> 1.0
        value < 0.0 -> -1.0
        else -> 0.0
    }

    /** Тот же знак для Float-метрики демодулятора. */
    internal fun sign(value: Float): Double = sign(value.toDouble())
}
