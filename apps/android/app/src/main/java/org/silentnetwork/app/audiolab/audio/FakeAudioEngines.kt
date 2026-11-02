package org.silentnetwork.app.audiolab.audio

import org.silentnetwork.app.audiolab.dsp.SignalGenerator
import java.util.concurrent.atomic.AtomicBoolean

/**
 * In-memory simulated audio player for headless unit tests and mock mode.
 * Does not interact with physical Android audio hardware.
 */
class FakeAudioPlayerEngine : AudioPlayerEngine {

    private val _isPlaying = AtomicBoolean(false)
    override val isPlaying: Boolean get() = _isPlaying.get()

    var lastPlayedPcmData: ByteArray? = null
        private set
    var lastSampleRate: Int = 0
        private set

    override fun playPcm(
        pcmData: ByteArray,
        sampleRate: Int,
        onProgress: (elapsedSeconds: Double, totalSeconds: Double) -> Unit,
        onComplete: () -> Unit,
        onError: (Throwable) -> Unit
    ): Result<Unit> {
        if (_isPlaying.get()) {
            return Result.failure(IllegalStateException("Simulated player is already playing"))
        }

        _isPlaying.set(true)
        lastPlayedPcmData = pcmData
        lastSampleRate = sampleRate

        val totalSamples = pcmData.size / 2
        val totalSec = totalSamples.toDouble() / sampleRate.toDouble()
        onProgress(0.0, totalSec)
        onProgress(totalSec, totalSec)
        _isPlaying.set(false)
        onComplete()

        return Result.success(Unit)
    }

    override fun stop() {
        _isPlaying.set(false)
    }

    override fun release() {
        stop()
    }
}

/**
 * In-memory simulated audio recorder for headless unit tests and mock mode.
 * Synthesizes test tones or returns injected audio buffers without opening physical microphone.
 */
class FakeAudioRecorderEngine(
    private val simulatedToneFrequencyHz: Double = 18500.0,
    private val simulatedToneAmplitude: Double = 0.25
) : AudioRecorderEngine {

    private val _isRecording = AtomicBoolean(false)
    override val isRecording: Boolean get() = _isRecording.get()

    var simulatedPcmDataToSupply: ByteArray? = null

    override fun startRecording(
        sampleRate: Int,
        durationSeconds: Int,
        onProgress: (elapsedSeconds: Double) -> Unit,
        onComplete: (pcmData: ByteArray, actualSampleRate: Int) -> Unit,
        onError: (Throwable) -> Unit
    ): Result<Unit> {
        if (_isRecording.get()) {
            return Result.failure(IllegalStateException("Simulated recorder is already recording"))
        }

        _isRecording.set(true)
        onProgress(0.0)
        onProgress(durationSeconds.toDouble())

        val pcm = simulatedPcmDataToSupply ?: run {
            val floatSamples = SignalGenerator.generateSineWave(
                frequencyHz = simulatedToneFrequencyHz,
                durationSeconds = durationSeconds.toDouble(),
                sampleRate = sampleRate,
                amplitude = simulatedToneAmplitude,
                leadInPauseSeconds = 0.0,
                leadOutPauseSeconds = 0.0
            )
            SignalGenerator.toPcm16LittleEndian(floatSamples)
        }

        _isRecording.set(false)
        onComplete(pcm, sampleRate)
        return Result.success(Unit)
    }

    override fun stop() {
        _isRecording.set(false)
    }

    override fun release() {
        stop()
    }
}
