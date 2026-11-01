package org.silentnetwork.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.silentnetwork.app.state.MessengerValidator

/**
 * Unit tests for message input validation and honest core availability checking.
 */
class MessengerValidatorTest {

    @Test
    fun testEmptyInputRejection() {
        assertTrue(MessengerValidator.validateInput(null) is MessengerValidator.ValidationResult.Empty)
        assertTrue(MessengerValidator.validateInput("") is MessengerValidator.ValidationResult.Empty)
        assertTrue(MessengerValidator.validateInput("   ") is MessengerValidator.ValidationResult.Empty)
        assertTrue(MessengerValidator.validateInput("\t\n") is MessengerValidator.ValidationResult.Empty)
    }

    @Test
    fun testValidInputAccepted() {
        val result = MessengerValidator.validateInput("  Hello SilentNetwork!  ")
        assertTrue(result is MessengerValidator.ValidationResult.Valid)
        assertEquals("Hello SilentNetwork!", (result as MessengerValidator.ValidationResult.Valid).cleanText)
    }

    @Test
    fun testMaxLengthLimitEnforced() {
        val exact500 = "A".repeat(500)
        val validResult = MessengerValidator.validateInput(exact500)
        assertTrue(validResult is MessengerValidator.ValidationResult.Valid)

        val over500 = "B".repeat(501)
        val invalidResult = MessengerValidator.validateInput(over500)
        assertTrue(invalidResult is MessengerValidator.ValidationResult.TooLong)
        val tooLong = invalidResult as MessengerValidator.ValidationResult.TooLong
        assertEquals(501, tooLong.length)
        assertEquals(500, tooLong.max)
    }

    @Test
    fun testEvaluateSendAttemptWhenCoreDisconnected() {
        val result = MessengerValidator.evaluateSendAttempt("Test message", isCoreConnected = false)
        assertTrue(result is MessengerValidator.SendAttemptResult.RejectedCoreNotConnected)
        val rejected = result as MessengerValidator.SendAttemptResult.RejectedCoreNotConnected
        assertTrue(rejected.reason.contains("Rust Core не подключен"))
    }

    @Test
    fun testEvaluateSendAttemptWithEmptyInput() {
        val result = MessengerValidator.evaluateSendAttempt("   ", isCoreConnected = false)
        assertTrue(result is MessengerValidator.SendAttemptResult.RejectedInvalidInput)
        val rejected = result as MessengerValidator.SendAttemptResult.RejectedInvalidInput
        assertEquals("Сообщение не может быть пустым", rejected.error)
    }
}
