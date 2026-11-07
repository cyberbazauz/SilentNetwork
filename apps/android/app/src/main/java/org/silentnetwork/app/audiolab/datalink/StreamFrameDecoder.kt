package org.silentnetwork.app.audiolab.datalink

import kotlin.math.ceil

/**
 * Incremental Frame v1 receiver for a continuous microphone stream.
 *
 * The chat mode cannot record a fixed-length WAV for every message: the
 * microphone is always open, and frames arrive at arbitrary offsets. This
 * decoder keeps a bounded tail of PCM, searches it for Frame v1 preambles,
 * validates every candidate with sync word / version / length / CRC-16 and
 * returns the payloads it has not consumed yet.
 *
 * The decoder never claims that a decoded frame crossed the air. It only
 * reports frames whose CRC passed. File assembly and UTF-8 validation are the
 * caller's responsibility.
 *
 * @param maxPayloadBytes Frame v1 payload limit; 64 bytes in data link v0.
 * @param minPreambleMatches acoustic threshold from the SN-028 protocol.
 */
class StreamFrameDecoder(
    private val profile: BfskProfile,
    private val maxPayloadBytes: Int = DataLinkCodec.MAX_DATA_PAYLOAD_BYTES,
    private val minPreambleMatches: Int = DataLinkCodec.ACOUSTIC_MIN_PREAMBLE_MATCHES
) {
    data class FrameEvent(
        val payload: ByteArray,
        val absoluteStartSample: Long,
        val preambleMatches: Int,
        val crc: Int
    ) {
        val payloadSha256: String get() = DataLinkCodec.sha256Hex(payload)
    }

    private var buffer: FloatArray = FloatArray(0)
    private var bufferStartSample: Long = 0
    private var samplesSinceDecode: Int = 0

    private val symbolsPerBit: Int = profile.samplesPerSymbol
    private val preambleSpan: Int = BfskDemodulator.PREAMBLE_BITS * symbolsPerBit
    private val maxFrameBits: Int = FrameV1.FRAME_OVERHEAD_BITS + maxPayloadBytes * 8
    private val maxFrameSamples: Int =
        ceil(maxFrameBits.toDouble() / profile.symbolRate * profile.sampleRate).toInt() +
            symbolsPerBit * 2
    private val minDecodeSamples: Int = maxOf(preambleSpan, profile.sampleRate / 4)
    private val decodeStepSamples: Int = profile.sampleRate / 2

    init {
        require(maxPayloadBytes > 0) { "maxPayloadBytes должен быть положительным" }
        require(minPreambleMatches in 1..BfskDemodulator.PREAMBLE_BITS) {
            "minPreambleMatches должен быть в диапазоне 1..${BfskDemodulator.PREAMBLE_BITS}"
        }
    }

    /** Total number of samples consumed or currently buffered. */
    val receivedSamples: Long get() = bufferStartSample + buffer.size

    /** PCM samples currently held for resynchronisation. */
    val bufferedSamples: Int get() = buffer.size

    /** Absolute sample index of the first buffered sample. */
    val bufferedStartSample: Long get() = bufferStartSample

    /**
     * Append PCM samples in [-1, 1]. Returns frames found by this call.
     * Decoding runs at most twice per second even when audio blocks are small.
     */
    fun feed(samples: FloatArray): List<FrameEvent> {
        if (samples.isEmpty()) return emptyList()
        append(samples)
        samplesSinceDecode += samples.size
        if (buffer.size < minDecodeSamples || samplesSinceDecode < decodeStepSamples) {
            return emptyList()
        }
        samplesSinceDecode = 0
        return decodeBuffered()
    }

    /**
     * Decode the remaining buffer immediately. Call after capture stops or
     * when a period of silence has passed.
     */
    fun flush(): List<FrameEvent> {
        if (buffer.size < preambleSpan) return emptyList()
        samplesSinceDecode = 0
        return decodeBuffered()
    }

    /** Drop buffered audio. Useful when the user changes profile or stops. */
    fun reset() {
        bufferStartSample += buffer.size
        buffer = FloatArray(0)
        samplesSinceDecode = 0
    }

    private fun append(samples: FloatArray) {
        val copy = samples.copyOf()
        if (buffer.isEmpty()) {
            buffer = copy
            return
        }
        val merged = FloatArray(buffer.size + copy.size)
        System.arraycopy(buffer, 0, merged, 0, buffer.size)
        System.arraycopy(copy, 0, merged, buffer.size, copy.size)
        buffer = merged
    }

    private fun decodeBuffered(): List<FrameEvent> {
        if (buffer.size < preambleSpan) return emptyList()
        val metric = try {
            BfskDemodulator.bitMetric(buffer, profile)
        } catch (_: BfskDemodulator.ReceiverError) {
            reset()
            return emptyList()
        }

        val events = ArrayList<FrameEvent>()
        var scan = 0
        var retainFrom = 0

        while (scan < metric.size) {
            if (metric.size - scan < preambleSpan) break

            val candidates = try {
                BfskDemodulator.findFrameCandidates(
                    metric,
                    profile,
                    minPreambleMatches = minPreambleMatches,
                    from = scan,
                    limit = 32
                )
            } catch (_: BfskDemodulator.SignalNotFound) {
                break
            }

            // Стриминговый приём разбирает кадры по порядку. Выбор кандидата
            // с лучшим совпадением преамбулы и окна ±4 символа вокруг него
            // мог потерять истинное начало: периодическая преамбула `1010…`
            // даёт кандидата на два символа раньше, а настоящий старт
            // оказывается дальше окна. Пробуем кандидатов по возрастанию
            // позиции; арбитр по-прежнему CRC-16.
            val ordered = candidates.sortedBy { it.sample }
            val failureStart = ordered.first().sample

            val frame = try {
                BfskDemodulator.decodeCandidates(
                    metric,
                    profile,
                    ordered,
                    maxPayloadBytes = maxPayloadBytes,
                    minPreambleMatches = minPreambleMatches
                )
            } catch (error: BfskDemodulator.ReceiverError) {
                if (error is BfskDemodulator.IncompleteFrame) {
                    // Кадр начался, но ещё не доехал целиком: оставляем его
                    // в буфере до следующего блока.
                    retainFrom = failureStart
                    break
                }
                val resync = failureStart + BfskDemodulator.PREAMBLE_BITS * symbolsPerBit
                if (resync >= metric.size) {
                    retainFrom = failureStart
                    break
                }
                scan = resync
                retainFrom = scan
                continue
            }

            events.add(
                FrameEvent(
                    payload = frame.payload,
                    absoluteStartSample = bufferStartSample + frame.startSample,
                    preambleMatches = frame.preambleMatches,
                    crc = frame.crc
                )
            )
            val consumed = frame.startSample + frame.totalBits * symbolsPerBit
            if (consumed <= scan) break
            scan = consumed
            retainFrom = consumed
        }

        // Ограничить память. Если преамбула не найдена, храним только хвост,
        // в который помещается самый длинный допустимый кадр.
        val maxRetained = maxFrameSamples + decodeStepSamples
        if (retainFrom < buffer.size - maxRetained) {
            retainFrom = buffer.size - maxRetained
        }
        if (retainFrom < 0) retainFrom = 0
        if (retainFrom > buffer.size) retainFrom = buffer.size

        if (retainFrom > 0) {
            buffer = buffer.copyOfRange(retainFrom, buffer.size)
            bufferStartSample += retainFrom
        }
        return events
    }
}
