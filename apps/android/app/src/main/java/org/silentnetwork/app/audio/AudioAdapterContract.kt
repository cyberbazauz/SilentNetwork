package org.silentnetwork.app.audio

/**
 * Boundary contract for Android Audio HAL adapter (AudioRecord / AudioTrack).
 * NOTE: Exact parameters (sample rate, buffer chunk size, thread model)
 * will be aligned with docs/android-core-api.md once published by Agent 1.
 */
interface AudioAdapterContract {
    val isRecording: Boolean
    val isPlaying: Boolean

    /**
     * Start capturing PCM audio buffer from physical microphone.
     */
    fun startCapture(onBufferAvailable: (ByteArray) -> Unit): Result<Unit>

    /**
     * Stop capturing audio and release hardware resources.
     */
    fun stopCapture()

    /**
     * Play modulated acoustic PCM buffer through device speaker.
     */
    fun playChunk(pcmChunk: ByteArray): Result<Unit>
}
