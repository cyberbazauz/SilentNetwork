package org.silentnetwork.app.audiolab.presentation

import org.silentnetwork.app.audiolab.dsp.AudioAnalyzer

/**
 * Presentation state and business logic helpers for Audio Lab.
 * Decoupled from Android views to enable deterministic JVM unit tests.
 */
object AudioLabState {

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

    val RECEIVER_DURATIONS_SEC = listOf(3, 5, 10)

    val SAFE_AMPLITUDES = listOf(0.05, 0.10, 0.15, 0.20, 0.30, 0.50)

    const val DEFAULT_FREQUENCY_HZ = 18500.0
    const val DEFAULT_SAMPLE_RATE_HZ = 48000
    const val DEFAULT_TRANSMITTER_DURATION_SEC = 2
    const val DEFAULT_RECEIVER_DURATION_SEC = 5
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
}
