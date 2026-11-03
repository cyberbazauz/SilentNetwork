package org.silentnetwork.app.audiolab.presentation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.audiolab.datalink.BfskProfile
import org.silentnetwork.app.audiolab.datalink.DataLinkCodec

class DataLinkStateTest {

    private fun message(
        ok: Boolean,
        errorType: String? = null,
        text: String? = null,
        detail: String? = null,
        crcOk: Boolean = ok
    ): DataLinkCodec.ReceivedMessage = DataLinkCodec.ReceivedMessage(
        ok = ok,
        profile = BfskProfile.DATA_AUDIBLE,
        text = text,
        errorType = errorType,
        message = detail,
        crcOk = crcOk,
        preambleMatches = if (ok) 32 else null,
        sampleRate = 48000,
        sampleCount = 48000,
        durationS = 1.0,
        rmsDbfs = -30.0,
        peakDbfs = -10.0,
        clippedSamples = 0
    )

    @Test
    fun profileLabelsAndDefaults() {
        assertEquals("data-audible", DataLinkState.DEFAULT_PROFILE)
        assertEquals(28, DataLinkState.ACOUSTIC_MIN_PREAMBLE)
        assertEquals(10, DataLinkState.DEFAULT_RECEIVER_DURATION_SEC)
        assertEquals(listOf(5, 10, 15), DataLinkState.RECEIVER_DURATIONS_SEC)

        val audible = DataLinkState.profileLabel(BfskProfile.DATA_AUDIBLE)
        assertTrue(audible.contains("300"))
        val robust = DataLinkState.profileLabel(BfskProfile.DATA_ROBUST)
        assertTrue(robust.contains("100"))

        assertEquals(
            BfskProfile.DATA_AUDIBLE,
            DataLinkState.getProfile(DataLinkState.DEFAULT_PROFILE)
        )
        assertEquals(
            BfskProfile.DATA_AUDIBLE,
            DataLinkState.getProfile("unknown-profile")
        )
    }

    @Test
    fun verdictTitlesCoverKnownErrors() {
        assertEquals("Сообщение принято", DataLinkState.verdictTitle(message(ok = true)))
        assertEquals(
            "Сигнал не найден",
            DataLinkState.verdictTitle(message(ok = false, errorType = "signal_not_found"))
        )
        assertEquals(
            "Ошибка CRC",
            DataLinkState.verdictTitle(message(ok = false, errorType = "crc_mismatch"))
        )
        assertEquals(
            "Кадр неполный",
            DataLinkState.verdictTitle(message(ok = false, errorType = "incomplete_frame"))
        )
        assertEquals(
            "Приём не удался",
            DataLinkState.verdictTitle(message(ok = false, errorType = "receiver_error"))
        )
    }

    @Test
    fun verdictHintsDoNotClaimAuthentication() {
        val success = DataLinkState.verdictHint(message(ok = true))
        assertTrue(success.contains("CRC"))
        assertTrue(success.contains("не является аутентификацией"))

        val failure = DataLinkState.verdictHint(
            message(ok = false, errorType = "signal_not_found", detail = "преамбула не найдена")
        )
        assertEquals("преамбула не найдена", failure)

        val fallback = DataLinkState.verdictHint(message(ok = false, errorType = "unknown"))
        assertTrue(fallback.contains("Проверьте"))
    }

    @Test
    fun recordingSummaryContainsMetrics() {
        val summary = DataLinkState.recordingSummary(message(ok = true, text = "Ping"))
        assertTrue(summary.contains("48000"))
        assertTrue(summary.contains("RMS"))
        assertTrue(summary.contains("пик"))
        assertTrue(summary.contains("клиппинг"))
    }
}
