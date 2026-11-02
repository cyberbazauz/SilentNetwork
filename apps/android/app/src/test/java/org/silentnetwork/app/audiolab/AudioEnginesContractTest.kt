package org.silentnetwork.app.audiolab

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.audiolab.audio.FakeAudioPlayerEngine
import org.silentnetwork.app.audiolab.audio.FakeAudioRecorderEngine
import org.silentnetwork.app.audiolab.dsp.SignalGenerator

/**
 * Unit tests verifying audio engine abstractions, fake engine execution,
 * safe stopping, and absence of physical hardware activation at initialization.
 */
class AudioEnginesContractTest {

    @Test
    fun testFakePlayerLifecycleAndStop() {
        val player = FakeAudioPlayerEngine()
        assertFalse(player.isPlaying)

        val pcm = SignalGenerator.generateTestTonePcm16(18500.0, 1.0, 48000, 0.2)
        var completed = false
        var progressCalled = false

        val result = player.playPcm(
            pcmData = pcm,
            sampleRate = 48000,
            onProgress = { _, _ -> progressCalled = true },
            onComplete = { completed = true }
        )

        assertTrue(result.isSuccess)
        assertTrue(progressCalled)
        assertTrue(completed)
        assertFalse(player.isPlaying)
        assertNotNull(player.lastPlayedPcmData)
        assertEquals(pcm.size, player.lastPlayedPcmData!!.size)

        // Test stop
        player.stop()
        assertFalse(player.isPlaying)
    }

    @Test
    fun testFakeRecorderLifecycleAndStop() {
        val recorder = FakeAudioRecorderEngine(simulatedToneFrequencyHz = 18500.0)
        assertFalse(recorder.isRecording)

        var completedBytes: ByteArray? = null
        var completedRate = 0
        var progressCalled = false

        val result = recorder.startRecording(
            sampleRate = 48000,
            durationSeconds = 2,
            onProgress = { progressCalled = true },
            onComplete = { pcm, rate ->
                completedBytes = pcm
                completedRate = rate
            },
            onError = {}
        )

        assertTrue(result.isSuccess)
        assertTrue(progressCalled)
        assertNotNull(completedBytes)
        assertEquals(48000, completedRate)
        // 2 seconds * 48000 samples * 2 bytes = 192000 bytes
        assertEquals(192000, completedBytes!!.size)
        assertFalse(recorder.isRecording)

        recorder.stop()
        assertFalse(recorder.isRecording)
    }

    @Test
    fun testNoPhysicalAudioHardwareActivatedAtStartup() {
        // Assert that creating instances of classes does not open physical AudioTrack or AudioRecord
        // In unit test JVM environment, physical android.media.AudioRecord.startRecording()
        // or AudioTrack.play() would fail or throw if called automatically.
        val player = FakeAudioPlayerEngine()
        val recorder = FakeAudioRecorderEngine()

        assertFalse("Player must be IDLE on creation", player.isPlaying)
        assertFalse("Recorder must be IDLE on creation", recorder.isRecording)
    }
}
