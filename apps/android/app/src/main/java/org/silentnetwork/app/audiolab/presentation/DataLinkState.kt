package org.silentnetwork.app.audiolab.presentation

import org.silentnetwork.app.audiolab.datalink.BfskProfile
import org.silentnetwork.app.audiolab.datalink.DataLinkCodec

object DataLinkState {
    const val DEFAULT_PROFILE = "data-audible"
    const val ACOUSTIC_MIN_PREAMBLE = DataLinkCodec.ACOUSTIC_MIN_PREAMBLE_MATCHES

    val PROFILE_OPTIONS = listOf(
        Pair("data-audible", BfskProfile.DATA_AUDIBLE),
        Pair("data-robust", BfskProfile.DATA_ROBUST)
    )

    val RECEIVER_DURATIONS_SEC = listOf(5, 10, 15)
    const val DEFAULT_RECEIVER_DURATION_SEC = 10

    fun getProfile(name: String): BfskProfile {
        return BfskProfile.dataProfiles()[name] ?: BfskProfile.DATA_AUDIBLE
    }

    fun profileLabel(profile: BfskProfile): String {
        return when (profile.name) {
            "data-audible" -> "data-audible (1200/2200 Гц, 300 бод)"
            "data-robust" -> "data-robust (1000/2000 Гц, 100 бод)"
            else -> profile.name
        }
    }

    fun verdictTitle(received: DataLinkCodec.ReceivedMessage): String {
        return if (received.ok) {
            "Сообщение принято"
        } else {
            when (received.errorType) {
                "signal_not_found" -> "Сигнал не найден"
                "sync_mismatch" -> "Синхронизация не пройдена"
                "version_unsupported" -> "Неподдерживаемая версия кадра"
                "length_invalid" -> "Неверная длина кадра"
                "incomplete_frame" -> "Кадр неполный"
                "crc_mismatch" -> "Ошибка CRC"
                "payload_utf8_error" -> "Payload не является корректным UTF-8"
                "wav_format_error" -> "Неверный WAV"
                "wav_not_found" -> "WAV не найден"
                else -> "Приём не удался"
            }
        }
    }

    fun verdictHint(received: DataLinkCodec.ReceivedMessage): String {
        if (received.ok) {
            return "Кадр принят, CRC совпал. CRC не является аутентификацией."
        }
        return received.message ?: "Проверьте расстояние, уровень громкости и профиль."
    }

    fun recordingSummary(received: DataLinkCodec.ReceivedMessage): String {
        return String.format(
            java.util.Locale.US,
            "WAV: %d Гц, %.2f с, RMS %.1f dBFS, пик %.1f dBFS, клиппинг: %d",
            received.sampleRate,
            received.durationS,
            received.rmsDbfs,
            received.peakDbfs,
            received.clippedSamples
        )
    }
}
