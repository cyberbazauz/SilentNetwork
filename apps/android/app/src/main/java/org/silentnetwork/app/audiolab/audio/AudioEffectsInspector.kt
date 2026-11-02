package org.silentnetwork.app.audiolab.audio

import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor

/**
 * Utility to inspect Android hardware audio preprocessing effects.
 * For raw calibration and acoustic demodulation, hardware preprocessing
 * (AGC, NS, AEC) must be avoided or documented.
 */
object AudioEffectsInspector {

    fun inspectEffects(): AudioHardwareEffectsInfo {
        var aec = false
        var agc = false
        var ns = false

        try {
            aec = AcousticEchoCanceler.isAvailable()
        } catch (_: Throwable) {
            // Some OEM ROMs throw on audiofx query
        }

        try {
            agc = AutomaticGainControl.isAvailable()
        } catch (_: Throwable) {
        }

        try {
            ns = NoiseSuppressor.isAvailable()
        } catch (_: Throwable) {
        }

        val desc = buildString {
            append("AEC: ").append(if (aec) "Доступен" else "Не поддерживается").append("; ")
            append("AGC: ").append(if (agc) "Доступен" else "Не поддерживается").append("; ")
            append("NS: ").append(if (ns) "Доступен" else "Не поддерживается")
        }

        return AudioHardwareEffectsInfo(
            aecAvailable = aec,
            agcAvailable = agc,
            nsAvailable = ns,
            description = desc
        )
    }
}
