package org.silentnetwork.app.audiolab.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Android HAL implementation of AudioRecorderEngine using AudioRecord.
 *
 * CRITICAL SAFETY RULES:
 * 1. AudioRecord is NOT created or opened at application initialization.
 * 2. It is ONLY instantiated and started upon explicit user button tap.
 * 3. Never runs hidden background captures.
 * 4. Strictly releases hardware microphone upon stop or fragment destruction.
 */
class AndroidAudioRecorderEngine : AudioRecorderEngine {

    @Volatile
    private var audioRecord: AudioRecord? = null

    private val _isRecording = AtomicBoolean(false)
    override val isRecording: Boolean get() = _isRecording.get()

    private var recordThread: Thread? = null

    var selectedAudioSource: Int = MediaRecorder.AudioSource.UNPROCESSED
        private set

    var actualSampleRate: Int = 48000
        private set

    @SuppressLint("MissingPermission")
    override fun startRecording(
        sampleRate: Int,
        durationSeconds: Int,
        onProgress: (elapsedSeconds: Double) -> Unit,
        onComplete: (pcmData: ByteArray, actualSampleRate: Int) -> Unit,
        onError: (Throwable) -> Unit
    ): Result<Unit> {
        if (_isRecording.get()) {
            return Result.failure(IllegalStateException("Запись уже выполняется"))
        }

        // Try UNPROCESSED first, then VOICE_RECOGNITION, then MIC
        val sourcesToTry = listOf(
            MediaRecorder.AudioSource.UNPROCESSED,
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            MediaRecorder.AudioSource.MIC
        )

        val ratesToTry = if (sampleRate == 48000) listOf(48000, 44100) else listOf(sampleRate, 48000)

        var recordInstance: AudioRecord? = null
        var chosenSource = MediaRecorder.AudioSource.MIC
        var chosenRate = sampleRate
        var bufferSizeBytes = 0

        for (source in sourcesToTry) {
            for (rate in ratesToTry) {
                try {
                    val minBuf = AudioRecord.getMinBufferSize(
                        rate,
                        AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT
                    )
                    if (minBuf > 0) {
                        val bufSize = maxOf(minBuf * 2, 4096)
                        val candidate = AudioRecord(
                            source,
                            rate,
                            AudioFormat.CHANNEL_IN_MONO,
                            AudioFormat.ENCODING_PCM_16BIT,
                            bufSize
                        )
                        if (candidate.state == AudioRecord.STATE_INITIALIZED) {
                            recordInstance = candidate
                            chosenSource = source
                            chosenRate = rate
                            bufferSizeBytes = bufSize
                            break
                        } else {
                            candidate.release()
                        }
                    }
                } catch (_: Throwable) {
                    // Try next combination
                }
            }
            if (recordInstance != null) break
        }

        if (recordInstance == null) {
            return Result.failure(
                IllegalStateException("Не удалось инициализировать AudioRecord (проверьте разрешение RECORD_AUDIO и доступность микрофона)")
            )
        }

        audioRecord = recordInstance
        selectedAudioSource = chosenSource
        actualSampleRate = chosenRate
        _isRecording.set(true)

        val totalTargetSamples = chosenRate * durationSeconds
        val totalTargetBytes = totalTargetSamples * 2

        recordThread = Thread {
            val outputStream = ByteArrayOutputStream()
            val chunk = ByteArray(2048)
            var totalReadBytes = 0

            try {
                recordInstance.startRecording()

                while (_isRecording.get() && totalReadBytes < totalTargetBytes) {
                    val toRead = minOf(chunk.size, totalTargetBytes - totalReadBytes)
                    val read = recordInstance.read(chunk, 0, toRead)

                    if (read > 0) {
                        outputStream.write(chunk, 0, read)
                        totalReadBytes += read
                        val elapsedSamples = totalReadBytes / 2
                        val elapsedSec = elapsedSamples.toDouble() / chosenRate.toDouble()
                        onProgress(elapsedSec)
                    } else if (read < 0) {
                        throw IllegalStateException("AudioRecord error code: $read")
                    }
                }

                val finalPcm = outputStream.toByteArray()
                onComplete(finalPcm, chosenRate)
            } catch (t: Throwable) {
                if (_isRecording.get()) {
                    onError(t)
                }
            } finally {
                cleanUpRecord()
            }
        }.apply {
            name = "AudioLab-RecordThread"
            start()
        }

        return Result.success(Unit)
    }

    override fun stop() {
        _isRecording.set(false)
        recordThread?.interrupt()
        cleanUpRecord()
    }

    override fun release() {
        stop()
    }

    private fun cleanUpRecord() {
        _isRecording.set(false)
        try {
            audioRecord?.let { record ->
                if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                    record.stop()
                }
                record.release()
            }
        } catch (_: Throwable) {
        } finally {
            audioRecord = null
        }
    }
}
