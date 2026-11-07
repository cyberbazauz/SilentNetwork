package org.silentnetwork.app.audiolab.datalink

import kotlin.math.abs
import kotlin.math.round

data class BfskProfile(
    val name: String,
    val sampleRate: Int = 48000,
    val markHz: Double = 18000.0,
    val spaceHz: Double = 19000.0,
    val symbolRate: Double = 300.0,
    val amplitude: Double = 0.25
) {
    init {
        require(name.isNotBlank()) { "имя профиля не может быть пустым" }
        require(sampleRate in 8000..192000) { "sampleRate вне диапазона" }
        require(markHz.isFinite() && spaceHz.isFinite()) { "частоты должны быть конечными" }
        require(markHz in 100.0..23500.0 && spaceHz in 100.0..23500.0) { "частоты вне диапазона" }
        require(markHz != spaceHz) { "mark и space должны различаться" }
        val nyquist = sampleRate / 2.0
        require(maxOf(markHz, spaceHz) < nyquist) { "частоты должны быть ниже Найквиста" }
        require(symbolRate.isFinite() && symbolRate in 10.0..4000.0) { "symbolRate вне диапазона" }
        require(symbolRate < nyquist) { "symbolRate ниже Найквиста" }
        require(abs(markHz - spaceHz) >= symbolRate) { "разнос частот меньше символьной скорости" }
        val sps = sampleRate.toDouble() / symbolRate
        require(abs(sps - round(sps)) < 1e-9) { "sampleRate должен делиться на symbolRate нацело" }
        require(amplitude > 0.0 && amplitude <= 1.0) { "amplitude вне диапазона" }
    }

    val samplesPerSymbol: Int get() = round(sampleRate.toDouble() / symbolRate).toInt()
    val bitRateBps: Double get() = symbolRate

    fun toMap(): Map<String, Any> = mapOf(
        "name" to name,
        "sample_rate" to sampleRate,
        "mark_hz" to markHz,
        "space_hz" to spaceHz,
        "symbol_rate" to symbolRate,
        "amplitude" to amplitude,
        "samples_per_symbol" to samplesPerSymbol,
        "bit_rate_bps" to bitRateBps
    )

    companion object {
        val DATA_AUDIBLE = BfskProfile(
            name = "data-audible",
            markHz = 1200.0,
            spaceHz = 2200.0,
            symbolRate = 300.0,
            amplitude = 0.30
        )
        val DATA_ROBUST = BfskProfile(
            name = "data-robust",
            markHz = 1000.0,
            spaceHz = 2000.0,
            symbolRate = 100.0,
            amplitude = 0.30
        )

        // Chat modes. Frequencies must stay in sync with
        // acoustic-lab/snlab/config.py (CHAT_MODES).
        val CHAT_NORMAL = BfskProfile(
            name = "chat-normal",
            markHz = 1200.0,
            spaceHz = 2200.0,
            symbolRate = 300.0,
            amplitude = 0.30
        )
        val CHAT_EXCELLENT = BfskProfile(
            name = "chat-excellent",
            markHz = 1000.0,
            spaceHz = 2000.0,
            symbolRate = 150.0,
            amplitude = 0.30
        )
        val CHAT_HIGH_FREQ = BfskProfile(
            name = "chat-highfreq",
            markHz = 16000.0,
            spaceHz = 17500.0,
            symbolRate = 100.0,
            amplitude = 0.30
        )

        fun dataProfiles(): Map<String, BfskProfile> = mapOf(
            DATA_AUDIBLE.name to DATA_AUDIBLE,
            DATA_ROBUST.name to DATA_ROBUST
        )

        fun chatProfiles(): Map<String, BfskProfile> = mapOf(
            CHAT_NORMAL.name to CHAT_NORMAL,
            CHAT_EXCELLENT.name to CHAT_EXCELLENT,
            CHAT_HIGH_FREQ.name to CHAT_HIGH_FREQ
        )

        /**
         * Profiles accepted by the shared Frame v1 codecs.
         *
         * The transport is identical for data-link and chat profiles; only the
         * UI that chooses a profile differs. Validators must use this set, not
         * [dataProfiles] alone, otherwise chat sends are rejected before any
         * PCM is generated.
         */
        fun supportedProfiles(): Map<String, BfskProfile> =
            dataProfiles() + chatProfiles()
    }
}
