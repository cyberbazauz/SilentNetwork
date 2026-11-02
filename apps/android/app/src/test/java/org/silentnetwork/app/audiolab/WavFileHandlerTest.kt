package org.silentnetwork.app.audiolab

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import org.silentnetwork.app.audiolab.dsp.SignalGenerator
import org.silentnetwork.app.audiolab.dsp.WavFileHandler
import java.io.ByteArrayInputStream

/**
 * Unit tests for strict WAV encoding, decoding, and corruption handling.
 * Compatible with snlab/wavio.py.
 */
class WavFileHandlerTest {

    @Test
    fun testWavHeaderFormat() {
        val dummyPcm = ByteArray(100) // 50 samples
        val wavBytes = WavFileHandler.encodePcm16Wav(dummyPcm, sampleRate = 48000, channels = 1)

        assertEquals(44 + 100, wavBytes.size)

        // Verify RIFF identifier
        assertEquals('R'.code.toByte(), wavBytes[0])
        assertEquals('I'.code.toByte(), wavBytes[1])
        assertEquals('F'.code.toByte(), wavBytes[2])
        assertEquals('F'.code.toByte(), wavBytes[3])

        // Verify WAVE identifier
        assertEquals('W'.code.toByte(), wavBytes[8])
        assertEquals('A'.code.toByte(), wavBytes[9])
        assertEquals('V'.code.toByte(), wavBytes[10])
        assertEquals('E'.code.toByte(), wavBytes[11])

        // Verify 'fmt ' identifier
        assertEquals('f'.code.toByte(), wavBytes[12])
        assertEquals('m'.code.toByte(), wavBytes[13])
        assertEquals('t'.code.toByte(), wavBytes[14])
        assertEquals(' '.code.toByte(), wavBytes[15])

        // Verify 'data' identifier
        assertEquals('d'.code.toByte(), wavBytes[36])
        assertEquals('a'.code.toByte(), wavBytes[37])
        assertEquals('t'.code.toByte(), wavBytes[38])
        assertEquals('a'.code.toByte(), wavBytes[39])
    }

    @Test
    fun testWavHeaderFieldsMatchPcm16MonoContract() {
        val sampleRate = 44100
        val sampleCount = 44100
        val pcm = ByteArray(sampleCount * 2)
        val wav = WavFileHandler.encodePcm16Wav(pcm, sampleRate, channels = 1)
        val buffer = java.nio.ByteBuffer.wrap(wav).order(java.nio.ByteOrder.LITTLE_ENDIAN)

        assertEquals("RIFF", String(wav, 0, 4, Charsets.US_ASCII))
        assertEquals(36 + pcm.size, buffer.getInt(4))
        assertEquals("WAVE", String(wav, 8, 4, Charsets.US_ASCII))
        assertEquals("fmt ", String(wav, 12, 4, Charsets.US_ASCII))
        assertEquals(16, buffer.getInt(16)) // PCM fmt chunk size
        assertEquals(1, buffer.getShort(20).toInt()) // PCM format
        assertEquals(1, buffer.getShort(22).toInt()) // mono
        assertEquals(sampleRate, buffer.getInt(24))
        assertEquals(sampleRate * 2, buffer.getInt(28)) // byte rate
        assertEquals(2, buffer.getShort(32).toInt()) // block align
        assertEquals(16, buffer.getShort(34).toInt()) // bits per sample
        assertEquals("data", String(wav, 36, 4, Charsets.US_ASCII))
        assertEquals(pcm.size, buffer.getInt(40))

        val decoded = WavFileHandler.decodePcm16Wav(ByteArrayInputStream(wav))
        assertEquals(sampleRate, decoded.metadata.sampleRate)
        assertEquals(sampleCount, decoded.metadata.sampleCount)
        assertEquals(1.0, decoded.metadata.durationSeconds, 1e-9)
    }

    @Test
    fun testPcm16FullScaleUses32768Divisor() {
        val min = java.nio.ByteBuffer.allocate(4)
            .order(java.nio.ByteOrder.LITTLE_ENDIAN)
            .putShort(Short.MIN_VALUE)
            .putShort(Short.MAX_VALUE)
            .array()
        val wav = WavFileHandler.encodePcm16Wav(min, 48000, 1)
        val decoded = WavFileHandler.decodePcm16Wav(ByteArrayInputStream(wav))

        assertEquals(-1.0f, decoded.samples[0], 0f)
        assertEquals(32767.0f / 32768.0f, decoded.samples[1], 1e-6f)
    }

    @Test
    fun testSha256MatchesRawFileBytesAndIsDeterministic() {
        val pcm = ByteArray(400) { (it % 251).toByte() }
        val wav = WavFileHandler.encodePcm16Wav(pcm, 48000, 1)
        val first = WavFileHandler.sha256Hex(wav)
        val second = WavFileHandler.sha256Hex(wav)

        assertEquals(64, first.length)
        assertEquals(first, second)
        val expected = java.security.MessageDigest.getInstance("SHA-256")
            .digest(wav)
            .joinToString("") { "%02x".format(it) }
        assertEquals(expected, first)
    }

    @Test
    fun testEncodeRejectsIncompletePcm16Frame() {
        try {
            WavFileHandler.encodePcm16Wav(ByteArray(3), 48000, 1)
            fail("Expected IllegalArgumentException for an odd PCM16 payload")
        } catch (_: IllegalArgumentException) {
            // Success
        }
    }

    /**
     * Writes a real WAV produced by Android code so the Python Acoustic Lab
     * reader (snlab/wavio.py) can be run against the exact same bytes.
     */
    @Test
    fun testExportAndroidWavForPythonCompatibility() {
        val sampleRate = 48000
        val samples = SignalGenerator.generateSineWave(
            frequencyHz = 18500.0,
            durationSeconds = 1.0,
            sampleRate = sampleRate,
            amplitude = 0.25,
            leadInPauseSeconds = 0.0,
            leadOutPauseSeconds = 0.0
        )
        val pcm = SignalGenerator.toPcm16LittleEndian(samples)
        val wav = WavFileHandler.encodePcm16Wav(pcm, sampleRate, 1)

        val output = java.io.File("build/sn015-android-wav/sn015_android_rx.wav")
        output.parentFile?.mkdirs()
        output.writeBytes(wav)

        assertTrue(output.length() > 44)
        assertEquals((44 + pcm.size).toLong(), output.length())
    }

    @Test
    fun testWavRoundtripEncodeDecode() {
        val originalFloats = SignalGenerator.generateSineWave(
            frequencyHz = 18500.0,
            durationSeconds = 0.1,
            sampleRate = 48000,
            amplitude = 0.25
        )
        val pcmBytes = SignalGenerator.toPcm16LittleEndian(originalFloats)

        val wavBytes = WavFileHandler.encodePcm16Wav(pcmBytes, sampleRate = 48000, channels = 1)

        val decoded = WavFileHandler.decodePcm16Wav(ByteArrayInputStream(wavBytes))

        assertNotNull(decoded)
        assertEquals(48000, decoded.metadata.sampleRate)
        assertEquals(1, decoded.metadata.channels)
        assertEquals(16, decoded.metadata.bitsPerSample)
        assertEquals(originalFloats.size, decoded.samples.size)

        for (i in originalFloats.indices) {
            assertEquals("Sample $i mismatch", originalFloats[i], decoded.samples[i], 0.0011f)
        }
    }

    @Test
    fun testCorruptedWavTooShort() {
        val shortBytes = ByteArray(20)
        try {
            WavFileHandler.decodePcm16Wav(ByteArrayInputStream(shortBytes))
            fail("Expected IllegalArgumentException for too short WAV")
        } catch (e: IllegalArgumentException) {
            // Success
        }
    }

    @Test
    fun testCorruptedWavInvalidMagic() {
        val dummyPcm = ByteArray(100)
        val wavBytes = WavFileHandler.encodePcm16Wav(dummyPcm, 48000, 1)
        wavBytes[0] = 'X'.code.toByte() // Corrupt RIFF

        try {
            WavFileHandler.decodePcm16Wav(ByteArrayInputStream(wavBytes))
            fail("Expected IllegalArgumentException for invalid magic")
        } catch (e: IllegalArgumentException) {
            // Success
        }
    }

    @Test
    fun testCorruptedWavMissingDataChunk() {
        val invalidHeader = ByteArray(44)
        "RIFF".toByteArray(Charsets.US_ASCII).copyInto(invalidHeader, 0)
        "WAVE".toByteArray(Charsets.US_ASCII).copyInto(invalidHeader, 8)
        "fmt ".toByteArray(Charsets.US_ASCII).copyInto(invalidHeader, 12)

        try {
            WavFileHandler.decodePcm16Wav(ByteArrayInputStream(invalidHeader))
            fail("Expected IllegalArgumentException for missing data")
        } catch (e: IllegalArgumentException) {
            // Success
        }
    }
}
