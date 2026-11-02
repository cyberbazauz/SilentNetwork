package org.silentnetwork.app.audiolab.dsp

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * Deterministic audio signal generator for acoustic calibration and tone testing.
 * Produces PCM 16-bit mono little-endian audio buffers.
 * Matches conventions used in SilentNetwork Acoustic Lab (snlab/wavio.py).
 */
object SignalGenerator {

    const val DEFAULT_SAMPLE_RATE = 48000
    const val DEFAULT_AMPLITUDE = 0.20 // -14 dBFS safe default to protect ears and hardware
    const val MAX_SAFE_AMPLITUDE = 0.80

    /**
     * Generates a sinusoidal tone with smooth cosine ramp onset/offset to prevent
     * acoustic clicking transients that could trigger non-linear speaker distortion.
     *
     * @param frequencyHz Frequency of the sine wave in Hertz (e.g. 18500.0)
     * @param durationSeconds Duration of the active tone in seconds
     * @param sampleRate Sampling frequency in Hz (typically 48000 or 44100)
     * @param amplitude Peak amplitude in range [0.0, 1.0]
     * @param leadInPauseSeconds Silence duration before the tone
     * @param leadOutPauseSeconds Silence duration after the tone
     * @param rampDurationSeconds Duration of the smooth cosine onset/offset ramp (default 5ms)
     * @return Normalized floating-point sample array in range [-1.0, 1.0]
     */
    fun generateSineWave(
        frequencyHz: Double,
        durationSeconds: Double,
        sampleRate: Int = DEFAULT_SAMPLE_RATE,
        amplitude: Double = DEFAULT_AMPLITUDE,
        leadInPauseSeconds: Double = 0.05,
        leadOutPauseSeconds: Double = 0.05,
        rampDurationSeconds: Double = 0.005
    ): FloatArray {
        require(frequencyHz > 0.0) { "Frequency must be positive" }
        require(frequencyHz < sampleRate / 2.0) { "Frequency must be below Nyquist (${sampleRate / 2} Hz)" }
        require(durationSeconds > 0.0) { "Duration must be positive" }
        require(sampleRate > 0) { "Sample rate must be positive" }
        require(amplitude in 0.0..1.0) { "Amplitude must be within [0.0, 1.0]" }

        val leadInSamples = (leadInPauseSeconds * sampleRate).roundToInt().coerceAtLeast(0)
        val toneSamples = (durationSeconds * sampleRate).roundToInt().coerceAtLeast(1)
        val leadOutSamples = (leadOutPauseSeconds * sampleRate).roundToInt().coerceAtLeast(0)
        val totalSamples = leadInSamples + toneSamples + leadOutSamples

        val result = FloatArray(totalSamples)
        val rampSamples = (rampDurationSeconds * sampleRate).roundToInt().coerceAtLeast(1).coerceAtMost(toneSamples / 2)

        val omega = 2.0 * PI * frequencyHz / sampleRate

        for (i in 0 until toneSamples) {
            // Apply raised-cosine (Hann/Tukey-style) taper to onset and offset
            val envelope = when {
                i < rampSamples -> 0.5 * (1.0 - cos(PI * i / rampSamples))
                i >= toneSamples - rampSamples -> {
                    val remaining = toneSamples - 1 - i
                    0.5 * (1.0 - cos(PI * remaining / rampSamples))
                }
                else -> 1.0
            }

            val sampleVal = (amplitude * envelope * sin(omega * i)).coerceIn(-1.0, 1.0)
            result[leadInSamples + i] = sampleVal.toFloat()
        }

        return result
    }

    /**
     * Converts normalized floating-point samples [-1.0, 1.0] into signed 16-bit
     * PCM little-endian byte array, matching snlab/wavio.py:to_pcm16_bytes.
     *
     * Value mapping: round(clip(x, -1.0, 1.0) * 32767.0)
     */
    fun toPcm16LittleEndian(samples: FloatArray): ByteArray {
        val bytes = ByteArray(samples.size * 2)
        for (i in samples.indices) {
            val clamped = samples[i].coerceIn(-1.0f, 1.0f)
            val pcm16 = (clamped * 32767.0f).roundToInt().coerceIn(-32768, 32767)
            bytes[i * 2] = (pcm16 and 0xFF).toByte()
            bytes[i * 2 + 1] = ((pcm16 shr 8) and 0xFF).toByte()
        }
        return bytes
    }

    /**
     * Converts signed 16-bit PCM little-endian byte array into normalized float array [-1.0, 1.0].
     */
    fun fromPcm16LittleEndian(pcmBytes: ByteArray): FloatArray {
        val sampleCount = pcmBytes.size / 2
        val samples = FloatArray(sampleCount)
        for (i in 0 until sampleCount) {
            val b0 = pcmBytes[i * 2].toInt() and 0xFF
            val b1 = pcmBytes[i * 2 + 1].toInt()
            val shortVal = (b1 shl 8) or b0
            samples[i] = (shortVal / 32767.0f).coerceIn(-1.0f, 1.0f)
        }
        return samples
    }

    /**
     * Convenience method to generate PCM16 byte array directly.
     */
    fun generateTestTonePcm16(
        frequencyHz: Double,
        durationSeconds: Double,
        sampleRate: Int = DEFAULT_SAMPLE_RATE,
        amplitude: Double = DEFAULT_AMPLITUDE
    ): ByteArray {
        val floatSamples = generateSineWave(
            frequencyHz = frequencyHz,
            durationSeconds = durationSeconds,
            sampleRate = sampleRate,
            amplitude = amplitude
        )
        return toPcm16LittleEndian(floatSamples)
    }
}
