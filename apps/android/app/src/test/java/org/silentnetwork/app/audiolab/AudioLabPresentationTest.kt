package org.silentnetwork.app.audiolab

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.audiolab.presentation.AudioLabState

/**
 * Unit tests for presentation logic, parameter validation, and formatting.
 */
class AudioLabPresentationTest {

    @Test
    fun testExperimentalM0BandClassification() {
        assertTrue(AudioLabState.isExperimentalM0Band(18000.0))
        assertTrue(AudioLabState.isExperimentalM0Band(18500.0))
        assertTrue(AudioLabState.isExperimentalM0Band(19000.0))

        assertFalse(AudioLabState.isExperimentalM0Band(1000.0))
        assertFalse(AudioLabState.isExperimentalM0Band(17999.0))
        assertFalse(AudioLabState.isExperimentalM0Band(19001.0))
        assertFalse(AudioLabState.isExperimentalM0Band(22000.0))
    }

    @Test
    fun testAudibleVsUltrasonicClassification() {
        assertTrue(AudioLabState.isAudibleRange(1000.0))
        assertTrue(AudioLabState.isAudibleRange(15000.0))

        assertFalse(AudioLabState.isAudibleRange(18000.0))
        assertFalse(AudioLabState.isAudibleRange(18500.0))
        assertFalse(AudioLabState.isAudibleRange(20000.0))
    }

    @Test
    fun testTransmitterParameterValidation() {
        // Valid
        val valid = AudioLabState.validateTransmitterParameters(
            frequencyHz = 18500.0,
            amplitude = 0.20,
            durationSec = 2.0,
            sampleRate = 48000
        )
        assertTrue(valid.isSuccess)

        // Invalid: Frequency >= Nyquist (e.g. 25000Hz at 48000Hz)
        val nyquistFail = AudioLabState.validateTransmitterParameters(
            frequencyHz = 25000.0,
            amplitude = 0.20,
            durationSec = 2.0,
            sampleRate = 48000
        )
        assertTrue(nyquistFail.isFailure)

        // Invalid: Excessive amplitude > 0.80
        val ampFail = AudioLabState.validateTransmitterParameters(
            frequencyHz = 18500.0,
            amplitude = 0.95,
            durationSec = 2.0,
            sampleRate = 48000
        )
        assertTrue(ampFail.isFailure)

        // Invalid: Negative frequency
        val freqFail = AudioLabState.validateTransmitterParameters(
            frequencyHz = -100.0,
            amplitude = 0.20,
            durationSec = 2.0,
            sampleRate = 48000
        )
        assertTrue(freqFail.isFailure)
    }

    @Test
    fun testFormattingHelpers() {
        assertEquals("Клиппинг: ОТСУТСТВУЕТ (0 отсчётов)", AudioLabState.formatClippingStatus(0))
        assertEquals("ВНИМАНИЕ: КЛИППИНГ (15 отсчётов перегрузки!)", AudioLabState.formatClippingStatus(15))

        assertEquals("Пик: -12.5 dBFS", AudioLabState.formatPeakStatus(-12.5))
        assertEquals("RMS: -18.2 dBFS", AudioLabState.formatRmsStatus(-18.2))

        assertEquals("1000 Гц", AudioLabState.formatFrequencyLabel(1000.0))
        assertEquals("18 кГц", AudioLabState.formatFrequencyLabel(18000.0))
        assertEquals("18.5 кГц", AudioLabState.formatFrequencyLabel(18500.0))
        assertEquals("19 кГц", AudioLabState.formatFrequencyLabel(19000.0))
    }
}
