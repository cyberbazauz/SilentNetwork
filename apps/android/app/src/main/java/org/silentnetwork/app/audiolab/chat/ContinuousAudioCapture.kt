package org.silentnetwork.app.audiolab.chat

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.max
import kotlin.math.min

/**
 * Continuous microphone capture for the chat mode.
 *
 * Unlike [org.silentnetwork.app.audiolab.audio.AndroidAudioRecorderEngine],
 * this engine does not store a WAV and has no fixed duration. It reads fixed
 * PCM16 blocks in a background thread, converts them to `FloatArray`, and
 * hands them to a single consumer thread through a bounded queue.
 *
 * The microphone stays open while [pause]d so the caller can transmit without
 * re-initialising AudioRecord. Samples captured during a pause are discarded,
 * and [onGap] is invoked on the consumer thread afterwards: a frame that
 * crossed the pause boundary is incomplete and the decoder must resynchronise.
 * A full queue is treated the same way: old audio is lost, so pretending that
 * the stream is continuous would corrupt the decoder state.
 *
 * The caller must hold `RECORD_AUDIO` and must call [stop] (or [release]) when
 * the screen is destroyed. No hidden capture is allowed.
 */
class ContinuousAudioCapture(
    private val sampleRate: Int = 48_000,
    private val blockFrames: Int = 4_096,
    private val queueCapacity: Int = 16,
    private val onSamples: (FloatArray) -> Unit,
    private val onError: (Throwable) -> Unit,
    private val onGap: (String) -> Unit = {}
) {
    private val running = AtomicBoolean(false)
    private val queue = ArrayBlockingQueue<FloatArray>(queueCapacity)
    private val droppedBlocks = AtomicLong(0)

    @Volatile
    private var paused = false

    @Volatile
    private var gapReason: String? = null

    @Volatile
    private var audioRecord: AudioRecord? = null
    private var readThread: Thread? = null
    private var consumeThread: Thread? = null

    val isRunning: Boolean get() = running.get()
    val isPaused: Boolean get() = paused
    val droppedBlockCount: Long get() = droppedBlocks.get()

    @SuppressLint("MissingPermission")
    fun start(): Result<Unit> {
        if (!running.compareAndSet(false, true)) {
            return Result.failure(IllegalStateException("Запись уже запущена"))
        }
        if (sampleRate <= 0 || blockFrames <= 0 || queueCapacity <= 0) {
            running.set(false)
            return Result.failure(IllegalArgumentException("Некорректные параметры захвата"))
        }

        return try {
            val minBuffer = AudioRecord.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            )
            if (minBuffer <= 0) {
                throw IllegalStateException(
                    "Микрофон не поддерживает PCM16 mono $sampleRate Гц (код $minBuffer)"
                )
            }
            val record = AudioRecord.Builder()
                .setAudioSource(MediaRecorder.AudioSource.MIC)
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setSampleRate(sampleRate)
                        .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .build()
                )
                .setBufferSizeInBytes(max(minBuffer * 2, blockFrames * 4))
                .build()
            if (record.state != AudioRecord.STATE_INITIALIZED) {
                record.release()
                throw IllegalStateException("AudioRecord не инициализирован (state=${record.state})")
            }
            audioRecord = record
            paused = false
            gapReason = null
            record.startRecording()
            if (record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                throw IllegalStateException("AudioRecord не начал запись")
            }
            droppedBlocks.set(0)

            consumeThread = Thread {
                consumeLoop()
            }.apply {
                name = "AcousticChat-Consumer"
                isDaemon = true
                start()
            }
            readThread = Thread {
                readLoop(record)
            }.apply {
                name = "AcousticChat-AudioRecord"
                isDaemon = true
                start()
            }
            Result.success(Unit)
        } catch (error: Throwable) {
            running.set(false)
            releaseRecord()
            Result.failure(error)
        }
    }

    /**
     * Stop consuming microphone samples without closing AudioRecord. The read
     * thread keeps draining the driver buffer so it does not overflow.
     */
    fun pause() {
        if (!running.get()) return
        paused = true
    }

    /** Resume consumption after [pause]; the decoder is told about the gap. */
    fun resume() {
        if (!running.get()) return
        paused = false
        gapReason = "pause"
    }

    fun stop() {
        if (!running.getAndSet(false)) return
        paused = false
        readThread?.interrupt()
        consumeThread?.interrupt()
        releaseRecord()
        joinQuietly(readThread)
        joinQuietly(consumeThread)
        readThread = null
        consumeThread = null
        queue.clear()
    }

    fun release() {
        stop()
    }

    private fun readLoop(record: AudioRecord) {
        val blockBytes = blockFrames * 2
        val pcm = ByteArray(blockBytes)
        try {
            while (running.get()) {
                val read = record.read(pcm, 0, pcm.size)
                if (read < 0) {
                    if (running.get()) {
                        onError(IllegalStateException("AudioRecord.read вернул ошибку $read"))
                    }
                    break
                }
                if (read == 0) continue
                if (paused || !running.get()) {
                    droppedBlocks.incrementAndGet()
                    continue
                }
                val samples = pcm16ToFloat(pcm, read)
                if (!queue.offer(samples)) {
                    droppedBlocks.incrementAndGet()
                    gapReason = "overflow"
                    // Queued blocks predate the lost one. Keeping them would
                    // hide the discontinuity: they would be decoded and then
                    // followed by newer audio with no resynchronisation.
                    queue.clear()
                }
            }
        } catch (error: Throwable) {
            if (running.get()) {
                onError(error)
            }
        } finally {
            running.set(false)
        }
    }

    private fun consumeLoop() {
        try {
            while (running.get() || queue.isNotEmpty()) {
                val reason = gapReason
                if (reason != null) {
                    gapReason = null
                    onGap(reason)
                }
                val block = queue.poll(100, TimeUnit.MILLISECONDS) ?: continue
                onSamples(block)
            }
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        } catch (error: Throwable) {
            onError(error)
        }
    }

    private fun pcm16ToFloat(pcm: ByteArray, length: Int): FloatArray {
        val safeLength = min(length, pcm.size) and 1.inv()
        val frames = safeLength / 2
        val samples = FloatArray(frames)
        var index = 0
        for (frame in 0 until frames) {
            val low = pcm[index].toInt() and 0xFF
            val high = pcm[index + 1].toInt()
            val value = (high shl 8) or low
            samples[frame] = (value / 32768.0).toFloat()
            index += 2
        }
        return samples
    }

    private fun releaseRecord() {
        val record = audioRecord
        audioRecord = null
        if (record == null) return
        try {
            if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                record.stop()
            }
        } catch (_: Throwable) {
        }
        try {
            record.release()
        } catch (_: Throwable) {
        }
    }

    private fun joinQuietly(thread: Thread?) {
        if (thread == null) return
        try {
            thread.join(1_000)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
    }
}
