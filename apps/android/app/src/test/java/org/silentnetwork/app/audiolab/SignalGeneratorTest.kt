package org.silentnetwork.app.audiolab

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.audiolab.dsp.SignalGenerator
import kotlin.math.abs

/**
 * Unit tests for deterministic signal generation, PCM16 conversion,
 * and amplitude bounds.
 */
class SignalGeneratorTest {

    @Test
    fun testSineGenerationSampleCountAndDuration() {
        val sampleRate = 48000
        val durationSec = 1.0
        val leadInSec = 0.05
        val leadOutSec = 0.05

        val samples = SignalGenerator.generateSineWave(
            frequencyHz = 18500.0,
            durationSeconds = durationSec,
            sampleRate = sampleRate,
            amplitude = 0.20,
            leadInPauseSeconds = leadInSec,
            leadOutPauseSeconds = leadOutSec
        )

        val expectedSamples = ((durationSec + leadInSec + leadOutSec) * sampleRate).toInt()
        assertEquals(expectedSamples, samples.size)
    }

    @Test
    fun testSineWaveAmplitudeBounds() {
        val samples = SignalGenerator.generateSineWave(
            frequencyHz = 18000.0,
            durationSeconds = 0.5,
            sampleRate = 48000,
            amplitude = 0.30
        )

        for (s in samples) {
            assertTrue("Sample must be in [-0.31, 0.31]: $s", abs(s) <= 0.31f)
        }
    }

    @Test
    fun testPcm16LittleEndianRoundtrip() {
        val originalFloats = floatArrayOf(-1.0f, -0.5f, 0.0f, 0.5f, 1.0f)
        val pcmBytes = SignalGenerator.toPcm16LittleEndian(originalFloats)

        assertEquals(10, pcmBytes.size) // 5 samples * 2 bytes

        val decodedFloats = SignalGenerator.fromPcm16LittleEndian(pcmBytes)
        assertEquals(originalFloats.size, decodedFloats.size)

        for (i in originalFloats.indices) {
            assertEquals("Sample $i mismatch", originalFloats[i], decodedFloats[i], 0.001f)
        }
    }

    @Test
    fun testPcm16ExtremeValuesClamped() {
        val extremeFloats = floatArrayOf(-2.5f, 3.8f)
        val pcmBytes = SignalGenerator.toPcm16LittleEndian(extremeFloats)
        val decoded = SignalGenerator.fromPcm16LittleEndian(pcmBytes)

        assertEquals(-1.0f, decoded[0], 0.001f)
        assertEquals(1.0f, decoded[1], 0.001f)
    }

    @Test
    fun testLeadInAndLeadOutSilence() {
        val sampleRate = 48000
        val leadInSec = 0.05
        val leadOutSec = 0.05

        val samples = SignalGenerator.generateSineWave(
            frequencyHz = 19000.0,
            durationSeconds = 0.2,
            sampleRate = sampleRate,
            amplitude = 0.2,
            leadInPauseSeconds = leadInSec,
            leadOutPauseSeconds = leadOutSec
        )

        val leadInCount = (leadInSec * sampleRate).toInt()
        for (i in 0 until leadInCount) {
            assertEquals("Lead-in sample $i must be zero", 0.0f, samples[i], 0.0f)
        }

        val leadOutStart = samples.size - (leadOutSec * sampleRate).toInt()
        for (i in leadOutStart until samples.size) {
            assertEquals("Lead-out sample $i must be zero", 0.0f, samples[i], 0.0f)
        }
    }
}
