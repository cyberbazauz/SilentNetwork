package org.silentnetwork.app.audiolab.datalink

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.audiolab.dsp.SignalGenerator
import java.io.InputStreamReader

class DataLinkVectorsTest {
    private fun loadFixture(): org.json.JSONObject {
        val stream = javaClass.classLoader!!.getResourceAsStream("data-link-vectors.json")
        assertNotNull("data-link-vectors.json отсутствует в тестовых ресурсах", stream)
        val text = InputStreamReader(stream, Charsets.UTF_8).readText()
        return org.json.JSONObject(text)
    }

    @Test
    fun frameV1MatchesFixture() {
        val fixture = loadFixture()
        val vectors = fixture.getJSONArray("vectors")
        for (index in 0 until vectors.length()) {
            val vector = vectors.getJSONObject(index)
            if (!vector.getBoolean("expect_ok")) continue
            val text = vector.getString("text")
            val maxBytes = vector.getInt("max_bytes")
            val payload = DataLinkCodec.textToPayload(text)
            assertEquals(maxBytes, vector.getInt("max_bytes"))
            assertEquals(vector.getString("payload_hex"), payload.joinToString("") { "%02x".format(it) })
            assertEquals(vector.getInt("payload_bytes"), payload.size)
            val frame = FrameV1.build(payload, maxPayloadBytes = maxBytes)
            assertEquals(vector.getJSONObject("frame").getInt("total_bits"), frame.totalBits)
            val crcHex = "0x%04X".format(frame.crc)
            assertEquals(vector.getJSONObject("frame").getString("crc16"), crcHex)
            assertEquals(vector.getString("bits_hex"), FrameV1.packBits(frame.bits).joinToString("") { "%02x".format(it) })
        }
    }

    @Test
    fun roundTripModulatorDemodulator() {
        val fixture = loadFixture()
        val vectors = fixture.getJSONArray("vectors")
        for (index in 0 until vectors.length()) {
            val vector = vectors.getJSONObject(index)
            if (!vector.getBoolean("expect_ok")) continue
            val text = vector.getString("text")
            val profileName = vector.getString("profile")
            val profile = BfskProfile.dataProfiles()[profileName]!!
            val (wavBytes, message) = DataLinkCodec.encodeToWav(text, profile)
            val decoded = DataLinkCodec.decodeFromWav(wavBytes, profile)
            assertTrue(
                "должен быть принят: ${vector.getString("id")} " +
                    "error=${decoded.errorType} msg=${decoded.message}",
                decoded.ok
            )
            assertEquals(text, decoded.text)
            assertTrue(decoded.crcOk)
            assertEquals(32, decoded.preambleMatches)
        }
    }

    @Test
    fun corruptedCrcIsReported() {
        val fixture = loadFixture()
        val negative = fixture.getJSONArray("negative_decode")
        var vector: org.json.JSONObject? = null
        for (index in 0 until negative.length()) {
            val item = negative.getJSONObject(index)
            if (item.getString("id") == "corrupted_crc") {
                vector = item
                break
            }
        }
        assertNotNull(vector)
        val bitsHex = vector!!.getString("bits_hex")
        val bits = hexToBits(bitsHex)
        val profile = BfskProfile.DATA_AUDIBLE
        val samples = BfskModulator.bitsToSamples(bits, profile)
        val pcm = SignalGenerator.toPcm16LittleEndian(samples)
        val wav = org.silentnetwork.app.audiolab.dsp.WavFileHandler.encodePcm16Wav(pcm, profile.sampleRate, 1)
        val decoded = DataLinkCodec.decodeFromWav(wav, profile)
        assertTrue(decoded.ok == false)
        assertEquals("crc_mismatch", decoded.errorType)
        assertTrue(decoded.crcOk == false)
    }

    @Test
    fun truncatedFrameIsReported() {
        val fixture = loadFixture()
        val negative = fixture.getJSONArray("negative_decode")
        var vector: org.json.JSONObject? = null
        for (index in 0 until negative.length()) {
            val item = negative.getJSONObject(index)
            if (item.getString("id") == "truncated") {
                vector = item
                break
            }
        }
        assertNotNull(vector)
        val bitsHex = vector!!.getString("bits_hex")
        val bits = hexToBits(bitsHex)
        val profile = BfskProfile.DATA_AUDIBLE
        val samples = BfskModulator.bitsToSamples(bits, profile)
        val pcm = SignalGenerator.toPcm16LittleEndian(samples)
        val wav = org.silentnetwork.app.audiolab.dsp.WavFileHandler.encodePcm16Wav(pcm, profile.sampleRate, 1)
        val decoded = DataLinkCodec.decodeFromWav(wav, profile)
        assertTrue(decoded.ok == false)
        assertEquals("incomplete_frame", decoded.errorType)
    }

    @Test
    fun noiseOnlyDoesNotDecode() {
        val fixture = loadFixture()
        val negative = fixture.getJSONArray("negative_decode")
        var vector: org.json.JSONObject? = null
        for (index in 0 until negative.length()) {
            val item = negative.getJSONObject(index)
            if (item.getString("id") == "noise_only") {
                vector = item
                break
            }
        }
        assertNotNull(vector)
        val noise = vector!!.getJSONObject("noise")
        val seed = noise.getLong("seed")
        val amplitude = noise.getDouble("amplitude")
        val samplesCount = noise.getInt("samples")
        val rng = java.util.Random(seed)
        val samples = FloatArray(samplesCount)
        for (index in 0 until samplesCount) {
            samples[index] = (rng.nextGaussian().toFloat() * amplitude.toFloat())
        }
        val pcm = SignalGenerator.toPcm16LittleEndian(samples)
        val wav = org.silentnetwork.app.audiolab.dsp.WavFileHandler.encodePcm16Wav(pcm, 48000, 1)
        val decoded = DataLinkCodec.decodeFromWav(wav, BfskProfile.DATA_AUDIBLE)
        assertTrue(decoded.ok == false)
        assertEquals("signal_not_found", decoded.errorType)
    }

    @Test
    fun roundTripDataRobustProfile() {
        // Векторы в фикстуре только для data-audible; резервный профиль
        // проверяем отдельным round-trip, чтобы не потерять его поддержку.
        for (text in listOf("Ping", "Привет")) {
            val (wav, _) = DataLinkCodec.encodeToWav(text, BfskProfile.DATA_ROBUST)
            val decoded = DataLinkCodec.decodeFromWav(wav, BfskProfile.DATA_ROBUST)
            assertTrue("data-robust должен принять '$text': ${decoded.errorType}", decoded.ok)
            assertEquals(text, decoded.text)
            assertTrue(decoded.crcOk)
            assertEquals(32, decoded.preambleMatches)
        }
    }

    @Test
    fun wrongProfileIsRejected() {
        val (wav, _) = DataLinkCodec.encodeToWav("Ping", BfskProfile.DATA_AUDIBLE)
        val decoded = DataLinkCodec.decodeFromWav(wav, BfskProfile.DATA_ROBUST)
        assertTrue(decoded.ok == false)
        assertEquals("signal_not_found", decoded.errorType)
    }

    @Test
    fun payloadLimitsAreEnforced() {
        val empty = runCatching { DataLinkCodec.textToPayload("") }
        assertTrue(empty.exceptionOrNull() is DataLinkCodec.PayloadError)

        val tooLong = runCatching { DataLinkCodec.textToPayload("A".repeat(65)) }
        assertTrue(tooLong.exceptionOrNull() is DataLinkCodec.PayloadError)

        val maxText = "A".repeat(64)
        assertEquals(64, DataLinkCodec.textToPayload(maxText).size)
    }

    @Test
    fun payloadUtf8ErrorReportsCrcOk() {
        val payload = byteArrayOf(0xFF.toByte(), 0xFE.toByte(), 0x00)
        val frame = FrameV1.build(payload, maxPayloadBytes = 512)
        val samples = BfskModulator.bitsToSamples(frame.bits, BfskProfile.DATA_AUDIBLE)
        val pcm = SignalGenerator.toPcm16LittleEndian(samples)
        val wav = org.silentnetwork.app.audiolab.dsp.WavFileHandler.encodePcm16Wav(pcm, 48000, 1)
        val decoded = DataLinkCodec.decodeFromWav(wav, BfskProfile.DATA_AUDIBLE)
        assertTrue(decoded.ok == false)
        assertEquals("payload_utf8_error", decoded.errorType)
        assertTrue(decoded.crcOk)
    }

    @Test
    fun receiveReportStoresThresholdNotActualMatches() {
        val (wav, _) = DataLinkCodec.encodeToWav("Ping", BfskProfile.DATA_AUDIBLE)
        val decoded = DataLinkCodec.decodeFromWav(
            wav,
            BfskProfile.DATA_AUDIBLE,
            minPreambleMatches = 28
        )
        assertTrue(decoded.ok)
        assertEquals(32, decoded.preambleMatches)
        val report = org.json.JSONObject(
            DataLinkCodec.buildReceiveReport(
                decoded,
                "sn028-w2p-30cm-M1-01.wav",
                "abc",
                minPreambleMatches = 28
            )
        )
        assertEquals(28, report.getInt("min_preamble_matches"))
        assertEquals(32, report.getInt("preamble_matches"))
    }

    @Test
    fun periodicPreambleDoesNotLockTwoSymbolsEarly() {
        val payload = DataLinkCodec.textToPayload("PERIODIC")
        val frame = FrameV1.build(payload)
        val profile = BfskProfile.DATA_AUDIBLE
        val bits = ByteArray(frame.bits.size + 2)
        bits[0] = 1
        bits[1] = 0
        frame.bits.copyInto(bits, 2)
        val tone = BfskModulator.bitsToSamples(bits, profile)
        val lead = FloatArray(1000)
        val tail = FloatArray(1000)
        val samples = FloatArray(lead.size + tone.size + tail.size)
        lead.copyInto(samples, 0)
        tone.copyInto(samples, lead.size)
        tail.copyInto(samples, lead.size + tone.size)

        val decoded = BfskDemodulator.decode(samples, profile)

        assertArrayEquals(payload, decoded.payload)
        assertEquals(frame.crc, decoded.crc)
    }

    @Test
    fun crcSelectsStartInsideSymbolPlateau() {
        val payload = DataLinkCodec.textToPayload("SHIFT")
        val frame = FrameV1.build(payload)
        val profile = BfskProfile.DATA_AUDIBLE
        val samples = BfskModulator.bitsToSamples(frame.bits, profile)
        val metric = BfskDemodulator.bitMetric(samples, profile)
        val candidates = BfskDemodulator.findFrameCandidates(metric, profile)
        val trueStart = candidates.minOf { it.sample }
        val shifted = listOf(
            BfskDemodulator.FrameStart(
                sample = trueStart + profile.samplesPerSymbol / 2,
                preambleMatches = BfskDemodulator.PREAMBLE_BITS,
                coarseSample = trueStart
            )
        )

        val decoded = BfskDemodulator.decodeCandidates(metric, profile, shifted)

        assertArrayEquals(payload, decoded.payload)
        assertEquals(frame.crc, decoded.crc)
    }

    @Test
    fun decodesAtZeroSnrWithDeterministicNoise() {
        val payload = DataLinkCodec.textToPayload("NOISY")
        val frame = FrameV1.build(payload)
        val profile = BfskProfile.DATA_AUDIBLE
        val tone = BfskModulator.bitsToSamples(frame.bits, profile)
        val samples = FloatArray(4800 + tone.size + 2400)
        tone.copyInto(samples, 4800)
        var sumSquares = 0.0
        for (value in samples) sumSquares += value.toDouble() * value
        val rms = kotlin.math.sqrt(sumSquares / samples.size)
        val random = java.util.Random(20261104L)

        repeat(5) {
            val noisy = FloatArray(samples.size) {
                (samples[it] + random.nextGaussian() * rms).toFloat()
            }
            val decoded = BfskDemodulator.decode(
                noisy,
                profile,
                minPreambleMatches = 28
            )
            assertArrayEquals(payload, decoded.payload)
        }
    }

    private fun hexToBits(hex: String): ByteArray {
        val bytes = ByteArray(hex.length / 2)
        for (index in bytes.indices) {
            bytes[index] = hex.substring(index * 2, index * 2 + 2).toInt(16).toByte()
        }
        val bits = ByteArray(bytes.size * 8)
        for (byteIndex in bytes.indices) {
            val value = bytes[byteIndex].toInt() and 0xFF
            for (bitIndex in 0 until 8) {
                bits[byteIndex * 8 + bitIndex] = ((value shr (7 - bitIndex)) and 1).toByte()
            }
        }
        return bits
    }
}
