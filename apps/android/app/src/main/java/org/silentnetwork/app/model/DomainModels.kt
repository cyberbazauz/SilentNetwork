package org.silentnetwork.app.model

/**
 * Honest representation of acoustic channel state.
 * Never reports CONNECTED unless authenticated by Core.
 */
enum class AcousticChannelState {
    DISCONNECTED,
    INITIALIZING,
    LISTENING,
    TRANSMITTING,
    ERROR
}

enum class MessageStatus {
    QUEUED,
    SENT_TO_CORE,
    ACK_DELIVERED,
    FAILED_NO_ACK
}

data class ChatMessage(
    val id: String,
    val senderId: String,
    val text: String,
    val timestamp: Long,
    val status: MessageStatus
)

data class PeerNode(
    val nodeId: String,
    val acousticLinkQuality: Float,
    val lastSeenMs: Long,
    val isDirectNeighbor: Boolean
)
