package org.silentnetwork.app.audiolab.chat

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.audiolab.datalink.BfskModulator
import org.silentnetwork.app.audiolab.datalink.BfskProfile
import org.silentnetwork.app.audiolab.datalink.FileTransferCodec
import org.silentnetwork.app.audiolab.datalink.FrameV1
import org.silentnetwork.app.audiolab.datalink.StreamFrameDecoder
import org.silentnetwork.app.audiolab.dsp.SignalGenerator
import java.io.File
import java.nio.file.Files
import kotlin.random.Random

class StreamFrameDecoderTest {

    // Small, fast profile for unit tests only. It is not an acoustic profile.
    private val profile = BfskProfile(
        name = "test-stream",
        sampleRate = 16_000,
        markHz = 3_000.0,
        spaceHz = 4_000.0,
        symbolRate = 500.0,
        amplitude = 0.6
    )

    private fun frameSamples(payload: ByteArray): FloatArray {
        val frame = FrameV1.build(payload, maxPayloadBytes = 64)
        val modulated = BfskModulator.bitsToSamples(frame.bits, profile, rampMs = 0.5)
        return FloatArray(2_000) + modulated + FloatArray(300)
    }

    @Test
    fun decodesOneFrameFromContinuousStream() {
        val payload = "привет".toByteArray(Charsets.UTF_8)
        val decoder = StreamFrameDecoder(profile)

        val events = decoder.feed(frameSamples(payload))

        assertEquals(1, events.size)
        assertArrayEquals(payload, events[0].payload)
        assertEquals(FrameV1.PREAMBLE_BITS, events[0].preambleMatches)
    }

    @Test
    fun decodesTwoFramesInOneBlockWithoutRepeatingThem() {
        val first = "первый".toByteArray(Charsets.UTF_8)
        val second = "второй".toByteArray(Charsets.UTF_8)
        val decoder = StreamFrameDecoder(profile)
        val samples = frameSamples(first) + FloatArray(500) + frameSamples(second)

        val events = decoder.feed(samples)

        assertEquals(2, events.size)
        assertArrayEquals(first, events[0].payload)
        assertArrayEquals(second, events[1].payload)
        assertTrue(events[1].absoluteStartSample > events[0].absoluteStartSample)
        assertEquals(emptyList<StreamFrameDecoder.FrameEvent>(), decoder.feed(FloatArray(2_000)))
    }

    @Test
    fun noiseProducesNoFrames() {
        val decoder = StreamFrameDecoder(profile)
        val random = Random(20261104)
        val noise = FloatArray(20_000) { (random.nextDouble() * 2.0 - 1.0).toFloat() * 0.2f }

        val events = decoder.feed(noise)

        assertTrue(events.isEmpty())
    }

    @Test
    fun bufferStaysBoundedDuringLongSilence() {
        val decoder = StreamFrameDecoder(profile)
        val random = Random(7)
        repeat(30) {
            val block = FloatArray(4_000) {
                (random.nextDouble() * 2.0 - 1.0).toFloat() * 0.1f
            }
            decoder.feed(block)
            assertTrue(
                "буфер ${decoder.bufferedSamples} отсчётов не ограничен",
                decoder.bufferedSamples <= 40_000
            )
        }
    }
}

class ChatSessionTest {

    private val profile = BfskProfile(
        name = "test-stream",
        sampleRate = 16_000,
        markHz = 3_000.0,
        spaceHz = 4_000.0,
        symbolRate = 500.0,
        amplitude = 0.6
    )

    private fun frameSamples(payload: ByteArray): FloatArray {
        val frame = FrameV1.build(payload, maxPayloadBytes = 64)
        val modulated = BfskModulator.bitsToSamples(frame.bits, profile, rampMs = 0.5)
        return FloatArray(2_000) + modulated + FloatArray(300)
    }

    @Test
    fun receivesTextPayload() {
        val session = ChatSession(profile = profile, minPreambleMatches = 28)
        val text = "тест чата"

        val events = session.feed(frameSamples(text.toByteArray(Charsets.UTF_8)))

        val received = events.filterIsInstance<ChatSession.Event.TextReceived>()
        assertEquals(1, received.size)
        assertEquals(text, received[0].text)
    }

    @Test
    fun assemblesFileAndEmitsOneCompletion() {
        val outputDir = Files.createTempDirectory("sn-chat-test").toFile()
        val content = ByteArray(120) { (it * 7 and 0xFF).toByte() }
        val message = FileTransferCodec.encodeFileToPcm(
            data = content,
            filename = "note.bin",
            profile = BfskProfile.DATA_AUDIBLE,
            transferId = 42
        )
        val session = ChatSession(
            profile = BfskProfile.DATA_AUDIBLE,
            outputDir = outputDir,
            minPreambleMatches = 28
        )

        val events = session.feed(
            SignalGenerator.fromPcm16LittleEndian(message.pcmBytes)
        )

        assertEquals(
            1,
            events.filterIsInstance<ChatSession.Event.FileStarted>().size
        )
        val completed = events.filterIsInstance<ChatSession.Event.FileCompleted>()
        assertEquals(1, completed.size)
        assertEquals("note.bin", completed[0].filename)
        assertEquals(content.size, completed[0].fileSize)
        assertNotNull(completed[0].savedTo)
        val saved = File(completed[0].savedTo!!)
        assertTrue(saved.isFile)
        assertArrayEquals(content, saved.readBytes())
        assertTrue(events.none { it is ChatSession.Event.FileFailed })
    }
}
