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

        binding.btnRequestPermission.setOnClickListener {
            requestPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
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
        hardwareInfo.append("Целевой Sample Rate: 48 000 Гц\n")
        hardwareInfo.append("Канал: Mono, PCM 16-bit LE\n")
        if (minBufferSize > 0) {
            hardwareInfo.append("Аппаратный Min Buffer Size: $minBufferSize байт (~${minBufferSize / 2} сэмплов)\n")
            hardwareInfo.append("AudioRecord HAL: Поддерживается оборудованием")
        } else {
            hardwareInfo.append("AudioRecord HAL: Конфигурация требует уточнения")
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
