package org.silentnetwork.app

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import org.silentnetwork.app.databinding.ActivityMainBinding
import org.silentnetwork.app.ui.devices.DevicesFragment
import org.silentnetwork.app.ui.messenger.MessengerFragment
import org.silentnetwork.app.ui.settings.SettingsFragment
import org.silentnetwork.app.ui.status.StatusFragment

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var currentTabId: Int = R.id.tab_status

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setupNavigation()

        if (savedInstanceState != null) {
            // Restore active tab selection after configuration change / process recreation
            currentTabId = savedInstanceState.getInt(KEY_CURRENT_TAB_ID, R.id.tab_status)
            updateTabAppearance(currentTabId)
        } else {
            // First launch: initialize default Status tab
            selectTab(R.id.tab_status, StatusFragment.newInstance(), TAG_STATUS)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putInt(KEY_CURRENT_TAB_ID, currentTabId)
    }

    private fun setupNavigation() {
        binding.tabStatus.setOnClickListener {
            if (currentTabId != R.id.tab_status) {
                selectTab(R.id.tab_status, StatusFragment.newInstance(), TAG_STATUS)
            }
        }
        binding.tabMessenger.setOnClickListener {
            if (currentTabId != R.id.tab_messenger) {
                selectTab(R.id.tab_messenger, MessengerFragment.newInstance(), TAG_MESSENGER)
            }
        }
        binding.tabDevices.setOnClickListener {
            if (currentTabId != R.id.tab_devices) {
                selectTab(R.id.tab_devices, DevicesFragment.newInstance(), TAG_DEVICES)
            }
        }
        binding.tabSettings.setOnClickListener {
            if (currentTabId != R.id.tab_settings) {
                selectTab(R.id.tab_settings, SettingsFragment.newInstance(), TAG_SETTINGS)
            }
        }
    }

    fun selectTab(tabId: Int, fragment: Fragment, tag: String) {
        currentTabId = tabId
        supportFragmentManager.beginTransaction()
            .setCustomAnimations(android.R.anim.fade_in, android.R.anim.fade_out)
            .replace(R.id.fragment_container, fragment, tag)
            .commit()

        updateTabAppearance(tabId)
    }

    private fun updateTabAppearance(activeTabId: Int) {
        val activeColor = ContextCompat.getColor(this, R.color.accent_cyan)
        val inactiveColor = ContextCompat.getColor(this, R.color.text_muted)

        // Status Tab
        val isStatus = activeTabId == R.id.tab_status
        binding.tvTabStatus.setTextColor(if (isStatus) activeColor else inactiveColor)
        binding.ivTabStatus.setColorFilter(if (isStatus) activeColor else inactiveColor)

        // Messenger Tab
        val isMessenger = activeTabId == R.id.tab_messenger
        binding.tvTabMessenger.setTextColor(if (isMessenger) activeColor else inactiveColor)
        binding.ivTabMessenger.setColorFilter(if (isMessenger) activeColor else inactiveColor)

        // Devices Tab
        val isDevices = activeTabId == R.id.tab_devices
        binding.tvTabDevices.setTextColor(if (isDevices) activeColor else inactiveColor)
        binding.ivTabDevices.setColorFilter(if (isDevices) activeColor else inactiveColor)

        // Settings Tab
        val isSettings = activeTabId == R.id.tab_settings
        binding.tvTabSettings.setTextColor(if (isSettings) activeColor else inactiveColor)
        binding.ivTabSettings.setColorFilter(if (isSettings) activeColor else inactiveColor)
    }

    companion object {
        const val KEY_CURRENT_TAB_ID = "org.silentnetwork.app.KEY_CURRENT_TAB_ID"
        const val TAG_STATUS = "tab_status"
        const val TAG_MESSENGER = "tab_messenger"
        const val TAG_DEVICES = "tab_devices"
        const val TAG_SETTINGS = "tab_settings"
    }
}
