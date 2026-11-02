package org.silentnetwork.app.ui.audiolab

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import org.silentnetwork.app.R
import org.silentnetwork.app.audiolab.audio.AndroidAudioPlayerEngine
import org.silentnetwork.app.audiolab.audio.AndroidAudioRecorderEngine
import org.silentnetwork.app.audiolab.audio.AudioEffectsInspector
import org.silentnetwork.app.audiolab.audio.AudioPlayerEngine
import org.silentnetwork.app.audiolab.audio.AudioRecorderEngine
import org.silentnetwork.app.audiolab.dsp.AudioAnalyzer
import org.silentnetwork.app.audiolab.dsp.SignalGenerator
import org.silentnetwork.app.audiolab.dsp.WavFileHandler
import org.silentnetwork.app.audiolab.presentation.AudioLabState
import org.silentnetwork.app.audiolab.reporting.CalibrationJsonReport
import org.silentnetwork.app.databinding.FragmentAudioLabBinding
import java.io.File
import java.io.FileOutputStream
import java.util.Locale

/**
 * Diagnostic screen for hardware audio calibration, acoustic frequency validation,
 * and on-device spectrum analysis (SN-014).
 */
class AudioLabFragment : Fragment() {

    private var _binding: FragmentAudioLabBinding? = null
    private val binding get() = _binding!!

    private val mainHandler = Handler(Looper.getMainLooper())

    // Audio Engines: Real Android HAL engines instantiated on demand
    var playerEngine: AudioPlayerEngine = AndroidAudioPlayerEngine()
    var recorderEngine: AudioRecorderEngine = AndroidAudioRecorderEngine()

    // Cached files
    private var lastGeneratedTxFile: File? = null
    private var lastCapturedRxFile: File? = null
    private var lastAnalysisResult: AudioAnalyzer.AnalysisResult? = null

    // Document Pickers & Permission Launchers
    private val requestRecordPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        updateMicPermissionBadge(isGranted)
        if (isGranted) {
            Toast.makeText(requireContext(), "Разрешение на микрофон предоставлено", Toast.LENGTH_SHORT).show()
        } else {
            Toast.makeText(requireContext(), "Для записи тестового сигнала требуется разрешение", Toast.LENGTH_LONG).show()
        }
    }

    private val createTxWavLauncher = registerForActivityResult(
        ActivityResultContracts.CreateDocument("audio/wav")
    ) { uri ->
        if (uri != null && lastGeneratedTxFile != null) {
            exportFileToUri(lastGeneratedTxFile!!, uri)
        }
    }

    private val createRxWavLauncher = registerForActivityResult(
        ActivityResultContracts.CreateDocument("audio/wav")
    ) { uri ->
        if (uri != null && lastCapturedRxFile != null) {
            exportFileToUri(lastCapturedRxFile!!, uri)
        }
    }

    private val createJsonReportLauncher = registerForActivityResult(
        ActivityResultContracts.CreateDocument("application/json")
    ) { uri ->
        if (uri != null && lastAnalysisResult != null) {
            exportJsonReportToUri(uri)
        }
    }

    private val importWavLauncher = registerForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri ->
        if (uri != null) {
            importAndAnalyzeWav(uri)
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentAudioLabBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        setupTransmitterSection()
        setupReceiverSection()
        setupAnalyzerSection()
        checkHardwareEffects()
        checkMicPermissionStatus()
    }

    override fun onPause() {
        super.onPause()
        // Strictly stop and release audio capture/playback when fragment is paused
        stopAudioOperations()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        stopAudioOperations()
        playerEngine.release()
        recorderEngine.release()
        _binding = null
    }

    private fun stopAudioOperations() {
        try {
            if (playerEngine.isPlaying) {
                playerEngine.stop()
            }
        } catch (_: Throwable) {
        }

        try {
            if (recorderEngine.isRecording) {
                recorderEngine.stop()
            }
        } catch (_: Throwable) {
        }
    }

    // ==========================================
    // SECTION A: TRANSMITTER SETUP & ACTIONS
    // ==========================================

    private fun setupTransmitterSection() {
        // Frequency Spinner
        val freqLabels = AudioLabState.TRANSMITTER_FREQUENCIES_HZ.map { freq ->
            if (freq == 1000.0) {
                "1000 Гц (Контрольный слышимый тон)"
            } else if (AudioLabState.isExperimentalM0Band(freq)) {
                "${AudioLabState.formatFrequencyLabel(freq)} [M0 Эксперимент]"
            } else {
                AudioLabState.formatFrequencyLabel(freq)
            }
        }
        val freqAdapter = ArrayAdapter(requireContext(), android.R.layout.simple_spinner_dropdown_item, freqLabels)
        binding.spinnerTxFrequency.adapter = freqAdapter
        binding.spinnerTxFrequency.setSelection(5) // Default: 18.5 kHz

        // Sample Rate Spinner
        val srLabels = AudioLabState.SAMPLE_RATES_HZ.map { "$it Гц" }
        binding.spinnerTxSampleRate.adapter = ArrayAdapter(requireContext(), android.R.layout.simple_spinner_dropdown_item, srLabels)
        binding.spinnerTxSampleRate.setSelection(0) // Default: 48000 Hz

        // Duration Spinner
        val durLabels = AudioLabState.TRANSMITTER_DURATIONS_SEC.map { "$it сек" }
        binding.spinnerTxDuration.adapter = ArrayAdapter(requireContext(), android.R.layout.simple_spinner_dropdown_item, durLabels)
        binding.spinnerTxDuration.setSelection(1) // Default: 2 sec

        // Amplitude Spinner
        val ampLabels = AudioLabState.SAFE_AMPLITUDES.map { amp ->
            val dbfs = AudioAnalyzer.toDbfs(amp)
            String.format(Locale.US, "%.2f (%.1f dBFS)", amp, dbfs)
        }
        binding.spinnerTxAmplitude.adapter = ArrayAdapter(requireContext(), android.R.layout.simple_spinner_dropdown_item, ampLabels)
        binding.spinnerTxAmplitude.setSelection(2) // Default: 0.15

        val updatePreviewListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, v: View?, position: Int, id: Long) {
                updateTransmitterPreview()
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        binding.spinnerTxFrequency.onItemSelectedListener = updatePreviewListener
        binding.spinnerTxSampleRate.onItemSelectedListener = updatePreviewListener
        binding.spinnerTxDuration.onItemSelectedListener = updatePreviewListener
        binding.spinnerTxAmplitude.onItemSelectedListener = updatePreviewListener

        // Button: Generate WAV (Silent creation)
        binding.btnGenerateTxWav.setOnClickListener {
            generateTransmitterWav()
        }

        // Button: Play Tone (with explicit safety confirmation dialog)
        binding.btnPlayTx.setOnClickListener {
            showPlaySafetyDialog()
        }

        // Button: Stop Playback
        binding.btnStopTx.setOnClickListener {
            playerEngine.stop()
            binding.btnPlayTx.isEnabled = true
            binding.btnStopTx.isEnabled = false
            binding.tvTxStatus.text = "Воспроизведение остановлено"
        }

        // Button: Export TX WAV
        binding.btnExportTxWav.setOnClickListener {
            val freq = getSelectedTxFrequency()
            createTxWavLauncher.launch("silentnetwork_tone_${freq.toInt()}hz.wav")
        }

        // Button: Analyze generated TX
        binding.btnAnalyzeGeneratedTx.setOnClickListener {
            lastGeneratedTxFile?.let { file ->
                analyzeWavFile(file, getSelectedTxFrequency())
            }
        }
    }

    private fun getSelectedTxFrequency(): Double {
        val idx = binding.spinnerTxFrequency.selectedItemPosition.coerceAtLeast(0)
        return AudioLabState.TRANSMITTER_FREQUENCIES_HZ[idx]
    }

    private fun getSelectedTxSampleRate(): Int {
        val idx = binding.spinnerTxSampleRate.selectedItemPosition.coerceAtLeast(0)
        return AudioLabState.SAMPLE_RATES_HZ[idx]
    }

    private fun getSelectedTxDuration(): Int {
        val idx = binding.spinnerTxDuration.selectedItemPosition.coerceAtLeast(0)
        return AudioLabState.TRANSMITTER_DURATIONS_SEC[idx]
    }

    private fun getSelectedTxAmplitude(): Double {
        val idx = binding.spinnerTxAmplitude.selectedItemPosition.coerceAtLeast(0)
        return AudioLabState.SAFE_AMPLITUDES[idx]
    }

    private fun updateTransmitterPreview() {
        val freq = getSelectedTxFrequency()
        val sr = getSelectedTxSampleRate()
        val dur = getSelectedTxDuration()
        val amp = getSelectedTxAmplitude()
        val dbfs = AudioAnalyzer.toDbfs(amp)

        binding.tvTxPreviewParams.text = String.format(
            Locale.US,
            "Параметры: %.0f Гц | %d Гц | %d сек | amp: %.2f (%.1f dBFS)",
            freq, sr, dur, amp, dbfs
        )

        val isM0 = AudioLabState.isExperimentalM0Band(freq)
        binding.tvTransmitterBandBadge.visibility = View.VISIBLE
        if (isM0) {
            binding.tvTransmitterBandBadge.text = "ГИПОТЕЗА M0"
            binding.tvTransmitterBandBadge.setBackgroundResource(R.drawable.bg_badge_warning)
            binding.tvTransmitterBandBadge.setTextColor(ContextCompat.getColor(requireContext(), R.color.accent_rose))
        } else if (AudioLabState.isAudibleRange(freq)) {
            binding.tvTransmitterBandBadge.text = "СЛЫШИМЫЙ ТОН"
            binding.tvTransmitterBandBadge.setBackgroundResource(R.drawable.bg_badge_mock)
            binding.tvTransmitterBandBadge.setTextColor(ContextCompat.getColor(requireContext(), R.color.accent_amber))
        } else {
            binding.tvTransmitterBandBadge.text = "УЛЬТРАЗВУК"
            binding.tvTransmitterBandBadge.setBackgroundResource(R.drawable.bg_badge_ready)
            binding.tvTransmitterBandBadge.setTextColor(ContextCompat.getColor(requireContext(), R.color.accent_cyan))
        }
    }

    private fun generateTransmitterWav() {
        val freq = getSelectedTxFrequency()
        val sr = getSelectedTxSampleRate()
        val dur = getSelectedTxDuration()
        val amp = getSelectedTxAmplitude()

        val validation = AudioLabState.validateTransmitterParameters(freq, amp, dur.toDouble(), sr)
        if (validation.isFailure) {
            Toast.makeText(requireContext(), validation.exceptionOrNull()?.message, Toast.LENGTH_SHORT).show()
            return
        }

        try {
            val pcmBytes = SignalGenerator.generateTestTonePcm16(
                frequencyHz = freq,
                durationSeconds = dur.toDouble(),
                sampleRate = sr,
                amplitude = amp
            )

            val destFile = File(requireContext().cacheDir, "calibration_tx_${freq.toInt()}hz.wav")
            WavFileHandler.writePcm16Wav(destFile, pcmBytes, sr, 1)

            lastGeneratedTxFile = destFile
            binding.btnExportTxWav.isEnabled = true
            binding.btnAnalyzeGeneratedTx.isEnabled = true
            binding.tvTxStatus.text = "WAV успешно сгенерирован: ${destFile.name} (${destFile.length() / 1024} КБ)"
            Toast.makeText(requireContext(), "Тестовый WAV создан (беззвучно)", Toast.LENGTH_SHORT).show()
        } catch (t: Throwable) {
            binding.tvTxStatus.text = "Ошибка генерации: ${t.message}"
        }
    }

    private fun showPlaySafetyDialog() {
        val freq = getSelectedTxFrequency()
        val amp = getSelectedTxAmplitude()
        val freqLabel = AudioLabState.formatFrequencyLabel(freq)
        val msg = getString(R.string.audiolab_play_dialog_msg, "$freqLabel (амплитуда $amp)")

        AlertDialog.Builder(requireContext())
            .setTitle(R.string.audiolab_play_dialog_title)
            .setMessage(msg)
            .setPositiveButton("Воспроизвести") { _, _ ->
                startTransmitterPlayback()
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun startTransmitterPlayback() {
        val freq = getSelectedTxFrequency()
        val sr = getSelectedTxSampleRate()
        val dur = getSelectedTxDuration()
        val amp = getSelectedTxAmplitude()

        val pcmBytes = SignalGenerator.generateTestTonePcm16(
            frequencyHz = freq,
            durationSeconds = dur.toDouble(),
            sampleRate = sr,
            amplitude = amp
        )

        binding.btnPlayTx.isEnabled = false
        binding.btnStopTx.isEnabled = true
        binding.tvTxStatus.text = "Воспроизведение тестового сигнала $freq Гц..."

        val result = playerEngine.playPcm(
            pcmData = pcmBytes,
            sampleRate = sr,
            onProgress = { elapsed, total ->
                mainHandler.post {
                    _binding?.tvTxStatus?.text = String.format(Locale.US, "Воспроизведение: %.1f / %.1f сек", elapsed, total)
                }
            },
            onComplete = {
                mainHandler.post {
                    _binding?.btnPlayTx?.isEnabled = true
                    _binding?.btnStopTx?.isEnabled = false
                    _binding?.tvTxStatus?.text = "Воспроизведение завершено успешно"
                }
            },
            onError = { error ->
                mainHandler.post {
                    _binding?.btnPlayTx?.isEnabled = true
                    _binding?.btnStopTx?.isEnabled = false
                    _binding?.tvTxStatus?.text = "Ошибка воспроизведения: ${error.message}"
                }
            }
        )

        if (result.isFailure) {
            binding.btnPlayTx.isEnabled = true
            binding.btnStopTx.isEnabled = false
            binding.tvTxStatus.text = "Сбой запуска: ${result.exceptionOrNull()?.message}"
        }
    }

    // ==========================================
    // SECTION B: RECEIVER SETUP & ACTIONS
    // ==========================================

    private fun setupReceiverSection() {
        val durLabels = AudioLabState.RECEIVER_DURATIONS_SEC.map { "$it сек" }
        binding.spinnerRxDuration.adapter = ArrayAdapter(requireContext(), android.R.layout.simple_spinner_dropdown_item, durLabels)
        binding.spinnerRxDuration.setSelection(1) // Default: 5 sec

        val srLabels = AudioLabState.SAMPLE_RATES_HZ.map { "$it Гц" }
        binding.spinnerRxSampleRate.adapter = ArrayAdapter(requireContext(), android.R.layout.simple_spinner_dropdown_item, srLabels)
        binding.spinnerRxSampleRate.setSelection(0) // Default: 48000 Hz

        // Start Recording
        binding.btnStartRecord.setOnClickListener {
            if (hasRecordAudioPermission()) {
                startAcousticRecording()
            } else {
                requestRecordPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
            }
        }

        // Stop Recording
        binding.btnStopRecord.setOnClickListener {
            recorderEngine.stop()
            binding.btnStartRecord.isEnabled = true
            binding.btnStopRecord.isEnabled = false
            binding.tvRxStatus.text = "Запись остановлена пользователем"
        }

        // Export RX WAV
        binding.btnExportRxWav.setOnClickListener {
            createRxWavLauncher.launch("silentnetwork_capture_rx.wav")
        }

        // Send RX to Analyzer
        binding.btnSendRxToAnalyzer.setOnClickListener {
            lastCapturedRxFile?.let { file ->
                analyzeWavFile(file, getSelectedTxFrequency())
            }
        }
    }

    private fun hasRecordAudioPermission(): Boolean {
        return ContextCompat.checkSelfPermission(
            requireContext(),
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun checkMicPermissionStatus() {
        updateMicPermissionBadge(hasRecordAudioPermission())
    }

    private fun updateMicPermissionBadge(granted: Boolean) {
        if (_binding == null) return
        if (granted) {
            binding.tvRxPermissionBadge.text = "MIC: ДОСТУПЕН"
            binding.tvRxPermissionBadge.setBackgroundResource(R.drawable.bg_badge_ready)
            binding.tvRxPermissionBadge.setTextColor(ContextCompat.getColor(requireContext(), R.color.accent_emerald))
        } else {
            binding.tvRxPermissionBadge.text = "MIC: ТРЕБУЕТСЯ ДОСТУП"
            binding.tvRxPermissionBadge.setBackgroundResource(R.drawable.bg_badge_warning)
            binding.tvRxPermissionBadge.setTextColor(ContextCompat.getColor(requireContext(), R.color.accent_rose))
        }
    }

    private fun startAcousticRecording() {
        val durIdx = binding.spinnerRxDuration.selectedItemPosition.coerceAtLeast(0)
        val durationSec = AudioLabState.RECEIVER_DURATIONS_SEC[durIdx]

        val srIdx = binding.spinnerRxSampleRate.selectedItemPosition.coerceAtLeast(0)
        val sampleRate = AudioLabState.SAMPLE_RATES_HZ[srIdx]

        binding.btnStartRecord.isEnabled = false
        binding.btnStopRecord.isEnabled = true
        binding.tvRxStatus.text = getString(R.string.audiolab_rec_state_recording, 0.0, durationSec)

        val result = recorderEngine.startRecording(
            sampleRate = sampleRate,
            durationSeconds = durationSec,
            onProgress = { elapsed ->
                mainHandler.post {
                    _binding?.tvRxStatus?.text = getString(R.string.audiolab_rec_state_recording, elapsed, durationSec)
                }
            },
            onComplete = { pcmData, actualRate ->
                mainHandler.post {
                    handleRecordingCompleted(pcmData, actualRate)
                }
            },
            onError = { error ->
                mainHandler.post {
                    _binding?.btnStartRecord?.isEnabled = true
                    _binding?.btnStopRecord?.isEnabled = false
                    _binding?.tvRxStatus?.text = "Ошибка записи: ${error.message}"
                }
            }
        )

        if (result.isFailure) {
            binding.btnStartRecord.isEnabled = true
            binding.btnStopRecord.isEnabled = false
            binding.tvRxStatus.text = "Сбой запуска: ${result.exceptionOrNull()?.message}"
        }
    }

    private fun handleRecordingCompleted(pcmData: ByteArray, actualRate: Int) {
        if (_binding == null) return
        binding.btnStartRecord.isEnabled = true
        binding.btnStopRecord.isEnabled = false

        val sampleCount = pcmData.size / 2
        binding.tvRxStatus.text = getString(R.string.audiolab_rec_state_completed, sampleCount)

        try {
            val destFile = File(requireContext().cacheDir, "calibration_rx_capture.wav")
            WavFileHandler.writePcm16Wav(destFile, pcmData, actualRate, 1)
            lastCapturedRxFile = destFile
            binding.btnExportRxWav.isEnabled = true
            binding.btnSendRxToAnalyzer.isEnabled = true
            Toast.makeText(requireContext(), "Аудио записано и сохранено в WAV", Toast.LENGTH_SHORT).show()
        } catch (t: Throwable) {
            binding.tvRxStatus.text = "Ошибка сохранения WAV: ${t.message}"
        }
    }

    // ==========================================
    // SECTION C: ANALYZER SETUP & ACTIONS
    // ==========================================

    private fun setupAnalyzerSection() {
        binding.btnImportWav.setOnClickListener {
            importWavLauncher.launch(arrayOf("audio/x-wav", "audio/wav", "audio/*"))
        }

        binding.btnExportJsonReport.setOnClickListener {
            createJsonReportLauncher.launch("silentnetwork_calibration_report.json")
        }
    }

    private fun checkHardwareEffects() {
        val fx = AudioEffectsInspector.inspectEffects()
        binding.tvRxAudioSourceInfo.text = "AudioSource: UNPROCESSED (целевой) | ${fx.description}"
    }

    private fun analyzeWavFile(file: File, targetFrequencyHz: Double?) {
        try {
            val decoded = WavFileHandler.decodePcm16Wav(file)
            val result = AudioAnalyzer.analyze(
                samples = decoded.samples,
                sampleRate = decoded.metadata.sampleRate,
                channels = decoded.metadata.channels,
                targetToneFrequencyHz = targetFrequencyHz
            )
            renderAnalysisResult(result, file.name)
        } catch (t: Throwable) {
            Toast.makeText(requireContext(), "Ошибка анализа WAV: ${t.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun importAndAnalyzeWav(uri: Uri) {
        try {
            val inputStream = requireContext().contentResolver.openInputStream(uri)
                ?: throw IllegalStateException("Не удалось открыть файл")
            val decoded = inputStream.use { WavFileHandler.decodePcm16Wav(it) }

            val result = AudioAnalyzer.analyze(
                samples = decoded.samples,
                sampleRate = decoded.metadata.sampleRate,
                channels = decoded.metadata.channels
            )
            renderAnalysisResult(result, "Импортированный WAV")
            Toast.makeText(requireContext(), "WAV успешно проанализирован", Toast.LENGTH_SHORT).show()
        } catch (t: Throwable) {
            Toast.makeText(requireContext(), "Ошибка импорта: ${t.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun renderAnalysisResult(result: AudioAnalyzer.AnalysisResult, sourceName: String) {
        lastAnalysisResult = result
        binding.btnExportJsonReport.isEnabled = true

        // 1. Spectrum graph
        binding.spectrumView.updateSpectrum(result.spectrumBinsHz, result.spectrumBinsPowerDbfs)

        // 2. Metrics
        binding.tvMetricRms.text = AudioLabState.formatRmsStatus(result.rmsDbfs)
        binding.tvMetricPeak.text = AudioLabState.formatPeakStatus(result.peakDbfs)

        binding.tvMetricClipping.text = AudioLabState.formatClippingStatus(result.clippingCount)
        if (result.hasClipping) {
            binding.tvClippingBadge.text = "КЛИППИНГ ОБНАРУЖЕН"
            binding.tvClippingBadge.setBackgroundResource(R.drawable.bg_badge_warning)
            binding.tvClippingBadge.setTextColor(ContextCompat.getColor(requireContext(), R.color.accent_rose))
        } else {
            binding.tvClippingBadge.text = "КЛИППИНГ: НЕТ"
            binding.tvClippingBadge.setBackgroundResource(R.drawable.bg_badge_ready)
            binding.tvClippingBadge.setTextColor(ContextCompat.getColor(requireContext(), R.color.accent_emerald))
        }

        binding.tvMetricDcOffset.text = String.format(Locale.US, "DC offset: %.5f", result.dcOffset)
        binding.tvMetricSamples.text = "Отсчёты: ${result.sampleCount} (${result.sampleRate} Гц)"
        binding.tvMetricDuration.text = String.format(Locale.US, "Длительность: %.2f сек", result.durationSeconds)

        binding.tvMetricDominantFreq.text = String.format(
            Locale.US,
            "Доминирующая частота: %.0f Гц (%.1f dBFS)",
            result.dominantFrequencyHz,
            result.dominantPeakDbfs
        )

        binding.tvMetricBandPower.text = String.format(
            Locale.US,
            "Доля энергии 18–19 кГц: %.1f %%",
            result.band18to19kHzPowerFraction * 100.0
        )

        val peaksText = buildString {
            append("Главные спектральные пики ($sourceName):\n")
            if (result.topSpectralPeaks.isEmpty()) {
                append("• (нет выраженных пиков)")
            } else {
                for ((idx, p) in result.topSpectralPeaks.withIndex()) {
                    append(String.format(Locale.US, "%d. %.0f Гц: %.1f dBFS (доля: %.1f%%)\n",
                        idx + 1, p.frequencyHz, p.magnitudeDbfs, p.relativePowerFraction * 100.0))
                }
            }
        }
        binding.tvMetricPeaksList.text = peaksText.trimEnd()
    }

    // ==========================================
    // EXPORT HELPERS
    // ==========================================

    private fun exportFileToUri(sourceFile: File, destinationUri: Uri) {
        try {
            requireContext().contentResolver.openOutputStream(destinationUri)?.use { out ->
                sourceFile.inputStream().use { inp ->
                    inp.copyTo(out)
                }
            }
            Toast.makeText(requireContext(), "WAV сохранён в выбранную папку", Toast.LENGTH_SHORT).show()
        } catch (t: Throwable) {
            Toast.makeText(requireContext(), "Ошибка экспорта: ${t.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun exportJsonReportToUri(destinationUri: Uri) {
        val analysis = lastAnalysisResult ?: return

        val dev = CalibrationJsonReport.DeviceInfo(
            manufacturer = Build.MANUFACTURER,
            model = Build.MODEL,
            androidRelease = Build.VERSION.RELEASE,
            sdkInt = Build.VERSION.SDK_INT
        )

        val fx = AudioEffectsInspector.inspectEffects()
        val hw = CalibrationJsonReport.AudioHardwareState(
            requestedSampleRate = analysis.sampleRate,
            actualSampleRate = analysis.sampleRate,
            channelCount = analysis.channels,
            audioFormat = "PCM 16-bit mono",
            audioSource = "UNPROCESSED",
            bufferSizeBytes = analysis.sampleCount * 2,
            aecAvailable = fx.aecAvailable,
            agcAvailable = fx.agcAvailable,
            nsAvailable = fx.nsAvailable,
            effectsStateDescription = fx.description
        )

        val tp = CalibrationJsonReport.TestParameters(
            testType = if (lastCapturedRxFile != null) "RECEIVER_CAPTURE" else "WAV_ANALYSIS",
            transmitterFrequencyHz = getSelectedTxFrequency(),
            transmitterAmplitude = getSelectedTxAmplitude(),
            transmitterDurationSeconds = getSelectedTxDuration().toDouble(),
            targetFrequencyHz = getSelectedTxFrequency()
        )

        val warnings = mutableListOf<String>()
        if (analysis.hasClipping) {
            warnings.add("Обнаружен клиппинг (${analysis.clippingCount} сэмплов) — уменьшите громкость или отодвиньте телефон")
        }
        if (analysis.rmsDbfs < -60.0) {
            warnings.add("Низкий уровень сигнала (${String.format(Locale.US, "%.1f", analysis.rmsDbfs)} dBFS) — сигнал едва различим над шумом")
        }

        val json = CalibrationJsonReport.buildReport(
            device = dev,
            hardware = hw,
            testParams = tp,
            analysis = analysis,
            warnings = warnings
        )

        try {
            requireContext().contentResolver.openOutputStream(destinationUri)?.use { out ->
                out.write(json.toByteArray(Charsets.UTF_8))
            }
            Toast.makeText(requireContext(), "JSON-отчёт успешно сохранён", Toast.LENGTH_SHORT).show()
        } catch (t: Throwable) {
            Toast.makeText(requireContext(), "Ошибка экспорта JSON: ${t.message}", Toast.LENGTH_LONG).show()
        }
    }

    companion object {
        fun newInstance() = AudioLabFragment()
    }
}
