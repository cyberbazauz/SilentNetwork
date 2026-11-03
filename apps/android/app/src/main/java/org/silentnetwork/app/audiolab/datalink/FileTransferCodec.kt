package org.silentnetwork.app.audiolab.datalink

import org.json.JSONArray
import org.json.JSONObject
import org.silentnetwork.app.audiolab.dsp.SignalGenerator
import org.silentnetwork.app.audiolab.dsp.WavFileHandler
import java.io.ByteArrayInputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sqrt

/**
 * Acoustic File Transfer v0 for Android: file -> MANIFEST/DATA/END -> Frame v1
 * -> BFSK -> PCM/WAV and back.
 *
 * This is the Kotlin port of `acoustic-lab/snlab/file_transfer.py`. Frame v1,
 * CRC-16/CCITT-FALSE, BFSK profiles and the 64-byte Frame payload limit are not
 * modified. SHA-256 checks file integrity but does **not** authenticate the
 * sender. CRC-16 and SHA-256 do not replace AEAD.
 *
 * Limits: one file per transfer, 0..4096 bytes, filename 1..40 UTF-8 bytes,
 * 54 data bytes per DATA packet, no ACK and no encryption.
 */
object FileTransferCodec {
    const val REPORT_SCHEMA = "silentnetwork.acoustic-lab.file-transfer/1"
    const val FT_VERSION = 1
    const val PACKET_HEADER_BYTES = 10
    const val CHUNK_DATA_BYTES = DataLinkCodec.MAX_DATA_PAYLOAD_BYTES - PACKET_HEADER_BYTES
    const val MAX_FILENAME_BYTES = 40
    const val MAX_FILE_BYTES = 4096
    const val MAX_CHUNKS = (MAX_FILE_BYTES + CHUNK_DATA_BYTES - 1) / CHUNK_DATA_BYTES

    const val PACKET_MANIFEST = 1
    const val PACKET_DATA = 2
    const val PACKET_END = 3

    const val DEFAULT_INTER_FRAME_GAP_S = 0.25
    const val DEFAULT_LEAD_SILENCE_S = 1.0
    const val DEFAULT_TAIL_SILENCE_S = 0.5
    const val DEFAULT_MANIFEST_REPEAT_EVERY = 16
    const val DEFAULT_END_REPEATS = 2
    const val CLIP_THRESHOLD = 0.999

    val FT_MAGIC = byteArrayOf(0x53, 0x46)

    private val PACKET_TYPE_NAMES = mapOf(
        PACKET_MANIFEST to "manifest",
        PACKET_DATA to "data",
        PACKET_END to "end"
    )

    private val WINDOWS_RESERVED = buildSet {
        addAll(listOf("CON", "PRN", "AUX", "NUL"))
        for (index in 1..9) {
            add("COM$index")
            add("LPT$index")
        }
    }

    open class FileTransferError(
        message: String,
        val errorType: String = "file_transfer_error"
    ) : IllegalArgumentException(message)

    class FileTooLargeError(message: String) :
        FileTransferError(message, "file_too_large")

    class FilenameError(message: String) :
        FileTransferError(message, "filename_invalid")

    open class PacketError(message: String, errorType: String = "packet_invalid") :
        FileTransferError(message, errorType)

    class NotFileTransferError(message: String) :
        PacketError(message, "not_file_transfer")

    class ManifestError(message: String) :
        PacketError(message, "manifest_invalid")

    class TransferConflictError(message: String) :
        FileTransferError(message, "transfer_conflict")

    class IncompleteTransferError(message: String) :
        FileTransferError(message, "incomplete_transfer")

    class HashMismatchError(message: String) :
        FileTransferError(message, "hash_mismatch")

    data class Manifest(
        val filename: String,
        val filenameBytes: ByteArray,
        val fileSize: Int,
        val chunkDataBytes: Int = CHUNK_DATA_BYTES
    )

    data class EndInfo(
        val fileSize: Int,
        val totalChunks: Int,
        val sha256: ByteArray
    )

    data class FilePacket(
        val packetType: Int,
        val transferId: Int,
        val seq: Int,
        val totalChunks: Int,
        val payload: ByteArray
    ) {
        val typeName: String
            get() = PACKET_TYPE_NAMES[packetType] ?: "unknown-$packetType"

        fun toBytes(): ByteArray {
            if (packetType !in PACKET_TYPE_NAMES) {
                throw PacketError("неизвестный тип пакета: $packetType")
            }
            validateTransferId(transferId)
            validateTotalChunks(totalChunks)
            if (seq !in 0..0xFFFF) throw PacketError("seq должен помещаться в 16 бит")
            if ((packetType == PACKET_MANIFEST || packetType == PACKET_END) && seq != 0) {
                throw PacketError("у MANIFEST и END seq должен быть 0")
            }
            if (payload.size > CHUNK_DATA_BYTES) {
                throw PacketError(
                    "payload пакета ${payload.size} байт превышает $CHUNK_DATA_BYTES"
                )
            }
            val out = ByteArray(PACKET_HEADER_BYTES + payload.size)
            out[0] = FT_MAGIC[0]
            out[1] = FT_MAGIC[1]
            out[2] = FT_VERSION.toByte()
            out[3] = packetType.toByte()
            out[4] = transferId.toByte()
            out[5] = ((seq shr 8) and 0xFF).toByte()
            out[6] = (seq and 0xFF).toByte()
            out[7] = ((totalChunks shr 8) and 0xFF).toByte()
            out[8] = (totalChunks and 0xFF).toByte()
            out[9] = payload.size.toByte()
            payload.copyInto(out, PACKET_HEADER_BYTES)
            return out
        }
    }

    data class FileTransferPlan(
        val manifest: Manifest,
        val transferId: Int,
        val totalChunks: Int,
        val packets: List<FilePacket>,
        val dataBytes: ByteArray
    ) {
        val packetCount: Int get() = packets.size
    }

    data class DecodedFilePacket(
        val packet: FilePacket,
        val startSample: Int,
        val startTimeS: Double,
        val preambleMatches: Int,
        val crc: Int
    )

    data class FrameFailure(
        val startSample: Int,
        val startTimeS: Double,
        val errorType: String,
        val message: String
    )

    data class PacketErrorInfo(
        val startSample: Int,
        val startTimeS: Double,
        val errorType: String,
        val message: String
    )

    data class PacketStream(
        val packets: List<DecodedFilePacket>,
        val frameFailures: List<FrameFailure>,
        val packetErrors: List<PacketErrorInfo>,
        val framesFound: Int,
        val totalSamples: Int
    )

    data class TransferState(
        val transferId: Int?,
        val manifest: Manifest?,
        val totalChunks: Int?,
        val chunksReceived: Int,
        val missingChunks: List<Int>,
        val duplicateChunks: Int,
        val manifestSeen: Boolean,
        val endSeen: Boolean,
        val expectedSha256: String?,
        val complete: Boolean
    )

    data class FileMessage(
        val filename: String,
        val content: ByteArray,
        val profile: BfskProfile,
        val transferId: Int,
        val plan: FileTransferPlan,
        val pcmBytes: ByteArray,
        val frameDurationS: Double,
        val interFrameGapS: Double,
        val leadSilenceS: Double,
        val tailSilenceS: Double,
        val rampMs: Double,
        val sampleRate: Int
    ) {
        val sampleCount: Int get() = pcmBytes.size / 2
        val durationS: Double get() = sampleCount.toDouble() / sampleRate
        val pcmSha256: String get() = sha256Hex(pcmBytes)
        val contentSha256: String get() = sha256Hex(content)
    }

    data class FileReceiveResult(
        val ok: Boolean,
        val errorType: String?,
        val message: String,
        val profile: BfskProfile,
        val stream: PacketStream,
        val state: TransferState?,
        val content: ByteArray?,
        val filename: String?,
        val actualSha256: String?,
        val hashOk: Boolean,
        val conflicts: List<String>,
        val outputPath: String?,
        val sampleRate: Int,
        val sampleCount: Int,
        val durationS: Double,
        val rmsDbfs: Double,
        val peakDbfs: Double,
        val clippedSamples: Int
    ) {
        val crcIsAuthentication: Boolean get() = false
        val sha256IsAuthentication: Boolean get() = false
    }

    /** How many DATA chunks a file of this size needs. */
    fun expectedChunks(fileSize: Int): Int {
        validateFileSize(fileSize)
        if (fileSize == 0) return 0
        return (fileSize + CHUNK_DATA_BYTES - 1) / CHUNK_DATA_BYTES
    }

    /** Validate a filename and return its canonical UTF-8 bytes. */
    fun validateFilename(filename: String): ByteArray {
        if (filename.isEmpty()) throw FilenameError("имя файла не может быть пустым")
        if (filename == "." || filename == "..") {
            throw FilenameError("имя файла не может быть '.' или '..'")
        }
        if (filename.contains('/') || filename.contains('\\')) {
            throw FilenameError("имя файла не может содержать разделители пути")
        }
        if (filename.any { it.code < 32 }) {
            throw FilenameError("имя файла не может содержать управляющие символы")
        }
        if (filename != filename.trim()) {
            throw FilenameError("имя файла не может начинаться или заканчиваться пробелом")
        }
        if (filename.endsWith(".")) {
            throw FilenameError("имя файла не может заканчиваться точкой")
        }
        val stem = filename.substringBefore('.').uppercase(Locale.ROOT)
        if (stem in WINDOWS_RESERVED) {
            throw FilenameError("зарезервированное имя Windows: $filename")
        }
        val encoded = filename.toByteArray(StandardCharsets.UTF_8)
        if (encoded.size !in 1..MAX_FILENAME_BYTES) {
            throw FilenameError(
                "имя файла занимает ${encoded.size} байт UTF-8, " +
                    "допустимо 1..$MAX_FILENAME_BYTES"
            )
        }
        return encoded
    }

    /** Parse a Frame v1 payload as a File Transfer v0 packet. */
    fun parsePacket(raw: ByteArray): FilePacket {
        if (raw.size < 2 || raw[0] != FT_MAGIC[0] || raw[1] != FT_MAGIC[1]) {
            throw NotFileTransferError("нет сигнатуры файлового пакета SF")
        }
        if (raw.size < PACKET_HEADER_BYTES) {
            throw PacketError("пакет короче заголовка")
        }
        val version = raw[2].toInt() and 0xFF
        if (version != FT_VERSION) {
            throw PacketError("версия файлового протокола $version не поддерживается")
        }
        val packetType = raw[3].toInt() and 0xFF
        if (packetType !in PACKET_TYPE_NAMES) {
            throw PacketError("неизвестный тип пакета: $packetType")
        }
        val transferId = raw[4].toInt() and 0xFF
        val seq = uint16(raw, 5)
        val totalChunks = uint16(raw, 7)
        val payloadLen = raw[9].toInt() and 0xFF
        if (raw.size != PACKET_HEADER_BYTES + payloadLen) {
            throw PacketError(
                "payload_len=$payloadLen, а пакет занимает " +
                    "${raw.size - PACKET_HEADER_BYTES} байт"
            )
        }
        if (payloadLen > CHUNK_DATA_BYTES) {
            throw PacketError("payload $payloadLen байт превышает $CHUNK_DATA_BYTES")
        }
        val payload = raw.copyOfRange(PACKET_HEADER_BYTES, raw.size)
        validateTransferId(transferId)
        validateTotalChunks(totalChunks)

        when (packetType) {
            PACKET_MANIFEST -> {
                if (seq != 0) throw ManifestError("у MANIFEST seq должен быть 0")
                val manifest = parseManifestPayload(payload)
                if (expectedChunks(manifest.fileSize) != totalChunks) {
                    throw ManifestError(
                        "MANIFEST: для ${manifest.fileSize} байт нужно " +
                            "${expectedChunks(manifest.fileSize)} чанков, " +
                            "указано $totalChunks"
                    )
                }
            }

            PACKET_DATA -> {
                if (totalChunks == 0) throw PacketError("DATA при total_chunks=0")
                if (seq !in 0 until totalChunks) {
                    throw PacketError("seq=$seq вне диапазона 0..${totalChunks - 1}")
                }
                if (payloadLen == 0) throw PacketError("пустой DATA-пакет")
            }

            else -> {
                if (seq != 0) throw PacketError("у END seq должен быть 0")
                parseEndPayload(payload)
            }
        }

        return FilePacket(packetType, transferId, seq, totalChunks, payload)
    }

    /** Split a file into MANIFEST / DATA / END packets. */
    fun buildTransferPlan(
        data: ByteArray,
        filename: String,
        transferId: Int,
        manifestRepeatEvery: Int = DEFAULT_MANIFEST_REPEAT_EVERY,
        endRepeats: Int = DEFAULT_END_REPEATS
    ): FileTransferPlan {
        validateFileSize(data.size)
        validateTransferId(transferId)
        if (manifestRepeatEvery !in 1..1000) {
            throw FileTransferError("manifest_repeat_every должен быть 1..1000")
        }
        if (endRepeats !in 1..5) {
            throw FileTransferError("end_repeats должен быть 1..5")
        }

        val nameBytes = validateFilename(filename)
        val manifest = Manifest(
            filename = filename,
            filenameBytes = nameBytes,
            fileSize = data.size
        )
        val totalChunks = expectedChunks(data.size)
        val digest = sha256(data)
        val manifestPacket = FilePacket(
            packetType = PACKET_MANIFEST,
            transferId = transferId,
            seq = 0,
            totalChunks = totalChunks,
            payload = manifestPayload(manifest)
        )

        val packets = mutableListOf(manifestPacket)
        for (index in 0 until totalChunks) {
            val from = index * CHUNK_DATA_BYTES
            val to = min((index + 1) * CHUNK_DATA_BYTES, data.size)
            packets.add(
                FilePacket(
                    packetType = PACKET_DATA,
                    transferId = transferId,
                    seq = index,
                    totalChunks = totalChunks,
                    payload = data.copyOfRange(from, to)
                )
            )
            if ((index + 1) % manifestRepeatEvery == 0 && index + 1 < totalChunks) {
                packets.add(manifestPacket)
            }
        }

        val endPacket = FilePacket(
            packetType = PACKET_END,
            transferId = transferId,
            seq = 0,
            totalChunks = totalChunks,
            payload = endPayload(data.size, totalChunks, digest)
        )
        repeat(endRepeats) { packets.add(endPacket) }

        return FileTransferPlan(
            manifest = manifest,
            transferId = transferId,
            totalChunks = totalChunks,
            packets = packets,
            dataBytes = data.copyOf()
        )
    }

    /**
     * Encode a file into one WAV with Frame v1 packets and inter-frame gaps.
     * Creating a WAV is not an acoustic transmission.
     */
    fun encodeFileToWav(
        data: ByteArray,
        filename: String,
        profile: BfskProfile,
        transferId: Int,
        interFrameGapS: Double = DEFAULT_INTER_FRAME_GAP_S,
        leadSilenceS: Double = DEFAULT_LEAD_SILENCE_S,
        tailSilenceS: Double = DEFAULT_TAIL_SILENCE_S,
        rampMs: Double = BfskModulator.DEFAULT_RAMP_MS
    ): Pair<ByteArray, FileMessage> {
        validateDataProfile(profile)
        val gap = validateSeconds(interFrameGapS, "inter_frame_gap_s", 5.0)
        val lead = validateSeconds(leadSilenceS, "lead_silence_s", 10.0)
        val tail = validateSeconds(tailSilenceS, "tail_silence_s", 10.0)
        val plan = buildTransferPlan(data, filename, transferId)

        val gapSamples = (gap * profile.sampleRate).roundToInt()
        val leadSamples = (lead * profile.sampleRate).roundToInt()
        val tailSamples = (tail * profile.sampleRate).roundToInt()

        val modulated = ArrayList<FloatArray>(plan.packets.size)
        var frameDurationS = 0.0
        for (packet in plan.packets) {
            val frame = FrameV1.build(
                packet.toBytes(),
                maxPayloadBytes = DataLinkCodec.MAX_DATA_PAYLOAD_BYTES
            )
            frameDurationS = frame.totalBits / profile.symbolRate
            modulated.add(BfskModulator.bitsToSamples(frame.bits, profile, rampMs = rampMs))
        }

        var totalSamples = leadSamples + tailSamples
        for (samples in modulated) totalSamples += samples.size
        totalSamples += gapSamples * (modulated.size - 1).coerceAtLeast(0)
        val out = FloatArray(totalSamples)
        var cursor = leadSamples
        for ((index, samples) in modulated.withIndex()) {
            System.arraycopy(samples, 0, out, cursor, samples.size)
            cursor += samples.size
            if (index + 1 < modulated.size) cursor += gapSamples
        }

        val pcmBytes = SignalGenerator.toPcm16LittleEndian(out)
        val wavBytes = WavFileHandler.encodePcm16Wav(pcmBytes, profile.sampleRate, 1)
        val message = FileMessage(
            filename = filename,
            content = data.copyOf(),
            profile = profile,
            transferId = transferId,
            plan = plan,
            pcmBytes = pcmBytes,
            frameDurationS = frameDurationS,
            interFrameGapS = gap,
            leadSilenceS = lead,
            tailSilenceS = tail,
            rampMs = rampMs,
            sampleRate = profile.sampleRate
        )
        return wavBytes to message
    }

    /**
     * Find every Frame v1 in a recording and parse it as a file packet.
     * A damaged frame does not stop the scan: the receiver resynchronises on
     * the next preamble. CRC of every frame is checked by the demodulator.
     */
    fun decodePacketsFromSamples(
        samples: FloatArray,
        profile: BfskProfile,
        minPreambleMatches: Int = DataLinkCodec.ACOUSTIC_MIN_PREAMBLE_MATCHES
    ): PacketStream {
        validateDataProfile(profile)
        validateMinPreamble(minPreambleMatches)
        if (samples.isEmpty()) {
            throw FileTransferError("сигнал должен быть непустым")
        }
        for (value in samples) {
            if (!value.isFinite()) {
                throw FileTransferError("сигнал содержит NaN или бесконечность")
            }
        }

        val metric = BfskDemodulator.bitMetric(samples, profile)
        val symbolsPerBit = profile.samplesPerSymbol
        var offset = 0
        val packets = mutableListOf<DecodedFilePacket>()
        val failures = mutableListOf<FrameFailure>()
        val packetErrors = mutableListOf<PacketErrorInfo>()
        var framesFound = 0

        while (offset < metric.size) {
            val start = try {
                BfskDemodulator.findFrameStart(
                    metric,
                    profile,
                    minPreambleMatches,
                    from = offset
                )
            } catch (_: BfskDemodulator.SignalNotFound) {
                break
            }

            val frame = try {
                BfskDemodulator.decodeFromMetric(
                    metric,
                    profile,
                    start,
                    maxPayloadBytes = DataLinkCodec.MAX_DATA_PAYLOAD_BYTES,
                    minPreambleMatches = minPreambleMatches
                )
            } catch (exc: BfskDemodulator.ReceiverError) {
                failures.add(
                    FrameFailure(
                        startSample = start.sample,
                        startTimeS = start.sample.toDouble() / profile.sampleRate,
                        errorType = receiverErrorType(exc),
                        message = exc.message ?: "ошибка приёма кадра"
                    )
                )
                // Пропускаем преамбулу повреждённого кадра и ищем следующий.
                val resync = start.sample + FrameV1.PREAMBLE_BITS * symbolsPerBit
                if (resync >= metric.size) break
                offset = resync
                continue
            }

            framesFound++
            val consumed = frame.startSample + frame.totalBits * symbolsPerBit
            try {
                val packet = parsePacket(frame.payload)
                packets.add(
                    DecodedFilePacket(
                        packet = packet,
                        startSample = frame.startSample,
                        startTimeS = frame.startSample.toDouble() / profile.sampleRate,
                        preambleMatches = frame.preambleMatches,
                        crc = frame.crc
                    )
                )
            } catch (exc: FileTransferError) {
                packetErrors.add(
                    PacketErrorInfo(
                        startSample = frame.startSample,
                        startTimeS = frame.startSample.toDouble() / profile.sampleRate,
                        errorType = exc.errorType,
                        message = exc.message ?: "некорректный файловый пакет"
                    )
                )
            }
            if (consumed <= 0) break
            offset = consumed
        }

        return PacketStream(
            packets = packets,
            frameFailures = failures,
            packetErrors = packetErrors,
            framesFound = framesFound,
            totalSamples = samples.size
        )
    }

    /**
     * Decode a received WAV, assemble the file and verify size and SHA-256.
     * When [outputDir] is provided the file is written only after a full match.
     */
    fun decodeWavToFile(
        wavBytes: ByteArray,
        profile: BfskProfile,
        outputDir: File? = null,
        minPreambleMatches: Int = DataLinkCodec.ACOUSTIC_MIN_PREAMBLE_MATCHES
    ): FileReceiveResult {
        validateDataProfile(profile)
        validateMinPreamble(minPreambleMatches)

        val decoded = try {
            WavFileHandler.decodePcm16Wav(ByteArrayInputStream(wavBytes))
        } catch (exc: Throwable) {
            return wavFailure(
                profile,
                "wav_format_error",
                exc.message ?: "не удалось прочитать WAV"
            )
        }
        if (decoded.metadata.channels != 1) {
            return wavFailure(
                profile,
                "wav_format_error",
                "ожидается моно, в файле ${decoded.metadata.channels} канал(ов)"
            )
        }
        if (decoded.metadata.bitsPerSample != 16) {
            return wavFailure(
                profile,
                "wav_format_error",
                "ожидается PCM16, в файле ${decoded.metadata.bitsPerSample} бит"
            )
        }
        if (decoded.metadata.sampleRate != profile.sampleRate) {
            return wavFailure(
                profile,
                "wav_format_error",
                "ожидается частота ${profile.sampleRate} Гц, " +
                    "в файле ${decoded.metadata.sampleRate} Гц"
            )
        }

        val samples = decoded.samples
        val metrics = recordingMetrics(samples, profile.sampleRate)
        val stream = try {
            decodePacketsFromSamples(samples, profile, minPreambleMatches)
        } catch (exc: BfskDemodulator.ReceiverError) {
            return baseResult(
                profile = profile,
                metrics = metrics,
                stream = emptyStream(samples.size),
                errorType = receiverErrorType(exc),
                message = exc.message ?: "не удалось разобрать запись"
            )
        } catch (exc: FileTransferError) {
            return baseResult(
                profile = profile,
                metrics = metrics,
                stream = emptyStream(samples.size),
                errorType = exc.errorType,
                message = exc.message ?: "не удалось разобрать запись"
            )
        }

        if (stream.framesFound == 0) {
            return baseResult(
                profile = profile,
                metrics = metrics,
                stream = stream,
                errorType = "signal_not_found",
                message = "ни одного кадра Frame v1 не найдено"
            )
        }
        if (stream.packets.isEmpty()) {
            val notFile = stream.packetErrors.isNotEmpty() &&
                stream.packetErrors.all { it.errorType == "not_file_transfer" }
            return baseResult(
                profile = profile,
                metrics = metrics,
                stream = stream,
                errorType = if (notFile) "not_file_transfer" else "packet_invalid",
                message = if (notFile) {
                    "кадры найдены, но это не файловые пакеты"
                } else {
                    "ни одного корректного файлового пакета"
                }
            )
        }

        val assembler = FileAssembler()
        val conflicts = mutableListOf<String>()
        for (item in stream.packets) {
            try {
                assembler.add(item.packet)
            } catch (exc: FileTransferError) {
                conflicts.add(exc.message ?: "конфликт пакетов")
            }
        }
        val state = assembler.state()
        if (conflicts.isNotEmpty()) {
            return baseResult(
                profile = profile,
                metrics = metrics,
                stream = stream,
                state = state,
                conflicts = conflicts,
                errorType = "transfer_conflict",
                message = conflicts.joinToString("; ")
            )
        }
        if (!state.complete) {
            val detail = if (state.totalChunks != null) {
                "чанков ${state.chunksReceived} из ${state.totalChunks}"
            } else {
                "нет манифеста или END"
            }
            val missing = state.missingChunks.size
            return baseResult(
                profile = profile,
                metrics = metrics,
                stream = stream,
                state = state,
                errorType = "incomplete_transfer",
                message = "передача не завершена: $detail" +
                    if (missing > 0) ", отсутствуют $missing" else ""
            )
        }

        val content = try {
            assembler.assemble()
        } catch (exc: IncompleteTransferError) {
            return baseResult(
                profile = profile,
                metrics = metrics,
                stream = stream,
                state = state,
                errorType = "incomplete_transfer",
                message = exc.message ?: "передача не завершена"
            )
        } catch (exc: HashMismatchError) {
            return baseResult(
                profile = profile,
                metrics = metrics,
                stream = stream,
                state = state,
                errorType = "hash_mismatch",
                message = exc.message ?: "SHA-256 не совпал"
            )
        }

        val manifest = assembler.manifest ?: return baseResult(
            profile = profile,
            metrics = metrics,
            stream = stream,
            state = state,
            errorType = "incomplete_transfer",
            message = "нет манифеста"
        )
        val actualSha256 = sha256Hex(content)
        val target = if (outputDir != null) {
            try {
                safeOutputFile(outputDir, manifest.filename)
            } catch (exc: FileTransferError) {
                return baseResult(
                    profile = profile,
                    metrics = metrics,
                    stream = stream,
                    state = state,
                    content = content,
                    filename = manifest.filename,
                    actualSha256 = actualSha256,
                    hashOk = true,
                    errorType = "output_error",
                    message = exc.message ?: "не удалось собрать путь файла"
                )
            }
        } else {
            null
        }
        if (target != null) {
            try {
                target.parentFile?.mkdirs()
                target.writeBytes(content)
            } catch (exc: Throwable) {
                return baseResult(
                    profile = profile,
                    metrics = metrics,
                    stream = stream,
                    state = state,
                    content = content,
                    filename = manifest.filename,
                    actualSha256 = actualSha256,
                    hashOk = true,
                    errorType = "output_error",
                    message = exc.message ?: "не удалось сохранить файл"
                )
            }
        }

        return FileReceiveResult(
            ok = true,
            errorType = null,
            message = "Файл собран, размер и SHA-256 совпали",
            profile = profile,
            stream = stream,
            state = state,
            content = content,
            filename = manifest.filename,
            actualSha256 = actualSha256,
            hashOk = true,
            conflicts = emptyList(),
            outputPath = target?.path,
            sampleRate = profile.sampleRate,
            sampleCount = samples.size,
            durationS = metrics.durationS,
            rmsDbfs = metrics.rmsDbfs,
            peakDbfs = metrics.peakDbfs,
            clippedSamples = metrics.clippedSamples
        )
    }

    /** Assemble the file from packets, checking field consistency. */
    class FileAssembler {
        var transferId: Int? = null
            private set
        var manifest: Manifest? = null
            private set
        var totalChunks: Int? = null
            private set
        var end: EndInfo? = null
            private set
        var duplicates: Int = 0
            private set

        private val chunks = LinkedHashMap<Int, ByteArray>()

        fun add(packet: FilePacket) {
            if (transferId == null) {
                transferId = packet.transferId
            } else if (packet.transferId != transferId) {
                throw TransferConflictError(
                    "transfer_id ${packet.transferId} не совпадает с $transferId"
                )
            }
            if (totalChunks == null) {
                totalChunks = packet.totalChunks
            } else if (packet.totalChunks != totalChunks) {
                throw TransferConflictError(
                    "total_chunks ${packet.totalChunks} не совпадает с $totalChunks"
                )
            }

            when (packet.packetType) {
                PACKET_MANIFEST -> addManifest(packet)
                PACKET_DATA -> addData(packet)
                else -> addEnd(packet)
            }
        }

        private fun addManifest(packet: FilePacket) {
            val candidate = parseManifestPayload(packet.payload)
            val current = manifest
            if (current != null && !sameManifest(current, candidate)) {
                throw TransferConflictError("повтор MANIFEST отличается от первого")
            }
            manifest = candidate
            for ((seq, chunk) in chunks) {
                validateChunkLength(seq, chunk)
            }
        }

        private fun addData(packet: FilePacket) {
            val expectedTotal = totalChunks
            if (expectedTotal == null || packet.totalChunks != expectedTotal) {
                throw TransferConflictError("не согласовано число чанков")
            }
            if (packet.seq !in 0 until packet.totalChunks) {
                throw TransferConflictError("seq=${packet.seq} вне диапазона")
            }
            val chunk = packet.payload
            if (chunk.size !in 1..CHUNK_DATA_BYTES) {
                throw TransferConflictError("чанк ${packet.seq} имеет длину ${chunk.size}")
            }
            if (manifest != null) {
                validateChunkLength(packet.seq, chunk)
            }
            val previous = chunks[packet.seq]
            if (previous != null) {
                if (!previous.contentEquals(chunk)) {
                    throw TransferConflictError(
                        "чанк ${packet.seq} повторён с другими данными"
                    )
                }
                duplicates++
                return
            }
            chunks[packet.seq] = chunk.copyOf()
        }

        private fun validateChunkLength(seq: Int, chunk: ByteArray) {
            val currentManifest = manifest ?: return
            val total = totalChunks
            if (total == null || seq !in 0 until total) {
                throw TransferConflictError("seq=$seq вне диапазона")
            }
            val offset = seq * currentManifest.chunkDataBytes
            val expected = min(
                currentManifest.chunkDataBytes,
                currentManifest.fileSize - offset
            )
            if (expected <= 0 || chunk.size != expected) {
                throw TransferConflictError(
                    "чанк $seq: длина ${chunk.size}, ожидается $expected"
                )
            }
        }

        private fun addEnd(packet: FilePacket) {
            val info = parseEndPayload(packet.payload)
            val currentManifest = manifest
            if (currentManifest != null && info.fileSize != currentManifest.fileSize) {
                throw TransferConflictError("END: размер файла не совпал")
            }
            if (totalChunks != null && info.totalChunks != totalChunks) {
                throw TransferConflictError("END: число чанков не совпало")
            }
            val currentEnd = end
            if (currentEnd != null && !sameEnd(currentEnd, info)) {
                throw TransferConflictError("повтор END отличается от первого")
            }
            end = info
        }

        val missingChunks: List<Int>
            get() {
                val total = totalChunks ?: return emptyList()
                return (0 until total).filter { it !in chunks }
            }

        val complete: Boolean
            get() = manifest != null && end != null && totalChunks != null &&
                missingChunks.isEmpty()

        fun state(): TransferState = TransferState(
            transferId = transferId,
            manifest = manifest,
            totalChunks = totalChunks,
            chunksReceived = chunks.size,
            missingChunks = missingChunks,
            duplicateChunks = duplicates,
            manifestSeen = manifest != null,
            endSeen = end != null,
            expectedSha256 = end?.sha256?.joinToString("") { "%02x".format(it) },
            complete = complete
        )

        /** Concatenate chunks and verify length and SHA-256. */
        fun assemble(): ByteArray {
            val currentManifest = manifest
            val currentEnd = end
            val total = totalChunks
            if (!complete || currentManifest == null || currentEnd == null || total == null) {
                throw IncompleteTransferError(
                    "передача не завершена: чанков ${chunks.size} из $totalChunks"
                )
            }
            val content = ByteArray(currentManifest.fileSize)
            var cursor = 0
            for (seq in 0 until total) {
                val chunk = chunks[seq] ?: throw IncompleteTransferError(
                    "нет чанка $seq"
                )
                chunk.copyInto(content, cursor)
                cursor += chunk.size
            }
            if (content.size != currentManifest.fileSize) {
                throw IncompleteTransferError(
                    "собрано ${content.size} байт вместо ${currentManifest.fileSize}"
                )
            }
            val actual = sha256(content)
            if (!actual.contentEquals(currentEnd.sha256)) {
                throw HashMismatchError(
                    "SHA-256 не совпал: получен ${toHex(actual)}, " +
                        "ожидался ${toHex(currentEnd.sha256)}"
                )
            }
            return content
        }
    }

    /** Build the TX JSON report for a generated file WAV. */
    fun buildTransmitReport(
        message: FileMessage,
        wavFilename: String,
        wavBytes: Int,
        wavSha256: String
    ): String {
        val root = JSONObject()
        root.put("schema", REPORT_SCHEMA)
        root.put("direction", "tx")
        root.put("timestamp", timestampUtc())
        root.put("result", "ok")
        root.put("error_type", JSONObject.NULL)
        root.put("message", "WAV создан; воспроизведение ещё не является приёмом")
        root.put("profile", profileJson(message.profile))
        root.put("file", JSONObject().apply {
            put("filename", message.filename)
            put("size", message.content.size)
            put("sha256", message.contentSha256)
        })
        root.put("transfer", transferPlanJson(message))
        root.put("timing", JSONObject().apply {
            put("frame_duration_s", message.frameDurationS)
            put("inter_frame_gap_s", message.interFrameGapS)
            put("lead_silence_s", message.leadSilenceS)
            put("tail_silence_s", message.tailSilenceS)
            put("total_duration_s", message.durationS)
        })
        root.put("wav", JSONObject().apply {
            put("filename", wavFilename)
            put("bytes", wavBytes)
            put("sha256", wavSha256)
            put("pcm_sha256", message.pcmSha256)
        })
        root.put("crc_is_authentication", false)
        root.put("sha256_is_authentication", false)
        return root.toString(2)
    }

    /** Build the RX JSON report including stream, transfer state and result. */
    fun buildReceiveReport(
        result: FileReceiveResult,
        wavFilename: String?,
        wavSha256: String?,
        minPreambleMatches: Int = DataLinkCodec.ACOUSTIC_MIN_PREAMBLE_MATCHES
    ): String {
        val root = JSONObject()
        root.put("schema", REPORT_SCHEMA)
        root.put("direction", "rx")
        root.put("timestamp", timestampUtc())
        root.put("profile", profileJson(result.profile))
        root.put("min_preamble_matches", minPreambleMatches)
        root.put("max_file_bytes", MAX_FILE_BYTES)
        root.put("chunk_data_bytes", CHUNK_DATA_BYTES)
        root.put("wav", JSONObject().apply {
            put("filename", wavFilename ?: JSONObject.NULL)
            put("sha256", wavSha256 ?: JSONObject.NULL)
        })
        root.put("crc_is_authentication", false)
        root.put("sha256_is_authentication", false)
        root.put("recording", JSONObject().apply {
            put("sample_rate_hz", result.sampleRate)
            put("samples", result.sampleCount)
            put("duration_s", result.durationS)
            put("rms_dbfs", result.rmsDbfs)
            put("peak_dbfs", result.peakDbfs)
            put("clipped_samples", result.clippedSamples)
            put("clip_threshold", CLIP_THRESHOLD)
        })
        root.put("result", if (result.ok) "ok" else "error")
        root.put("error_type", result.errorType ?: JSONObject.NULL)
        root.put("message", result.message)
        root.put("stream", streamJson(result.stream))

        result.state?.let { state ->
            val transfer = JSONObject()
            transfer.put("transfer_id", state.transferId ?: JSONObject.NULL)
            transfer.put("filename", state.manifest?.filename ?: JSONObject.NULL)
            transfer.put("file_size", state.manifest?.fileSize ?: JSONObject.NULL)
            transfer.put("total_chunks", state.totalChunks ?: JSONObject.NULL)
            transfer.put("chunks_received", state.chunksReceived)
            transfer.put("missing_chunks", JSONArray(state.missingChunks))
            transfer.put("duplicate_chunks", state.duplicateChunks)
            transfer.put("manifest_seen", state.manifestSeen)
            transfer.put("end_seen", state.endSeen)
            transfer.put("expected_sha256", state.expectedSha256 ?: JSONObject.NULL)
            transfer.put("complete", state.complete)
            transfer.put("hash_ok", result.hashOk)
            transfer.put("actual_sha256", result.actualSha256 ?: JSONObject.NULL)
            transfer.put("output_path", result.outputPath ?: JSONObject.NULL)
            if (result.conflicts.isNotEmpty()) {
                transfer.put("conflicts", JSONArray(result.conflicts))
            }
            root.put("transfer", transfer)
        }

        if (result.ok && result.filename != null) {
            root.put("file", JSONObject().apply {
                put("filename", result.filename)
                put("size", result.content?.size ?: 0)
                put("sha256", result.actualSha256 ?: JSONObject.NULL)
            })
        }
        return root.toString(2)
    }

    /** Recommended record duration for a WAV of [durationS] plus a margin. */
    fun recommendedRecordSeconds(durationS: Double, marginS: Double = 3.0): Double {
        require(durationS.isFinite() && durationS >= 0.0) {
            "длительность WAV должна быть неотрицательной"
        }
        return min(300.0, maxOf(1.0, durationS + marginS))
    }

    fun sha256(bytes: ByteArray): ByteArray =
        MessageDigest.getInstance("SHA-256").digest(bytes)

    fun sha256Hex(bytes: ByteArray): String = toHex(sha256(bytes))

    // ---------------------------------------------------------------------
    // Packet payloads
    // ---------------------------------------------------------------------

    private fun manifestPayload(manifest: Manifest): ByteArray {
        val name = manifest.filenameBytes
        val out = ByteArray(1 + name.size + 4 + 1)
        out[0] = name.size.toByte()
        name.copyInto(out, 1)
        val size = manifest.fileSize
        out[1 + name.size] = ((size shr 24) and 0xFF).toByte()
        out[2 + name.size] = ((size shr 16) and 0xFF).toByte()
        out[3 + name.size] = ((size shr 8) and 0xFF).toByte()
        out[4 + name.size] = (size and 0xFF).toByte()
        out[5 + name.size] = manifest.chunkDataBytes.toByte()
        return out
    }

    private fun parseManifestPayload(payload: ByteArray): Manifest {
        if (payload.size < 6) throw ManifestError("MANIFEST короче минимальной длины")
        val nameLen = payload[0].toInt() and 0xFF
        if (nameLen !in 1..MAX_FILENAME_BYTES) {
            throw ManifestError("недопустимая длина имени: $nameLen")
        }
        val endOfName = 1 + nameLen
        if (payload.size < endOfName + 5) throw ManifestError("MANIFEST обрезан")
        if (payload.size != endOfName + 5) {
            throw ManifestError("MANIFEST содержит лишние байты")
        }
        val nameBytes = payload.copyOfRange(1, endOfName)
        val filename = try {
            decodeUtf8Strict(nameBytes)
        } catch (exc: CharacterCodingException) {
            throw ManifestError("имя файла не UTF-8: ${exc.message}")
        }
        val canonical = try {
            validateFilename(filename)
        } catch (exc: FilenameError) {
            throw ManifestError(exc.message ?: "недопустимое имя файла")
        }
        if (!canonical.contentEquals(nameBytes)) {
            throw ManifestError("имя файла не в канонической форме UTF-8")
        }
        val fileSize = uint32(payload, endOfName)
        val chunkDataBytes = payload[endOfName + 4].toInt() and 0xFF
        if (chunkDataBytes != CHUNK_DATA_BYTES) {
            throw ManifestError(
                "chunk_data_bytes=$chunkDataBytes, ожидается $CHUNK_DATA_BYTES"
            )
        }
        try {
            validateFileSize(fileSize)
        } catch (exc: FileTransferError) {
            throw ManifestError(exc.message ?: "недопустимый размер файла")
        }
        return Manifest(filename, nameBytes, fileSize, chunkDataBytes)
    }

    private fun endPayload(fileSize: Int, totalChunks: Int, sha256: ByteArray): ByteArray {
        if (sha256.size != 32) throw FileTransferError("SHA-256 должен быть длиной 32 байта")
        val out = ByteArray(38)
        out[0] = ((fileSize shr 24) and 0xFF).toByte()
        out[1] = ((fileSize shr 16) and 0xFF).toByte()
        out[2] = ((fileSize shr 8) and 0xFF).toByte()
        out[3] = (fileSize and 0xFF).toByte()
        out[4] = ((totalChunks shr 8) and 0xFF).toByte()
        out[5] = (totalChunks and 0xFF).toByte()
        sha256.copyInto(out, 6)
        return out
    }

    private fun parseEndPayload(payload: ByteArray): EndInfo {
        if (payload.size != 38) {
            throw PacketError("END должен быть 38 байт, получено ${payload.size}")
        }
        val fileSize = uint32(payload, 0)
        val totalChunks = uint16(payload, 4)
        try {
            validateFileSize(fileSize)
            validateTotalChunks(totalChunks)
        } catch (exc: FileTransferError) {
            throw PacketError(exc.message ?: "недопустимый END")
        }
        if (expectedChunks(fileSize) != totalChunks) {
            throw PacketError(
                "END: для $fileSize байт нужно ${expectedChunks(fileSize)} " +
                    "чанков, указано $totalChunks"
            )
        }
        return EndInfo(fileSize, totalChunks, payload.copyOfRange(6, 38))
    }

    // ---------------------------------------------------------------------
    // Validation and helpers
    // ---------------------------------------------------------------------

    private fun validateFileSize(fileSize: Int) {
        if (fileSize !in 0..MAX_FILE_BYTES) {
            throw FileTooLargeError(
                "файл $fileSize байт не входит в лимит v0 0..$MAX_FILE_BYTES"
            )
        }
    }

    private fun validateTransferId(transferId: Int) {
        if (transferId !in 0..0xFF) {
            throw FileTransferError("transfer_id должен быть в диапазоне 0..255")
        }
    }

    private fun validateTotalChunks(totalChunks: Int) {
        if (totalChunks !in 0..MAX_CHUNKS) {
            throw PacketError("total_chunks должен быть в диапазоне 0..$MAX_CHUNKS")
        }
    }

    private fun validateDataProfile(profile: BfskProfile) {
        if (!BfskProfile.dataProfiles().containsKey(profile.name)) {
            throw FileTransferError(
                "профиль должен быть data-audible или data-robust",
                "profile_invalid"
            )
        }
    }

    private fun validateMinPreamble(value: Int) {
        if (value !in 1..FrameV1.PREAMBLE_BITS) {
            throw FileTransferError(
                "min_preamble_matches должен быть в диапазоне 1..${FrameV1.PREAMBLE_BITS}",
                "profile_invalid"
            )
        }
    }

    private fun validateSeconds(value: Double, fieldName: String, maximum: Double): Double {
        if (!value.isFinite() || value < 0.0 || value > maximum) {
            throw FileTransferError(
                "$fieldName должен быть в диапазоне 0..${maximum.toInt()} с"
            )
        }
        return value
    }

    private fun safeOutputFile(outDir: File, filename: String): File {
        val name = validateFilename(filename).toString(StandardCharsets.UTF_8)
        val root = outDir.canonicalFile
        val target = File(root, name).canonicalFile
        if (target.parentFile != root) {
            throw FilenameError("имя файла выводит за пределы каталога назначения")
        }
        return target
    }

    private fun sameManifest(left: Manifest, right: Manifest): Boolean =
        left.filename == right.filename &&
            left.fileSize == right.fileSize &&
            left.chunkDataBytes == right.chunkDataBytes &&
            left.filenameBytes.contentEquals(right.filenameBytes)

    private fun sameEnd(left: EndInfo, right: EndInfo): Boolean =
        left.fileSize == right.fileSize &&
            left.totalChunks == right.totalChunks &&
            left.sha256.contentEquals(right.sha256)

    private fun decodeUtf8Strict(bytes: ByteArray): String {
        val decoder = StandardCharsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
        return decoder.decode(ByteBuffer.wrap(bytes)).toString()
    }

    private fun uint16(data: ByteArray, offset: Int): Int =
        ((data[offset].toInt() and 0xFF) shl 8) or (data[offset + 1].toInt() and 0xFF)

    private fun uint32(data: ByteArray, offset: Int): Int =
        ((data[offset].toInt() and 0xFF) shl 24) or
            ((data[offset + 1].toInt() and 0xFF) shl 16) or
            ((data[offset + 2].toInt() and 0xFF) shl 8) or
            (data[offset + 3].toInt() and 0xFF)

    private fun toHex(bytes: ByteArray): String =
        bytes.joinToString("") { "%02x".format(it) }

    private fun receiverErrorType(exc: BfskDemodulator.ReceiverError): String = when (exc) {
        is BfskDemodulator.SignalNotFound -> "signal_not_found"
        is BfskDemodulator.SyncWordError -> "sync_mismatch"
        is BfskDemodulator.FrameVersionError -> "version_unsupported"
        is BfskDemodulator.FrameLengthError -> "length_invalid"
        is BfskDemodulator.IncompleteFrame -> "incomplete_frame"
        is BfskDemodulator.CrcError -> "crc_mismatch"
        else -> "receiver_error"
    }

    private data class RecordingMetrics(
        val durationS: Double,
        val rmsDbfs: Double,
        val peakDbfs: Double,
        val clippedSamples: Int
    )

    private fun recordingMetrics(samples: FloatArray, sampleRate: Int): RecordingMetrics {
        if (samples.isEmpty()) return RecordingMetrics(0.0, -999.0, -999.0, 0)
        var sumSquares = 0.0
        var peak = 0.0
        var clipped = 0
        for (value in samples) {
            val magnitude = abs(value.toDouble())
            sumSquares += magnitude * magnitude
            if (magnitude > peak) peak = magnitude
            if (magnitude >= CLIP_THRESHOLD) clipped++
        }
        val rms = sqrt(sumSquares / samples.size)
        return RecordingMetrics(
            durationS = samples.size.toDouble() / sampleRate,
            rmsDbfs = dbfs(rms),
            peakDbfs = dbfs(peak),
            clippedSamples = clipped
        )
    }

    private fun dbfs(value: Double): Double =
        if (value <= 0.0) -999.0 else 20.0 * log10(value)

    private fun emptyStream(totalSamples: Int): PacketStream = PacketStream(
        packets = emptyList(),
        frameFailures = emptyList(),
        packetErrors = emptyList(),
        framesFound = 0,
        totalSamples = totalSamples
    )

    private fun wavFailure(
        profile: BfskProfile,
        errorType: String,
        message: String
    ): FileReceiveResult = FileReceiveResult(
        ok = false,
        errorType = errorType,
        message = message,
        profile = profile,
        stream = emptyStream(0),
        state = null,
        content = null,
        filename = null,
        actualSha256 = null,
        hashOk = false,
        conflicts = emptyList(),
        outputPath = null,
        sampleRate = profile.sampleRate,
        sampleCount = 0,
        durationS = 0.0,
        rmsDbfs = -999.0,
        peakDbfs = -999.0,
        clippedSamples = 0
    )

    private fun baseResult(
        profile: BfskProfile,
        metrics: RecordingMetrics,
        stream: PacketStream,
        state: TransferState? = null,
        content: ByteArray? = null,
        filename: String? = null,
        actualSha256: String? = null,
        hashOk: Boolean = false,
        conflicts: List<String> = emptyList(),
        errorType: String,
        message: String
    ): FileReceiveResult = FileReceiveResult(
        ok = false,
        errorType = errorType,
        message = message,
        profile = profile,
        stream = stream,
        state = state,
        content = content,
        filename = filename,
        actualSha256 = actualSha256,
        hashOk = hashOk,
        conflicts = conflicts,
        outputPath = null,
        sampleRate = profile.sampleRate,
        sampleCount = stream.totalSamples,
        durationS = metrics.durationS,
        rmsDbfs = metrics.rmsDbfs,
        peakDbfs = metrics.peakDbfs,
        clippedSamples = metrics.clippedSamples
    )

    private fun transferPlanJson(message: FileMessage): JSONObject {
        val counts = JSONObject()
        counts.put("manifest", 0)
        counts.put("data", 0)
        counts.put("end", 0)
        for (packet in message.plan.packets) {
            counts.put(packet.typeName, counts.optInt(packet.typeName) + 1)
        }
        return JSONObject().apply {
            put("transfer_id", message.transferId)
            put("manifest", JSONObject().apply {
                put("filename", message.plan.manifest.filename)
                put("filename_bytes", message.plan.manifest.filenameBytes.size)
                put("file_size", message.plan.manifest.fileSize)
                put("chunk_data_bytes", message.plan.manifest.chunkDataBytes)
            })
            put("total_chunks", message.plan.totalChunks)
            put("packet_count", message.plan.packetCount)
            put("packet_types", counts)
        }
    }

    private fun streamJson(stream: PacketStream): JSONObject = JSONObject().apply {
        put("frames_found", stream.framesFound)
        put("packets_decoded", stream.packets.size)
        put("frame_failures", JSONArray().apply {
            for (item in stream.frameFailures) {
                put(JSONObject().apply {
                    put("start_sample", item.startSample)
                    put("start_time_s", item.startTimeS)
                    put("error_type", item.errorType)
                    put("message", item.message)
                })
            }
        })
        put("packet_errors", JSONArray().apply {
            for (item in stream.packetErrors) {
                put(JSONObject().apply {
                    put("start_sample", item.startSample)
                    put("start_time_s", item.startTimeS)
                    put("error_type", item.errorType)
                    put("message", item.message)
                })
            }
        })
    }

    private fun profileJson(profile: BfskProfile): JSONObject = JSONObject().apply {
        put("name", profile.name)
        put("sample_rate", profile.sampleRate)
        put("mark_hz", profile.markHz)
        put("space_hz", profile.spaceHz)
        put("symbol_rate", profile.symbolRate)
        put("amplitude", profile.amplitude)
        put("samples_per_symbol", profile.samplesPerSymbol)
        put("bit_rate_bps", profile.bitRateBps)
    }

    private fun timestampUtc(): String {
        val formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
        formatter.timeZone = TimeZone.getTimeZone("UTC")
        return formatter.format(Date())
    }
}
