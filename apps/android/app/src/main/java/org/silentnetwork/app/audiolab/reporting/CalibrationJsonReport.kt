package org.silentnetwork.app.audiolab.reporting

import org.json.JSONArray
import org.json.JSONObject
import org.silentnetwork.app.audiolab.dsp.AudioAnalyzer
import org.silentnetwork.app.audiolab.presentation.AudioLabState
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Diagnostic JSON report generator for acoustic calibration and verification.
 * Follows strict privacy guidelines: zero PII, zero geolocation, zero device identifiers (IMEI/MAC).
 *
 * SN-015 adds the shared top-level result contract
 * (`docs/hardware-calibration/SN-015-RESULT-FORMAT.md`). The older nested
 * fields are kept so existing tools and reports remain readable.
 */
object CalibrationJsonReport {

    const val SCHEMA_VERSION = "silentnetwork.sn015.result/1.0"

    data class DeviceInfo(
        val manufacturer: String,
        val model: String,
        val androidRelease: String,
        val sdkInt: Int
    )

    data class AudioHardwareState(
        val requestedSampleRate: Int,
        val actualSampleRate: Int,
        val channelCount: Int,
        val audioFormat: String,
        val audioSource: String,
        val bufferSizeBytes: Int,
        val aecAvailable: Boolean,
        val agcAvailable: Boolean,
        val nsAvailable: Boolean,
        val effectsStateDescription: String,
        val requestedDevice: String = "builtin_mic",
        val actualRoutedDevice: String? = null
    )

    data class TestParameters(
        val testType: String, // "TRANSMITTER_SELF_TEST" or "RECEIVER_CAPTURE" or "WAV_IMPORT"
        val transmitterFrequencyHz: Double?,
        val transmitterAmplitude: Double?,
        val transmitterDurationSeconds: Double?,
        val targetFrequencyHz: Double?
    )

    data class ResultMetadata(
        val testId: String,
        val deviceRole: String = "android_receiver",
        val schemaVersion: String = SCHEMA_VERSION,
        val timestampUtc: String? = null
    )

    data class WavInfo(
        val filename: String,
        val sha256: String,
        val sampleCount: Int? = null,
        val sampleRateHz: Int? = null,
        val durationSeconds: Double? = null
    )

    /**
     * Builds the standard JSON representation of a calibration session.
     *
     * @param expectedFrequencyHz frequency shown on Windows before PLAY.
     * @param detectedFrequencyHz frequency measured on the phone; null when
     *   analysis has not run or no tone was detected.
     */
    fun buildReport(
        device: DeviceInfo,
        hardware: AudioHardwareState,
        testParams: TestParameters,
        analysis: AudioAnalyzer.AnalysisResult,
        result: ResultMetadata,
        wavInfo: WavInfo? = null,
        expectedFrequencyHz: Double? = testParams.targetFrequencyHz,
        detectedFrequencyHz: Double? = null,
        warnings: List<String> = emptyList(),
        errors: List<String> = emptyList()
    ): String {
        val root = JSONObject()

        val dateFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
        dateFormat.timeZone = TimeZone.getTimeZone("UTC")
        val timestamp = result.timestampUtc ?: dateFormat.format(Date())

        // Shared top-level contract (required by Windows analysis/reporting).
        root.put("schema_version", result.schemaVersion)
        root.put("test_id", result.testId)
        root.put("timestamp_utc", timestamp)
        root.put("device_role", result.deviceRole)
        root.putNullable("expected_frequency_hz", expectedFrequencyHz)
        root.put("actual_sample_rate_hz", hardware.actualSampleRate)
        root.put("requested_sample_rate_hz", hardware.requestedSampleRate)
        root.put("duration_seconds", analysis.durationSeconds)
        root.put("rms_dbfs", analysis.rmsDbfs)
        root.put("peak_dbfs", analysis.peakDbfs)
        root.put("dominant_frequency_hz", analysis.dominantFrequencyHz)
        root.putNullable("detected_frequency_hz", detectedFrequencyHz)
        root.putNullable(
            "frequency_error_hz",
            AudioLabState.frequencyErrorHz(expectedFrequencyHz, detectedFrequencyHz)
        )
        root.put("clipping_count", analysis.clippingCount)
        root.putNullable("wav_filename", wavInfo?.filename)
        root.putNullable("wav_sha256", wavInfo?.sha256)
        root.put("warnings", warnings.toJsonArray())

        // Backwards-compatible report identity.
        root.put("report_schema_version", result.schemaVersion)
        root.put("project", "SilentNetwork")
        root.put("component", "Android Audio Lab (SN-015 receiver)")

        // Device metadata (hardware type only, no PII)
        val devObj = JSONObject()
        devObj.put("manufacturer", device.manufacturer)
        devObj.put("model", device.model)
        devObj.put("android_release", device.androidRelease)
        devObj.put("sdk_int", device.sdkInt)
        root.put("device", devObj)

        // Audio hardware configuration
        val hwObj = JSONObject()
        hwObj.put("requested_sample_rate_hz", hardware.requestedSampleRate)
        hwObj.put("actual_sample_rate_hz", hardware.actualSampleRate)
        hwObj.put("channel_count", hardware.channelCount)
        hwObj.put("audio_format", hardware.audioFormat)
        hwObj.put("audio_source", hardware.audioSource)
        hwObj.put("requested_device", hardware.requestedDevice)
        hwObj.putNullable("actual_routed_device", hardware.actualRoutedDevice)
        hwObj.put("buffer_size_bytes", hardware.bufferSizeBytes)

        val fxObj = JSONObject()
        fxObj.put("acoustic_echo_canceler_available", hardware.aecAvailable)
        fxObj.put("automatic_gain_control_available", hardware.agcAvailable)
        fxObj.put("noise_suppressor_available", hardware.nsAvailable)
        fxObj.put("state_description", hardware.effectsStateDescription)
        hwObj.put("hardware_audio_effects", fxObj)
        root.put("hardware", hwObj)

        // Test parameters
        val tpObj = JSONObject()
        tpObj.put("test_type", testParams.testType)
        tpObj.putNullable("transmitter_frequency_hz", testParams.transmitterFrequencyHz)
        tpObj.putNullable("transmitter_amplitude", testParams.transmitterAmplitude)
        tpObj.putNullable("transmitter_duration_sec", testParams.transmitterDurationSeconds)
        tpObj.putNullable("target_frequency_hz", testParams.targetFrequencyHz)
        tpObj.putNullable("expected_frequency_hz", expectedFrequencyHz)
        tpObj.putNullable("detected_frequency_hz", detectedFrequencyHz)
        tpObj.putNullable(
            "frequency_error_hz",
            AudioLabState.frequencyErrorHz(expectedFrequencyHz, detectedFrequencyHz)
        )
        root.put("test_parameters", tpObj)

        // Analysis results
        val anObj = JSONObject()
        anObj.put("sample_count", analysis.sampleCount)
        anObj.put("duration_seconds", analysis.durationSeconds)
        anObj.put("sample_rate_hz", analysis.sampleRate)
        anObj.put("rms_linear", analysis.rms)
        anObj.put("rms_dbfs", analysis.rmsDbfs)
        anObj.put("peak_linear", analysis.peak)
        anObj.put("peak_dbfs", analysis.peakDbfs)
        anObj.put("clipping_count", analysis.clippingCount)
        anObj.put("has_clipping", analysis.hasClipping)
        anObj.put("dc_offset", analysis.dcOffset)
        anObj.put("dominant_frequency_hz", analysis.dominantFrequencyHz)
        anObj.put("dominant_peak_dbfs", analysis.dominantPeakDbfs)
        anObj.putNullable("detected_frequency_hz", detectedFrequencyHz)
        anObj.putNullable(
            "frequency_error_hz",
            AudioLabState.frequencyErrorHz(expectedFrequencyHz, detectedFrequencyHz)
        )
        anObj.put("band_18_19_khz_power_fraction", analysis.band18to19kHzPowerFraction)
        anObj.put("snr_disclaimer", analysis.snrDisclaimer)

        val peaksArray = JSONArray()
        for (peak in analysis.topSpectralPeaks) {
            val pObj = JSONObject()
            pObj.put("frequency_hz", peak.frequencyHz)
            pObj.put("magnitude_dbfs", peak.magnitudeDbfs)
            pObj.put("relative_power_fraction", peak.relativePowerFraction)
            peaksArray.put(pObj)
        }
        anObj.put("top_spectral_peaks", peaksArray)
        root.put("analysis", anObj)

        // WAV provenance. Present for RECEIVER_CAPTURE, optional for WAV_IMPORT.
        if (wavInfo != null) {
            val wavObj = JSONObject()
            wavObj.put("filename", wavInfo.filename)
            wavObj.put("sha256", wavInfo.sha256)
            wavObj.putNullable("sample_count", wavInfo.sampleCount)
            wavObj.putNullable("sample_rate_hz", wavInfo.sampleRateHz)
            wavObj.putNullable("duration_seconds", wavInfo.durationSeconds)
            root.put("wav", wavObj)
        }

        root.put("errors", errors.toJsonArray())

        return root.toString(2)
    }

    private fun List<String>.toJsonArray(): JSONArray {
        val array = JSONArray()
        for (value in this) array.put(value)
        return array
    }

    private fun JSONObject.putNullable(name: String, value: Any?) {
        put(name, value ?: JSONObject.NULL)
    }
}
