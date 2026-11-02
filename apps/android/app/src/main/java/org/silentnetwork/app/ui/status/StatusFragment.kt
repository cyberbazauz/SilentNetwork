package org.silentnetwork.app.ui.status

import android.Manifest
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import org.silentnetwork.app.R
import org.silentnetwork.app.databinding.FragmentStatusBinding

class StatusFragment : Fragment() {

    private var _binding: FragmentStatusBinding? = null
    private val binding get() = _binding!!

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted: Boolean ->
        updatePermissionStatus(isGranted)
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentStatusBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val hasPermission = ContextCompat.checkSelfPermission(
            requireContext(),
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED

        updatePermissionStatus(hasPermission)
        inspectAudioHardware()

        binding.tvStatusDetailsToggle.setOnClickListener {
            val expanded = binding.llStatusDetails.visibility == View.VISIBLE
            binding.llStatusDetails.visibility = if (expanded) View.GONE else View.VISIBLE
            binding.tvStatusDetailsToggle.setText(
                if (expanded) R.string.status_details_closed else R.string.status_details_opened
            )
        }

        binding.btnRequestPermission.setOnClickListener {
            requestPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }

        binding.btnOpenAudioLab.setOnClickListener {
            (activity as? org.silentnetwork.app.MainActivity)?.let { act ->
                act.selectTab(
                    R.id.tab_audio_lab,
                    org.silentnetwork.app.ui.audiolab.AudioLabFragment.newInstance(),
                    org.silentnetwork.app.MainActivity.TAG_AUDIO_LAB
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        if (isAdded) {
            val hasPermission = ContextCompat.checkSelfPermission(
                requireContext(),
                Manifest.permission.RECORD_AUDIO
            ) == PackageManager.PERMISSION_GRANTED
            updatePermissionStatus(hasPermission)
        }
    }

    private fun updatePermissionStatus(isGranted: Boolean) {
        if (!isAdded) return
        if (isGranted) {
            binding.tvMicPermStatus.text = getString(R.string.status_perm_granted)
            binding.tvMicPermStatus.setTextColor(ContextCompat.getColor(requireContext(), R.color.accent_emerald))
            binding.btnRequestPermission.visibility = View.GONE
        } else {
            binding.tvMicPermStatus.text = getString(R.string.status_perm_denied)
            binding.tvMicPermStatus.setTextColor(ContextCompat.getColor(requireContext(), R.color.accent_rose))
            binding.btnRequestPermission.visibility = View.VISIBLE
        }
    }

    private fun inspectAudioHardware() {
        if (!isAdded) return
        val sampleRate = 48000
        val channelConfig = AudioFormat.CHANNEL_IN_MONO
        val audioFormat = AudioFormat.ENCODING_PCM_16BIT
        val minBufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)

        val hardwareInfo = StringBuilder()
        hardwareInfo.append("Частота дискретизации: 48 000 Гц\n")
        hardwareInfo.append("Канал: моно, PCM 16 бит\n")
        if (minBufferSize > 0) {
            hardwareInfo.append("Минимальный буфер: $minBufferSize байт\n")
            hardwareInfo.append("AudioRecord: поддерживается устройством")
        } else {
            hardwareInfo.append("AudioRecord: параметры требуют уточнения")
        }
        binding.tvHardwareInfo.text = hardwareInfo.toString()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }

    companion object {
        fun newInstance() = StatusFragment()
    }
}
