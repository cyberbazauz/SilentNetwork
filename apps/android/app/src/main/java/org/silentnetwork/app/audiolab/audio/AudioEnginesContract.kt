package org.silentnetwork.app.audiolab.audio

/**
 * Contracts for audio playback and capture engines.
 * Enables clean decoupling of UI and business logic from physical Android HAL,
 * ensuring deterministic testability with fake/mock engines.
 */
interface AudioPlayerEngine {
    val isPlaying: Boolean

    /**
     * Feeds PCM16 mono audio buffer to the player.
     * Must be called explicitly upon user interaction.
     */
    fun playPcm(
        pcmData: ByteArray,
        sampleRate: Int,
        onProgress: (elapsedSeconds: Double, totalSeconds: Double) -> Unit = { _, _ -> },
        onComplete: () -> Unit = {},
        onError: (Throwable) -> Unit = {}
    ): Result<Unit>

    /**
     * Immediately aborts any ongoing playback and drains buffers.
     */
    fun stop()

    /**
     * Releases hardware resources.
     */
    fun release()
}

interface AudioRecorderEngine {
    val isRecording: Boolean

    /**
     * Begins capturing PCM16 mono audio from microphone.
     * Must be called explicitly upon user interaction.
     */
    fun startRecording(
        sampleRate: Int,
        durationSeconds: Int,
        onProgress: (elapsedSeconds: Double) -> Unit,
        onComplete: (pcmData: ByteArray, actualSampleRate: Int) -> Unit,
        onError: (Throwable) -> Unit
    ): Result<Unit>

    /**
     * Immediately terminates recording and delivers captured audio so far.
     */
    fun stop()

    /**
     * Releases hardware resources.
     */
    fun release()
}

data class AudioHardwareEffectsInfo(
    val aecAvailable: Boolean,
    val agcAvailable: Boolean,
    val nsAvailable: Boolean,
    val description: String
)
