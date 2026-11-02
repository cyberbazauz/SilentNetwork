package org.silentnetwork.app.audiolab.reporting

import org.json.JSONArray
import org.json.JSONObject
import org.silentnetwork.app.audiolab.dsp.AudioAnalyzer
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Diagnostic JSON report generator for acoustic calibration and verification.
 * Follows strict privacy guidelines: zero PII, zero geolocation, zero device identifiers (IMEI/MAC).
 * Compatible with SilentNetwork calibration analysis tools.
 */
object CalibrationJsonReport {

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
        val effectsStateDescription: String
    )

    data class TestParameters(
        val testType: String, // "TRANSMITTER_SELF_TEST" or "RECEIVER_CAPTURE" or "WAV_IMPORT"
        val transmitterFrequencyHz: Double?,
        val transmitterAmplitude: Double?,
        val transmitterDurationSeconds: Double?,
        val targetFrequencyHz: Double?
    )

    /**
     * Builds standard JSON representation of the calibration session.
     */
    fun buildReport(
        device: DeviceInfo,
        hardware: AudioHardwareState,
        testParams: TestParameters,
        analysis: AudioAnalyzer.AnalysisResult,
        warnings: List<String> = emptyList(),
        errors: List<String> = emptyList()
    ): String {
        val root = JSONObject()

        val dateFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
        dateFormat.timeZone = TimeZone.getTimeZone("UTC")
        root.put("report_schema_version", "1.0.0")
        root.put("timestamp_utc", dateFormat.format(Date()))
        root.put("project", "SilentNetwork")
        root.put("component", "Android Audio Lab (SN-014)")

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
        if (testParams.transmitterFrequencyHz != null) {
            tpObj.put("transmitter_frequency_hz", testParams.transmitterFrequencyHz)
        }
        if (testParams.transmitterAmplitude != null) {
            tpObj.put("transmitter_amplitude", testParams.transmitterAmplitude)
        }
        if (testParams.transmitterDurationSeconds != null) {
            tpObj.put("transmitter_duration_sec", testParams.transmitterDurationSeconds)
        }
        if (testParams.targetFrequencyHz != null) {
            tpObj.put("target_frequency_hz", testParams.targetFrequencyHz)
        }
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

        // Warnings and errors
        val warnArray = JSONArray()
        for (w in warnings) warnArray.put(w)
        root.put("warnings", warnArray)

        val errArray = JSONArray()
        for (e in errors) errArray.put(e)
        root.put("errors", errArray)

        return root.toString(2)
    }
}
