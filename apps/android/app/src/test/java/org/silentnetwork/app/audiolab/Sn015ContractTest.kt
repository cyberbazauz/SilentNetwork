package org.silentnetwork.app.audiolab

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.audiolab.dsp.AudioAnalyzer
import org.silentnetwork.app.audiolab.presentation.AudioLabState
import org.silentnetwork.app.audiolab.reporting.CalibrationJsonReport

class Sn015ContractTest {

    @Test
    fun testReceiverDefaultsAndStates() {
        assertEquals(10, AudioLabState.DEFAULT_RECEIVER_DURATION_SEC)
        assertTrue(AudioLabState.RECEIVER_DURATIONS_SEC.containsAll(listOf(5, 10, 15)))
        assertTrue(AudioLabState.RECEIVER_EXPECTED_FREQUENCIES_HZ.isNotEmpty())
        assertTrue(AudioLabState.RECEIVER_EXPECTED_FREQUENCIES_HZ.contains(18500.0))
    }

    @Test
    fun testTestIdGenerationAndValidation() {
        val id = AudioLabState.generateTestId()
        assertTrue(AudioLabState.isValidTestId(id))
        assertTrue(id.startsWith("SN015-"))
        assertTrue(AudioLabState.isValidTestId("SN015-20261102T120000Z-DEADBEEF"))
        assertTrue(AudioLabState.isValidTestId("SN015-20261102T120000Z-ABC12345"))
        assertFalse(AudioLabState.isValidTestId("sn015-bad"))
        assertFalse(AudioLabState.isValidTestId("SN015-20261102-ABC12345"))
    }

    @Test
    fun testFrequencyErrorFormatting() {
        val error = AudioLabState.frequencyErrorHz(18500.0, 18501.0)
        assertEquals(1.0, error!!, 1e-9)
        val text = AudioLabState.formatFrequencyError(18500.0, 18501.0)
        assertTrue(text.contains("Expected"))
        assertTrue(text.contains("Detected"))
        assertTrue(text.contains("Error"))
        assertNull(AudioLabState.frequencyErrorHz(null, 18500.0))
    }

    @Test
    fun testReceiverUiStatesExist() {
        val states = AudioLabState.ReceiverUiState.values()
        assertTrue(states.contains(AudioLabState.ReceiverUiState.READY))
        assertTrue(states.contains(AudioLabState.ReceiverUiState.PERMISSION_REQUIRED))
        assertTrue(states.contains(AudioLabState.ReceiverUiState.RECORDING))
        assertTrue(states.contains(AudioLabState.ReceiverUiState.RECORDING_COMPLETE))
        assertTrue(states.contains(AudioLabState.ReceiverUiState.ANALYZED))
        assertTrue(states.contains(AudioLabState.ReceiverUiState.ERROR))
    }

    @Test
    fun testRecorderHardwareIsNotOpenedWithoutUserAction() {
        val engine = org.silentnetwork.app.audiolab.audio.AndroidAudioRecorderEngine()
        assertFalse("AudioRecord must not start on construction", engine.isRecording)
        assertNull("No route may be reported before a capture", engine.lastRouteInfo)
        // release() must be safe when nothing was ever opened.
        engine.release()
        assertFalse(engine.isRecording)
    }

    @Test
    fun testExportAndroidJsonForPythonCompatibility() {
        val sampleRate = 48000
        val expected = 18500.0
        val samples = org.silentnetwork.app.audiolab.dsp.SignalGenerator.generateSineWave(
            frequencyHz = expected,
            durationSeconds = 1.0,
            sampleRate = sampleRate,
            amplitude = 0.25,
            leadInPauseSeconds = 0.0,
            leadOutPauseSeconds = 0.0
        )
        val pcm = org.silentnetwork.app.audiolab.dsp.SignalGenerator.toPcm16LittleEndian(samples)
        val wav = org.silentnetwork.app.audiolab.dsp.WavFileHandler.encodePcm16Wav(pcm, sampleRate, 1)
        val analysis = AudioAnalyzer.analyze(samples, sampleRate, 1, targetToneFrequencyHz = expected)

        val wavFile = java.io.File("build/sn015-android-json/sn015_android_rx.wav")
        wavFile.parentFile?.mkdirs()
        wavFile.writeBytes(wav)

        val json = CalibrationJsonReport.buildReport(
            device = CalibrationJsonReport.DeviceInfo("Google", "Pixel", "14", 34),
            hardware = CalibrationJsonReport.AudioHardwareState(
                requestedSampleRate = sampleRate,
                actualSampleRate = sampleRate,
                channelCount = 1,
                audioFormat = "PCM 16-bit mono",
                audioSource = "UNPROCESSED",
                bufferSizeBytes = 96000,
                aecAvailable = false,
                agcAvailable = false,
                nsAvailable = false,
                effectsStateDescription = "none",
                requestedDevice = "builtin_mic",
                actualRoutedDevice = "builtin_mic"
            ),
            testParams = CalibrationJsonReport.TestParameters(
                "RECEIVER_CAPTURE", null, null, null, expected
            ),
            analysis = analysis,
            result = CalibrationJsonReport.ResultMetadata(
                testId = "SN015-20261102T120000Z-A1B2C3D4",
                timestampUtc = "2026-11-02T12:00:00Z"
            ),
            wavInfo = CalibrationJsonReport.WavInfo(
                filename = wavFile.name,
                sha256 = org.silentnetwork.app.audiolab.dsp.WavFileHandler.sha256Hex(wav),
                sampleCount = analysis.sampleCount,
                sampleRateHz = sampleRate,
                durationSeconds = analysis.durationSeconds
            ),
            expectedFrequencyHz = expected,
            detectedFrequencyHz = analysis.detectedFrequencyHz
        )

        val jsonFile = java.io.File("build/sn015-android-json/sn015_android_result.json")
        jsonFile.writeText(json, Charsets.UTF_8)
        assertTrue(jsonFile.length() > 0)
    }

    @Test
    fun testJsonContractTopLevelFields() {
        val json = org.json.JSONObject(
            CalibrationJsonReport.buildReport(
                device = CalibrationJsonReport.DeviceInfo("Google", "Pixel", "14", 34),
                hardware = CalibrationJsonReport.AudioHardwareState(
                    requestedSampleRate = 48000,
                    actualSampleRate = 48000,
                    channelCount = 1,
                    audioFormat = "PCM 16-bit mono",
                    audioSource = "UNPROCESSED",
                    bufferSizeBytes = 96000,
                    aecAvailable = false,
                    agcAvailable = false,
                    nsAvailable = false,
                    effectsStateDescription = "none",
                    requestedDevice = "builtin_mic",
                    actualRoutedDevice = "builtin_mic"
                ),
                testParams = CalibrationJsonReport.TestParameters(
                    "RECEIVER_CAPTURE", null, null, null, 18500.0
                ),
                analysis = AudioAnalyzer.analyze(FloatArray(1000), 48000, 1),
                result = CalibrationJsonReport.ResultMetadata(
                    testId = "SN015-20261102T120000Z-A1B2C3D4",
                    timestampUtc = "2026-11-02T12:00:00Z"
                ),
                wavInfo = CalibrationJsonReport.WavInfo(
                    filename = "sn015_sn015-20261102t120000z-a1b2c3d4_rx.wav",
                    sha256 = "b".repeat(64)
                ),
                expectedFrequencyHz = 18500.0,
                detectedFrequencyHz = null
            )
        )

        assertEquals("silentnetwork.sn015.result/1.0", json.getString("schema_version"))
        assertEquals("SN015-20261102T120000Z-A1B2C3D4", json.getString("test_id"))
        assertEquals("2026-11-02T12:00:00Z", json.getString("timestamp_utc"))
        assertEquals("android_receiver", json.getString("device_role"))
        assertEquals(18500.0, json.getDouble("expected_frequency_hz"), 0.1)
        assertEquals(48000, json.getInt("actual_sample_rate_hz"))
        assertTrue(json.has("rms_dbfs"))
        assertTrue(json.has("peak_dbfs"))
        assertTrue(json.has("dominant_frequency_hz"))
        assertTrue(json.has("clipping_count"))
        assertEquals("sn015_sn015-20261102t120000z-a1b2c3d4_rx.wav", json.getString("wav_filename"))
        assertEquals("b".repeat(64), json.getString("wav_sha256"))
        assertTrue(json.getJSONArray("warnings") is org.json.JSONArray)
    }
}
