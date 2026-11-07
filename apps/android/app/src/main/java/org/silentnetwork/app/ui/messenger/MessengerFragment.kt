package org.silentnetwork.app.ui.messenger

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.OpenableColumns
import android.util.TypedValue
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import org.silentnetwork.app.R
import org.silentnetwork.app.audiolab.audio.AndroidAudioPlayerEngine
import org.silentnetwork.app.audiolab.chat.ChatMode
import org.silentnetwork.app.audiolab.chat.ChatSession
import org.silentnetwork.app.audiolab.chat.ContinuousAudioCapture
import org.silentnetwork.app.audiolab.datalink.BfskProfile
import org.silentnetwork.app.audiolab.datalink.DataLinkCodec
import org.silentnetwork.app.audiolab.datalink.FileTransferCodec
import org.silentnetwork.app.databinding.FragmentMessengerBinding
import org.silentnetwork.app.state.MessengerValidator
import java.io.ByteArrayOutputStream
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Continuous acoustic chat.
 *
 * The microphone is open while this screen is visible. Received frames are
 * decoded incrementally; sending immediately plays the acoustic signal. A
 * message is shown as "в эфире" when playback finished, which does not prove
 * delivery: there is no ACK and no encryption in v0.
 */
class MessengerFragment : Fragment() {

    private var _binding: FragmentMessengerBinding? = null
    private val binding get() = _binding!!

    private val mainHandler = Handler(Looper.getMainLooper())
    private val timeFormat = SimpleDateFormat("HH:mm", Locale.getDefault())

    private var chatMode: ChatMode = ChatMode.DEFAULT
    private val profile: BfskProfile get() = chatMode.profile
    private var suppressModeCallback = false
    private var player: AndroidAudioPlayerEngine? = null
    private var capture: ContinuousAudioCapture? = null
    private var chatSession: ChatSession? = null

    private var busy = false
    private var playing = false
    private var playbackGeneration = 0
    @Volatile
    private var lastProgressPercent = -1

    private var activeStatusView: TextView? = null
    private var activeDoneText = ""
    private var activeDurationS = 0.0
    private var activeIsFile = false
    private var activeFileName = ""

    private var transferCounter = 0
    private val fileStatusViews = LinkedHashMap<Int, TextView>()
    private val fileNames = HashMap<Int, String>()
    private val completedFiles = HashSet<Int>()

    private enum class Tone { LISTENING, WARNING, ERROR }

    private val requestRecordPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            startCapture()
        } else {
            setStatus(getString(R.string.messenger_status_no_permission), Tone.ERROR)
            Toast.makeText(
                requireContext(),
                getString(R.string.messenger_status_no_permission),
                Toast.LENGTH_LONG
            ).show()
        }
    }

    private val pickFile = registerForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri ->
        if (uri != null) {
            sendFile(uri)
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentMessengerBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        savedInstanceState?.getString(KEY_DRAFT_MESSAGE)?.let { draft ->
            binding.etMessageInput.setText(draft)
        }
        savedInstanceState?.getString(KEY_CHAT_MODE)?.let { modeId ->
            ChatMode.fromId(modeId)?.let { chatMode = it }
        }

        player = AndroidAudioPlayerEngine { requireContext().applicationContext }
        setupModeSelector()

        binding.btnSendMessage.setOnClickListener { sendText() }
        binding.btnAttachFile.setOnClickListener {
            if (busy || playing) {
                toastBusy()
            } else {
                pickFile.launch(arrayOf("*/*"))
            }
        }
        binding.btnStopPlayback.setOnClickListener { stopPlayback() }
        binding.etMessageInput.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                sendText()
                true
            } else {
                false
            }
        }

        binding.llEmptyState.visibility = View.VISIBLE
        if (hasRecordPermission()) {
            startCapture()
        } else {
            setStatus(getString(R.string.messenger_status_starting), Tone.WARNING)
            requestRecordPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun hasRecordPermission(): Boolean {
        return ContextCompat.checkSelfPermission(
            requireContext(),
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun setupModeSelector() {
        val modes = ChatMode.entries
        val adapter = ArrayAdapter(
            requireContext(),
            android.R.layout.simple_spinner_item,
            modes.map { modeLabel(it) }
        )
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        binding.spinnerChatMode.adapter = adapter
        binding.spinnerChatMode.setSelection(modes.indexOf(chatMode), false)
        updateModeHint()
        binding.spinnerChatMode.onItemSelectedListener =
            object : AdapterView.OnItemSelectedListener {
                override fun onItemSelected(
                    parent: AdapterView<*>?,
                    view: View?,
                    position: Int,
                    id: Long
                ) {
                    if (suppressModeCallback) return
                    onModeSelected(modes[position])
                }

                override fun onNothingSelected(parent: AdapterView<*>?) = Unit
            }
    }

    private fun onModeSelected(selected: ChatMode) {
        if (selected == chatMode) {
            updateModeHint()
            return
        }
        if (busy || playing) {
            suppressModeCallback = true
            binding.spinnerChatMode.setSelection(
                ChatMode.entries.indexOf(chatMode),
                false
            )
            suppressModeCallback = false
            Toast.makeText(
                requireContext(),
                getString(R.string.messenger_mode_busy),
                Toast.LENGTH_SHORT
            ).show()
            return
        }

        chatMode = selected
        updateModeHint()
        if (capture != null) {
            // The demodulator is profile-specific: stop the old capture,
            // drop the old decoder/assembler, and open the stream again with
            // the new profile.
            capture?.stop()
            capture = null
            chatSession = null
            startCapture()
        } else {
            setStatus(
                getString(
                    R.string.messenger_mode_selected_no_capture,
                    modeLabel(selected)
                ),
                Tone.WARNING
            )
        }
    }

    private fun modeLabel(mode: ChatMode): String = getString(
        when (mode) {
            ChatMode.NORMAL -> R.string.messenger_mode_normal
            ChatMode.EXCELLENT -> R.string.messenger_mode_excellent
            ChatMode.HIGH_FREQ -> R.string.messenger_mode_highfreq
        }
    )

    private fun modeSummary(mode: ChatMode): String = getString(
        when (mode) {
            ChatMode.NORMAL -> R.string.messenger_mode_normal_summary
            ChatMode.EXCELLENT -> R.string.messenger_mode_excellent_summary
            ChatMode.HIGH_FREQ -> R.string.messenger_mode_highfreq_summary
        }
    )

    private fun updateModeHint() {
        if (_binding == null) return
        binding.tvChatModeHint.text = modeSummary(chatMode)
    }

    private fun listeningLabel(): String = "${modeLabel(chatMode)} · ${profile.name}"

    private fun startCapture() {
        if (capture != null || _binding == null) return
        if (!hasRecordPermission()) {
            setStatus(getString(R.string.messenger_status_no_permission), Tone.ERROR)
            return
        }

        val outputDir = File(
            requireContext().getExternalFilesDir(null) ?: requireContext().filesDir,
            "received"
        )
        outputDir.mkdirs()
        val session = ChatSession(profile = profile, outputDir = outputDir)
        chatSession = session

        val engine = ContinuousAudioCapture(
            sampleRate = profile.sampleRate,
            onSamples = { samples ->
                val events = try {
                    session.feed(samples)
                } catch (error: Throwable) {
                    emptyList()
                }
                if (events.isNotEmpty()) {
                    mainHandler.post {
                        if (_binding != null) handleEvents(events)
                    }
                }
            },
            onError = { error ->
                mainHandler.post {
                    if (_binding != null) {
                        setStatus(
                            getString(
                                R.string.messenger_status_error,
                                error.message ?: error.javaClass.simpleName
                            ),
                            Tone.ERROR
                        )
                    }
                }
            },
            onGap = { reason ->
                // Keep file chunks already accepted; only the PCM window that
                // crossed the gap is untrustworthy.
                session.resetDecoder()
                mainHandler.post {
                    if (_binding == null) return@post
                    val text = if (reason == "overflow") {
                        getString(R.string.messenger_gap_overflow)
                    } else {
                        getString(R.string.messenger_gap_pause)
                    }
                    setStatus(text, Tone.WARNING)
                }
            }
        )
        capture = engine
        val result = engine.start()
        if (result.isSuccess) {
            setStatus(
                getString(R.string.messenger_status_listening, listeningLabel()),
                Tone.LISTENING
            )
        } else {
            capture = null
            val error = result.exceptionOrNull()
            setStatus(
                getString(
                    R.string.messenger_status_error,
                    error?.message ?: "не удалось открыть микрофон"
                ),
                Tone.ERROR
            )
        }
    }

    private fun sendText() {
        if (busy || playing) {
            toastBusy()
            return
        }
        val raw = binding.etMessageInput.text?.toString()
        val text = when (val result = MessengerValidator.validateInput(raw)) {
            is MessengerValidator.ValidationResult.Empty -> {
                Toast.makeText(
                    requireContext(),
                    "Сообщение не может быть пустым",
                    Toast.LENGTH_SHORT
                ).show()
                return
            }
            is MessengerValidator.ValidationResult.TooLong -> {
                Toast.makeText(
                    requireContext(),
                    "Сообщение слишком длинное",
                    Toast.LENGTH_SHORT
                ).show()
                return
            }
            is MessengerValidator.ValidationResult.Valid -> result.cleanText
        }

        val message = try {
            val payload = DataLinkCodec.textToPayload(text)
            if (payload.size >= 2 &&
                payload[0] == 0x53.toByte() &&
                payload[1] == 0x46.toByte()
            ) {
                Toast.makeText(
                    requireContext(),
                    "Текст не может начинаться с SF: это префикс файлового пакета",
                    Toast.LENGTH_LONG
                ).show()
                return
            }
            DataLinkCodec.encodeToPcm(text, profile)
        } catch (error: Throwable) {
            Toast.makeText(
                requireContext(),
                error.message ?: "Не удалось закодировать сообщение",
                Toast.LENGTH_LONG
            ).show()
            return
        }

        binding.etMessageInput.setText("")
        busy = true
        setComposerEnabled(false)
        activeStatusView = addMessage(
            text = text,
            status = getString(R.string.messenger_tx_playing),
            outgoing = true
        )
        activeDoneText = getString(R.string.messenger_tx_done)
        activeDurationS = message.durationS
        activeIsFile = false
        activeFileName = ""
        startPlayback(message.pcmBytes, message.sampleRate)
    }

    private fun sendFile(uri: Uri) {
        if (busy || playing) {
            toastBusy()
            return
        }
        val (rawName, declaredSize) = queryFile(uri)
        if (declaredSize != null && declaredSize > FileTransferCodec.MAX_FILE_BYTES) {
            Toast.makeText(
                requireContext(),
                getString(R.string.messenger_file_too_large),
                Toast.LENGTH_LONG
            ).show()
            return
        }
        val filename = try {
            FileTransferCodec.validateFilename(rawName)
            rawName
        } catch (error: Throwable) {
            Toast.makeText(
                requireContext(),
                error.message ?: "Недопустимое имя файла",
                Toast.LENGTH_LONG
            ).show()
            return
        }

        busy = true
        setComposerEnabled(false)
        val statusView = addMessage(
            text = filename,
            status = getString(R.string.messenger_tx_playing),
            outgoing = true
        )
        activeStatusView = statusView

        Thread {
            try {
                val content = readBounded(uri, FileTransferCodec.MAX_FILE_BYTES)
                if (content.isEmpty()) {
                    throw IllegalArgumentException(getString(R.string.messenger_file_empty))
                }
                val transferId = nextTransferId()
                val message = FileTransferCodec.encodeFileToPcm(
                    data = content,
                    filename = filename,
                    profile = profile,
                    transferId = transferId
                )
                mainHandler.post {
                    if (_binding == null || !busy) return@post
                    activeDoneText = getString(R.string.messenger_tx_done)
                    activeDurationS = message.durationS
                    activeIsFile = true
                    activeFileName = filename
                    startPlayback(message.pcmBytes, message.sampleRate)
                }
            } catch (error: Throwable) {
                mainHandler.post {
                    if (_binding == null) return@post
                    busy = false
                    setComposerEnabled(true)
                    activeStatusView = null
                    statusView.text = getString(R.string.messenger_tx_error)
                    val messageText = error.message
                        ?: getString(R.string.messenger_file_read_error)
                    Toast.makeText(requireContext(), messageText, Toast.LENGTH_LONG).show()
                    setStatus(messageText, Tone.ERROR)
                }
            }
        }.apply {
            name = "AcousticChat-FilePrepare"
            isDaemon = true
            start()
        }
    }

    private fun startPlayback(pcm: ByteArray, sampleRate: Int) {
        if (_binding == null) return
        val engine = player ?: run {
            busy = false
            setComposerEnabled(true)
            return
        }
        val generation = ++playbackGeneration
        playing = true
        lastProgressPercent = -1
        binding.llPlayback.visibility = View.VISIBLE
        binding.progressPlayback.progress = 0
        binding.tvPlayback.text = if (activeIsFile) {
            getString(
                R.string.messenger_playback_file,
                0,
                activeFileName
            )
        } else {
            getString(R.string.messenger_playback_progress, 0)
        }
        capture?.pause()
        setStatus(getString(R.string.messenger_status_paused), Tone.WARNING)

        val result = engine.playPcm(
            pcmData = pcm,
            sampleRate = sampleRate,
            onProgress = { elapsed, total ->
                val percent = if (total <= 0.0) {
                    0
                } else {
                    ((elapsed / total) * 100.0).toInt().coerceIn(0, 99)
                }
                if (percent != lastProgressPercent) {
                    lastProgressPercent = percent
                    mainHandler.post {
                        if (generation != playbackGeneration || _binding == null) {
                            return@post
                        }
                        binding.progressPlayback.progress = percent
                        binding.tvPlayback.text = if (activeIsFile) {
                            getString(
                                R.string.messenger_playback_file,
                                percent,
                                activeFileName
                            )
                        } else {
                            getString(R.string.messenger_playback_progress, percent)
                        }
                    }
                }
            },
            onComplete = {
                mainHandler.post {
                    if (generation == playbackGeneration) {
                        finishPlayback(success = true, error = null, stopped = false)
                    }
                }
            },
            onError = { error ->
                mainHandler.post {
                    if (generation == playbackGeneration) {
                        finishPlayback(success = false, error = error, stopped = false)
                    }
                }
            }
        )
        if (result.isFailure) {
            finishPlayback(
                success = false,
                error = result.exceptionOrNull(),
                stopped = false
            )
        }
    }

    private fun stopPlayback() {
        if (!playing) return
        playbackGeneration++
        player?.stop()
        finishPlayback(success = false, error = null, stopped = true)
    }

    private fun finishPlayback(success: Boolean, error: Throwable?, stopped: Boolean) {
        if (!busy && !playing && activeStatusView == null) return
        playing = false
        busy = false
        if (_binding == null) {
            activeStatusView = null
            capture?.resume()
            return
        }
        binding.llPlayback.visibility = View.GONE
        setComposerEnabled(true)
        binding.progressPlayback.progress = if (success) 100 else 0

        val statusView = activeStatusView
        activeStatusView = null
        when {
            success -> {
                val suffix = String.format(Locale.US, "%.1f с", activeDurationS)
                statusView?.text = "$activeDoneText · $suffix"
                setStatus(
                    getString(R.string.messenger_status_listening, listeningLabel()),
                    Tone.LISTENING
                )
            }
            stopped -> {
                statusView?.text = getString(R.string.messenger_tx_stopped)
                setStatus(
                    getString(R.string.messenger_status_listening, listeningLabel()),
                    Tone.LISTENING
                )
            }
            else -> {
                val messageText = error?.message ?: "не удалось воспроизвести звук"
                statusView?.text = getString(R.string.messenger_tx_error)
                setStatus(messageText, Tone.ERROR)
                Toast.makeText(requireContext(), messageText, Toast.LENGTH_LONG).show()
            }
        }
        activeIsFile = false
        activeFileName = ""
        capture?.resume()
    }

    private fun handleEvents(events: List<ChatSession.Event>) {
        if (_binding == null) return
        for (event in events) {
            when (event) {
                is ChatSession.Event.TextReceived -> {
                    addMessage(
                        text = event.text,
                        status = "${getString(R.string.messenger_rx_text)} · " +
                            timeFormat.format(Date(event.atMillis)),
                        outgoing = false
                    )
                    setStatus(
                        getString(R.string.messenger_status_listening, listeningLabel()),
                        Tone.LISTENING
                    )
                }

                is ChatSession.Event.FileStarted -> {
                    completedFiles.remove(event.transferId)
                    val status = addMessage(
                        text = event.filename,
                        status = getString(
                            R.string.messenger_file_progress,
                            event.filename,
                            0,
                            event.totalChunks
                        ),
                        outgoing = false
                    )
                    fileStatusViews[event.transferId] = status
                    fileNames[event.transferId] = event.filename
                    setStatus(
                        getString(
                            R.string.messenger_file_started,
                            event.filename,
                            event.fileSize
                        ),
                        Tone.LISTENING
                    )
                }

                is ChatSession.Event.FileProgress -> {
                    if (event.transferId in completedFiles) continue
                    val statusView = fileStatusViews[event.transferId] ?: continue
                    statusView.text = getString(
                        R.string.messenger_file_progress,
                        fileNames[event.transferId] ?: "файл",
                        event.receivedChunks,
                        event.totalChunks
                    )
                }

                is ChatSession.Event.FileCompleted -> {
                    completedFiles.add(event.transferId)
                    val statusView = fileStatusViews[event.transferId]
                    statusView?.text = if (event.savedTo != null) {
                        getString(
                            R.string.messenger_file_saved,
                            event.filename,
                            event.fileSize
                        ) + "\n" + getString(R.string.messenger_saved_to, event.savedTo)
                    } else {
                        getString(R.string.messenger_file_not_saved)
                    }
                    setStatus(
                        getString(
                            R.string.messenger_file_saved,
                            event.filename,
                            event.fileSize
                        ),
                        Tone.LISTENING
                    )
                }

                is ChatSession.Event.FileFailed -> {
                    val statusView = event.transferId?.let { fileStatusViews[it] }
                    val text = getString(R.string.messenger_file_failed, event.message)
                    if (statusView != null) {
                        statusView.text = text
                    } else {
                        addSystemMessage(text)
                    }
                    setStatus(text, Tone.ERROR)
                }

                is ChatSession.Event.FrameFailed -> {
                    addSystemMessage(
                        getString(R.string.messenger_frame_failed, event.message)
                    )
                }
            }
        }
    }

    private fun addMessage(text: String, status: String, outgoing: Boolean): TextView {
        val context = requireContext()
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = if (outgoing) Gravity.END else Gravity.START
            setPadding(0, dp(4), 0, dp(4))
        }
        val bubble = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(
                if (outgoing) R.drawable.bg_bubble_out else R.drawable.bg_bubble_in
            )
            setPadding(dp(12), dp(8), dp(12), dp(8))
        }
        val maxWidth = (resources.displayMetrics.widthPixels * 0.74).toInt()

        val content = TextView(context).apply {
            this.text = text
            setTextColor(
                ContextCompat.getColor(
                    context,
                    if (outgoing) R.color.text_on_accent else R.color.text_primary
                )
            )
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            this.maxWidth = maxWidth
        }
        val statusView = TextView(context).apply {
            this.text = status
            setTextColor(
                ContextCompat.getColor(
                    context,
                    if (outgoing) R.color.text_on_accent else R.color.text_muted
                )
            )
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 10f)
            setPadding(0, dp(3), 0, 0)
            this.maxWidth = maxWidth
        }
        bubble.addView(content)
        bubble.addView(statusView)

        val params = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        )
        row.addView(bubble, params)
        binding.llMessages.addView(row)
        binding.llEmptyState.visibility = View.GONE
        scrollToBottom()
        return statusView
    }

    private fun addSystemMessage(text: String) {
        if (_binding == null) return
        val context = requireContext()
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(0, dp(3), 0, dp(3))
        }
        val label = TextView(context).apply {
            this.text = text
            setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            setBackgroundResource(R.drawable.bg_bubble_system)
            setPadding(dp(10), dp(6), dp(10), dp(6))
            maxWidth = (resources.displayMetrics.widthPixels * 0.82).toInt()
        }
        row.addView(label)
        binding.llMessages.addView(row)
        binding.llEmptyState.visibility = View.GONE
        scrollToBottom()
    }

    private fun scrollToBottom() {
        binding.scrollMessages.post {
            binding.scrollMessages.fullScroll(View.FOCUS_DOWN)
        }
    }

    private fun setStatus(text: String, tone: Tone) {
        if (_binding == null) return
        binding.tvChatStatus.text = text
        binding.llChatStatus.setBackgroundResource(
            when (tone) {
                Tone.LISTENING -> R.drawable.bg_badge_ready
                Tone.WARNING -> R.drawable.bg_chat_status_warning
                Tone.ERROR -> R.drawable.bg_badge_error
            }
        )
        binding.tvChatStatus.setTextColor(
            ContextCompat.getColor(
                requireContext(),
                when (tone) {
                    Tone.LISTENING -> R.color.accent_emerald
                    Tone.WARNING -> R.color.accent_amber
                    Tone.ERROR -> R.color.accent_rose
                }
            )
        )
    }

    private fun setComposerEnabled(enabled: Boolean) {
        if (_binding == null) return
        binding.etMessageInput.isEnabled = enabled
        binding.btnSendMessage.isEnabled = enabled
        binding.btnAttachFile.isEnabled = enabled
        val alpha = if (enabled) 1f else 0.5f
        binding.etMessageInput.alpha = alpha
        binding.btnSendMessage.alpha = alpha
        binding.btnAttachFile.alpha = alpha
    }

    private fun toastBusy() {
        Toast.makeText(
            requireContext(),
            "Дождитесь окончания воспроизведения",
            Toast.LENGTH_SHORT
        ).show()
    }

    private fun queryFile(uri: Uri): Pair<String, Long?> {
        var name: String? = null
        var size: Long? = null
        try {
            requireContext().contentResolver.query(uri, null, null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) {
                    val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if (nameIndex >= 0 && !cursor.isNull(nameIndex)) {
                        name = cursor.getString(nameIndex)
                    }
                    val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                    if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) {
                        size = cursor.getLong(sizeIndex)
                    }
                }
            }
        } catch (_: Throwable) {
        }
        return (name ?: "file.bin") to size
    }

    private fun readBounded(uri: Uri, limit: Int): ByteArray {
        val stream = requireContext().contentResolver.openInputStream(uri)
            ?: throw IllegalStateException(getString(R.string.messenger_file_read_error))
        stream.use { input ->
            val out = ByteArrayOutputStream()
            val buffer = ByteArray(4_096)
            var total = 0
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                total += read
                if (total > limit) {
                    throw IllegalArgumentException(getString(R.string.messenger_file_too_large))
                }
                out.write(buffer, 0, read)
            }
            return out.toByteArray()
        }
    }

    private fun nextTransferId(): Int {
        transferCounter = (transferCounter % 255) + 1
        return transferCounter
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(KEY_CHAT_MODE, chatMode.id)
        _binding?.let {
            val currentDraft = it.etMessageInput.text?.toString()
            if (!currentDraft.isNullOrEmpty()) {
                outState.putString(KEY_DRAFT_MESSAGE, currentDraft)
            }
        }
    }

    override fun onDestroyView() {
        busy = false
        playing = false
        playbackGeneration++
        capture?.stop()
        capture = null
        chatSession = null
        player?.release()
        player = null
        super.onDestroyView()
        _binding = null
    }

    companion object {
        const val KEY_DRAFT_MESSAGE = "org.silentnetwork.app.KEY_DRAFT_MESSAGE"
        const val KEY_CHAT_MODE = "org.silentnetwork.app.KEY_CHAT_MODE"
        fun newInstance() = MessengerFragment()
    }
}
