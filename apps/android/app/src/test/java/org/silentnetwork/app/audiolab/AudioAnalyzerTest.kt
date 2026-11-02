package org.silentnetwork.app.audiolab

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.audiolab.dsp.AudioAnalyzer
import org.silentnetwork.app.audiolab.dsp.SignalGenerator
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Unit tests for DSP analysis: RMS, Peak, Clipping, DC offset,
 * Goertzel filter and FFT spectral peak detection.
 */
class AudioAnalyzerTest {

    @Test
    fun testRmsAndPeakOnKnownSineWave() {
        val sampleRate = 48000
        val amplitude = 0.50
        // Sine wave without lead-in pause or ramp for pure math verification
        val durationSec = 0.2
        val n = (durationSec * sampleRate).toInt()
        val samples = FloatArray(n)
        val omega = 2.0 * Math.PI * 1000.0 / sampleRate

        for (i in 0 until n) {
            samples[i] = (amplitude * kotlin.math.sin(omega * i)).toFloat()
        }

        val result = AudioAnalyzer.analyze(samples, sampleRate, 1)

        // For pure sine of amplitude A, Peak is A, RMS is A / sqrt(2)
        val expectedRms = amplitude / sqrt(2.0)
        assertEquals(amplitude, result.peak, 0.01)
        assertEquals(expectedRms, result.rms, 0.01)

        // Peak dBFS for 0.5 is 20 * log10(0.5) = -6.02 dBFS
        assertEquals(-6.02, result.peakDbfs, 0.2)
        // RMS dBFS for 0.5/sqrt(2) is -9.03 dBFS
        assertEquals(-9.03, result.rmsDbfs, 0.2)
    }

    @Test
    fun testClippingDetection() {
        // Normal signal: no clipping
        val normalSamples = floatArrayOf(0.1f, 0.5f, -0.6f, 0.8f)
        val normalResult = AudioAnalyzer.analyze(normalSamples, 48000, 1)
        assertFalse(normalResult.hasClipping)
        assertEquals(0, normalResult.clippingCount)

        // Clipped signal: hits 1.0f
        val clippedSamples = floatArrayOf(0.1f, 1.0f, -1.0f, 0.5f)
        val clippedResult = AudioAnalyzer.analyze(clippedSamples, 48000, 1)
        assertTrue(clippedResult.hasClipping)
        assertEquals(2, clippedResult.clippingCount)
    }

    @Test
    fun testDcOffsetDetection() {
        val dcValue = 0.05f
        val samples = FloatArray(1000) { dcValue }
        val result = AudioAnalyzer.analyze(samples, 48000, 1)

        assertEquals(dcValue.toDouble(), result.dcOffset, 0.0001)
    }

    @Test
    fun testFrequencyDetection1000Hz() {
        val sampleRate = 48000
        val targetFreq = 1000.0
        val samples = SignalGenerator.generateSineWave(
            frequencyHz = targetFreq,
            durationSeconds = 0.5,
            sampleRate = sampleRate,
            amplitude = 0.3
        )

        val result = AudioAnalyzer.analyze(samples, sampleRate, 1)
        // FFT bin resolution for 4096 bins at 48kHz is ~11.7 Hz
        assertEquals(targetFreq, result.dominantFrequencyHz, 25.0)

        // Check Goertzel power
        val powerAt1000 = AudioAnalyzer.computeGoertzelPower(samples, sampleRate, 1000.0)
        val powerAt5000 = AudioAnalyzer.computeGoertzelPower(samples, sampleRate, 5000.0)
        assertTrue("Power at 1000Hz must be substantially higher than at 5000Hz", powerAt1000 > powerAt5000 * 100.0)
    }

    @Test
    fun testFrequencyDetection18500HzM0Center() {
        val sampleRate = 48000
        val targetFreq = 18500.0
        val samples = SignalGenerator.generateSineWave(
            frequencyHz = targetFreq,
            durationSeconds = 0.5,
            sampleRate = sampleRate,
            amplitude = 0.25
        )

        val result = AudioAnalyzer.analyze(samples, sampleRate, 1, targetToneFrequencyHz = targetFreq)
        assertEquals(targetFreq, result.dominantFrequencyHz, 30.0)

        // 18-19 kHz power fraction should be high (> 0.7)
        assertTrue("Band 18-19kHz should contain majority of power", result.band18to19kHzPowerFraction > 0.70)
    }

    @Test
    fun testSnrDisclaimerPresence() {
        val samples = floatArrayOf(0.1f, 0.2f, 0.3f)
        val result = AudioAnalyzer.analyze(samples, 48000, 1)

        assertTrue(result.snrDisclaimer.contains("SNR не рассчитывается"))
    }
}
