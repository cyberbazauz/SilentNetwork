package org.silentnetwork.app.audiolab.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Android HAL implementation of AudioPlayerEngine using AudioTrack.
 *
 * CRITICAL SAFETY RULES:
 * 1. AudioTrack is NOT created or opened at application initialization.
 * 2. It is ONLY instantiated and started upon explicit user button tap.
 * 3. Does not alter system volume level.
 * 4. Can be stopped immediately at any time.
 */
class AndroidAudioPlayerEngine : AudioPlayerEngine {

    @Volatile
    private var audioTrack: AudioTrack? = null

    private val _isPlaying = AtomicBoolean(false)
    override val isPlaying: Boolean get() = _isPlaying.get()

    private var playbackThread: Thread? = null

    override fun playPcm(
        pcmData: ByteArray,
        sampleRate: Int,
        onProgress: (elapsedSeconds: Double, totalSeconds: Double) -> Unit,
        onComplete: () -> Unit,
        onError: (Throwable) -> Unit
    ): Result<Unit> {
        if (_isPlaying.get()) {
            return Result.failure(IllegalStateException("Воспроизведение уже выполняется"))
        }

        return try {
            val minBufSize = AudioTrack.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            )

            val bufferSize = maxOf(minBufSize, pcmData.size, 4096)

            val track = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setSampleRate(sampleRate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .build()
                )
                .setBufferSizeInBytes(bufferSize)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()

            audioTrack = track
            _isPlaying.set(true)

            val totalSamples = pcmData.size / 2
            val totalSeconds = totalSamples.toDouble() / sampleRate.toDouble()

            playbackThread = Thread {
                try {
                    track.play()
                    var offset = 0
                    val chunkSize = 2048

                    while (offset < pcmData.size && _isPlaying.get()) {
                        val toWrite = minOf(chunkSize, pcmData.size - offset)
                        val written = track.write(pcmData, offset, toWrite)
                        if (written <= 0) break
                        offset += written

                        val elapsedSamples = offset / 2
                        val elapsedSeconds = elapsedSamples.toDouble() / sampleRate.toDouble()
                        onProgress(elapsedSeconds, totalSeconds)
                    }

                    if (_isPlaying.get()) {
                        // Allow tail buffer to drain
                        Thread.sleep(100)
                        onComplete()
                    }
                } catch (t: Throwable) {
                    if (_isPlaying.get()) {
                        onError(t)
                    }
                } finally {
                    cleanUpTrack()
                }
            }.apply {
                name = "AudioLab-PlayerThread"
                start()
            }

            Result.success(Unit)
        } catch (t: Throwable) {
            _isPlaying.set(false)
            cleanUpTrack()
            Result.failure(t)
        }
    }

    override fun stop() {
        _isPlaying.set(false)
        playbackThread?.interrupt()
        cleanUpTrack()
    }

    override fun release() {
        stop()
    }

    private fun cleanUpTrack() {
        _isPlaying.set(false)
        try {
            audioTrack?.let { track ->
                if (track.playState == AudioTrack.PLAYSTATE_PLAYING) {
                    track.stop()
                }
                track.release()
            }
        } catch (_: Throwable) {
        } finally {
            audioTrack = null
        }
    }
}
