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
    fun bitMetric(samples: FloatArray, profile: BfskProfile): FloatArray {
        if (samples.isEmpty()) throw SignalNotFound("запись не содержит отсчётов")
        for (value in samples) {
            if (!value.isFinite()) throw ReceiverError("сигнал содержит NaN или бесконечность")
        }
        val symbolsPerBit = profile.samplesPerSymbol
        if (samples.size < symbolsPerBit) throw SignalNotFound("сигнал короче одного символа")

        val windows = samples.size - symbolsPerBit + 1
        // Float, а не Double: на записи 420 с (20 млн отсчётов) массив метрики
        // занимает 80 МБ вместо 161 МБ. Раньше рядом лежали ещё два
        // cumulative-массива по 161 МБ, и длинный приём падал по памяти.
        val metric = FloatArray(windows)

        for (pass in 0..1) {
            val frequency = if (pass == 0) profile.markHz else profile.spaceHz
            val angleStep = -TWO_PI * frequency / profile.sampleRate
            val stepCos = cos(angleStep)
            val stepSin = sin(angleStep)

            // Скользящая корреляция по окну ровно в один символ. Поворот
            // вектора обновляется умножением на постоянный комплексный шаг,
            // поэтому cos/sin не вызываются на каждый отсчёт, а два массива
            // длины samples.size не нужны.
            var real = 0.0
            var imag = 0.0
            var addReal = 1.0
            var addImag = 0.0
            for (index in 0 until symbolsPerBit) {
                val value = samples[index].toDouble()
                real += value * addReal
                imag += value * addImag
                val nextReal = addReal * stepCos - addImag * stepSin
                addImag = addReal * stepSin + addImag * stepCos
                addReal = nextReal
            }

            var removeReal = 1.0
            var removeImag = 0.0
            for (start in 0 until windows) {
                val energy = real * real + imag * imag
                if (pass == 0) {
                    metric[start] = energy.toFloat()
                } else {
                    metric[start] = (metric[start] - energy).toFloat()
                }
                if (start + 1 >= windows) continue

                val addIndex = start + symbolsPerBit
                val addValue = samples[addIndex].toDouble()
                val removeValue = samples[start].toDouble()
                real += addValue * addReal - removeValue * removeReal
                imag += addValue * addImag - removeValue * removeImag

                val nextAddReal = addReal * stepCos - addImag * stepSin
                addImag = addReal * stepSin + addImag * stepCos
                addReal = nextAddReal
                val nextRemoveReal = removeReal * stepCos - removeImag * stepSin
                removeImag = removeReal * stepSin + removeImag * stepCos
                removeReal = nextRemoveReal

                // Раз в 262144 окон нормируем поворот, чтобы накопленная
                // ошибка умножения не росла на многомиллионной записи.
                if ((start and 0x3FFFF) == 0) {
                    val addMagnitude = kotlin.math.hypot(addReal, addImag)
                    addReal /= addMagnitude
                    addImag /= addMagnitude
                    val removeMagnitude = kotlin.math.hypot(removeReal, removeImag)
                    removeReal /= removeMagnitude
                    removeImag /= removeMagnitude
                }
            }
        }
        return metric
    }

    /**
     * Find all plausible frame starts, best first.
     *
     * The `1010…` preamble repeats every two symbols, so shifting the window by
     * an even number of symbols gives the **same** correlation. Picking only
     * the first best window occasionally locked the frame two symbols early:
     * the preamble still matched, but the sync word did not. Here every
     * candidate is returned; the final choice is made by sync word, version,
     * length and CRC in [decode].
     *
     * @param from absolute metric index to start searching from.
     */
    fun findFrameCandidates(
        metric: FloatArray,
        profile: BfskProfile,
        minPreambleMatches: Int = PREAMBLE_BITS,
        from: Int = 0,
        limit: Int = 8
    ): List<FrameStart> {
        validateMinPreambleMatches(minPreambleMatches)
        require(from in 0..metric.size) { "from вне диапазона метрики" }
        require(limit >= 1) { "limit должен быть положительным" }
        val symbolsPerBit = profile.samplesPerSymbol
        val span = PREAMBLE_BITS * symbolsPerBit
        val available = metric.size - from
        if (available < span) {
            throw SignalNotFound("сигнала недостаточно для 32-битной преамбулы")
        }

        val threshold = 2.0 * minPreambleMatches - PREAMBLE_BITS
        val peaks = ArrayList<Pair<Double, Int>>()
        var bestScore = Double.NEGATIVE_INFINITY

        for (phase in 0 until symbolsPerBit) {
            val count = (available - phase + symbolsPerBit - 1) / symbolsPerBit
            if (count < PREAMBLE_BITS) continue

            // Скользящая корреляция с 1010…: чётные и нечётные позиции
            // окна из 32 символов. Обновление за O(1) вместо 32 сложений
            // на каждое окно — иначе длинная запись декодируется слишком
            // медленно на телефоне.
            var even = 0.0
            var odd = 0.0
            for (position in 0 until PREAMBLE_BITS) {
                val value = BfskModulator.sign(
                    metric[from + phase + position * symbolsPerBit]
                )
                if (position % 2 == 0) even += value else odd += value
            }

            // Первые несколько положений на фазу, начиная с самых ранних.
            // Брать глобальные максимумы нельзя: на длинной записи это
            // окажутся последние кадры, и сканер файла перепрыгнет через
            // начало. Несколько позиций нужны из-за периодичности `1010…` и
            // шума.
            var offered = 0
            var localScore = even - odd
            if (localScore > bestScore) bestScore = localScore
            if (localScore >= threshold) {
                peaks.add(Pair(localScore, from + phase))
                offered++
            }
            for (index in 1..(count - PREAMBLE_BITS)) {
                val leaving = BfskModulator.sign(
                    metric[from + phase + (index - 1) * symbolsPerBit]
                )
                val entering = BfskModulator.sign(
                    metric[from + phase + (index + PREAMBLE_BITS - 1) * symbolsPerBit]
                )
                val nextEven = odd
                val nextOdd = even - leaving + entering
                even = nextEven
                odd = nextOdd
                localScore = even - odd
                if (localScore > bestScore) bestScore = localScore
                if (localScore >= threshold && offered < 8) {
                    peaks.add(Pair(localScore, from + phase + index * symbolsPerBit))
                    offered++
                }
            }
        }

        if (peaks.isEmpty()) {
            val matches = ((bestScore + PREAMBLE_BITS) / 2.0).roundToInt().coerceAtLeast(0)
            throw SignalNotFound(
                "преамбула не найдена: лучшее совпадение $matches/$PREAMBLE_BITS " +
                    "(требуется $minPreambleMatches)"
            )
        }

        peaks.sortByDescending { it.first }
        val coarsePositions = ArrayList<Pair<Double, Int>>()
        for (peak in peaks) {
            if (coarsePositions.all {
                    kotlin.math.abs(peak.second - it.second) > symbolsPerBit / 2
                }
            ) {
                coarsePositions.add(peak)
            }
            if (coarsePositions.size >= limit) break
        }

        val candidates = ArrayList<FrameStart>()
        for ((_, coarse) in coarsePositions) {
            val low = maxOf(0, coarse - symbolsPerBit)
            var high = minOf(coarse, metric.size - span)
            if (high < low) high = low

            var bestRelative = coarse
            var bestResponse = Double.NEGATIVE_INFINITY
            for (sample in low..high) {
                var response = 0.0
                for (position in 0 until PREAMBLE_BITS) {
                    response += PREAMBLE_PATTERN[position] *
                        metric[sample + position * symbolsPerBit]
                }
                if (response > bestResponse) {
                    bestResponse = response
                    bestRelative = sample
                }
            }

            var matches = 0
            for (position in 0 until PREAMBLE_BITS) {
                val observed = metric[bestRelative + position * symbolsPerBit] > 0.0
                val expected = PREAMBLE_PATTERN[position] > 0.0
                if (observed == expected) matches++
            }
            if (matches < minPreambleMatches) continue
            candidates.add(
                FrameStart(
                    sample = bestRelative,
                    preambleMatches = matches,
                    coarseSample = coarse
                )
            )
        }

        if (candidates.isEmpty()) {
            throw SignalNotFound(
                "преамбула не подтверждена: ни один из ${coarsePositions.size} " +
                    "кандидатов не дал $minPreambleMatches/$PREAMBLE_BITS"
            )
        }
        return candidates
    }

    /**
     * Find the best frame start. Kept for diagnostics and for callers that
     * resynchronise after a damaged frame; full decoding uses
     * [findFrameCandidates] and validates CRC for every candidate.
     */
    fun findFrameStart(
        metric: FloatArray,
        profile: BfskProfile,
        minPreambleMatches: Int = PREAMBLE_BITS,
        from: Int = 0
    ): FrameStart {
        return findFrameCandidates(
            metric,
            profile,
            minPreambleMatches = minPreambleMatches,
            from = from,
            limit = 1
        ).first()
    }

    fun decodeFromMetric(
        metric: FloatArray,
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

    /**
     * Decode a frame from PCM samples.
     *
     * Every plausible preamble position is tried; the first frame that passes
     * sync word, version, length and CRC-16 is accepted. If none passes, the
     * error of the best candidate is thrown, because it describes why the
     * reception failed.
     */
    fun decode(
        samples: FloatArray,
        profile: BfskProfile,
        maxPayloadBytes: Int = DataLinkCodec.MAX_DATA_PAYLOAD_BYTES,
        minPreambleMatches: Int = PREAMBLE_BITS
    ): DecodedFrame {
        val metric = bitMetric(samples, profile)
        val candidates = findFrameCandidates(metric, profile, minPreambleMatches)
        return decodeCandidates(
            metric,
            profile,
            candidates,
            maxPayloadBytes = maxPayloadBytes,
            minPreambleMatches = minPreambleMatches
        )
    }

    /**
     * Decode a frame, trying every candidate and then a small fine shift of
     * the start.
     *
     * The coarse refinement gives a plateau almost one symbol wide: on a clean
     * signal several neighbouring samples produce the same preamble, but an
     * energy window shifted by a dozen samples can catch the next symbol and
     * corrupt one bit. CRC-16 is the only reliable arbiter, so candidates are
     * tried as is, then shifted by ±1, ±2, … samples up to half a symbol.
     */
    fun decodeCandidates(
        metric: FloatArray,
        profile: BfskProfile,
        candidates: List<FrameStart>,
        maxPayloadBytes: Int = DataLinkCodec.MAX_DATA_PAYLOAD_BYTES,
        minPreambleMatches: Int = PREAMBLE_BITS
    ): DecodedFrame {
        var firstError: ReceiverError? = null
        // IncompleteFrame means a candidate already passed the preamble, sync
        // word, version and length checks but the buffer ends inside the
        // declared frame. It must win over an earlier periodic preamble
        // candidate that failed the sync word: the stream decoder has to keep
        // the frame and wait for the rest instead of resynchronising past it.
        var incompleteError: BfskDemodulator.IncompleteFrame? = null
        for (start in candidates) {
            try {
                return decodeFromMetric(
                    metric,
                    profile,
                    start,
                    maxPayloadBytes = maxPayloadBytes,
                    minPreambleMatches = minPreambleMatches
                )
            } catch (error: BfskDemodulator.IncompleteFrame) {
                if (incompleteError == null) incompleteError = error
                if (firstError == null) firstError = error
            } catch (error: ReceiverError) {
                if (firstError == null) firstError = error
            }
        }

        val half = profile.samplesPerSymbol / 2
        for (distance in 1..half) {
            for (start in candidates) {
                for (direction in intArrayOf(-1, 1)) {
                    val sample = start.sample + direction * distance
                    if (sample < 0) continue
                    val trial = FrameStart(
                        sample = sample,
                        preambleMatches = start.preambleMatches,
                        coarseSample = start.coarseSample
                    )
                    try {
                        return decodeFromMetric(
                            metric,
                            profile,
                            trial,
                            maxPayloadBytes = maxPayloadBytes,
                            minPreambleMatches = minPreambleMatches
                        )
                    } catch (error: BfskDemodulator.IncompleteFrame) {
                        if (incompleteError == null) incompleteError = error
                    } catch (_: ReceiverError) {
                        // Следующий сдвиг.
                    }
                }
            }
        }

        if (incompleteError != null) throw incompleteError
        throw firstError ?: SignalNotFound("не найдено ни одного кандидата начала кадра")
    }

    /**
     * Move the start back when the frame runs into the end of the recording.
     * The preamble refinement may pick a sample a few positions too late.
     */
    private fun fitFrameStart(
        metric: FloatArray,
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
        metric: FloatArray,
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
