package org.silentnetwork.app.audiolab.datalink

import java.security.MessageDigest

/**
 * Frame v1: the acoustic PHY frame shared with SilentNetwork Acoustic Lab.
 *
 * Layout (bits are transmitted MSB first):
 * preamble 32 | sync 16 (0x1ACF) | version 8 | length 16 BE | payload 8*N | CRC-16 16
 *
 * CRC-16/CCITT-FALSE detects accidental corruption but is **not**
 * authentication: it does not protect against an active sender substitution
 * and must not be used instead of AEAD.
 */
object FrameV1 {
    const val PREAMBLE_BITS = 32
    const val SYNC_WORD = 0x1ACF
    const val SYNC_BITS = 16
    const val VERSION_BITS = 8
    const val LENGTH_BITS = 16
    const val CRC_BITS = 16
    const val FRAME_VERSION = 1
    const val CRC_POLYNOMIAL = 0x1021
    const val CRC_INITIAL = 0xFFFF
    const val FRAME_OVERHEAD_BITS = PREAMBLE_BITS + SYNC_BITS + VERSION_BITS + LENGTH_BITS + CRC_BITS
    const val MAX_PAYLOAD_BYTES = 512

    class FrameError(message: String) : IllegalArgumentException(message)

    data class Frame(
        val payload: ByteArray,
        val version: Int,
        val crc: Int,
        val bits: ByteArray
    ) {
        val payloadBits: Int get() = payload.size * 8
        val totalBits: Int get() = bits.size

        val payloadSha256: String
            get() = MessageDigest.getInstance("SHA-256").digest(payload)
                .joinToString("") { byte -> "%02x".format(byte) }
    }

    /** CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection. */
    fun crc16Ccitt(data: ByteArray, offset: Int = 0, length: Int = data.size - offset): Int {
        require(offset >= 0 && length >= 0 && offset + length <= data.size) {
            "неверный диапазон CRC"
        }
        var crc = CRC_INITIAL
        for (index in offset until offset + length) {
            crc = crc xor ((data[index].toInt() and 0xFF) shl 8)
            repeat(8) {
                crc = if (crc and 0x8000 != 0) {
                    ((crc shl 1) xor CRC_POLYNOMIAL) and 0xFFFF
                } else {
                    (crc shl 1) and 0xFFFF
                }
            }
        }
        return crc
    }

    fun build(
        payload: ByteArray,
        version: Int = FRAME_VERSION,
        maxPayloadBytes: Int = MAX_PAYLOAD_BYTES
    ): Frame {
        if (payload.isEmpty()) throw FrameError("пустой payload: передавать нечего")
        if (payload.size > maxPayloadBytes) {
            throw FrameError("payload ${payload.size} байт превышает лимит $maxPayloadBytes")
        }
        if (version !in 0..0xFF) throw FrameError("version должен помещаться в 8 бит")

        val header = ByteArray(3)
        header[0] = version.toByte()
        header[1] = ((payload.size shr 8) and 0xFF).toByte()
        header[2] = (payload.size and 0xFF).toByte()

        val crcInput = ByteArray(header.size + payload.size)
        header.copyInto(crcInput, 0)
        payload.copyInto(crcInput, header.size)
        val crc = crc16Ccitt(crcInput)

        val bits = ByteArray(FRAME_OVERHEAD_BITS + payload.size * 8)
        var offset = 0
        for (index in 0 until PREAMBLE_BITS) {
            bits[offset++] = if (index % 2 == 0) 1 else 0
        }
        offset = writeUInt(bits, offset, SYNC_WORD, SYNC_BITS)
        offset = writeUInt(bits, offset, version, VERSION_BITS)
        offset = writeUInt(bits, offset, payload.size, LENGTH_BITS)
        offset = writeBytes(bits, offset, payload)
        writeUInt(bits, offset, crc, CRC_BITS)

        return Frame(payload = payload.copyOf(), version = version, crc = crc, bits = bits)
    }

    /** Pack 0/1 frame bits into bytes (MSB first), matching numpy.packbits. */
    fun packBits(bits: ByteArray): ByteArray {
        require(bits.isNotEmpty()) { "пустая последовательность бит" }
        require(bits.size % 8 == 0) { "packBits ожидает кратное 8 число бит" }
        val bytes = ByteArray(bits.size / 8)
        for (byteIndex in bytes.indices) {
            var value = 0
            for (bitIndex in 0 until 8) {
                value = (value shl 1) or (bits[byteIndex * 8 + bitIndex].toInt() and 1)
            }
            bytes[byteIndex] = value.toByte()
        }
        return bytes
    }

    fun unpackBits(bytes: ByteArray): ByteArray {
        val bits = ByteArray(bytes.size * 8)
        for (byteIndex in bytes.indices) {
            val value = bytes[byteIndex].toInt() and 0xFF
            for (bitIndex in 0 until 8) {
                bits[byteIndex * 8 + bitIndex] =
                    ((value shr (7 - bitIndex)) and 1).toByte()
            }
        }
        return bits
    }

    fun bitsToBytes(bits: ByteArray, offset: Int, count: Int): ByteArray {
        require(offset >= 0 && count >= 0 && offset + count <= bits.size)
        val bytes = ByteArray(count)
        for (byteIndex in 0 until count) {
            var value = 0
            for (bitIndex in 0 until 8) {
                value = (value shl 1) or (bits[offset + byteIndex * 8 + bitIndex].toInt() and 1)
            }
            bytes[byteIndex] = value.toByte()
        }
        return bytes
    }

    fun bitsToUInt(bits: ByteArray, offset: Int, width: Int): Int {
        require(offset >= 0 && width in 1..32 && offset + width <= bits.size)
        var value = 0
        for (index in 0 until width) {
            value = (value shl 1) or (bits[offset + index].toInt() and 1)
        }
        return value
    }

    fun hex16(value: Int): String = "0x%04X".format(value)

    private fun writeUInt(bits: ByteArray, offset: Int, value: Int, width: Int): Int {
        require(value >= 0 && (width == 32 || value < (1 shl width))) {
            "значение $value не помещается в $width бит"
        }
        for (index in 0 until width) {
            bits[offset + index] = ((value shr (width - 1 - index)) and 1).toByte()
        }
        return offset + width
    }

    private fun writeBytes(bits: ByteArray, offset: Int, payload: ByteArray): Int {
        var cursor = offset
        for (byte in payload) {
            val value = byte.toInt() and 0xFF
            for (bitIndex in 0 until 8) {
                bits[cursor++] = ((value shr (7 - bitIndex)) and 1).toByte()
            }
        }
        return cursor
    }
}
