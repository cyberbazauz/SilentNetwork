package org.silentnetwork.app.audiolab.datalink

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.audiolab.dsp.SignalGenerator
import org.silentnetwork.app.audiolab.dsp.WavFileHandler
import java.io.File
import java.io.InputStreamReader
import kotlin.math.roundToInt

class FileTransferVectorsTest {
    private fun loadFixture(): JSONObject {
        val stream = javaClass.classLoader!!.getResourceAsStream("file-transfer-vectors.json")
        assertNotNull("file-transfer-vectors.json отсутствует в тестовых ресурсах", stream)
        val text = InputStreamReader(stream, Charsets.UTF_8).readText()
        return JSONObject(text)
    }

    private fun ByteArray.hex(): String = joinToString("") { "%02x".format(it) }

    private fun hexToBytes(hex: String): ByteArray {
        val bytes = ByteArray(hex.length / 2)
        for (index in bytes.indices) {
            bytes[index] = hex.substring(index * 2, index * 2 + 2).toInt(16).toByte()
        }
        return bytes
    }

    private fun tempDir(): File {
        val dir = File(System.getProperty("java.io.tmpdir"), "sn-ft-${System.nanoTime()}")
        assertTrue(dir.mkdirs())
        return dir
    }

    private fun wavFromPackets(
        packets: List<FileTransferCodec.FilePacket>,
        profile: BfskProfile,
        gapS: Double = FileTransferCodec.DEFAULT_INTER_FRAME_GAP_S,
        leadS: Double = FileTransferCodec.DEFAULT_LEAD_SILENCE_S,
        tailS: Double = FileTransferCodec.DEFAULT_TAIL_SILENCE_S
    ): ByteArray {
        val gap = (gapS * profile.sampleRate).roundToInt()
        val lead = (leadS * profile.sampleRate).roundToInt()
        val tail = (tailS * profile.sampleRate).roundToInt()
        val frames = packets.map { packet ->
            val frame = FrameV1.build(
                packet.toBytes(),
                maxPayloadBytes = DataLinkCodec.MAX_DATA_PAYLOAD_BYTES
            )
            BfskModulator.bitsToSamples(frame.bits, profile)
        }
        var total = lead + tail
        for (frame in frames) total += frame.size
        total += gap * (frames.size - 1).coerceAtLeast(0)
        val samples = FloatArray(total)
        var cursor = lead
        for ((index, frame) in frames.withIndex()) {
            System.arraycopy(frame, 0, samples, cursor, frame.size)
            cursor += frame.size
            if (index + 1 < frames.size) cursor += gap
        }
        val pcm = SignalGenerator.toPcm16LittleEndian(samples)
        return WavFileHandler.encodePcm16Wav(pcm, profile.sampleRate, 1)
    }

    @Test
    fun fixtureConstantsMatchContract() {
        val fixture = loadFixture()
        assertEquals(
            "silentnetwork.acoustic-lab.file-transfer-vectors/1",
            fixture.getString("schema")
        )
        assertEquals(FileTransferCodec.FT_VERSION, fixture.getInt("ft_version"))
        assertEquals(FileTransferCodec.CHUNK_DATA_BYTES, fixture.getInt("chunk_data_bytes"))
        assertEquals(FileTransferCodec.MAX_FILE_BYTES, fixture.getInt("max_file_bytes"))
        assertEquals(FileTransferCodec.MAX_FILENAME_BYTES, fixture.getInt("max_filename_bytes"))
        assertEquals(54, FileTransferCodec.CHUNK_DATA_BYTES)
        assertEquals(152, FileTransferCodec.MAX_CHUNKS)
    }

    @Test
    fun packetVectorsMatchExactly() {
        val fixture = loadFixture()
        val vectors = fixture.getJSONArray("vectors")
        val content = "Hello, world".toByteArray(Charsets.UTF_8)
        val plan = FileTransferCodec.buildTransferPlan(content, "note.txt", 7)

        for (index in 0 until vectors.length()) {
            val vector = vectors.getJSONObject(index)
            val type = vector.getString("packet_type")
            val packet = when (type) {
                "manifest" -> plan.packets[0]
                "data" -> plan.packets[1]
                "end" -> plan.packets[2]
                else -> error("неизвестный тип вектора: $type")
            }
            assertEquals(
                "вектор ${vector.getString("name")}",
                vector.getString("packet_hex"),
                packet.toBytes().hex()
            )

            val parsed = FileTransferCodec.parsePacket(
                hexToBytes(vector.getString("packet_hex"))
            )
            assertEquals(type, parsed.typeName)
            assertEquals(vector.getInt("transfer_id"), parsed.transferId)
            assertEquals(vector.getInt("seq"), parsed.seq)
            assertEquals(vector.getInt("total_chunks"), parsed.totalChunks)

            val assembler = FileTransferCodec.FileAssembler()
            assembler.add(parsed)
            when (type) {
                "manifest" -> {
                    val state = assembler.state()
                    assertEquals("note.txt", state.manifest!!.filename)
                    assertEquals(12, state.manifest!!.fileSize)
                }

                "data" -> assertArrayEquals(
                    hexToBytes(vector.getString("payload_hex")),
                    parsed.payload
                )

                "end" -> {
                    val state = assembler.state()
                    assertEquals(vector.getString("sha256"), state.expectedSha256)
                }
            }
        }

        assertEquals(
            "4ae7c3b6ac0beff671efa8cf57386151c06e58ca53a78d83f36107316cec125f",
            FileTransferCodec.sha256Hex(content)
        )
    }

    @Test
    fun planStructureAndLimits() {
        assertEquals(0, FileTransferCodec.expectedChunks(0))
        assertEquals(1, FileTransferCodec.expectedChunks(1))
        assertEquals(1, FileTransferCodec.expectedChunks(54))
        assertEquals(2, FileTransferCodec.expectedChunks(55))
        assertEquals(76, FileTransferCodec.expectedChunks(4096))
        assertEquals(152, FileTransferCodec.expectedChunks(8192))

        val small = FileTransferCodec.buildTransferPlan(ByteArray(100), "a.bin", 3)
        assertEquals(2, small.totalChunks)
        assertEquals(5, small.packetCount)
        assertEquals(listOf("manifest", "data", "data", "end", "end"), small.packets.map { it.typeName })

        val max = FileTransferCodec.buildTransferPlan(ByteArray(8192), "max.bin", 3)
        assertEquals(152, max.totalChunks)
        assertEquals(164, max.packetCount)
        assertEquals(
            "MANIFEST повторяется после каждых 16 DATA-чанков",
            9,
            max.packets.count { it.typeName == "manifest" } - 1
        )

        assertTrue(
            runCatching {
                FileTransferCodec.buildTransferPlan(ByteArray(8193), "big.bin", 1)
            }.exceptionOrNull() is FileTransferCodec.FileTooLargeError
        )
        assertTrue(
            runCatching {
                FileTransferCodec.buildTransferPlan(ByteArray(1), "a.bin", -1)
            }.exceptionOrNull() is FileTransferCodec.FileTransferError
        )
        assertTrue(
            runCatching {
                FileTransferCodec.buildTransferPlan(ByteArray(1), "a.bin", 256)
            }.exceptionOrNull() is FileTransferCodec.FileTransferError
        )
        assertTrue(
            runCatching {
                FileTransferCodec.buildTransferPlan(
                    ByteArray(1),
                    "a.bin",
                    1,
                    manifestRepeatEvery = 0
                )
            }.exceptionOrNull() is FileTransferCodec.FileTransferError
        )
        assertTrue(
            runCatching {
                FileTransferCodec.buildTransferPlan(
                    ByteArray(1),
                    "a.bin",
                    1,
                    endRepeats = 0
                )
            }.exceptionOrNull() is FileTransferCodec.FileTransferError
        )
    }

    @Test
    fun filenameValidationMatchesContract() {
        assertEquals("note.txt", FileTransferCodec.validateFilename("note.txt").toString(Charsets.UTF_8))
        assertEquals(
            "привет.txt",
            FileTransferCodec.validateFilename("привет.txt").toString(Charsets.UTF_8)
        )
        assertEquals(40, FileTransferCodec.validateFilename("a".repeat(40)).size)

        val invalid = listOf(
            "",
            ".",
            "..",
            "a/b.txt",
            "a\\b.txt",
            "a\u0001.txt",
            " note.txt",
            "note.txt ",
            "note.txt.",
            "CON",
            "con.txt",
            "COM1.log",
            "LPT9.bin",
            "a".repeat(41)
        )
        for (name in invalid) {
            val error = runCatching { FileTransferCodec.validateFilename(name) }.exceptionOrNull()
            assertTrue("должно быть отклонено: '$name'", error is FileTransferCodec.FilenameError)
        }
        // CONSOLE не является зарезервированным именем.
        assertEquals(11, FileTransferCodec.validateFilename("CONSOLE.txt").size)
    }

    @Test
    fun roundTripSmallFileThroughWav() {
        val outDir = tempDir()
        try {
            val content = "Привет, это файл через звук.\n".repeat(2)
                .toByteArray(Charsets.UTF_8)
            val (wav, _) = FileTransferCodec.encodeFileToWav(
                content,
                "note.txt",
                BfskProfile.DATA_AUDIBLE,
                transferId = 7
            )
            val result = FileTransferCodec.decodeWavToFile(
                wav,
                BfskProfile.DATA_AUDIBLE,
                outDir
            )
            assertTrue("приём не удался: ${result.errorType} ${result.message}", result.ok)
            assertArrayEquals(content, result.content)
            assertEquals("note.txt", result.filename)
            assertEquals(FileTransferCodec.sha256Hex(content), result.actualSha256)
            assertEquals(2, result.state!!.totalChunks)
            assertEquals(2, result.state!!.chunksReceived)
            assertEquals(0, result.state!!.duplicateChunks)
            assertTrue(result.state!!.complete)
            assertTrue(result.hashOk)
            assertEquals(32, result.stream.packets.first().preambleMatches)
            assertEquals(5, result.stream.framesFound)

            val written = File(outDir, "note.txt")
            assertTrue(written.isFile)
            assertArrayEquals(content, written.readBytes())

            val report = JSONObject(
                FileTransferCodec.buildReceiveReport(
                    result,
                    "rx.wav",
                    "abc",
                    minPreambleMatches = 28
                )
            )
            assertEquals("ok", report.getString("result"))
            assertFalse(report.getBoolean("crc_is_authentication"))
            assertFalse(report.getBoolean("sha256_is_authentication"))
            assertTrue(report.getJSONObject("transfer").getBoolean("hash_ok"))
            assertEquals("note.txt", report.getJSONObject("file").getString("filename"))
            assertEquals(
                FileTransferCodec.sha256Hex(content),
                report.getJSONObject("file").getString("sha256")
            )
            assertEquals(5, report.getJSONObject("stream").getInt("frames_found"))
            assertEquals(28, report.getInt("min_preamble_matches"))
        } finally {
            outDir.deleteRecursively()
        }
    }

    @Test
    fun emptyFileRoundTrip() {
        val outDir = tempDir()
        try {
            val (wav, message) = FileTransferCodec.encodeFileToWav(
                ByteArray(0),
                "empty.bin",
                BfskProfile.DATA_AUDIBLE,
                transferId = 1
            )
            assertEquals(0, message.plan.totalChunks)
            assertEquals(3, message.plan.packetCount)
            val result = FileTransferCodec.decodeWavToFile(wav, BfskProfile.DATA_AUDIBLE, outDir)
            assertTrue("приём не удался: ${result.errorType} ${result.message}", result.ok)
            assertEquals(0, result.content!!.size)
            assertEquals(
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                result.actualSha256
            )
            assertTrue(File(outDir, "empty.bin").isFile)
        } finally {
            outDir.deleteRecursively()
        }
    }

    @Test
    fun missingChunkIsIncomplete() {
        val outDir = tempDir()
        try {
            val plan = FileTransferCodec.buildTransferPlan(
                ByteArray(100) { it.toByte() },
                "missing.bin",
                9
            )
            val damaged = plan.packets.filterNot {
                it.typeName == "data" && it.seq == 1
            }
            val wav = wavFromPackets(damaged, BfskProfile.DATA_AUDIBLE)
            val result = FileTransferCodec.decodeWavToFile(wav, BfskProfile.DATA_AUDIBLE, outDir)
            assertFalse(result.ok)
            assertEquals("incomplete_transfer", result.errorType)
            assertEquals(listOf(1), result.state!!.missingChunks)
            assertEquals(1, result.state!!.chunksReceived)
            assertFalse(File(outDir, "missing.bin").exists())
        } finally {
            outDir.deleteRecursively()
        }
    }

    @Test
    fun duplicateChunkIsIdempotent() {
        val outDir = tempDir()
        try {
            val plan = FileTransferCodec.buildTransferPlan(
                ByteArray(100) { it.toByte() },
                "dup.bin",
                9
            )
            val withDuplicate = plan.packets.toMutableList()
            val firstDataIndex = withDuplicate.indexOfFirst { it.typeName == "data" }
            withDuplicate.add(firstDataIndex + 1, withDuplicate[firstDataIndex])
            val wav = wavFromPackets(withDuplicate, BfskProfile.DATA_AUDIBLE)
            val result = FileTransferCodec.decodeWavToFile(wav, BfskProfile.DATA_AUDIBLE, outDir)
            assertTrue("приём не удался: ${result.errorType} ${result.message}", result.ok)
            assertEquals(1, result.state!!.duplicateChunks)
            assertEquals(2, result.state!!.chunksReceived)
        } finally {
            outDir.deleteRecursively()
        }
    }

    @Test
    fun hashMismatchIsRejectedAndNoFileIsWritten() {
        val outDir = tempDir()
        try {
            val plan = FileTransferCodec.buildTransferPlan(
                "Hello, world".toByteArray(Charsets.UTF_8),
                "hash.bin",
                5
            )
            val corrupted = plan.packets.map { packet ->
                if (packet.packetType != FileTransferCodec.PACKET_END) {
                    packet
                } else {
                    val payload = packet.payload.copyOf()
                    payload[6] = (payload[6].toInt() xor 0x01).toByte()
                    packet.copy(payload = payload)
                }
            }
            val wav = wavFromPackets(corrupted, BfskProfile.DATA_AUDIBLE)
            val result = FileTransferCodec.decodeWavToFile(wav, BfskProfile.DATA_AUDIBLE, outDir)
            assertFalse(result.ok)
            assertEquals("hash_mismatch", result.errorType)
            assertFalse(result.hashOk)
            assertFalse(File(outDir, "hash.bin").exists())
        } finally {
            outDir.deleteRecursively()
        }
    }

    @Test
    fun conflictingDataIsReported() {
        val outDir = tempDir()
        try {
            val plan = FileTransferCodec.buildTransferPlan(
                ByteArray(100) { it.toByte() },
                "conflict.bin",
                4
            )
            val corrupted = plan.packets.toMutableList()
            val firstData = corrupted.first {
                it.packetType == FileTransferCodec.PACKET_DATA && it.seq == 1
            }
            val payload = firstData.payload.copyOf()
            payload[0] = (payload[0].toInt() xor 0x7F).toByte()
            corrupted.add(
                corrupted.indexOf(firstData) + 1,
                firstData.copy(payload = payload)
            )
            val wav = wavFromPackets(corrupted, BfskProfile.DATA_AUDIBLE)
            val result = FileTransferCodec.decodeWavToFile(wav, BfskProfile.DATA_AUDIBLE, outDir)
            assertFalse(result.ok)
            assertEquals("transfer_conflict", result.errorType)
            assertTrue(result.conflicts.isNotEmpty())
            assertFalse(File(outDir, "conflict.bin").exists())
        } finally {
            outDir.deleteRecursively()
        }
    }

    @Test
    fun transferIdAndManifestConflictsAreRejectedByAssembler() {
        val plan = FileTransferCodec.buildTransferPlan(ByteArray(100), "x.bin", 7)
        val assembler = FileTransferCodec.FileAssembler()
        for (packet in plan.packets) assembler.add(packet)
        assertTrue(assembler.complete)

        val other = FileTransferCodec.buildTransferPlan(ByteArray(100), "x.bin", 8)
        val idError = runCatching { assembler.add(other.packets[0]) }.exceptionOrNull()
        assertTrue(idError is FileTransferCodec.TransferConflictError)

        val differentManifest = FileTransferCodec.buildTransferPlan(ByteArray(101), "x.bin", 7)
        val manifestError = runCatching {
            FileTransferCodec.FileAssembler().apply {
                add(plan.packets[0])
                add(differentManifest.packets[0])
            }
        }.exceptionOrNull()
        assertTrue(manifestError is FileTransferCodec.TransferConflictError)
    }

    @Test
    fun notFileTransferIsReported() {
        val (wav, _) = DataLinkCodec.encodeToWav("SILENTNETWORK", BfskProfile.DATA_AUDIBLE)
        val result = FileTransferCodec.decodeWavToFile(wav, BfskProfile.DATA_AUDIBLE)
        assertFalse(result.ok)
        assertEquals("not_file_transfer", result.errorType)
        assertEquals(1, result.stream.framesFound)
        assertTrue(result.stream.packetErrors.isNotEmpty())
        assertEquals("not_file_transfer", result.stream.packetErrors.first().errorType)
    }

    @Test
    fun wrongProfileIsNotDecoded() {
        val (wav, _) = FileTransferCodec.encodeFileToWav(
            "Ping".toByteArray(),
            "ping.txt",
            BfskProfile.DATA_AUDIBLE,
            transferId = 2
        )
        val result = FileTransferCodec.decodeWavToFile(wav, BfskProfile.DATA_ROBUST)
        assertFalse(result.ok)
        assertEquals("signal_not_found", result.errorType)
    }

    @Test
    fun parseRejectsCorruptPackets() {
        val valid = FileTransferCodec.buildTransferPlan(
            "Hello, world".toByteArray(),
            "note.txt",
            7
        ).packets[0].toBytes()

        fun errorOf(bytes: ByteArray): Throwable? =
            runCatching { FileTransferCodec.parsePacket(bytes) }.exceptionOrNull()

        assertTrue(errorOf(byteArrayOf()) is FileTransferCodec.NotFileTransferError)
        assertTrue(errorOf(byteArrayOf(0x53, 0x46)) is FileTransferCodec.PacketError)

        val wrongVersion = valid.copyOf()
        wrongVersion[2] = 2
        assertTrue(errorOf(wrongVersion) is FileTransferCodec.PacketError)

        val unknownType = valid.copyOf()
        unknownType[3] = 9
        assertTrue(errorOf(unknownType) is FileTransferCodec.PacketError)

        val badLength = valid.copyOf()
        badLength[9] = (valid[9].toInt() + 1).toByte()
        assertTrue(errorOf(badLength) is FileTransferCodec.PacketError)

        val data = FileTransferCodec.buildTransferPlan(
            ByteArray(54),
            "a.bin",
            1
        ).packets[1].toBytes()
        val badSeq = data.copyOf()
        badSeq[5] = 0
        badSeq[6] = 1
        assertTrue(errorOf(badSeq) is FileTransferCodec.PacketError)

        val badManifestTotal = valid.copyOf()
        badManifestTotal[8] = 9
        assertTrue(errorOf(badManifestTotal) is FileTransferCodec.ManifestError)
    }

    @Test
    fun transmitReportDeclaresNoAuthentication() {
        val (wav, message) = FileTransferCodec.encodeFileToWav(
            "Hello, world".toByteArray(),
            "note.txt",
            BfskProfile.DATA_AUDIBLE,
            transferId = 7
        )
        val report = JSONObject(
            FileTransferCodec.buildTransmitReport(
                message,
                "tx.wav",
                wav.size,
                FileTransferCodec.sha256Hex(wav)
            )
        )
        assertEquals("tx", report.getString("direction"))
        assertFalse(report.getBoolean("crc_is_authentication"))
        assertFalse(report.getBoolean("sha256_is_authentication"))
        assertEquals("note.txt", report.getJSONObject("file").getString("filename"))
        assertEquals(7, report.getJSONObject("transfer").getInt("transfer_id"))
        assertEquals(4, report.getJSONObject("transfer").getInt("packet_count"))
        assertEquals(wav.size, report.getJSONObject("wav").getInt("bytes"))
        assertEquals(
            FileTransferCodec.sha256Hex(wav),
            report.getJSONObject("wav").getString("sha256")
        )
    }

    @Test
    fun wavHeaderMismatchIsReported() {
        val (wav, _) = FileTransferCodec.encodeFileToWav(
            "Ping".toByteArray(),
            "ping.txt",
            BfskProfile.DATA_AUDIBLE,
            transferId = 2
        )
        // Ломаем RIFF-заголовок: декодер должен вернуть wav_format_error.
        val broken = wav.copyOf()
        broken[0] = 0
        val result = FileTransferCodec.decodeWavToFile(broken, BfskProfile.DATA_AUDIBLE)
        assertFalse(result.ok)
        assertEquals("wav_format_error", result.errorType)
        assertNull(result.state)
    }

    @Test
    fun recordingShorterThanOneSymbolIsReported() {
        // 100 отсчётов при 48 кГц короче одного символа data-audible (160).
        val pcm = SignalGenerator.toPcm16LittleEndian(FloatArray(100))
        val wav = WavFileHandler.encodePcm16Wav(pcm, 48000, 1)
        val result = FileTransferCodec.decodeWavToFile(wav, BfskProfile.DATA_AUDIBLE)
        assertFalse(result.ok)
        assertEquals("signal_not_found", result.errorType)
        assertNull(result.state)
    }
}
