package org.silentnetwork.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.model.AcousticChannelState
import org.silentnetwork.app.state.SettingsProfileInspector
import org.silentnetwork.app.state.StatusFormatter

/**
 * Unit tests for Settings profile honesty and Status formatter rules.
 */
class SettingsAndStatusStateTest {

    @Test
    fun testM0ProfileMarkedExperimental() {
        val profile = SettingsProfileInspector.getM0PreliminaryProfile()
        assertEquals("BFSK 18–19 кГц, 300 бит/с (M0)", profile.name)
        assertTrue("M0 BFSK profile MUST be explicitly marked experimental", profile.isExperimental)
        assertFalse("M0 BFSK profile MUST NOT claim hardware verification without measurements", profile.verifiedOnHardware)
        assertEquals("18000..19000 Hz", profile.acousticFrequencyBand)
    }

    @Test
    fun testStrictRadioTransportProhibition() {
        assertFalse("SilentNetwork core principle: Radio transport is strictly prohibited", SettingsProfileInspector.isRadioTransportPermitted())
    }

    @Test
    fun testStatusFormatterHonestReporting() {
        assertEquals("DISCONNECTED (ядро не подключено)", StatusFormatter.formatChannelStateLabel(AcousticChannelState.DISCONNECTED))
        assertEquals("INITIALIZING (инициализация FFI)", StatusFormatter.formatChannelStateLabel(AcousticChannelState.INITIALIZING))
        assertEquals("LISTENING (акустический приём)", StatusFormatter.formatChannelStateLabel(AcousticChannelState.LISTENING))
        assertEquals("TRANSMITTING (акустическая модуляция)", StatusFormatter.formatChannelStateLabel(AcousticChannelState.TRANSMITTING))
        assertEquals("ERROR (ошибка аудиотракта)", StatusFormatter.formatChannelStateLabel(AcousticChannelState.ERROR))
    }

    @Test
    fun testStatusFormatterNeverFakesOnlineDelivery() {
        assertFalse(StatusFormatter.isOnlineOrDelivering(AcousticChannelState.DISCONNECTED))
        assertFalse(StatusFormatter.isOnlineOrDelivering(AcousticChannelState.INITIALIZING))
        assertFalse(StatusFormatter.isOnlineOrDelivering(AcousticChannelState.LISTENING))
        assertFalse(StatusFormatter.isOnlineOrDelivering(AcousticChannelState.ERROR))
        assertTrue(StatusFormatter.isOnlineOrDelivering(AcousticChannelState.TRANSMITTING))
    }
}
