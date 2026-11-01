package org.silentnetwork.app.ui.messenger

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.Toast
import androidx.fragment.app.Fragment
import org.silentnetwork.app.R
import org.silentnetwork.app.databinding.FragmentMessengerBinding

class MessengerFragment : Fragment() {

    private var _binding: FragmentMessengerBinding? = null
    private val binding get() = _binding!!

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

        // Restore saved draft message if present
        savedInstanceState?.getString(KEY_DRAFT_MESSAGE)?.let { draft ->
            binding.etMessageInput.setText(draft)
        }

        binding.btnSendMessage.setOnClickListener {
            handleSendMessage()
        }

        binding.etMessageInput.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                handleSendMessage()
                true
            } else {
                false
            }
        }
    }

    private fun handleSendMessage() {
        val raw = binding.etMessageInput.text?.toString()
        when (val result = org.silentnetwork.app.state.MessengerValidator.evaluateSendAttempt(raw, isCoreConnected = false)) {
            is org.silentnetwork.app.state.MessengerValidator.SendAttemptResult.RejectedInvalidInput -> {
                Toast.makeText(
                    requireContext(),
                    result.error,
                    Toast.LENGTH_SHORT
                ).show()
            }
            is org.silentnetwork.app.state.MessengerValidator.SendAttemptResult.RejectedCoreNotConnected -> {
                Toast.makeText(
                    requireContext(),
                    result.reason,
                    Toast.LENGTH_LONG
                ).show()
            }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        _binding?.let {
            val currentDraft = it.etMessageInput.text?.toString()
            if (!currentDraft.isNullOrEmpty()) {
                outState.putString(KEY_DRAFT_MESSAGE, currentDraft)
            }
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    companion object {
        const val KEY_DRAFT_MESSAGE = "org.silentnetwork.app.KEY_DRAFT_MESSAGE"
        fun newInstance() = MessengerFragment()
    }
}
