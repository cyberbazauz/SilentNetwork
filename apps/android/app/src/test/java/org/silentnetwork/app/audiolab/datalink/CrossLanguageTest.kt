package org.silentnetwork.app.audiolab.datalink

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * SN-027: кросс-языковая проверка Python ↔ Kotlin.
 *
 * Тест запускается только из `acoustic-lab/cross_check_android.py`: скрипт
 * создаёт каталог, манифест и WAV, сгенерированные Python, а затем передаёт
 * каталог через `-PsnCrosscheckDir=...`. Без этого свойства тест пропускается,
 * поэтому обычный `gradlew test` не зависит от внешних файлов.
 *
 * Проверяются два слоя:
 *  - Data Link: Python WAV → Kotlin-декодер и Kotlin WAV → Python-декодер;
 *  - File Transfer v0: Python-файл → Kotlin-декодер → файл и SHA-256,
 *    затем Kotlin-файл → WAV для обратной проверки Python-декодером.
 *
 * Здесь проверяется только файловый канал L0. Успешное декодирование WAV не
 * доказывает передачу через воздух. SHA-256 проверяет целостность, но не
 * аутентифицирует отправителя.
 */
class CrossLanguageTest {

    private fun hexToBytes(hex: String): ByteArray {
        require(hex.length % 2 == 0) { "hex должен иметь чётную длину" }
        val bytes = ByteArray(hex.length / 2)
        for (index in bytes.indices) {
            bytes[index] = hex.substring(index * 2, index * 2 + 2).toInt(16).toByte()
        }
        return bytes
    }

    @Test
    fun crossCheckPythonAndKotlin() {
        val dirProperty = System.getProperty("sn.crosscheck.dir")
        assumeTrue("sn.crosscheck.dir не задан: кросс-проверка пропущена", dirProperty != null)
        val dir = File(dirProperty!!)
        assumeTrue("каталог кросс-проверки не найден: $dir", dir.isDirectory)

        val manifestFile = File(dir, "crosscheck-input.json")
        assumeTrue("нет crosscheck-input.json в $dir", manifestFile.isFile)

        val manifest = JSONObject(manifestFile.readText(Charsets.UTF_8))
        val cases = manifest.getJSONArray("cases")
        assertTrue("манифест не содержит кейсов", cases.length() > 0)

        val results = JSONArray()
        for (index in 0 until cases.length()) {
            val case = cases.getJSONObject(index)
            val id = case.getString("id")
            val profileName = case.getString("profile")
            val pythonText = case.getString("text")
            val wavName = case.getString("wav")
            val profile = requireNotNull(BfskProfile.dataProfiles()[profileName]) {
                "неизвестный профиль в манифесте: $profileName"
            }

            val pythonDecoded = DataLinkCodec.decodeWavFile(File(dir, wavName), profile)

            // Kotlin кодирует свой текст тем же профилем; Python проверит его
            // отдельным запуском после Gradle.
            val kotlinText = "KT-$id: привет"
            val (kotlinWav, kotlinMessage) = DataLinkCodec.encodeToWav(kotlinText, profile)
            val kotlinWavName = "kt_tx_$id.wav"
            File(dir, kotlinWavName).writeBytes(kotlinWav)

            results.put(
                JSONObject().apply {
                    put("id", id)
                    put("profile", profileName)
                    put("py_text", pythonText)
                    put("kotlin_ok", pythonDecoded.ok)
                    put("kotlin_text", pythonDecoded.text ?: JSONObject.NULL)
                    put("kotlin_error", pythonDecoded.errorType ?: JSONObject.NULL)
                    put("kotlin_message", pythonDecoded.message ?: JSONObject.NULL)
                    put(
                        "kotlin_preamble_matches",
                        pythonDecoded.preambleMatches ?: JSONObject.NULL
                    )
                    put("kotlin_crc_ok", pythonDecoded.crcOk)
                    put("kt_text", kotlinText)
                    put("kt_wav", kotlinWavName)
                    put("kt_crc_hex", FrameV1.hex16(kotlinMessage.frame.crc))
                    put("kt_total_bits", kotlinMessage.frame.totalBits)
                }
            )
        }

        val fileResults = crossCheckFileTransfer(dir, manifest)

        val root = JSONObject()
        root.put("schema", "silentnetwork.cross-check/1")
        root.put("cases", results)
        root.put("files", fileResults)
        File(dir, "crosscheck-result.json")
            .writeText(root.toString(2), Charsets.UTF_8)
    }

    /**
     * Файловый слой: Kotlin собирает файл из Python-WAV и кодирует его же
     * обратно в WAV. Python после Gradle независимо проверит Kotlin-WAV.
     */
    private fun crossCheckFileTransfer(dir: File, manifest: JSONObject): JSONArray {
        val fileResults = JSONArray()
        val cases = manifest.optJSONArray("files") ?: return fileResults
        val decodedDir = File(dir, "kotlin-decoded")
        decodedDir.mkdirs()

        for (index in 0 until cases.length()) {
            val case = cases.getJSONObject(index)
            val id = case.getString("id")
            val profileName = case.getString("profile")
            val filename = case.getString("filename")
            val expectedContent = hexToBytes(case.getString("content_hex"))
            val transferId = case.getInt("transfer_id")
            val profile = requireNotNull(BfskProfile.dataProfiles()[profileName]) {
                "неизвестный профиль в манифесте: $profileName"
            }

            val pythonWav = File(dir, case.getString("wav"))
            val decoded = FileTransferCodec.decodeWavToFile(
                pythonWav.readBytes(),
                profile,
                decodedDir
            )
            val expectedSha256 = FileTransferCodec.sha256Hex(expectedContent)
            val pythonToKotlinOk = decoded.ok &&
                decoded.filename == filename &&
                decoded.content?.contentEquals(expectedContent) == true &&
                decoded.actualSha256 == expectedSha256 &&
                decoded.hashOk

            // Kotlin кодирует то же содержимое тем же профилем; Python
            // декодирует этот WAV после Gradle.
            val (kotlinWav, _) = FileTransferCodec.encodeFileToWav(
                expectedContent,
                filename,
                profile,
                transferId = transferId
            )
            val kotlinWavName = "kt_ft_$id.wav"
            File(dir, kotlinWavName).writeBytes(kotlinWav)

            fileResults.put(
                JSONObject().apply {
                    put("id", id)
                    put("profile", profileName)
                    put("kotlin_ok", pythonToKotlinOk)
                    put("kotlin_decode_ok", decoded.ok)
                    put("kotlin_error", decoded.errorType ?: JSONObject.NULL)
                    put("kotlin_message", decoded.message)
                    put("kotlin_filename", decoded.filename ?: JSONObject.NULL)
                    put("kotlin_size", decoded.content?.size ?: JSONObject.NULL)
                    put("kotlin_sha256", decoded.actualSha256 ?: JSONObject.NULL)
                    put("kotlin_hash_ok", decoded.hashOk)
                    put("kotlin_total_chunks", decoded.state?.totalChunks ?: JSONObject.NULL)
                    put("kotlin_frames_found", decoded.stream.framesFound)
                    put("kt_wav", kotlinWavName)
                    put("kt_filename", filename)
                    put("kt_size", expectedContent.size)
                    put("kt_sha256", expectedSha256)
                }
            )
        }
        return fileResults
    }
}
