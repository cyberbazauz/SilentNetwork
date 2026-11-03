package org.silentnetwork.app.audiolab.datalink

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
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Acoustic Data Link v0: UTF-8 text -> Frame v1 -> BFSK -> PCM/WAV and back.
 *
 * Payload limit for v0 is 64 bytes UTF-8. Frame v1 and CRC-16 are not
 * modified. CRC-16 detects corruption but is **not** authentication: do not
 * send secrets at this stage.
 */
object DataLinkCodec {
    const val REPORT_SCHEMA = "silentnetwork.acoustic-lab.data-link/1"
    const val MAX_DATA_PAYLOAD_BYTES = 64
    const val ACOUSTIC_MIN_PREAMBLE_MATCHES = 28
    const val DEFAULT_LEAD_SILENCE_S = 1.0
    const val DEFAULT_TAIL_SILENCE_S = 0.5
    const val MAX_SILENCE_S = 10.0
    const val CLIP_THRESHOLD = 0.999

    open class DataLinkError(message: String, val errorType: String = "data_link_error") :
        IllegalArgumentException(message)

    class PayloadError(message: String) :
        DataLinkError(message, errorType = "payload_invalid")

    class ProfileError(message: String) :
        DataLinkError(message, errorType = "profile_invalid")

    data class DataMessage(
        val text: String,
        val profile: BfskProfile,
        val payload: ByteArray,
        val frame: FrameV1.Frame,
        val pcmBytes: ByteArray,
        val sampleRate: Int,
        val leadSilenceS: Double,
        val tailSilenceS: Double,
        val rampMs: Double,
        val peakAmplitude: Double
    ) {
        val sampleCount: Int get() = pcmBytes.size / 2
        val durationS: Double get() = sampleCount.toDouble() / sampleRate
        val pcmSha256: String get() = sha256Hex(pcmBytes)
    }

    data class ReceivedMessage(
        val ok: Boolean,
        val profile: BfskProfile,
        val text: String? = null,
        val errorType: String? = null,
        val message: String? = null,
        val payloadHex: String? = null,
        val payloadBytes: Int? = null,
        val payloadSha256: String? = null,
        val crcOk: Boolean = false,
        val preambleMatches: Int? = null,
        val startSample: Int? = null,
        val startTimeS: Double? = null,
        val frameVersion: Int? = null,
        val crcHex: String? = null,
        val totalBits: Int? = null,
        val sampleRate: Int,
        val sampleCount: Int,
        val durationS: Double,
        val rmsDbfs: Double,
        val peakDbfs: Double,
        val clippedSamples: Int
    ) {
        val crcIsAuthentication: Boolean get() = false
    }

    /** Convert text to UTF-8 payload and enforce the v0 limit. */
    fun textToPayload(text: String): ByteArray {
        if (text.isEmpty()) throw PayloadError("сообщение пустое: передавать нечего")
        val payload = text.toByteArray(Charsets.UTF_8)
        if (payload.isEmpty()) throw PayloadError("сообщение пустое: передавать нечего")
        if (payload.size > MAX_DATA_PAYLOAD_BYTES) {
            throw PayloadError(
                "сообщение занимает ${payload.size} байт UTF-8, " +
                    "лимит $MAX_DATA_PAYLOAD_BYTES байт"
            )
        }
        return payload
    }

    /** Strict UTF-8 decode: invalid bytes map to `payload_utf8_error`, not to U+FFFD. */
    fun payloadToText(payload: ByteArray): String {
        if (payload.isEmpty()) throw PayloadError("payload пуст")
        if (payload.size > MAX_DATA_PAYLOAD_BYTES) {
            throw PayloadError(
                "payload ${payload.size} байт превышает лимит " +
                    "$MAX_DATA_PAYLOAD_BYTES байт"
            )
        }
        return try {
            val decoder = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
            decoder.decode(ByteBuffer.wrap(payload)).toString()
        } catch (exc: CharacterCodingException) {
            throw PayloadError("payload не является корректным UTF-8: ${exc.message}")
        }
    }

    /** Build PCM16 mono bytes for the message (with lead/tail silence). */
    fun encodeToPcm(
        text: String,
        profile: BfskProfile,
        leadSilenceS: Double = DEFAULT_LEAD_SILENCE_S,
        tailSilenceS: Double = DEFAULT_TAIL_SILENCE_S,
        rampMs: Double = BfskModulator.DEFAULT_RAMP_MS
    ): DataMessage {
        validateProfile(profile)
        val lead = validateSilence(leadSilenceS, "lead_silence_s")
        val tail = validateSilence(tailSilenceS, "tail_silence_s")
        val payload = textToPayload(text)
        val frame = FrameV1.build(payload, maxPayloadBytes = MAX_DATA_PAYLOAD_BYTES)

        val modulated = BfskModulator.bitsToSamples(frame.bits, profile, rampMs = rampMs)
        val leadSamples = (lead * profile.sampleRate).toInt()
        val tailSamples = (tail * profile.sampleRate).toInt()
        val total = leadSamples + modulated.size + tailSamples
        val out = FloatArray(total)
        System.arraycopy(modulated, 0, out, leadSamples, modulated.size)

        var peak = 0.0
        for (value in out) {
            val magnitude = abs(value.toDouble())
            if (magnitude > peak) peak = magnitude
        }

        return DataMessage(
            text = text,
            profile = profile,
            payload = payload,
            frame = frame,
            pcmBytes = SignalGenerator.toPcm16LittleEndian(out),
            sampleRate = profile.sampleRate,
            leadSilenceS = lead,
            tailSilenceS = tail,
            rampMs = rampMs,
            peakAmplitude = peak
        )
    }

    /** Encode the message to WAV bytes. */
    fun encodeToWav(
        text: String,
        profile: BfskProfile,
        leadSilenceS: Double = DEFAULT_LEAD_SILENCE_S,
        tailSilenceS: Double = DEFAULT_TAIL_SILENCE_S,
        rampMs: Double = BfskModulator.DEFAULT_RAMP_MS
    ): Pair<ByteArray, DataMessage> {
        val message = encodeToPcm(text, profile, leadSilenceS, tailSilenceS, rampMs)
        val wav = WavFileHandler.encodePcm16Wav(message.pcmBytes, message.sampleRate, 1)
        return wav to message
    }

    /** Decode PCM16 mono samples into a received message. */
    fun decodeFromPcm(
        pcmBytes: ByteArray,
        profile: BfskProfile,
        minPreambleMatches: Int = FrameV1.PREAMBLE_BITS
    ): ReceivedMessage {
        validateProfile(profile)
        validateMinPreamble(minPreambleMatches)
        val samples = SignalGenerator.fromPcm16LittleEndian(pcmBytes)
        return decodeSamples(samples, profile, minPreambleMatches)
    }

    /** Decode WAV bytes; header problems are reported as wav_format_error. */
    fun decodeFromWav(
        wavBytes: ByteArray,
        profile: BfskProfile,
        minPreambleMatches: Int = FrameV1.PREAMBLE_BITS
    ): ReceivedMessage {
        validateProfile(profile)
        validateMinPreamble(minPreambleMatches)
        val decoded = try {
            WavFileHandler.decodePcm16Wav(ByteArrayInputStream(wavBytes))
        } catch (exc: Throwable) {
            return formatError(profile, "wav_format_error", exc.message ?: "неверный WAV")
        }
        if (decoded.metadata.channels != 1) {
            return formatError(
                profile,
                "wav_format_error",
                "ожидается моно, в файле ${decoded.metadata.channels} канал(ов)"
            )
        }
        if (decoded.metadata.bitsPerSample != 16) {
            return formatError(
                profile,
                "wav_format_error",
                "ожидается PCM16, в файле ${decoded.metadata.bitsPerSample} бит"
            )
        }
        if (decoded.metadata.sampleRate != profile.sampleRate) {
            return formatError(
                profile,
                "wav_format_error",
                "ожидается частота ${profile.sampleRate} Гц, " +
                    "в файле ${decoded.metadata.sampleRate} Гц"
            )
        }
        return decodeSamples(decoded.samples, profile, minPreambleMatches)
    }

    fun decodeWavFile(
        file: File,
        profile: BfskProfile,
        minPreambleMatches: Int = FrameV1.PREAMBLE_BITS
    ): ReceivedMessage {
        if (!file.isFile) {
            return formatError(profile, "wav_not_found", "WAV не найден: ${file.name}")
        }
        return try {
            decodeFromWav(file.readBytes(), profile, minPreambleMatches)
        } catch (exc: Throwable) {
            formatError(profile, "wav_format_error", exc.message ?: "не удалось прочитать WAV")
        }
    }

    fun sha256Hex(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        return digest.joinToString("") { "%02x".format(it) }
    }

    fun buildTransmitReport(message: DataMessage, wavFilename: String, wavSha256: String): String {
        val root = JSONObject()
        root.put("schema", REPORT_SCHEMA)
        root.put("direction", "tx")
        root.put("timestamp", timestampUtc())
        root.put("result", "ok")
        root.put("error_type", JSONObject.NULL)
        root.put("message", "WAV создан; воспроизведение ещё не является приёмом")
        root.put("profile", profileJson(message.profile))
        root.put("text", message.text)
        root.put("payload_hex", message.payload.joinToString("") { "%02x".format(it) })
        root.put("payload_bytes", message.payload.size)
        root.put("payload_sha256", message.frame.payloadSha256)
        root.put("frame", frameJson(message.frame))
        root.put("signal", signalJson(message))
        root.put("wav", wavJson(wavFilename, wavSha256))
        root.put("crc_is_authentication", false)
        return root.toString(2)
    }

    fun buildReceiveReport(
        received: ReceivedMessage,
        wavFilename: String?,
        wavSha256: String?,
        minPreambleMatches: Int = FrameV1.PREAMBLE_BITS
    ): String {
        val root = JSONObject()
        root.put("schema", REPORT_SCHEMA)
        root.put("direction", "rx")
        root.put("timestamp", timestampUtc())
        root.put("profile", profileJson(received.profile))
        root.put("min_preamble_matches", minPreambleMatches)
        root.put("max_payload_bytes", MAX_DATA_PAYLOAD_BYTES)
        root.put("wav", wavJson(wavFilename, wavSha256))
        root.put("crc_is_authentication", false)
        root.put("recording", recordingJson(received))
        root.put("result", if (received.ok) "ok" else "error")
        root.put("error_type", received.errorType ?: JSONObject.NULL)
        root.put("message", received.message ?: JSONObject.NULL)
        root.put("crc_ok", received.crcOk)
        root.put("text", received.text ?: JSONObject.NULL)
        root.put("payload_hex", received.payloadHex ?: JSONObject.NULL)
        root.put("payload_bytes", received.payloadBytes ?: JSONObject.NULL)
        root.put("payload_sha256", received.payloadSha256 ?: JSONObject.NULL)
        root.put("preamble_matches", received.preambleMatches ?: JSONObject.NULL)
        root.put("start_sample", received.startSample ?: JSONObject.NULL)
        root.put("start_time_s", received.startTimeS ?: JSONObject.NULL)
        if (received.frameVersion != null && received.crcHex != null && received.totalBits != null) {
            val frame = JSONObject()
            frame.put("version", received.frameVersion)
            frame.put("preamble_bits", FrameV1.PREAMBLE_BITS)
            frame.put("preamble_matches", received.preambleMatches ?: JSONObject.NULL)
            frame.put("sync_word", FrameV1.hex16(FrameV1.SYNC_WORD))
            frame.put("payload_bytes", received.payloadBytes ?: JSONObject.NULL)
            frame.put("payload_bits", (received.payloadBytes ?: 0) * 8)
            frame.put("overhead_bits", FrameV1.FRAME_OVERHEAD_BITS)
            frame.put("total_bits", received.totalBits)
            frame.put("crc16", received.crcHex)
            frame.put("crc_algorithm", "CRC-16/CCITT-FALSE")
            root.put("frame", frame)
        }
        return root.toString(2)
    }

    private fun decodeSamples(
        samples: FloatArray,
        profile: BfskProfile,
        minPreambleMatches: Int
    ): ReceivedMessage {
        val metrics = recordingMetrics(samples, profile.sampleRate)
        val decoded = try {
            BfskDemodulator.decode(
                samples,
                profile,
                maxPayloadBytes = MAX_DATA_PAYLOAD_BYTES,
                minPreambleMatches = minPreambleMatches
            )
        } catch (exc: BfskDemodulator.SignalNotFound) {
            return failure(profile, samples, metrics, "signal_not_found", exc.message)
        } catch (exc: BfskDemodulator.SyncWordError) {
            return failure(profile, samples, metrics, "sync_mismatch", exc.message)
        } catch (exc: BfskDemodulator.FrameVersionError) {
            return failure(profile, samples, metrics, "version_unsupported", exc.message)
        } catch (exc: BfskDemodulator.FrameLengthError) {
            return failure(profile, samples, metrics, "length_invalid", exc.message)
        } catch (exc: BfskDemodulator.IncompleteFrame) {
            return failure(profile, samples, metrics, "incomplete_frame", exc.message)
        } catch (exc: BfskDemodulator.CrcError) {
            return failure(profile, samples, metrics, "crc_mismatch", exc.message)
        } catch (exc: Throwable) {
            return failure(profile, samples, metrics, "receiver_error", exc.message)
        }

        // Кадр и CRC уже проверены. Ошибка UTF-8 не отменяет crc_ok: отчёт
        // должен честно показать, что целостность кадра сошлась, а текст —
        // нет (как в snlab/datalink.py).
        var text: String? = null
        var errorType: String? = null
        var message: String? = "Сообщение принято, CRC верный"
        try {
            text = payloadToText(decoded.payload)
        } catch (exc: PayloadError) {
            errorType = "payload_utf8_error"
            message = exc.message
        }

        return ReceivedMessage(
            ok = errorType == null,
            profile = profile,
            text = text,
            errorType = errorType,
            message = message,
            payloadHex = decoded.payload.joinToString("") { "%02x".format(it) },
            payloadBytes = decoded.payload.size,
            payloadSha256 = sha256Hex(decoded.payload),
            crcOk = true,
            preambleMatches = decoded.preambleMatches,
            startSample = decoded.startSample,
            startTimeS = decoded.startTimeS,
            frameVersion = decoded.version,
            crcHex = FrameV1.hex16(decoded.crc),
            totalBits = decoded.totalBits,
            sampleRate = profile.sampleRate,
            sampleCount = samples.size,
            durationS = metrics.durationS,
            rmsDbfs = metrics.rmsDbfs,
            peakDbfs = metrics.peakDbfs,
            clippedSamples = metrics.clippedSamples
        )
    }

    private fun failure(
        profile: BfskProfile,
        samples: FloatArray,
        metrics: RecordingMetrics,
        errorType: String,
        message: String?
    ): ReceivedMessage = ReceivedMessage(
        ok = false,
        profile = profile,
        errorType = errorType,
        message = message,
        sampleRate = profile.sampleRate,
        sampleCount = samples.size,
        durationS = metrics.durationS,
        rmsDbfs = metrics.rmsDbfs,
        peakDbfs = metrics.peakDbfs,
        clippedSamples = metrics.clippedSamples
    )

    private fun formatError(
        profile: BfskProfile,
        errorType: String,
        message: String
    ): ReceivedMessage = ReceivedMessage(
        ok = false,
        profile = profile,
        errorType = errorType,
        message = message,
        sampleRate = profile.sampleRate,
        sampleCount = 0,
        durationS = 0.0,
        rmsDbfs = -999.0,
        peakDbfs = -999.0,
        clippedSamples = 0
    )

    private data class RecordingMetrics(
        val durationS: Double,
        val rmsDbfs: Double,
        val peakDbfs: Double,
        val clippedSamples: Int
    )

    private fun recordingMetrics(samples: FloatArray, sampleRate: Int): RecordingMetrics {
        if (samples.isEmpty()) {
            return RecordingMetrics(0.0, -999.0, -999.0, 0)
        }
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

    private fun dbfs(value: Double): Double = if (value <= 0.0) -999.0 else 20.0 * log10(value)

    private fun profileJson(profile: BfskProfile): JSONObject {
        val obj = JSONObject()
        obj.put("name", profile.name)
        obj.put("sample_rate", profile.sampleRate)
        obj.put("mark_hz", profile.markHz)
        obj.put("space_hz", profile.spaceHz)
        obj.put("symbol_rate", profile.symbolRate)
        obj.put("amplitude", profile.amplitude)
        obj.put("samples_per_symbol", profile.samplesPerSymbol)
        obj.put("bit_rate_bps", profile.bitRateBps)
        return obj
    }

    private fun frameJson(frame: FrameV1.Frame): JSONObject {
        val obj = JSONObject()
        obj.put("version", frame.version)
        obj.put("preamble_bits", FrameV1.PREAMBLE_BITS)
        obj.put("sync_word", FrameV1.hex16(FrameV1.SYNC_WORD))
        obj.put("payload_bytes", frame.payload.size)
        obj.put("payload_bits", frame.payloadBits)
        obj.put("payload_sha256", frame.payloadSha256)
        obj.put("crc16", FrameV1.hex16(frame.crc))
        obj.put("crc_algorithm", "CRC-16/CCITT-FALSE")
        obj.put("overhead_bits", FrameV1.FRAME_OVERHEAD_BITS)
        obj.put("total_bits", frame.totalBits)
        return obj
    }

    private fun signalJson(message: DataMessage): JSONObject {
        val obj = JSONObject()
        val modulatedSamples = message.frame.totalBits * message.profile.samplesPerSymbol
        obj.put("scheme", "continuous-phase BFSK")
        obj.put("bits", message.frame.totalBits)
        obj.put("samples_per_symbol", message.profile.samplesPerSymbol)
        obj.put("modulated_samples", modulatedSamples)
        obj.put("modulated_duration_s", modulatedSamples.toDouble() / message.sampleRate)
        obj.put("lead_silence_s", message.leadSilenceS)
        obj.put("tail_silence_s", message.tailSilenceS)
        obj.put("total_samples", message.sampleCount)
        obj.put("total_duration_s", message.durationS)
        obj.put("ramp_ms", message.rampMs)
        obj.put("peak_amplitude", message.peakAmplitude)
        obj.put("pcm_format", "PCM16 mono little-endian")
        obj.put("pcm_bytes", message.pcmBytes.size)
        obj.put("pcm_sha256", message.pcmSha256)
        return obj
    }

    private fun recordingJson(received: ReceivedMessage): JSONObject {
        val obj = JSONObject()
        obj.put("sample_rate_hz", received.sampleRate)
        obj.put("samples", received.sampleCount)
        obj.put("duration_s", received.durationS)
        obj.put("rms_dbfs", received.rmsDbfs)
        obj.put("peak_dbfs", received.peakDbfs)
        obj.put("clipped_samples", received.clippedSamples)
        obj.put("clip_threshold", CLIP_THRESHOLD)
        return obj
    }

    private fun wavJson(filename: String?, sha256: String?): JSONObject {
        val obj = JSONObject()
        obj.put("filename", filename ?: JSONObject.NULL)
        obj.put("sha256", sha256 ?: JSONObject.NULL)
        return obj
    }

    private fun timestampUtc(): String {
        val formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US)
        formatter.timeZone = TimeZone.getTimeZone("UTC")
        return formatter.format(Date())
    }

    private fun validateProfile(profile: BfskProfile) {
        if (!BfskProfile.dataProfiles().containsKey(profile.name)) {
            throw ProfileError("профиль должен быть data-audible или data-robust")
        }
    }

    private fun validateMinPreamble(value: Int) {
        if (value !in 1..FrameV1.PREAMBLE_BITS) {
            throw BfskDemodulator.ReceiverError(
                "min_preamble_matches должен быть в диапазоне 1..${FrameV1.PREAMBLE_BITS}"
            )
        }
    }

    private fun validateSilence(value: Double, fieldName: String): Double {
        require(value.isFinite() && value >= 0.0 && value <= MAX_SILENCE_S) {
            "$fieldName должен быть в диапазоне 0..${MAX_SILENCE_S.toInt()} с"
        }
        return value
    }
}
