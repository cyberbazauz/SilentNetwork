package org.silentnetwork.app.audiolab.audio

import android.media.AudioDeviceInfo
import android.media.MediaRecorder

/**
 * Human-readable names for the Android audio route actually used by a capture.
 *
 * The SN-015 result contract stores the requested and the actual route. Android
 * does not guarantee that a preferred device is honoured (for example when a
 * headset is plugged in), so both values are reported instead of assuming that
 * `setPreferredDevice()` succeeded.
 */
object AudioRouteInfo {

    const val REQUESTED_BUILTIN_MIC = "builtin_mic"

    fun sourceName(audioSource: Int): String = when (audioSource) {
        MediaRecorder.AudioSource.UNPROCESSED -> "UNPROCESSED"
        MediaRecorder.AudioSource.VOICE_RECOGNITION -> "VOICE_RECOGNITION"
        MediaRecorder.AudioSource.MIC -> "MIC"
        MediaRecorder.AudioSource.VOICE_COMMUNICATION -> "VOICE_COMMUNICATION"
        MediaRecorder.AudioSource.CAMCORDER -> "CAMCORDER"
        MediaRecorder.AudioSource.DEFAULT -> "DEFAULT"
        else -> "SOURCE_$audioSource"
    }

    fun deviceTypeName(type: Int): String = when (type) {
        AudioDeviceInfo.TYPE_BUILTIN_MIC -> "builtin_mic"
        AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "bluetooth_sco"
        AudioDeviceInfo.TYPE_BLUETOOTH_A2DP -> "bluetooth_a2dp"
        AudioDeviceInfo.TYPE_USB_DEVICE -> "usb_device"
        AudioDeviceInfo.TYPE_USB_HEADSET -> "usb_headset"
        AudioDeviceInfo.TYPE_WIRED_HEADSET -> "wired_headset"
        AudioDeviceInfo.TYPE_WIRED_HEADPHONES -> "wired_headphones"
        AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> "builtin_speaker"
        AudioDeviceInfo.TYPE_TELEPHONY -> "telephony"
        AudioDeviceInfo.TYPE_UNKNOWN -> "unknown"
        else -> "TYPE_$type"
    }

    /**
     * Describes the routed device. `AudioRecord.routedDevice` is nullable while
     * the recorder is not running, so callers must keep the null case explicit.
     */
    fun describeDevice(type: Int?, productName: String? = null): String? {
        if (type == null) return null
        val typeName = deviceTypeName(type)
        val product = productName?.trim().orEmpty()
        return if (product.isEmpty()) typeName else "$typeName ($product)"
    }
}
