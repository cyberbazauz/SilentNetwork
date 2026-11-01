package org.silentnetwork.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.model.AcousticChannelState
import org.silentnetwork.app.model.ChatMessage
import org.silentnetwork.app.model.MessageStatus
import org.silentnetwork.app.model.PeerNode

/**
 * Unit tests for domain models and state representations.
 */
class DomainModelsTest {

    @Test
    fun testAcousticChannelStates() {
        val states = AcousticChannelState.values()
        assertEquals(5, states.size)
        assertTrue(states.contains(AcousticChannelState.DISCONNECTED))
        assertTrue(states.contains(AcousticChannelState.INITIALIZING))
        assertTrue(states.contains(AcousticChannelState.LISTENING))
        assertTrue(states.contains(AcousticChannelState.TRANSMITTING))
        assertTrue(states.contains(AcousticChannelState.ERROR))
    }

    @Test
    fun testMessageStatuses() {
        val statuses = MessageStatus.values()
        assertEquals(4, statuses.size)
        assertTrue(statuses.contains(MessageStatus.QUEUED))
        assertTrue(statuses.contains(MessageStatus.SENT_TO_CORE))
        assertTrue(statuses.contains(MessageStatus.ACK_DELIVERED))
        assertTrue(statuses.contains(MessageStatus.FAILED_NO_ACK))
    }

    @Test
    fun testChatMessageIntegrity() {
        val msg = ChatMessage(
            id = "msg-001",
            senderId = "node-alpha",
            text = "Testing acoustic payload",
            timestamp = 1730476800000L,
            status = MessageStatus.QUEUED
        )

        assertEquals("msg-001", msg.id)
        assertEquals("node-alpha", msg.senderId)
        assertEquals("Testing acoustic payload", msg.text)
        assertEquals(1730476800000L, msg.timestamp)
        assertEquals(MessageStatus.QUEUED, msg.status)

        // Verify copy operation preserves immutability
        val updated = msg.copy(status = MessageStatus.SENT_TO_CORE)
        assertEquals(MessageStatus.SENT_TO_CORE, updated.status)
        assertEquals(MessageStatus.QUEUED, msg.status)
    }

    @Test
    fun testPeerNodeProperties() {
        val peer = PeerNode(
            nodeId = "acoustic-peer-42",
            acousticLinkQuality = 0.85f,
            lastSeenMs = 1730476810000L,
            isDirectNeighbor = true
        )

        assertEquals("acoustic-peer-42", peer.nodeId)
        assertEquals(0.85f, peer.acousticLinkQuality, 0.001f)
        assertTrue(peer.isDirectNeighbor)

        val indirectPeer = peer.copy(isDirectNeighbor = false, acousticLinkQuality = 0.2f)
        assertFalse(indirectPeer.isDirectNeighbor)
        assertEquals(0.2f, indirectPeer.acousticLinkQuality, 0.001f)
    }
}
