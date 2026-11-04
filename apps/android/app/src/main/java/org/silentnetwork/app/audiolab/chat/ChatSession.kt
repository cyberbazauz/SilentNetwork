package org.silentnetwork.app.audiolab.chat

import org.silentnetwork.app.audiolab.datalink.BfskProfile
import org.silentnetwork.app.audiolab.datalink.DataLinkCodec
import org.silentnetwork.app.audiolab.datalink.FileTransferCodec
import org.silentnetwork.app.audiolab.datalink.FrameV1
import org.silentnetwork.app.audiolab.datalink.StreamFrameDecoder
import java.io.File

/**
 * Half-duplex acoustic chat over Frame v1.
 *
 * The session receives an endless PCM stream, extracts frames, and turns each
 * payload into either a UTF-8 text message or a File Transfer v0 packet. It
 * never reports delivery: a decoded frame only proves that the microphone
 * heard a valid CRC-16 frame. There is no ACK, no encryption and no sender
 * authentication in v0.
 *
 * All methods must be called from a single consumer thread (the capture
 * analysis thread). [Event]s carry the sample offset so the UI can show where
 * the frame was heard, but they do not prove an over-the-air transmission.
 *
 * @param outputDir directory for assembled files; `null` keeps files in memory
 *   and reports no saved path.
 */
class ChatSession(
    val profile: BfskProfile,
    private val outputDir: File? = null,
    private val minPreambleMatches: Int = DataLinkCodec.ACOUSTIC_MIN_PREAMBLE_MATCHES,
    private val clockMillis: () -> Long = System::currentTimeMillis
) {
    sealed class Event {
        abstract val atMillis: Long

        data class TextReceived(
            val text: String,
            val payloadBytes: Int,
            val startSample: Long,
            val preambleMatches: Int,
            override val atMillis: Long
        ) : Event()

        data class FileStarted(
            val transferId: Int,
            val filename: String,
            val fileSize: Int,
            val totalChunks: Int,
            val startSample: Long,
            override val atMillis: Long
        ) : Event()

        data class FileProgress(
            val transferId: Int,
            val receivedChunks: Int,
            val totalChunks: Int,
            val duplicates: Int,
            val startSample: Long,
            override val atMillis: Long
        ) : Event()

        data class FileCompleted(
            val transferId: Int,
            val filename: String,
            val fileSize: Int,
            val sha256: String,
            val savedTo: String?,
            override val atMillis: Long
        ) : Event()

        data class FileFailed(
            val transferId: Int?,
            val errorType: String,
            val message: String,
            override val atMillis: Long
        ) : Event()

        data class FrameFailed(
            val errorType: String,
            val message: String,
            override val atMillis: Long
        ) : Event()
    }

    private val decoder = StreamFrameDecoder(
        profile = profile,
        maxPayloadBytes = DataLinkCodec.MAX_DATA_PAYLOAD_BYTES,
        minPreambleMatches = minPreambleMatches
    )

    private var assembler: FileTransferCodec.FileAssembler? = null
    private var announcedTransferId: Int? = null
    private var completedTransferId: Int? = null

    /** Total PCM samples accepted by the stream decoder. */
    val receivedSamples: Long get() = decoder.receivedSamples

    fun feed(samples: FloatArray): List<Event> {
        if (samples.isEmpty()) return emptyList()
        val frames = try {
            decoder.feed(samples)
        } catch (error: Throwable) {
            return listOf(
                Event.FrameFailed(
                    errorType = "stream_decode_error",
                    message = error.message ?: "не удалось разобрать поток",
                    atMillis = clockMillis()
                )
            )
        }
        return frames.flatMap { processFrame(it) }
    }

    /** Decode the remaining buffered audio; call when capture stops. */
    fun flush(): List<Event> = decoder.flush().flatMap { processFrame(it) }

    /**
     * Drop only buffered PCM after a capture gap. File packets already
     * accepted stay in the assembler, because the sender has no retransmit
     * and a gap must not destroy chunks that were decoded successfully.
     */
    fun resetDecoder() {
        decoder.reset()
    }

    /**
     * Drop buffered audio and the partial file transfer. Use it when the user
     * changes profile or explicitly starts a new session.
     */
    fun resetStream() {
        decoder.reset()
        releaseAssembler()
    }

    private fun processFrame(frame: StreamFrameDecoder.FrameEvent): List<Event> {
        val now = clockMillis()
        if (isFilePacket(frame.payload)) {
            return processFilePacket(frame, now)
        }
        val text = try {
            DataLinkCodec.payloadToText(frame.payload)
        } catch (error: Throwable) {
            return listOf(
                Event.FrameFailed(
                    errorType = "payload_utf8_error",
                    message = error.message ?: "payload не является UTF-8",
                    atMillis = now
                )
            )
        }
        return listOf(
            Event.TextReceived(
                text = text,
                payloadBytes = frame.payload.size,
                startSample = frame.absoluteStartSample,
                preambleMatches = frame.preambleMatches,
                atMillis = now
            )
        )
    }

    private fun processFilePacket(
        frame: StreamFrameDecoder.FrameEvent,
        now: Long
    ): List<Event> {
        val packet = try {
            FileTransferCodec.parsePacket(frame.payload)
        } catch (error: FileTransferCodec.NotFileTransferError) {
            return listOf(
                Event.FrameFailed(
                    errorType = "file_packet_invalid",
                    message = "payload начинается с SF, но не является файловым пакетом",
                    atMillis = now
                )
            )
        } catch (error: FileTransferCodec.FileTransferError) {
            return listOf(
                Event.FileFailed(
                    transferId = null,
                    errorType = error.errorType,
                    message = error.message ?: "некорректный файловый пакет",
                    atMillis = now
                )
            )
        }

        val current = assembler
        if (current != null && current.transferId != packet.transferId) {
            releaseAssembler()
        }
        val target = assembler ?: FileTransferCodec.FileAssembler().also {
            assembler = it
            announcedTransferId = null
            completedTransferId = null
        }

        val events = mutableListOf<Event>()
        try {
            target.add(packet)
        } catch (error: FileTransferCodec.FileTransferError) {
            // A conflicting packet makes the partial state untrustworthy. The
            // sender repeats MANIFEST, so a later repeat can restart assembly.
            releaseAssembler()
            events.add(
                Event.FileFailed(
                    transferId = packet.transferId,
                    errorType = error.errorType,
                    message = error.message ?: "конфликт файлового пакета",
                    atMillis = now
                )
            )
            return events
        }

        val state = target.state()
        val manifest = target.manifest
        if (manifest != null && announcedTransferId != packet.transferId) {
            announcedTransferId = packet.transferId
            events.add(
                Event.FileStarted(
                    transferId = packet.transferId,
                    filename = manifest.filename,
                    fileSize = manifest.fileSize,
                    totalChunks = state.totalChunks ?: 0,
                    startSample = frame.absoluteStartSample,
                    atMillis = now
                )
            )
        }
        events.add(
            Event.FileProgress(
                transferId = packet.transferId,
                receivedChunks = state.chunksReceived,
                totalChunks = state.totalChunks ?: 0,
                duplicates = state.duplicateChunks,
                startSample = frame.absoluteStartSample,
                atMillis = now
            )
        )

        if (!state.complete || completedTransferId == packet.transferId) {
            return events
        }

        val content = try {
            target.assemble()
        } catch (error: FileTransferCodec.FileTransferError) {
            events.add(
                Event.FileFailed(
                    transferId = packet.transferId,
                    errorType = error.errorType,
                    message = error.message ?: "не удалось собрать файл",
                    atMillis = now
                )
            )
            completedTransferId = packet.transferId
            return events
        }

        val digest = FileTransferCodec.sha256Hex(content)
        var savedTo: String? = null
        if (outputDir != null && manifest != null) {
            try {
                // A fixed output directory would overwrite a file received
                // earlier under the same name. Each transfer gets its own
                // subdirectory, and the validated filename stays unchanged.
                val targetDir = File(outputDir, "rx-$now-${packet.transferId}")
                savedTo = FileTransferCodec
                    .writeAssembledFile(targetDir, manifest.filename, content)
                    .absolutePath
            } catch (error: Throwable) {
                events.add(
                    Event.FileFailed(
                        transferId = packet.transferId,
                        errorType = "output_error",
                        message = error.message ?: "не удалось сохранить файл",
                        atMillis = now
                    )
                )
            }
        }
        completedTransferId = packet.transferId
        events.add(
            Event.FileCompleted(
                transferId = packet.transferId,
                filename = manifest?.filename ?: "file-${
                    packet.transferId.toString(16)
                }",
                fileSize = content.size,
                sha256 = digest,
                savedTo = savedTo,
                atMillis = now
            )
        )
        return events
    }

    private fun releaseAssembler() {
        assembler = null
        announcedTransferId = null
        completedTransferId = null
    }

    private fun isFilePacket(payload: ByteArray): Boolean =
        payload.size >= 2 &&
            payload[0] == FileTransferCodec.FT_MAGIC[0] &&
            payload[1] == FileTransferCodec.FT_MAGIC[1]

    companion object {
        /** Payload limit for a chat text message (Frame v1 data link v0). */
        const val MAX_TEXT_BYTES: Int = DataLinkCodec.MAX_DATA_PAYLOAD_BYTES

        /** Largest file accepted by File Transfer v0. */
        const val MAX_FILE_BYTES: Int = FileTransferCodec.MAX_FILE_BYTES

        fun framePayload(text: String): ByteArray = DataLinkCodec.textToPayload(text)

        fun framePayloadTooLarge(bytes: Int): Boolean = bytes > MAX_TEXT_BYTES

        /** Reserved two-byte prefix of a file packet; text starting with it is ambiguous. */
        val FILE_MAGIC: ByteArray get() = FileTransferCodec.FT_MAGIC.copyOf()

        /** Frame v1 checksum width, shown in diagnostics. */
        const val CRC_BITS: Int = FrameV1.CRC_BITS
    }
}
