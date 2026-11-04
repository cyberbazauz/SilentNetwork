package org.silentnetwork.app.audiolab.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
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
 *
 * PLAYBACK CORRECTNESS:
 * The AudioTrack buffer is deliberately small (a fraction of a second) instead
 * of being sized to the whole PCM buffer. If the whole message is pushed into
 * one huge buffer, `write()` returns immediately and a fixed "drain" sleep is
 * not enough: releasing the track truncates the sound to a click. A bounded
 * stream buffer plus a playback-head wait guarantees that the complete message
 * is actually played before `onComplete` is reported.
 *
 * @param contextProvider lazy context accessor used for audio focus and
 *   media-volume diagnostics. It is only invoked when playback starts, so the
 *   engine remains safe to construct in JVM unit tests without Android.
 */
class AndroidAudioPlayerEngine(
    private val contextProvider: (() -> Context)? = null
) : AudioPlayerEngine {

    @Volatile
    private var audioTrack: AudioTrack? = null

    private val _isPlaying = AtomicBoolean(false)
    override val isPlaying: Boolean get() = _isPlaying.get()

    private var playbackThread: Thread? = null
    private var audioManager: AudioManager? = null
    private var focusRequest: AudioFocusRequest? = null

    @Volatile
    private var aborted = false

    private val trackLock = Any()

    override fun playPcm(
        pcmData: ByteArray,
        sampleRate: Int,
        onProgress: (elapsedSeconds: Double, totalSeconds: Double) -> Unit,
        onComplete: () -> Unit,
        onError: (Throwable) -> Unit
    ): Result<Unit> {
        if (pcmData.isEmpty()) {
            return Result.failure(IllegalArgumentException("PCM-буфер пуст"))
        }
        if (sampleRate <= 0) {
            return Result.failure(IllegalArgumentException("Некорректная частота дискретизации: $sampleRate"))
        }
        if (!_isPlaying.compareAndSet(false, true)) {
            return Result.failure(IllegalStateException("Воспроизведение уже выполняется"))
        }
        aborted = false

        return try {
            val manager = contextProvider?.invoke()
                ?.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
            audioManager = manager
            manager?.let { requireAudibleMediaVolume(it) }
            requestAudioFocus(manager)

            val minBufSize = AudioTrack.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            )
            if (minBufSize <= 0) {
                throw IllegalStateException(
                    "Устройство не поддерживает PCM16 mono $sampleRate Гц (код $minBufSize)"
                )
            }

            // 100–250 мс звука: достаточно против underrun, но не настолько
            // много, чтобы release() обрезал сообщение.
            val oneSecondBytes = sampleRate * 2
            val bufferSize = minOf(
                maxOf(minBufSize * 2, 8192),
                maxOf(oneSecondBytes / 4, minBufSize)
            )

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

            if (track.state != AudioTrack.STATE_INITIALIZED) {
                track.release()
                throw IllegalStateException("AudioTrack не инициализирован (state=${track.state})")
            }

            synchronized(trackLock) { audioTrack = track }
            track.setVolume(1.0f)

            val totalSamples = pcmData.size / 2
            val totalSeconds = totalSamples.toDouble() / sampleRate.toDouble()

            playbackThread = Thread {
                try {
                    track.play()
                    if (track.playState != AudioTrack.PLAYSTATE_PLAYING) {
                        throw IllegalStateException("AudioTrack не перешёл в состояние PLAYING")
                    }

                    var offset = 0
                    val chunkSize = minOf(maxOf(bufferSize / 2, 1024), 8192)

                    while (offset < pcmData.size && !aborted) {
                        val toWrite = minOf(chunkSize, pcmData.size - offset)
                        val written = track.write(pcmData, offset, toWrite)
                        if (written < 0) {
                            throw IllegalStateException("AudioTrack.write вернул ошибку $written")
                        }
                        if (written == 0) {
                            Thread.sleep(5)
                            continue
                        }
                        offset += written

                        val elapsedSamples = offset / 2
                        val elapsedSeconds = elapsedSamples.toDouble() / sampleRate.toDouble()
                        onProgress(elapsedSeconds, totalSeconds)
                    }

                    if (!aborted) {
                        // Дать аппаратному буферу доиграть полностью. Без этого
                        // release() обрезает хвост кадра, и CRC на приёмнике
                        // закономерно не сходится.
                        waitForPlaybackDrain(track, totalSamples, totalSeconds)
                        if (!aborted) {
                            onComplete()
                        }
                    }
                } catch (t: Throwable) {
                    if (!aborted) {
                        onError(t)
                    }
                } finally {
                    _isPlaying.set(false)
                    releaseTrackImmediate()
                    abandonAudioFocus()
                }
            }.apply {
                name = "AudioLab-PlayerThread"
                start()
            }

            Result.success(Unit)
        } catch (t: Throwable) {
            _isPlaying.set(false)
            releaseTrackImmediate()
            abandonAudioFocus()
            Result.failure(t)
        }
    }

    override fun stop() {
        aborted = true
        _isPlaying.set(false)
        playbackThread?.interrupt()
        releaseTrackImmediate()
        abandonAudioFocus()
    }

    override fun release() {
        stop()
    }

    private fun waitForPlaybackDrain(
        track: AudioTrack,
        totalSamples: Int,
        totalSeconds: Double
    ) {
        val deadline = System.currentTimeMillis() +
            ((totalSeconds + 2.0) * 1000.0).toLong()
        while (!aborted && System.currentTimeMillis() < deadline) {
            val played = (track.playbackHeadPosition.toLong() and 0xFFFFFFFFL)
            if (played >= totalSamples) return
            Thread.sleep(20)
        }
    }

    private fun requireAudibleMediaVolume(manager: AudioManager) {
        val current = manager.getStreamVolume(AudioManager.STREAM_MUSIC)
        val max = manager.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        if (max > 0 && current <= 0) {
            throw IllegalStateException(
                "Громкость мультимедиа равна 0. Увеличьте громкость телефона и повторите."
            )
        }
    }

    private fun requestAudioFocus(manager: AudioManager?) {
        if (manager == null) return
        try {
            val attributes = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                .build()
            val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(attributes)
                .setOnAudioFocusChangeListener { }
                .build()
            focusRequest = request
            manager.requestAudioFocus(request)
        } catch (_: Throwable) {
            // Фокус — улучшение, а не условие воспроизведения. Если система
            // отказала, AudioTrack всё равно попробует проиграть сигнал.
        }
    }

    private fun abandonAudioFocus() {
        val request = focusRequest ?: return
        focusRequest = null
        try {
            audioManager?.abandonAudioFocusRequest(request)
        } catch (_: Throwable) {
        }
        audioManager = null
    }

    private fun releaseTrackImmediate() {
        val track = synchronized(trackLock) {
            val current = audioTrack
            audioTrack = null
            current
        } ?: return
        try {
            if (track.playState == AudioTrack.PLAYSTATE_PLAYING) {
                track.pause()
                track.flush()
            }
            track.stop()
        } catch (_: Throwable) {
        }
        try {
            track.release()
        } catch (_: Throwable) {
        }
    }
}
