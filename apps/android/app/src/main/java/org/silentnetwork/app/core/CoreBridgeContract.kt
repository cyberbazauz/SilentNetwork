package org.silentnetwork.app.core

import org.silentnetwork.app.model.AcousticChannelState

/**
 * Boundary contract between Android Kotlin UI and native Rust Core.
 * Agent 1 is the technical owner of Rust Core and its FFI definitions.
 * This interface defines the expected port until docs/android-core-api.md is finalized.
 */
interface CoreBridgeContract {
    val channelState: AcousticChannelState

    /**
     * Initialize Rust Core runtime and state machine.
     */
    fun initialize(): Result<Unit>

    /**
     * Submit application payload to Core for SCNP packet framing and acoustic modulation.
     */
    fun sendPayload(destinationNodeId: String, payload: ByteArray): Result<String>

    /**
     * Clean shutdown of Core runtime.
     */
    fun shutdown()
}
