package org.silentnetwork.app.audiolab.presentation

import org.silentnetwork.app.audiolab.dsp.AudioAnalyzer
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

/**
 * Presentation state and business logic helpers for Audio Lab.
 * Decoupled from Android views to enable deterministic JVM unit tests.
 */
object AudioLabState {

    /**
     * Receiver lifecycle shown to the user. The app must not claim that a tone
     * was received merely because a WAV file exists: ANALYZED is reached only
     * after the recording has actually been analyzed.
     */
    enum class ReceiverUiState {
        READY,
        PERMISSION_REQUIRED,
        RECORDING,
        RECORDING_COMPLETE,
        ANALYZED,
        ERROR
    }

    val TRANSMITTER_FREQUENCIES_HZ = listOf(
        1000.0,  // Контрольный слышимый тон (для проверки динамика)
        15000.0, // 15 kHz
        16000.0, // 16 kHz
        17000.0, // 17 kHz
        18000.0, // 18 kHz (M0 нижняя)
        18500.0, // 18.5 kHz (M0 центральная)
        19000.0, // 19 kHz (M0 верхняя)
        20000.0, // 20 kHz
        21000.0, // 21 kHz
        22000.0  // 22 kHz
    )

    val SAMPLE_RATES_HZ = listOf(48000, 44100)

    val TRANSMITTER_DURATIONS_SEC = listOf(1, 2, 3, 5)

    val RECEIVER_DURATIONS_SEC = listOf(5, 10, 15)

    val RECEIVER_EXPECTED_FREQUENCIES_HZ = TRANSMITTER_FREQUENCIES_HZ

    val SAFE_AMPLITUDES = listOf(0.05, 0.10, 0.15, 0.20, 0.30, 0.50)

    const val DEFAULT_FREQUENCY_HZ = 18500.0
    const val DEFAULT_SAMPLE_RATE_HZ = 48000
    const val DEFAULT_TRANSMITTER_DURATION_SEC = 2
    const val DEFAULT_RECEIVER_DURATION_SEC = 10
    const val DEFAULT_AMPLITUDE = 0.15 // -16.5 dBFS safe starting level

    fun isExperimentalM0Band(frequencyHz: Double): Boolean {
        return frequencyHz in 18000.0..19000.0
    }

    fun isAudibleRange(frequencyHz: Double): Boolean {
        return frequencyHz < 16000.0
    }

    fun validateTransmitterParameters(
        frequencyHz: Double,
        amplitude: Double,
        durationSec: Double,
        sampleRate: Int
    ): Result<Unit> {
        if (frequencyHz <= 0.0) {
            return Result.failure(IllegalArgumentException("Частота должна быть положительной"))
        }
        if (frequencyHz >= sampleRate / 2.0) {
            return Result.failure(IllegalArgumentException("Частота должна быть ниже частоты Найквиста (${sampleRate / 2} Гц)"))
        }
        if (amplitude !in 0.01..0.80) {
            return Result.failure(IllegalArgumentException("Амплитуда должна быть в безопасном диапазоне 0.01..0.80"))
        }
        if (durationSec !in 0.1..30.0) {
            return Result.failure(IllegalArgumentException("Длительность должна быть в пределах 0.1..30 сек"))
        }
        if (sampleRate !in listOf(44100, 48000, 96000)) {
            return Result.failure(IllegalArgumentException("Неподдерживаемая частота дискретизации: $sampleRate"))
        }
        return Result.success(Unit)
    }

    fun formatClippingStatus(clippingCount: Int): String {
        return if (clippingCount == 0) {
            "Клиппинг: ОТСУТСТВУЕТ (0 отсчётов)"
        } else {
            "ВНИМАНИЕ: КЛИППИНГ ($clippingCount отсчётов перегрузки!)"
        }
    }

    fun formatPeakStatus(peakDbfs: Double): String {
        return String.format(java.util.Locale.US, "Пик: %.1f dBFS", peakDbfs)
    }

    fun formatRmsStatus(rmsDbfs: Double): String {
        return String.format(java.util.Locale.US, "RMS: %.1f dBFS", rmsDbfs)
    }

    fun formatFrequencyLabel(freqHz: Double): String {
        return if (freqHz >= 10000.0) {
            if (freqHz % 1000.0 == 0.0) {
                "${(freqHz / 1000.0).toInt()} кГц"
            } else {
                String.format(java.util.Locale.US, "%.1f кГц", freqHz / 1000.0)
            }
        } else {
            "${freqHz.toInt()} Гц"
        }
    }

    fun receiverStateLabel(state: ReceiverUiState): String = when (state) {
        ReceiverUiState.READY -> "READY"
        ReceiverUiState.PERMISSION_REQUIRED -> "PERMISSION REQUIRED"
        ReceiverUiState.RECORDING -> "RECORDING"
        ReceiverUiState.RECORDING_COMPLETE -> "RECORDING COMPLETE"
        ReceiverUiState.ANALYZED -> "ANALYZED"
        ReceiverUiState.ERROR -> "ERROR"
    }

    /**
     * Frequency error in Hz, or null when either side is unknown. A value is
     * never treated as a PASS/FAIL verdict by itself.
     */
    fun frequencyErrorHz(expectedHz: Double?, detectedHz: Double?): Double? {
        if (expectedHz == null || detectedHz == null) return null
        if (!expectedHz.isFinite() || !detectedHz.isFinite()) return null
        return detectedHz - expectedHz
    }

    fun formatFrequencyError(expectedHz: Double?, detectedHz: Double?): String {
        val error = frequencyErrorHz(expectedHz, detectedHz)
        val expected = expectedHz?.let { String.format(Locale.US, "%.1f Hz", it) } ?: "N/A"
        val detected = detectedHz?.let { String.format(Locale.US, "%.1f Hz", it) } ?: "N/A"
        val offset = error?.let { String.format(Locale.US, "%+.1f Hz", it) } ?: "N/A"
        return "Expected: $expected\nDetected: $detected\nError: $offset"
    }

    /**
     * Canonical SN-015 test id. Windows shows this id; the user types the same
     * value on the phone so both WAV/JSON files can be matched without a network.
     */
    fun generateTestId(date: Date = Date(), suffix: String = randomSuffix()): String {
        val formatter = SimpleDateFormat("yyyyMMdd'T'HHmmss'Z'", Locale.US)
        formatter.timeZone = TimeZone.getTimeZone("UTC")
        val normalized = suffix.uppercase(Locale.US)
        require(TEST_ID_SUFFIX.matches(normalized)) {
            "test id suffix must be 8 hexadecimal characters"
        }
        return "SN015-${formatter.format(date)}-$normalized"
    }

    fun isValidTestId(value: String): Boolean = TEST_ID_PATTERN.matches(value.trim().uppercase(Locale.US))

    private fun randomSuffix(): String =
        UUID.randomUUID().toString().replace("-", "").take(8).uppercase(Locale.US)

    private val TEST_ID_SUFFIX = Regex("^[0-9A-F]{8}$")
    private val TEST_ID_PATTERN = Regex("^SN015-[0-9]{8}T[0-9]{6}Z-[0-9A-F]{8}$")
}
