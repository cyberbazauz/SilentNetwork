package org.silentnetwork.app.state

import org.silentnetwork.app.R
import org.silentnetwork.app.model.AcousticChannelState
import org.silentnetwork.app.model.MessageStatus

/**
 * Validation and business logic helpers for UI presentation.
 * Decoupled from Android Framework to enable deterministic JVM unit testing.
 */
object MessengerValidator {

    const val MAX_MESSAGE_LENGTH = 500

    sealed class ValidationResult {
        object Empty : ValidationResult()
        data class TooLong(val length: Int, val max: Int) : ValidationResult()
        data class Valid(val cleanText: String) : ValidationResult()
    }

    sealed class SendAttemptResult {
        data class RejectedCoreNotConnected(val reason: String) : SendAttemptResult()
        data class RejectedInvalidInput(val error: String) : SendAttemptResult()
    }

    fun validateInput(rawText: String?): ValidationResult {
        if (rawText == null) return ValidationResult.Empty
        val trimmed = rawText.trim()
        if (trimmed.isEmpty()) return ValidationResult.Empty
        if (trimmed.length > MAX_MESSAGE_LENGTH) {
            return ValidationResult.TooLong(trimmed.length, MAX_MESSAGE_LENGTH)
        }
        return ValidationResult.Valid(trimmed)
    }

    fun evaluateSendAttempt(rawText: String?, isCoreConnected: Boolean): SendAttemptResult {
        val validation = validateInput(rawText)
        if (validation is ValidationResult.Empty) {
            return SendAttemptResult.RejectedInvalidInput("Сообщение не может быть пустым")
        }
        if (validation is ValidationResult.TooLong) {
            return SendAttemptResult.RejectedInvalidInput(
                "Длина сообщения (${validation.length}) превышает лимит (${validation.max})"
            )
        }
        if (!isCoreConnected) {
            return SendAttemptResult.RejectedCoreNotConnected(
                "Rust Core не подключен. Акустическая передача заблокирована до реализации FFI (SN-042)."
            )
        }
        error("Core transmission path is not yet integrated")
    }
}

object NavigationStateManager {

    fun resolveTabTag(tabId: Int): String {
        return when (tabId) {
            R.id.tab_status -> "tab_status"
            R.id.tab_messenger -> "tab_messenger"
            R.id.tab_devices -> "tab_devices"
            R.id.tab_settings -> "tab_settings"
            else -> "tab_status"
        }
    }

    fun isKnownTab(tabId: Int): Boolean {
        return tabId in listOf(
            R.id.tab_status,
            R.id.tab_messenger,
            R.id.tab_devices,
            R.id.tab_settings
        )
    }

    fun shouldSwitchTab(currentTabId: Int, targetTabId: Int): Boolean {
        if (!isKnownTab(targetTabId)) return false
        return currentTabId != targetTabId
    }
}

object SettingsProfileInspector {

    data class ProfileDescriptor(
        val name: String,
        val isExperimental: Boolean,
        val verifiedOnHardware: Boolean,
        val acousticFrequencyBand: String
    )

    fun getM0PreliminaryProfile(): ProfileDescriptor {
        return ProfileDescriptor(
            name = "BFSK 18–19 кГц, 300 бит/с (M0)",
            isExperimental = true,
            verifiedOnHardware = false,
            acousticFrequencyBand = "18000..19000 Hz"
        )
    }

    fun isRadioTransportPermitted(): Boolean {
        // Strict principle: SilentNetwork NEVER uses radio fallback for data transport
        return false
    }
}

object StatusFormatter {

    fun formatChannelStateLabel(state: AcousticChannelState): String {
        return when (state) {
            AcousticChannelState.DISCONNECTED -> "DISCONNECTED (ядро не подключено)"
            AcousticChannelState.INITIALIZING -> "INITIALIZING (инициализация FFI)"
            AcousticChannelState.LISTENING -> "LISTENING (акустический приём)"
            AcousticChannelState.TRANSMITTING -> "TRANSMITTING (акустическая модуляция)"
            AcousticChannelState.ERROR -> "ERROR (ошибка аудиотракта)"
        }
    }

    fun isOnlineOrDelivering(state: AcousticChannelState): Boolean {
        // Honest status: never report delivering or active transmission unless explicitly transmitting
        return state == AcousticChannelState.TRANSMITTING
    }
}
