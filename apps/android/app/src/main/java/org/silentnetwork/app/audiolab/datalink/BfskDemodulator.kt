package org.silentnetwork.app.audiolab.datalink

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * Non-coherent BFSK receiver shared with `snlab/receiver.py`.
 *
 * The receiver does not know where the frame starts. It computes mark/space
 * energy for every one-symbol window, searches the `1010...` preamble over all
 * symbol phases, refines the start on the maximum preamble response, then
 * checks sync word, version, length and CRC-16.
 *
 * Decoding a WAV does not prove an acoustic transmission through air.
 */
object BfskDemodulator {
    const val PREAMBLE_BITS = 32
    const val HEADER_BITS = PREAMBLE_BITS + FrameV1.SYNC_BITS +
        FrameV1.VERSION_BITS + FrameV1.LENGTH_BITS
    private const val TWO_PI = 2.0 * PI

    private val PREAMBLE_PATTERN = DoubleArray(PREAMBLE_BITS) {
        if (it % 2 == 0) 1.0 else -1.0
    }
    private val PREAMBLE_BITS_EXPECTED = ByteArray(PREAMBLE_BITS) {
        if (it % 2 == 0) 1 else 0
    }

    open class ReceiverError(message: String) : IllegalArgumentException(message)
    class SignalNotFound(message: String) : ReceiverError(message)
    class SyncWordError(message: String) : ReceiverError(message)
    class FrameVersionError(message: String) : ReceiverError(message)
    class FrameLengthError(message: String) : ReceiverError(message)
    class IncompleteFrame(message: String) : ReceiverError(message)
    class CrcError(message: String) : ReceiverError(message)

    data class FrameStart(
        val sample: Int,
        val preambleMatches: Int,
        val coarseSample: Int
    ) {
        val sampleOffset: Int get() = sample - coarseSample
    }

    data class DecodedFrame(
        val payload: ByteArray,
        val version: Int,
        val crc: Int,
        val startSample: Int,
        val preambleMatches: Int,
        val totalBits: Int,
        val sampleRate: Int,
        val totalSamples: Int,
        val profileName: String
    ) {
        val payloadBits: Int get() = payload.size * 8
        val startTimeS: Double get() = startSample.toDouble() / sampleRate
        val durationS: Double get() = totalSamples.toDouble() / sampleRate
    }

    /**
     * Difference between mark and space energy for every one-symbol window.
     * Positive means bit 1, negative means bit 0.
     */
    fun bitMetric(samples: FloatArray, profile: BfskProfile): DoubleArray {
        if (samples.isEmpty()) throw SignalNotFound("запись не содержит отсчётов")
        for (value in samples) {
            if (!value.isFinite()) throw ReceiverError("сигнал содержит NaN или бесконечность")
        }
        val symbolsPerBit = profile.samplesPerSymbol
        if (samples.size < symbolsPerBit) throw SignalNotFound("сигнал короче одного символа")

        val windows = samples.size - symbolsPerBit + 1
        val metric = DoubleArray(windows)
        val cumulativeReal = DoubleArray(samples.size + 1)
        val cumulativeImag = DoubleArray(samples.size + 1)

        for (pass in 0..1) {
            val frequency = if (pass == 0) profile.markHz else profile.spaceHz
            var real = 0.0
            var imag = 0.0
            cumulativeReal[0] = 0.0
            cumulativeImag[0] = 0.0
            for (index in samples.indices) {
                val angle = -TWO_PI * frequency * index / profile.sampleRate
                val value = samples[index].toDouble()
                real += value * cos(angle)
                imag += value * sin(angle)
                cumulativeReal[index + 1] = real
                cumulativeImag[index + 1] = imag
            }
            for (start in 0 until windows) {
                val re = cumulativeReal[start + symbolsPerBit] - cumulativeReal[start]
                val im = cumulativeImag[start + symbolsPerBit] - cumulativeImag[start]
                val energy = re * re + im * im
                if (pass == 0) {
                    metric[start] = energy
                } else {
                    metric[start] -= energy
                }
            }
        }
        return metric
    }

    /** Find the preamble and refine the frame start. */
    fun findFrameStart(
        metric: DoubleArray,
        profile: BfskProfile,
        minPreambleMatches: Int = PREAMBLE_BITS
    ): FrameStart {
        validateMinPreambleMatches(minPreambleMatches)
        val symbolsPerBit = profile.samplesPerSymbol
        val span = PREAMBLE_BITS * symbolsPerBit
        if (metric.size < span) {
            throw SignalNotFound("сигнала недостаточно для 32-битной преамбулы")
        }

        var bestScore = Double.NEGATIVE_INFINITY
        var bestPhase = 0
        var bestIndex = 0
        for (phase in 0 until symbolsPerBit) {
            val count = (metric.size - phase + symbolsPerBit - 1) / symbolsPerBit
            if (count < PREAMBLE_BITS) continue
            for (index in 0..(count - PREAMBLE_BITS)) {
                var score = 0.0
                for (position in 0 until PREAMBLE_BITS) {
                    val value = metric[phase + (index + position) * symbolsPerBit]
                    score += BfskModulator.sign(value) * PREAMBLE_PATTERN[position]
                }
                if (score > bestScore) {
                    bestScore = score
                    bestPhase = phase
                    bestIndex = index
                }
            }
        }

        val threshold = 2.0 * minPreambleMatches - PREAMBLE_BITS
        if (bestScore < threshold) {
            val matches = ((bestScore + PREAMBLE_BITS) / 2.0).roundToInt().coerceAtLeast(0)
            throw SignalNotFound(
                "преамбула не найдена: лучшее совпадение $matches/$PREAMBLE_BITS " +
                    "(требуется $minPreambleMatches)"
            )
        }

        val coarse = bestPhase + bestIndex * symbolsPerBit
        val low = maxOf(0, coarse - symbolsPerBit)
        var high = minOf(coarse, metric.size - span)
        if (high < low) high = low

        var bestSample = coarse
        var bestResponse = Double.NEGATIVE_INFINITY
        for (sample in low..high) {
            var response = 0.0
            for (position in 0 until PREAMBLE_BITS) {
                response += PREAMBLE_PATTERN[position] *
                    metric[sample + position * symbolsPerBit]
            }
            if (response > bestResponse) {
                bestResponse = response
                bestSample = sample
            }
        }

        // Сравниваем наблюдаемый бит с ожидаемым 1010…, а не просто считаем
        // положительные значения: у преамбулы ровно 16 единиц из 32.
        var matches = 0
        for (position in 0 until PREAMBLE_BITS) {
            val observed = metric[bestSample + position * symbolsPerBit] > 0.0
            val expected = PREAMBLE_PATTERN[position] > 0.0
            if (observed == expected) matches++
        }
        if (matches < minPreambleMatches) {
            throw SignalNotFound(
                "преамбула не подтверждена: $matches/$PREAMBLE_BITS " +
                    "(требуется $minPreambleMatches)"
            )
        }

        return FrameStart(
            sample = bestSample,
            preambleMatches = matches,
            coarseSample = coarse
        )
    }

    fun decodeFromMetric(
        metric: DoubleArray,
        profile: BfskProfile,
        start: FrameStart,
        maxPayloadBytes: Int = DataLinkCodec.MAX_DATA_PAYLOAD_BYTES,
        minPreambleMatches: Int = PREAMBLE_BITS
    ): DecodedFrame {
        val symbolsPerBit = profile.samplesPerSymbol
        val header = readBits(metric, start.sample, HEADER_BITS, symbolsPerBit)

        var matched = 0
        for (index in 0 until PREAMBLE_BITS) {
            if (header[index] == PREAMBLE_BITS_EXPECTED[index]) matched++
        }
        if (matched < minPreambleMatches) {
            throw SignalNotFound(
                "преамбула не совпала при повторной проверке: " +
                    "$matched/$PREAMBLE_BITS (требуется $minPreambleMatches)"
            )
        }

        var offset = PREAMBLE_BITS
        val sync = FrameV1.bitsToUInt(header, offset, FrameV1.SYNC_BITS)
        if (sync != FrameV1.SYNC_WORD) {
            throw SyncWordError(
                "ожидалось синхрослово ${FrameV1.hex16(FrameV1.SYNC_WORD)}, " +
                    "получено ${FrameV1.hex16(sync)}"
            )
        }

        offset += FrameV1.SYNC_BITS
        val version = FrameV1.bitsToUInt(header, offset, FrameV1.VERSION_BITS)
        if (version != FrameV1.FRAME_VERSION) {
            throw FrameVersionError(
                "версия кадра $version не поддерживается " +
                    "(ожидается ${FrameV1.FRAME_VERSION})"
            )
        }

        offset += FrameV1.VERSION_BITS
        val length = FrameV1.bitsToUInt(header, offset, FrameV1.LENGTH_BITS)
        if (length !in 1..maxPayloadBytes) {
            throw FrameLengthError(
                "длина payload $length байт вне диапазона 1..$maxPayloadBytes"
            )
        }

        val totalBits = FrameV1.FRAME_OVERHEAD_BITS + length * 8
        val frameStart = fitFrameStart(metric, start.sample, totalBits, symbolsPerBit)
        val frameBits = readBits(metric, frameStart, totalBits, symbolsPerBit)

        val payloadOffset = HEADER_BITS
        val payload = FrameV1.bitsToBytes(frameBits, payloadOffset, length)
        val crcOffset = payloadOffset + length * 8
        val crc = FrameV1.bitsToUInt(frameBits, crcOffset, FrameV1.CRC_BITS)

        val crcInput = ByteArray(3 + payload.size)
        crcInput[0] = version.toByte()
        crcInput[1] = ((length shr 8) and 0xFF).toByte()
        crcInput[2] = (length and 0xFF).toByte()
        payload.copyInto(crcInput, 3)
        val expectedCrc = FrameV1.crc16Ccitt(crcInput)
        if (crc != expectedCrc) {
            throw CrcError(
                "CRC-16 не совпал: в кадре ${FrameV1.hex16(crc)}, " +
                    "расчёт ${FrameV1.hex16(expectedCrc)}"
            )
        }

        return DecodedFrame(
            payload = payload,
            version = version,
            crc = crc,
            startSample = frameStart,
            preambleMatches = start.preambleMatches,
            totalBits = totalBits,
            sampleRate = profile.sampleRate,
            totalSamples = metric.size + symbolsPerBit - 1,
            profileName = profile.name
        )
    }

    fun decode(
        samples: FloatArray,
        profile: BfskProfile,
        maxPayloadBytes: Int = DataLinkCodec.MAX_DATA_PAYLOAD_BYTES,
        minPreambleMatches: Int = PREAMBLE_BITS
    ): DecodedFrame {
        val metric = bitMetric(samples, profile)
        val start = findFrameStart(metric, profile, minPreambleMatches)
        return decodeFromMetric(
            metric,
            profile,
            start,
            maxPayloadBytes = maxPayloadBytes,
            minPreambleMatches = minPreambleMatches
        )
    }

    /**
     * Move the start back when the frame runs into the end of the recording.
     * The preamble refinement may pick a sample a few positions too late.
     */
    private fun fitFrameStart(
        metric: DoubleArray,
        start: Int,
        totalBits: Int,
        symbolsPerBit: Int
    ): Int {
        val latest = metric.size - totalBits * symbolsPerBit + (symbolsPerBit - 1)
        if (latest < 0 || start <= latest || start - latest > symbolsPerBit) return start

        val header = readBits(metric, latest, HEADER_BITS, symbolsPerBit)
        for (index in 0 until PREAMBLE_BITS) {
            if (header[index] != PREAMBLE_BITS_EXPECTED[index]) return start
        }
        return latest
    }

    private fun readBits(
        metric: DoubleArray,
        start: Int,
        count: Int,
        symbolsPerBit: Int
    ): ByteArray {
        val last = start + (count - 1) * symbolsPerBit
        if (last >= metric.size) {
            val available = maxOf(
                0,
                (metric.size - start + symbolsPerBit - 1) / symbolsPerBit
            )
            throw IncompleteFrame("кадр объявляет $count бит, в записи доступно $available")
        }
        val bits = ByteArray(count)
        for (index in 0 until count) {
            bits[index] = if (metric[start + index * symbolsPerBit] > 0.0) 1 else 0
        }
        return bits
    }

    private fun validateMinPreambleMatches(value: Int) {
        if (value !in 1..PREAMBLE_BITS) {
            throw ReceiverError(
                "min_preamble_matches должен быть в диапазоне 1..$PREAMBLE_BITS"
            )
        }
    }
}
