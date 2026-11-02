package org.silentnetwork.app.audiolab

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
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
            assertEquals("Sample $i mismatch", originalFloats[i], decoded.samples[i], 0.001f)
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
