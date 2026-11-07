package org.silentnetwork.app.audiolab.chat

import org.silentnetwork.app.audiolab.datalink.BfskProfile

/**
 * User-facing acoustic chat modes.
 *
 * The names describe the intent, not a guarantee. "HIGH_FREQ" only attempts
 * the 16–18 kHz band: audibility depends on the listener, and both speaker and
 * microphone must actually pass the band. No mode proves over-the-air delivery
 * and no mode guarantees a distance.
 *
 * Parameters must stay in sync with acoustic-lab/snlab/config.py.
 */
enum class ChatMode(
    val id: String,
    val profile: BfskProfile
) {
    NORMAL("normal", BfskProfile.CHAT_NORMAL),
    EXCELLENT("excellent", BfskProfile.CHAT_EXCELLENT),
    HIGH_FREQ("highfreq", BfskProfile.CHAT_HIGH_FREQ);

    companion object {
        val DEFAULT: ChatMode = NORMAL

        fun fromId(id: String?): ChatMode? = entries.firstOrNull { it.id == id }
    }
}
