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
 * Здесь проверяется только файловый канал L0. Успешное декодирование WAV не
 * доказывает передачу через воздух.
 */
class CrossLanguageTest {

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

        val root = JSONObject()
        root.put("schema", "silentnetwork.cross-check/1")
        root.put("cases", results)
        File(dir, "crosscheck-result.json")
            .writeText(root.toString(2), Charsets.UTF_8)
    }
}
