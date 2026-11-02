package org.silentnetwork.app.audiolab.dsp

import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * On-device acoustic signal analyzer for mobile microphone captures and audio calibration.
 * Performs RMS, Peak, DC offset, clipping detection, Goertzel single-bin tone detection,
 * and FFT spectral estimation.
 */
object AudioAnalyzer {

    const val SILENCE_DBFS_FLOOR = -120.0
    const val CLIPPING_THRESHOLD = 0.9990f // >= 32734 / 32767

    data class SpectralPeak(
        val frequencyHz: Double,
        val magnitudeDbfs: Double,
        val relativePowerFraction: Double
    )

    data class AnalysisResult(
        val sampleRate: Int,
        val channels: Int,
        val sampleCount: Int,
        val durationSeconds: Double,
        val rms: Double,
        val rmsDbfs: Double,
        val peak: Double,
        val peakDbfs: Double,
        val clippingCount: Int,
        val hasClipping: Boolean,
        val dcOffset: Double,
        val dominantFrequencyHz: Double,
        val dominantPeakDbfs: Double,
        val expectedFrequencyHz: Double?,
        val detectedFrequencyHz: Double?,
        val frequencyErrorHz: Double?,
        val detectionMarginDb: Double?,
        val topSpectralPeaks: List<SpectralPeak>,
        val band18to19kHzPowerFraction: Double,
        val spectrumBinsHz: List<Double>,
        val spectrumBinsPowerDbfs: List<Double>,
        val snrDisclaimer: String
    )

    /**
     * Performs full acoustic analysis on normalized samples.
     *
     * @param samples Array of float audio samples in [-1.0, 1.0]
     * @param sampleRate Sampling rate in Hz
     * @param channels Channel count
     * @param targetToneFrequencyHz Optional known test tone to inspect specifically
     */
    fun analyze(
        samples: FloatArray,
        sampleRate: Int,
        channels: Int = 1,
        targetToneFrequencyHz: Double? = null
    ): AnalysisResult {
        if (samples.isEmpty()) {
            return createEmptyResult(sampleRate, channels)
        }

        val n = samples.size
        var sumSquares = 0.0
        var peakAbs = 0.0
        var sum = 0.0
        var clipCount = 0

        for (i in 0 until n) {
            val s = samples[i]
            val absVal = abs(s)
            if (absVal > peakAbs) peakAbs = absVal.toDouble()
            if (absVal >= CLIPPING_THRESHOLD) clipCount++
            sumSquares += (s * s)
            sum += s
        }

        val meanSquare = sumSquares / n
        val rms = sqrt(meanSquare)
        val dcOffset = sum / n

        val rmsDbfs = toDbfs(rms)
        val peakDbfs = toDbfs(peakAbs)

        // Perform spectral estimation via FFT / Goertzel
        val (dominantFreq, dominantDbfs, expectedFreq, detectedFreq, detectionMargin,
            topPeaks, spectrumBins, spectrumPower, band1819Fraction) =
            computeSpectrum(samples, sampleRate, targetToneFrequencyHz)

        val disclaimer = "SNR не рассчитывается: для одиночной записи без эталонного сигнала " +
                "и известного фона шума оценка SNR некорректна. Требуется двухточечная модель канала (SN-014)."

        return AnalysisResult(
            sampleRate = sampleRate,
            channels = channels,
            sampleCount = n,
            durationSeconds = n.toDouble() / sampleRate.toDouble(),
            rms = rms,
            rmsDbfs = rmsDbfs,
            peak = peakAbs,
            peakDbfs = peakDbfs,
            clippingCount = clipCount,
            hasClipping = clipCount > 0,
            dcOffset = dcOffset,
            dominantFrequencyHz = dominantFreq,
            dominantPeakDbfs = dominantDbfs,
            expectedFrequencyHz = expectedFreq,
            detectedFrequencyHz = detectedFreq,
            frequencyErrorHz = if (expectedFreq != null && detectedFreq != null) {
                detectedFreq - expectedFreq
            } else {
                null
            },
            detectionMarginDb = detectionMargin,
            topSpectralPeaks = topPeaks,
            band18to19kHzPowerFraction = band1819Fraction,
            spectrumBinsHz = spectrumBins,
            spectrumBinsPowerDbfs = spectrumPower,
            snrDisclaimer = disclaimer
        )
    }

    /**
     * Computes single-frequency power using Goertzel's algorithm.
     * Mathematically exact and fast for testing discrete tone detection (e.g. 18500 Hz).
     */
    fun computeGoertzelPower(
        samples: FloatArray,
        sampleRate: Int,
        targetFrequencyHz: Double
    ): Double {
        if (samples.isEmpty() || sampleRate <= 0 || targetFrequencyHz <= 0.0) return 0.0

        val n = samples.size
        val k = (0.5 + (n * targetFrequencyHz / sampleRate)).toInt()
        val omega = (2.0 * PI * k) / n
        val coeff = 2.0 * cos(omega)

        var q0: Double
        var q1 = 0.0
        var q2 = 0.0

        for (i in 0 until n) {
            q0 = coeff * q1 - q2 + samples[i]
            q2 = q1
            q1 = q0
        }

        val power = (q1 * q1 + q2 * q2 - q1 * q2 * coeff) / (n.toDouble() * n.toDouble())
        return power
    }

    /**
     * Helper to compute spectrum power, dominant peak, top peaks, and 18-19kHz band power.
     */
    private fun computeSpectrum(
        samples: FloatArray,
        sampleRate: Int,
        targetToneFrequencyHz: Double?
    ): SpectrumData {
        // Choose FFT window size: power of 2, up to 4096 or length of samples
        var fftSize = 4096
        while (fftSize > samples.size && fftSize > 256) {
            fftSize /= 2
        }
        if (fftSize > samples.size) {
            fftSize = samples.size
        }

        // Take center slice of audio to avoid initial/trailing silence
        val offset = ((samples.size - fftSize) / 2).coerceAtLeast(0)
        val windowed = DoubleArray(fftSize)
        val imag = DoubleArray(fftSize)

        // Apply Hann window
        for (i in 0 until fftSize) {
            val hann = 0.5 * (1.0 - cos(2.0 * PI * i / (fftSize - 1)))
            windowed[i] = samples[offset + i] * hann
            imag[i] = 0.0
        }

        // Radix-2 Cooley-Tukey FFT (if power of 2) or standard DFT
        if ((fftSize and (fftSize - 1)) == 0) {
            cooleyTukeyFft(windowed, imag)
        }

        val halfSize = fftSize / 2
        val binWidthHz = sampleRate.toDouble() / fftSize.toDouble()
        val magnitudes = DoubleArray(halfSize)
        var maxMag = 0.0
        var dominantBin = 0
        var totalPower = 0.0
        var band1819Power = 0.0

        for (i in 1 until halfSize) { // Skip DC (bin 0)
            val re = windowed[i]
            val im = imag[i]
            val mag = sqrt(re * re + im * im) / halfSize
            magnitudes[i] = mag
            val power = mag * mag
            totalPower += power

            val freq = i * binWidthHz
            if (freq in 18000.0..19000.0) {
                band1819Power += power
            }

            if (mag > maxMag) {
                maxMag = mag
                dominantBin = i
            }
        }

        val dominantFreq = dominantBin * binWidthHz
        val dominantPeakDbfs = toDbfs(maxMag)

        // Find top 5 peaks
        val peaks = mutableListOf<SpectralPeak>()
        val sortedIndices = (1 until halfSize).sortedByDescending { magnitudes[it] }

        val usedFreqs = mutableListOf<Double>()
        for (bin in sortedIndices) {
            val freq = bin * binWidthHz
            // Only add if not within 300Hz of an already added peak
            val tooClose = usedFreqs.any { abs(it - freq) < 300.0 }
            if (!tooClose) {
                usedFreqs.add(freq)
                val mag = magnitudes[bin]
                val powerFrac = if (totalPower > 0.0) (mag * mag) / totalPower else 0.0
                peaks.add(
                    SpectralPeak(
                        frequencyHz = freq,
                        magnitudeDbfs = toDbfs(mag),
                        relativePowerFraction = powerFrac
                    )
                )
                if (peaks.size >= 5) break
            }
        }

        // Group into 64 display bins for the visual spectrum graph
        val displayBinCount = 64
        val displayFreqs = mutableListOf<Double>()
        val displayPowers = mutableListOf<Double>()

        val nyquist = sampleRate / 2.0
        val displayStep = nyquist / displayBinCount

        for (b in 0 until displayBinCount) {
            val startFreq = b * displayStep
            val endFreq = (b + 1) * displayStep
            val centerFreq = (startFreq + endFreq) / 2.0
            displayFreqs.add(centerFreq)

            var binMaxMag = 0.0
            for (i in 1 until halfSize) {
                val f = i * binWidthHz
                if (f >= startFreq && f < endFreq) {
                    if (magnitudes[i] > binMaxMag) {
                        binMaxMag = magnitudes[i]
                    }
                }
            }
            displayPowers.add(toDbfs(binMaxMag))
        }

        val band1819Fraction = if (totalPower > 0.0) (band1819Power / totalPower) else 0.0

        val (detectedFreq, detectionMargin) = findPeakNear(
            magnitudes = magnitudes,
            binWidthHz = binWidthHz,
            halfSize = halfSize,
            targetToneFrequencyHz = targetToneFrequencyHz
        )

        return SpectrumData(
            dominantFrequencyHz = dominantFreq,
            dominantPeakDbfs = dominantPeakDbfs,
            expectedFrequencyHz = targetToneFrequencyHz,
            detectedFrequencyHz = detectedFreq,
            detectionMarginDb = detectionMargin,
            topPeaks = peaks,
            spectrumBinsHz = displayFreqs,
            spectrumBinsPowerDbfs = displayPowers,
            band1819Fraction = band1819Fraction
        )
    }

    /**
     * Converts linear normalized amplitude in [0.0, 1.0] to dBFS.
     */
    fun toDbfs(linearAmplitude: Double): Double {
        if (linearAmplitude <= 1e-9) return SILENCE_DBFS_FLOOR
        val db = 20.0 * log10(linearAmplitude)
        return db.coerceAtLeast(SILENCE_DBFS_FLOOR)
    }

    private fun createEmptyResult(sampleRate: Int, channels: Int): AnalysisResult {
        return AnalysisResult(
            sampleRate = sampleRate,
            channels = channels,
            sampleCount = 0,
            durationSeconds = 0.0,
            rms = 0.0,
            rmsDbfs = SILENCE_DBFS_FLOOR,
            peak = 0.0,
            peakDbfs = SILENCE_DBFS_FLOOR,
            clippingCount = 0,
            hasClipping = false,
            dcOffset = 0.0,
            dominantFrequencyHz = 0.0,
            dominantPeakDbfs = SILENCE_DBFS_FLOOR,
            expectedFrequencyHz = null,
            detectedFrequencyHz = null,
            frequencyErrorHz = null,
            detectionMarginDb = null,
            topSpectralPeaks = emptyList(),
            band18to19kHzPowerFraction = 0.0,
            spectrumBinsHz = emptyList(),
            spectrumBinsPowerDbfs = emptyList(),
            snrDisclaimer = "Сигнал пуст"
        )
    }

    /**
     * In-place Radix-2 Cooley-Tukey FFT.
     */
    private fun cooleyTukeyFft(real: DoubleArray, imag: DoubleArray) {
        val n = real.size
        // Bit reversal
        var j = 0
        for (i in 0 until n - 1) {
            if (i < j) {
                val tr = real[i]; real[i] = real[j]; real[j] = tr
                val ti = imag[i]; imag[i] = imag[j]; imag[j] = ti
            }
            var k = n / 2
            while (k <= j) {
                j -= k
                k /= 2
            }
            j += k
        }

        // Butterflies
        var l = 2
        while (l <= n) {
            val halfL = l / 2
            val uStep = -2.0 * PI / l
            var u = 0.0
            for (m in 0 until halfL) {
                val cosU = cos(u)
                val sinU = sin(u)
                var i = m
                while (i < n) {
                    val pairIdx = i + halfL
                    val tr = cosU * real[pairIdx] - sinU * imag[pairIdx]
                    val ti = sinU * real[pairIdx] + cosU * imag[pairIdx]
                    real[pairIdx] = real[i] - tr
                    imag[pairIdx] = imag[i] - ti
                    real[i] += tr
                    imag[i] += ti
                    i += l
                }
                u += uStep
            }
            l *= 2
        }
    }

    private data class SpectrumData(
        val dominantFrequencyHz: Double,
        val dominantPeakDbfs: Double,
        val expectedFrequencyHz: Double?,
        val detectedFrequencyHz: Double?,
        val detectionMarginDb: Double?,
        val topPeaks: List<SpectralPeak>,
        val spectrumBinsHz: List<Double>,
        val spectrumBinsPowerDbfs: List<Double>,
        val band1819Fraction: Double
    )

    /**
     * Finds a significant tone within ±300 Hz of the expected frequency and
     * refines its position with parabolic interpolation. Returns null when the
     * window has no peak with enough margin over its median, so noise is not
     * reported as a received tone.
     */
    private fun findPeakNear(
        magnitudes: DoubleArray,
        binWidthHz: Double,
        halfSize: Int,
        targetToneFrequencyHz: Double?
    ): Pair<Double?, Double?> {
        if (targetToneFrequencyHz == null || binWidthHz <= 0.0 || halfSize < 3) {
            return null to null
        }
        val searchHz = 300.0
        val lowBin = ((targetToneFrequencyHz - searchHz) / binWidthHz)
            .toInt().coerceAtLeast(1)
        val highBin = ((targetToneFrequencyHz + searchHz) / binWidthHz)
            .toInt().coerceAtMost(halfSize - 2)
        if (highBin < lowBin) return null to null

        var peakBin = lowBin
        for (bin in lowBin..highBin) {
            if (magnitudes[bin] > magnitudes[peakBin]) peakBin = bin
        }
        val windowDb = (lowBin..highBin).map { toDbfs(magnitudes[it]) }.sorted()
        val medianDb = windowDb[windowDb.size / 2]
        val marginDb = toDbfs(magnitudes[peakBin]) - medianDb
        if (marginDb < 6.0) return null to marginDb

        var frequency = peakBin * binWidthHz
        if (peakBin in 1 until halfSize - 1) {
            val left = toDbfs(magnitudes[peakBin - 1])
            val center = toDbfs(magnitudes[peakBin])
            val right = toDbfs(magnitudes[peakBin + 1])
            val denominator = left - 2.0 * center + right
            if (abs(denominator) > 1e-18) {
                val delta = (0.5 * (left - right) / denominator).coerceIn(-0.5, 0.5)
                frequency += delta * binWidthHz
            }
        }
        return frequency to marginDb
    }
}
