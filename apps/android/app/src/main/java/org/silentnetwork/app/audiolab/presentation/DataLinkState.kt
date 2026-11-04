package org.silentnetwork.app.audiolab.presentation

import org.silentnetwork.app.audiolab.datalink.BfskProfile
import org.silentnetwork.app.audiolab.datalink.DataLinkCodec
import org.silentnetwork.app.audiolab.datalink.FileTransferCodec

object DataLinkState {
    const val DEFAULT_PROFILE = "data-audible"
    const val ACOUSTIC_MIN_PREAMBLE = DataLinkCodec.ACOUSTIC_MIN_PREAMBLE_MATCHES

    /** Below this peak level a close-range tone is suspiciously quiet. */
    private const val QUIET_PEAK_DBFS = -20.0

    val PROFILE_OPTIONS = listOf(
        Pair("data-audible", BfskProfile.DATA_AUDIBLE),
        Pair("data-robust", BfskProfile.DATA_ROBUST)
    )

    val RECEIVER_DURATIONS_SEC = listOf(5, 10, 15)
    const val DEFAULT_RECEIVER_DURATION_SEC = 10

    // 8 КиБ при 300 бод звучат около 364 с. 420 с даёт запас на паузы и
    // задержку старта записи; короткие значения оставлены для малых файлов.
    val FILE_RECEIVER_DURATIONS_SEC = listOf(15, 30, 60, 120, 240, 420)
    const val DEFAULT_FILE_RECEIVER_DURATION_SEC = 30

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
        val base = received.message ?: "Проверьте расстояние, уровень громкости и профиль."
        val quiet = quietSignalHint(received.peakDbfs, received.rmsDbfs)
        return if (quiet == null) base else "$base\n$quiet"
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

    fun fileVerdictTitle(result: FileTransferCodec.FileReceiveResult): String {
        return if (result.ok) {
            "Файл принят"
        } else {
            when (result.errorType) {
                "signal_not_found" -> "Сигнал не найден"
                "not_file_transfer" -> "Это не файловые пакеты"
                "packet_invalid" -> "Нет корректных файловых пакетов"
                "manifest_invalid" -> "Неверный MANIFEST"
                "transfer_conflict" -> "Конфликт пакетов"
                "incomplete_transfer" -> "Файл принят не полностью"
                "hash_mismatch" -> "SHA-256 не совпал"
                "output_error" -> "Не удалось сохранить файл"
                "wav_format_error" -> "Неверный WAV"
                "wav_not_found" -> "WAV не найден"
                else -> "Приём файла не удался"
            }
        }
    }

    fun fileVerdictHint(result: FileTransferCodec.FileReceiveResult): String {
        if (result.ok) {
            return "Собраны все чанки, размер и SHA-256 совпали. " +
                "SHA-256 не аутентифицирует отправителя."
        }
        val base = result.message.ifBlank {
            "Проверьте расстояние, уровень громкости и профиль сигнала."
        }
        val quiet = quietSignalHint(result.peakDbfs, result.rmsDbfs)
        return if (quiet == null) base else "$base\n$quiet"
    }

    /**
     * A recording much quieter than a normal close-range tone usually means
     * the phone listened to the wrong speaker or the volume was too low.
     * CRC mismatch alone does not distinguish that from frame timing errors.
     */
    private fun quietSignalHint(peakDbfs: Double, rmsDbfs: Double): String? {
        if (peakDbfs >= QUIET_PEAK_DBFS) return null
        return String.format(
            java.util.Locale.US,
            "Запись очень тихая: пик %.1f dBFS, RMS %.1f dBFS. " +
                "Поднесите телефон к динамику, увеличьте громкость и проверьте, " +
                "что звук идёт из нужного устройства.",
            peakDbfs,
            rmsDbfs
        )
    }

    fun fileRecordingSummary(result: FileTransferCodec.FileReceiveResult): String {
        return String.format(
            java.util.Locale.US,
            "WAV: %d Гц, %.2f с, RMS %.1f dBFS, пик %.1f dBFS, клиппинг: %d",
            result.sampleRate,
            result.durationS,
            result.rmsDbfs,
            result.peakDbfs,
            result.clippedSamples
        )
    }

    fun fileTransferSummary(result: FileTransferCodec.FileReceiveResult): String {
        val state = result.state ?: return "Пакеты файла не собраны"
        val lines = mutableListOf<String>()
        lines.add(
            String.format(
                java.util.Locale.US,
                "Кадров: %d · пакетов: %d · чанков: %d/%s",
                result.stream.framesFound,
                result.stream.packets.size,
                state.chunksReceived,
                state.totalChunks?.toString() ?: "?"
            )
        )
        state.manifest?.let { manifest ->
            lines.add("Файл: ${manifest.filename} · ${manifest.fileSize} байт")
        }
        if (state.duplicateChunks > 0) {
            lines.add("Повторов чанков: ${state.duplicateChunks}")
        }
        if (state.missingChunks.isNotEmpty()) {
            lines.add("Нет чанков: ${state.missingChunks.joinToString(", ")}")
        }
        result.actualSha256?.let { lines.add("SHA-256: $it") }
        if (result.stream.frameFailures.isNotEmpty()) {
            lines.add("Повреждённых кадров: ${result.stream.frameFailures.size}")
        }
        if (result.stream.packetErrors.isNotEmpty()) {
            lines.add("Ошибочных пакетов: ${result.stream.packetErrors.size}")
        }
        return lines.joinToString("\n")
    }
}
