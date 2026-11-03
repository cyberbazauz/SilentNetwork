package org.silentnetwork.app.audiolab.datalink

import kotlin.math.PI
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

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
        validateBits(bits)
        val rampSamples = validateRampMs(rampMs, profile.sampleRate)
        require(phase0.isFinite()) { "phase0 должен быть конечным" }

        val samplesPerSymbol = profile.samplesPerSymbol
        val totalSamples = bits.size * samplesPerSymbol
        val samples = DoubleArray(totalSamples)

        // Continuous phase: sample 0 has phase0, then each sample advances by
        // the step of the bit it belongs to (matches snlab/bfsk.py).
        var phase = phase0
        var cursor = 0
        for (bit in bits) {
            val frequency = if (bit.toInt() == 1) profile.markHz else profile.spaceHz
            val step = TWO_PI * frequency / profile.sampleRate
            for (offset in 0 until samplesPerSymbol) {
                samples[cursor++] = profile.amplitude * sin(phase)
                phase += step
            }
        }

        applyRampInPlace(samples, rampSamples)
        return FloatArray(totalSamples) { samples[it].toFloat() }
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

    private fun validateBits(bits: ByteArray) {
        if (bits.isEmpty()) throw ModulationError("пустая последовательность бит")
        for (bit in bits) {
            if (bit.toInt() != 0 && bit.toInt() != 1) {
                throw ModulationError("биты должны принимать значения 0 или 1")
            }
        }
    }

    /** Sign of a sample with the same convention as numpy.sign. */
    internal fun sign(value: Double): Double = when {
        value > 0.0 -> 1.0
        value < 0.0 -> -1.0
        else -> 0.0
    }
}
