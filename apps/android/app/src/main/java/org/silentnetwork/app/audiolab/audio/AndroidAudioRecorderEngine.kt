package org.silentnetwork.app.audiolab.audio

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
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
 * 5. Prefers TYPE_BUILTIN_MIC, but reports the actual routed device instead of
 *    pretending that the preference was honoured.
 *
 * @param contextProvider lazy context accessor. It is invoked only when a
 *   capture actually starts, so constructing the engine never touches Android
 *   framework services and remains safe for JVM unit tests.
 */
class AndroidAudioRecorderEngine(
    private val contextProvider: (() -> Context)? = null
) : AudioRecorderEngine {

    data class RecorderRouteInfo(
        val requestedAudioSource: String,
        val actualAudioSource: String,
        val requestedSampleRate: Int,
        val actualSampleRate: Int,
        val requestedDevice: String,
        val actualRoutedDevice: String?,
        val bufferSizeBytes: Int
    )

    @Volatile
    private var audioRecord: AudioRecord? = null

    private val _isRecording = AtomicBoolean(false)
    override val isRecording: Boolean get() = _isRecording.get()

    private var recordThread: Thread? = null

    @Volatile
    var lastRouteInfo: RecorderRouteInfo? = null
        private set

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

        val builtinMic = try {
            val context = contextProvider?.invoke()
            val audioManager = context?.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
            audioManager?.getDevices(AudioManager.GET_DEVICES_INPUTS)?.firstOrNull {
                it.type == AudioDeviceInfo.TYPE_BUILTIN_MIC
            }
        } catch (_: Throwable) {
            null
        }
        val requestedDevice = if (builtinMic != null) {
            AudioRouteInfo.REQUESTED_BUILTIN_MIC
        } else {
            "default_input"
        }

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
                            // Request the built-in microphone. This is only a
                            // preference: the OS may route to another device.
                            if (builtinMic != null) {
                                try {
                                    candidate.setPreferredDevice(builtinMic)
                                } catch (_: Throwable) {
                                    // Route preference is best-effort.
                                }
                            }
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
        val requestedAudioSource = AudioRouteInfo.sourceName(sourcesToTry.first())
        val actualAudioSource = AudioRouteInfo.sourceName(chosenSource)
        lastRouteInfo = RecorderRouteInfo(
            requestedAudioSource = requestedAudioSource,
            actualAudioSource = actualAudioSource,
            requestedSampleRate = sampleRate,
            actualSampleRate = chosenRate,
            requestedDevice = requestedDevice,
            actualRoutedDevice = null,
            bufferSizeBytes = bufferSizeBytes
        )
        _isRecording.set(true)

        val totalTargetSamples = chosenRate * durationSeconds
        val totalTargetBytes = totalTargetSamples * 2

        recordThread = Thread {
            val outputStream = ByteArrayOutputStream()
            val chunk = ByteArray(2048)
            var totalReadBytes = 0

            try {
                recordInstance.startRecording()

                val routed = try {
                    recordInstance.routedDevice
                } catch (_: Throwable) {
                    null
                }
                lastRouteInfo = RecorderRouteInfo(
                    requestedAudioSource = requestedAudioSource,
                    actualAudioSource = actualAudioSource,
                    requestedSampleRate = sampleRate,
                    actualSampleRate = chosenRate,
                    requestedDevice = requestedDevice,
                    actualRoutedDevice = routed?.let {
                        AudioRouteInfo.describeDevice(it.type, it.productName?.toString())
                    },
                    bufferSizeBytes = bufferSizeBytes
                )

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
                // Ручная остановка — это отмена: частичный буфер не должен
                // декодироваться как принятый кадр. При естественном
                // завершении записи _isRecording остаётся true до finally.
                if (!_isRecording.get() || Thread.currentThread().isInterrupted) {
                    return@Thread
                }
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
