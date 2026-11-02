package org.silentnetwork.app.ui.audiolab

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View

/**
 * Custom hardware-accelerated canvas view that displays power spectral density (PSD) bars,
 * with prominent visual demarcation for the 18–19 kHz M0 acoustic frequency band.
 */
class SpectrumView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val bgPaint = Paint().apply {
        color = Color.parseColor("#08090D")
        style = Paint.Style.FILL
    }

    private val gridPaint = Paint().apply {
        color = Color.parseColor("#1E2538")
        style = Paint.Style.STROKE
        strokeWidth = 1f
    }

    private val textPaint = Paint().apply {
        color = Color.parseColor("#64748B")
        textSize = 24f
        isAntiAlias = true
    }

    private val bandHighlightPaint = Paint().apply {
        color = Color.parseColor("#2606B6D4") // Translucent cyan glow
        style = Paint.Style.FILL
    }

    private val bandBorderPaint = Paint().apply {
        color = Color.parseColor("#06B6D4") // Neon cyan outline
        style = Paint.Style.STROKE
        strokeWidth = 2f
        isAntiAlias = true
    }

    private val barPaint = Paint().apply {
        style = Paint.Style.FILL
        isAntiAlias = true
    }

    private var binsHz: DoubleArray = DoubleArray(0)
    private var powersDbfs: DoubleArray = DoubleArray(0)

    private val minDb = -100.0f
    private val maxDb = 0.0f

    private val barRect = RectF()
    private val highlightRect = RectF()

    fun updateSpectrum(freqs: List<Double>, powers: List<Double>) {
        binsHz = freqs.toDoubleArray()
        powersDbfs = powers.toDoubleArray()
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) return

        // 1. Background
        canvas.drawRect(0f, 0f, w, h, bgPaint)

        // 2. Frequency range mapping (0 Hz to 24000 Hz)
        val maxFreq = 24000.0f

        // Draw 18k..19k target band highlight
        val bandStartX = (18000.0f / maxFreq) * w
        val bandEndX = (19000.0f / maxFreq) * w
        highlightRect.set(bandStartX, 0f, bandEndX, h)
        canvas.drawRect(highlightRect, bandHighlightPaint)
        canvas.drawRect(highlightRect, bandBorderPaint)

        // 3. Grid lines and frequency markers
        val gridFreqs = listOf(5000f, 10000f, 15000f, 18000f, 19000f, 22000f)
        for (freq in gridFreqs) {
            val x = (freq / maxFreq) * w
            canvas.drawLine(x, 0f, x, h, gridPaint)
            val label = "${(freq / 1000).toInt()}k"
            canvas.drawText(label, x + 4f, 28f, textPaint)
        }

        // Horizontal dB lines (-20dB, -40dB, -60dB, -80dB)
        val dbLevels = listOf(-20f, -40f, -60f, -80f)
        for (db in dbLevels) {
            val y = (1.0f - (db - minDb) / (maxDb - minDb)) * h
            canvas.drawLine(0f, y, w, y, gridPaint)
            canvas.drawText("${db.toInt()} dB", 8f, y - 4f, textPaint)
        }

        // 4. Spectrum Bars
        val count = binsHz.size
        if (count == 0) {
            // Draw placeholder idle line
            canvas.drawText("Ожидание данных спектра (запустите анализ или тест)...", 20f, h / 2f, textPaint)
            return
        }

        val barWidth = (w / count.toFloat()).coerceAtLeast(1f)

        for (i in 0 until count) {
            val db = powersDbfs[i].toFloat().coerceIn(minDb, maxDb)
            val normalized = (db - minDb) / (maxDb - minDb)
            val barHeight = normalized * (h - 32f)

            val left = i * barWidth
            val top = h - barHeight
            val right = left + barWidth - 1f
            val bottom = h

            barRect.set(left, top, right, bottom)

            val freq = binsHz[i].toFloat()
            if (freq in 18000.0f..19000.0f) {
                // Highlight target M0 band in bright cyan/amber
                barPaint.color = Color.parseColor("#06B6D4")
            } else if (normalized > 0.7f) {
                barPaint.color = Color.parseColor("#F59E0B") // Amber
            } else {
                barPaint.color = Color.parseColor("#10B981") // Emerald
            }

            canvas.drawRect(barRect, barPaint)
        }

        // Target band label at bottom
        canvas.drawText("18-19kHz (M0)", bandStartX + 4f, h - 8f, bandBorderPaint)
    }
}
