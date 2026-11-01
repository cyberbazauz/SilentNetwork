package org.silentnetwork.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.state.NavigationStateManager

/**
 * Unit tests for bottom navigation tab mapping and transition logic.
 */
class NavigationStateTest {

    @Test
    fun testResolveTabTag() {
        assertEquals("tab_status", NavigationStateManager.resolveTabTag(R.id.tab_status))
        assertEquals("tab_messenger", NavigationStateManager.resolveTabTag(R.id.tab_messenger))
        assertEquals("tab_devices", NavigationStateManager.resolveTabTag(R.id.tab_devices))
        assertEquals("tab_settings", NavigationStateManager.resolveTabTag(R.id.tab_settings))
        assertEquals("tab_status", NavigationStateManager.resolveTabTag(-999))
    }

    @Test
    fun testIsKnownTab() {
        assertTrue(NavigationStateManager.isKnownTab(R.id.tab_status))
        assertTrue(NavigationStateManager.isKnownTab(R.id.tab_messenger))
        assertTrue(NavigationStateManager.isKnownTab(R.id.tab_devices))
        assertTrue(NavigationStateManager.isKnownTab(R.id.tab_settings))
        assertFalse(NavigationStateManager.isKnownTab(12345))
        assertFalse(NavigationStateManager.isKnownTab(-1))
    }

    @Test
    fun testShouldSwitchTab() {
        // Switching to a different known tab should return true
        assertTrue(NavigationStateManager.shouldSwitchTab(R.id.tab_status, R.id.tab_messenger))
        assertTrue(NavigationStateManager.shouldSwitchTab(R.id.tab_messenger, R.id.tab_settings))

        // Clicking the currently active tab should return false (prevents redundant fragment recreation)
        assertFalse(NavigationStateManager.shouldSwitchTab(R.id.tab_status, R.id.tab_status))
        assertFalse(NavigationStateManager.shouldSwitchTab(R.id.tab_devices, R.id.tab_devices))

        // Switching to an unknown tab should return false
        assertFalse(NavigationStateManager.shouldSwitchTab(R.id.tab_status, 99999))
    }
}
