package org.silentnetwork.app.audiolab.datalink

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression tests for the shared codecs with chat profiles.
 *
 * Chat used to pass `chat-normal` / `chat-excellent` / `chat-highfreq` into
 * [DataLinkCodec] and [FileTransferCodec], whose validators only accepted the
 * data-link profiles. Every chat send failed before generating PCM. Frame v1,
 * CRC-16 and the 64-byte payload limit are unchanged.
 */
class ChatProfileCodecTest {

    private val chatProfiles = listOf(
        BfskProfile.CHAT_NORMAL,
        BfskProfile.CHAT_EXCELLENT,
        BfskProfile.CHAT_HIGH_FREQ
    )

    private val unknownProfile = BfskProfile(
        name = "not-a-supported-profile",
        sampleRate = 48_000,
        markHz = 1_500.0,
        spaceHz = 2_500.0,
        symbolRate = 500.0,
        amplitude = 0.3
    )

    @Test
    fun supportedProfilesContainsDataAndChatProfiles() {
        val supported = BfskProfile.supportedProfiles()
        for (profile in BfskProfile.dataProfiles().values) {
            assertTrue("нет data-профиля ${profile.name}", supported.containsKey(profile.name))
        }
        for (profile in chatProfiles) {
            assertTrue("нет chat-профиля ${profile.name}", supported.containsKey(profile.name))
        }
    }

    @Test
    fun textRoundTripWorksForEveryChatProfile() {
        val text = "привет"
        for (profile in chatProfiles) {
            val message = DataLinkCodec.encodeToPcm(text, profile)
            assertEquals(profile.name, message.profile.name)

            val decoded = DataLinkCodec.decodeFromWav(
                DataLinkCodec.encodeToWav(text, profile).first,
                profile
            )
            assertTrue(
                "${profile.name}: ожидался приём, получено " +
                    "error=${decoded.errorType} message=${decoded.message}",
                decoded.ok
            )
            assertEquals(text, decoded.text)
        }
    }

    @Test
    fun fileEncodingWorksWithChatProfile() {
        val content = ByteArray(40) { (it * 13 + 7 and 0xFF).toByte() }
        val (wavBytes, message) = FileTransferCodec.encodeFileToWav(
            data = content,
            filename = "chat.bin",
            profile = BfskProfile.CHAT_NORMAL,
            transferId = 9
        )
        assertEquals("chat.bin", message.filename)
        assertArrayEquals(content, message.content)

        val received = FileTransferCodec.decodeWavToFile(
            wavBytes = wavBytes,
            profile = BfskProfile.CHAT_NORMAL
        )
        assertTrue(
            "ожидался приём файла, получено error=${received.errorType} message=${received.message}",
            received.ok
        )
        assertEquals("chat.bin", received.filename)
        assertArrayEquals(content, received.content)
        assertTrue(received.hashOk)
    }

    @Test
    fun textCodecRejectsUnknownProfile() {
        val error = org.junit.Assert.assertThrows(DataLinkCodec.ProfileError::class.java) {
            DataLinkCodec.encodeToPcm("x", unknownProfile)
        }
        assertEquals("profile_invalid", error.errorType)
        assertTrue(error.message!!.contains(unknownProfile.name))
    }

    @Test
    fun fileCodecRejectsUnknownProfile() {
        val error = org.junit.Assert.assertThrows(FileTransferCodec.FileTransferError::class.java) {
            FileTransferCodec.encodeFileToPcm(
                data = byteArrayOf(1, 2, 3),
                filename = "x.bin",
                profile = unknownProfile,
                transferId = 1
            )
        }
        assertEquals("profile_invalid", error.errorType)
        assertTrue(error.message!!.contains(unknownProfile.name))
    }

    @Test
    fun chatProfilesStayDistinctFromDataProfiles() {
        // The shared validator must not silently collapse chat and data
        // profiles: the receiver uses the exact profile to demodulate.
        val dataNames = BfskProfile.dataProfiles().keys
        val chatNames = chatProfiles.map { it.name }.toSet()
        assertTrue(dataNames.intersect(chatNames).isEmpty())
        assertEquals(
            dataNames.size + chatNames.size,
            BfskProfile.supportedProfiles().size
        )
    }
}
