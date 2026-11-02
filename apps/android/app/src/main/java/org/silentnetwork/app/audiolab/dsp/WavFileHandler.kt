package org.silentnetwork.app.audiolab.dsp

import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest

/**
 * Strict WAV encoder and decoder for PCM 16-bit audio.
 * Compatible with SilentNetwork Acoustic Lab (snlab/wavio.py).
 */
object WavFileHandler {

    data class WavMetadata(
        val sampleRate: Int,
        val channels: Int,
        val bitsPerSample: Int,
        val sampleCount: Int,
        val durationSeconds: Double,
        val dataSizeBytes: Int
    )

    data class DecodedWav(
        val metadata: WavMetadata,
        val samples: FloatArray // Mono or interleaved normalized samples in [-1.0, 1.0]
    )

    /**
     * Encodes raw PCM 16-bit little endian byte array into a standard RIFF/WAVE container.
     */
    fun encodePcm16Wav(
        pcmData: ByteArray,
        sampleRate: Int,
        channels: Int = 1
    ): ByteArray {
        require(channels in 1..2) { "Only mono (1) or stereo (2) supported" }
        require(sampleRate > 0) { "Sample rate must be positive" }
        require(pcmData.size % (channels * 2) == 0) {
            "PCM16 payload must contain complete ${channels * 2}-byte frames"
        }

        val bitsPerSample = 16
        val byteRate = sampleRate * channels * (bitsPerSample / 8)
        val blockAlign = channels * (bitsPerSample / 8)
        val dataSize = pcmData.size
        val totalChunkSize = 36 + dataSize

        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        // RIFF chunk
        header.put("RIFF".toByteArray(Charsets.US_ASCII))
        header.putInt(totalChunkSize)
        header.put("WAVE".toByteArray(Charsets.US_ASCII))

        // "fmt " subchunk
        header.put("fmt ".toByteArray(Charsets.US_ASCII))
        header.putInt(16) // Subchunk1Size for PCM
        header.putShort(1.toShort()) // AudioFormat = 1 (PCM)
        header.putShort(channels.toShort())
        header.putInt(sampleRate)
        header.putInt(byteRate)
        header.putShort(blockAlign.toShort())
        header.putShort(bitsPerSample.toShort())

        // "data" subchunk
        header.put("data".toByteArray(Charsets.US_ASCII))
        header.putInt(dataSize)

        val out = ByteArrayOutputStream(44 + dataSize)
        out.write(header.array())
        out.write(pcmData)
        return out.toByteArray()
    }

    /**
     * Writes PCM16 data directly to a destination File as WAV.
     */
    fun writePcm16Wav(
        file: File,
        pcmData: ByteArray,
        sampleRate: Int,
        channels: Int = 1
    ) {
        file.parentFile?.mkdirs()
        FileOutputStream(file).use { fos ->
            writePcm16Wav(fos, pcmData, sampleRate, channels)
        }
    }

    /**
     * Writes PCM16 data to an arbitrary OutputStream as WAV.
     */
    fun writePcm16Wav(
        outputStream: OutputStream,
        pcmData: ByteArray,
        sampleRate: Int,
        channels: Int = 1
    ) {
        val wavBytes = encodePcm16Wav(pcmData, sampleRate, channels)
        outputStream.write(wavBytes)
        outputStream.flush()
    }

    /**
     * Decodes a WAV file from an InputStream.
     * Validates headers strictly; skips non-audio chunks (e.g. LIST, INFO).
     */
    fun decodePcm16Wav(inputStream: InputStream): DecodedWav {
        val rawBytes = inputStream.readBytes()
        if (rawBytes.size < 44) {
            throw IllegalArgumentException("WAV file too short (${rawBytes.size} bytes, min 44)")
        }

        val buffer = ByteBuffer.wrap(rawBytes).order(ByteOrder.LITTLE_ENDIAN)

        // Verify "RIFF"
        val riff = ByteArray(4)
        buffer.get(riff)
        if (String(riff, Charsets.US_ASCII) != "RIFF") {
            throw IllegalArgumentException("Invalid WAV: missing RIFF header")
        }

        val chunkSize = buffer.int
        val wave = ByteArray(4)
        buffer.get(wave)
        if (String(wave, Charsets.US_ASCII) != "WAVE") {
            throw IllegalArgumentException("Invalid WAV: missing WAVE header")
        }

        var sampleRate = 0
        var channels = 0
        var bitsPerSample = 0
        var audioFormat = 0
        var dataOffset = -1
        var dataSize = -1

        // Scan subchunks
        while (buffer.remaining() >= 8) {
            val chunkIdBytes = ByteArray(4)
            buffer.get(chunkIdBytes)
            val chunkId = String(chunkIdBytes, Charsets.US_ASCII)
            val subchunkSize = buffer.int

            if (subchunkSize < 0) {
                throw IllegalArgumentException("Invalid subchunk size: $subchunkSize")
            }

            when (chunkId) {
                "fmt " -> {
                    if (subchunkSize < 16) {
                        throw IllegalArgumentException("Invalid fmt chunk size ($subchunkSize < 16)")
                    }
                    val fmtPos = buffer.position()
                    audioFormat = buffer.short.toInt() and 0xFFFF
                    channels = buffer.short.toInt() and 0xFFFF
                    sampleRate = buffer.int
                    val byteRate = buffer.int
                    val blockAlign = buffer.short.toInt() and 0xFFFF
                    bitsPerSample = buffer.short.toInt() and 0xFFFF

                    // Skip any extra format bytes (e.g. cbSize)
                    buffer.position(fmtPos + subchunkSize)
                }
                "data" -> {
                    dataOffset = buffer.position()
                    dataSize = subchunkSize.coerceAtMost(buffer.remaining())
                    buffer.position(dataOffset + dataSize)
                    break
                }
                else -> {
                    // Skip unrecognized metadata chunks (e.g. LIST, JUNK, bext)
                    val nextPos = buffer.position() + subchunkSize
                    if (nextPos > rawBytes.size) {
                        break
                    }
                    buffer.position(nextPos)
                }
            }
        }

        if (audioFormat != 1) {
            throw IllegalArgumentException("Unsupported WAV format: $audioFormat (only PCM=1 supported)")
        }
        if (bitsPerSample != 16) {
            throw IllegalArgumentException("Unsupported bit depth: $bitsPerSample (only 16-bit PCM supported)")
        }
        if (channels < 1 || channels > 2) {
            throw IllegalArgumentException("Unsupported channel count: $channels (only 1 or 2 supported)")
        }
        if (sampleRate <= 0) {
            throw IllegalArgumentException("Invalid sample rate: $sampleRate")
        }
        if (dataOffset < 0 || dataSize <= 0) {
            throw IllegalArgumentException("Missing or empty 'data' chunk in WAV")
        }

        val bytesPerSample = bitsPerSample / 8
        val totalSamples = dataSize / bytesPerSample
        val sampleCountPerChannel = totalSamples / channels
        val durationSeconds = sampleCountPerChannel.toDouble() / sampleRate.toDouble()

        // Extract samples
        buffer.position(dataOffset)
        val samples = FloatArray(sampleCountPerChannel)

        if (channels == 1) {
            for (i in 0 until sampleCountPerChannel) {
                val shortVal = buffer.short
                samples[i] = (shortVal / 32768.0f).coerceIn(-1.0f, 1.0f)
            }
        } else {
            // Stereo downmix to mono (average left + right)
            for (i in 0 until sampleCountPerChannel) {
                val left = buffer.short / 32768.0f
                val right = buffer.short / 32768.0f
                samples[i] = ((left + right) * 0.5f).coerceIn(-1.0f, 1.0f)
            }
        }

        val metadata = WavMetadata(
            sampleRate = sampleRate,
            channels = channels,
            bitsPerSample = bitsPerSample,
            sampleCount = sampleCountPerChannel,
            durationSeconds = durationSeconds,
            dataSizeBytes = dataSize
        )

        return DecodedWav(metadata, samples)
    }

    /**
     * Convenience method to decode a WAV file from a File reference.
     */
    fun decodePcm16Wav(file: File): DecodedWav {
        return FileInputStream(file).use { fis ->
            decodePcm16Wav(fis)
        }
    }

    /**
     * SHA-256 of the raw WAV bytes, used by the shared SN-015 result contract.
     * The digest is lowercase hex and covers exactly the file that is exported,
     * so Windows can verify that the analyzed file is the one that was recorded.
     */
    fun sha256Hex(data: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(data)
        return digest.joinToString("") { byte -> "%02x".format(byte) }
    }

    fun sha256Hex(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { input ->
            val buffer = ByteArray(8192)
            while (true) {
                val read = input.read(buffer)
                if (read <= 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { byte -> "%02x".format(byte) }
    }
}
