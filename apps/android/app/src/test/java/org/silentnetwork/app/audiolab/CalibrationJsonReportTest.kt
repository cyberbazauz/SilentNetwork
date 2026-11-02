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
 * Verifies the shared SN-015 contract, backwards-compatible fields and strict
 * privacy guarantees.
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
            effectsStateDescription = "AEC: Доступен; AGC: Нет; NS: Нет",
            requestedDevice = "builtin_mic",
            actualRoutedDevice = "builtin_mic (Pixel bottom mic)"
        )

        val tp = CalibrationJsonReport.TestParameters(
            testType = "RECEIVER_CAPTURE",
            transmitterFrequencyHz = null,
            transmitterAmplitude = null,
            transmitterDurationSeconds = null,
            targetFrequencyHz = 18500.0
        )

        val dummySamples = floatArrayOf(0.1f, -0.1f, 0.2f, -0.2f)
        val analysis = AudioAnalyzer.analyze(dummySamples, 48000, 1, targetToneFrequencyHz = 18500.0)

        val wavInfo = CalibrationJsonReport.WavInfo(
            filename = "sn015_sn015-20261102t120000z-deadbeef_rx.wav",
            sha256 = "a".repeat(64),
            sampleCount = 4,
            sampleRateHz = 48000,
            durationSeconds = 4.0 / 48000.0
        )

        val result = CalibrationJsonReport.ResultMetadata(
            testId = "SN015-20261102T120000Z-DEADBEEF",
            timestampUtc = "2026-11-02T12:00:00Z"
        )

        val jsonString = CalibrationJsonReport.buildReport(
            device = dev,
            hardware = hw,
            testParams = tp,
            analysis = analysis,
            result = result,
            wavInfo = wavInfo,
            expectedFrequencyHz = 18500.0,
            detectedFrequencyHz = null,
            warnings = listOf("Тестовое предупреждение")
        )

        val json = JSONObject(jsonString)

        // Shared SN-015 contract (required top-level fields).
        assertEquals(CalibrationJsonReport.SCHEMA_VERSION, json.getString("schema_version"))
        assertEquals("silentnetwork.sn015.result/1.0", json.getString("schema_version"))
        assertEquals("SN015-20261102T120000Z-DEADBEEF", json.getString("test_id"))
        assertEquals("2026-11-02T12:00:00Z", json.getString("timestamp_utc"))
        assertEquals("android_receiver", json.getString("device_role"))
        assertEquals(18500.0, json.getDouble("expected_frequency_hz"), 0.1)
        assertEquals(48000, json.getInt("actual_sample_rate_hz"))
        assertTrue(json.has("duration_seconds"))
        assertTrue(json.has("rms_dbfs"))
        assertTrue(json.has("peak_dbfs"))
        assertTrue(json.has("dominant_frequency_hz"))
        assertEquals(0, json.getInt("clipping_count"))
        assertEquals(wavInfo.filename, json.getString("wav_filename"))
        assertEquals(wavInfo.sha256, json.getString("wav_sha256"))
        assertTrue(json.getJSONArray("warnings").length() == 1)

        // Backwards-compatible report identity.
        assertEquals(CalibrationJsonReport.SCHEMA_VERSION, json.getString("report_schema_version"))
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
        assertEquals("builtin_mic", hwObj.getString("requested_device"))
        assertEquals("builtin_mic (Pixel bottom mic)", hwObj.getString("actual_routed_device"))

        val fxObj = hwObj.getJSONObject("hardware_audio_effects")
        assertTrue(fxObj.getBoolean("acoustic_echo_canceler_available"))
        assertFalse(fxObj.getBoolean("automatic_gain_control_available"))

        // Test parameters
        val tpObj = json.getJSONObject("test_parameters")
        assertEquals("RECEIVER_CAPTURE", tpObj.getString("test_type"))
        assertEquals(18500.0, tpObj.getDouble("target_frequency_hz"), 0.1)

        // Analysis
        val anObj = json.getJSONObject("analysis")
        assertEquals(4, anObj.getInt("sample_count"))
        assertTrue(anObj.has("rms_dbfs"))
        assertTrue(anObj.has("peak_dbfs"))
        assertTrue(anObj.has("clipping_count"))
        assertTrue(anObj.has("snr_disclaimer"))

        // WAV provenance
        val wavObj = json.getJSONObject("wav")
        assertEquals(wavInfo.filename, wavObj.getString("filename"))
        assertEquals(wavInfo.sha256, wavObj.getString("sha256"))
        assertEquals(48000, wavObj.getInt("sample_rate_hz"))

        // Privacy verification: absolutely no personal / location keys
        assertFalse(json.has("latitude"))
        assertFalse(json.has("longitude"))
        assertFalse(json.has("imei"))
        assertFalse(json.has("mac_address"))
        assertFalse(json.has("user"))
        assertFalse(json.has("phone_number"))
        assertFalse(json.has("serial"))
        assertFalse(json.has("android_id"))
    }

    @Test
    fun testMissingWavAndFrequencyFieldsAreNullNotEmptyStrings() {
        val json = JSONObject(
            CalibrationJsonReport.buildReport(
                device = CalibrationJsonReport.DeviceInfo("Test", "Device", "14", 34),
                hardware = CalibrationJsonReport.AudioHardwareState(
                    requestedSampleRate = 44100,
                    actualSampleRate = 48000,
                    channelCount = 1,
                    audioFormat = "PCM 16-bit mono",
                    audioSource = "MIC",
                    bufferSizeBytes = 4096,
                    aecAvailable = false,
                    agcAvailable = false,
                    nsAvailable = false,
                    effectsStateDescription = "none"
                ),
                testParams = CalibrationJsonReport.TestParameters(
                    "WAV_ANALYSIS", null, null, null, null
                ),
                analysis = AudioAnalyzer.analyze(FloatArray(0), 48000, 1),
                result = CalibrationJsonReport.ResultMetadata(
                    testId = "SN015-20261102T120000Z-00000000",
                    timestampUtc = "2026-11-02T12:00:00Z"
                )
            )
        )

        assertTrue(json.isNull("expected_frequency_hz"))
        assertTrue(json.isNull("detected_frequency_hz"))
        assertTrue(json.isNull("wav_filename"))
        assertTrue(json.isNull("wav_sha256"))
        assertEquals(0, json.getJSONArray("warnings").length())
    }
}
