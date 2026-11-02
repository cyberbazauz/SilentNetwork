package org.silentnetwork.app.audiolab

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.audiolab.dsp.AudioAnalyzer
import org.silentnetwork.app.audiolab.reporting.CalibrationJsonReport

/**
 * Unit tests for CalibrationJsonReport generator.
 * Verifies structure, required schema fields, and strict privacy guarantees.
 */
class CalibrationJsonReportTest {

    @Test
    fun testJsonReportStructureAndRequiredFields() {
        val dev = CalibrationJsonReport.DeviceInfo(
            manufacturer = "Google",
            model = "Pixel 7 Pro",
            androidRelease = "14",
            sdkInt = 34
        )

        val hw = CalibrationJsonReport.AudioHardwareState(
            requestedSampleRate = 48000,
            actualSampleRate = 48000,
            channelCount = 1,
            audioFormat = "PCM 16-bit mono",
            audioSource = "UNPROCESSED",
            bufferSizeBytes = 96000,
            aecAvailable = true,
            agcAvailable = false,
            nsAvailable = false,
            effectsStateDescription = "AEC: Доступен; AGC: Нет; NS: Нет"
        )

        val tp = CalibrationJsonReport.TestParameters(
            testType = "TRANSMITTER_SELF_TEST",
            transmitterFrequencyHz = 18500.0,
            transmitterAmplitude = 0.20,
            transmitterDurationSeconds = 2.0,
            targetFrequencyHz = 18500.0
        )

        val dummySamples = floatArrayOf(0.1f, -0.1f, 0.2f, -0.2f)
        val analysis = AudioAnalyzer.analyze(dummySamples, 48000, 1)

        val jsonString = CalibrationJsonReport.buildReport(
            device = dev,
            hardware = hw,
            testParams = tp,
            analysis = analysis,
            warnings = listOf("Тестовое предупреждение")
        )

        val json = JSONObject(jsonString)

        assertEquals("1.0.0", json.getString("report_schema_version"))
        assertEquals("SilentNetwork", json.getString("project"))
        assertNotNull(json.getString("timestamp_utc"))

        // Device
        val devObj = json.getJSONObject("device")
        assertEquals("Google", devObj.getString("manufacturer"))
        assertEquals("Pixel 7 Pro", devObj.getString("model"))
        assertEquals("14", devObj.getString("android_release"))
        assertEquals(34, devObj.getInt("sdk_int"))

        // Hardware
        val hwObj = json.getJSONObject("hardware")
        assertEquals(48000, hwObj.getInt("requested_sample_rate_hz"))
        assertEquals("UNPROCESSED", hwObj.getString("audio_source"))

        val fxObj = hwObj.getJSONObject("hardware_audio_effects")
        assertTrue(fxObj.getBoolean("acoustic_echo_canceler_available"))
        assertFalse(fxObj.getBoolean("automatic_gain_control_available"))

        // Test parameters
        val tpObj = json.getJSONObject("test_parameters")
        assertEquals("TRANSMITTER_SELF_TEST", tpObj.getString("test_type"))
        assertEquals(18500.0, tpObj.getDouble("transmitter_frequency_hz"), 0.1)

        // Analysis
        val anObj = json.getJSONObject("analysis")
        assertEquals(4, anObj.getInt("sample_count"))
        assertTrue(anObj.has("rms_dbfs"))
        assertTrue(anObj.has("peak_dbfs"))
        assertTrue(anObj.has("clipping_count"))
        assertTrue(anObj.has("snr_disclaimer"))

        // Privacy verification: absolutely no personal / location keys
        assertFalse(json.has("latitude"))
        assertFalse(json.has("longitude"))
        assertFalse(json.has("imei"))
        assertFalse(json.has("mac_address"))
        assertFalse(json.has("user"))
        assertFalse(json.has("phone_number"))
    }
}
