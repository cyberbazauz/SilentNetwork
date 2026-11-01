package org.silentnetwork.app

import android.app.Application
import android.util.Log

/**
 * Main application class for SilentNetwork Android client.
 * Responsible for high-level application lifecycle and logging.
 */
class SilentApplication : Application() {

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "SilentNetwork Android application initialized [Skeleton Mode]")
    }

    companion object {
        private const val TAG = "SilentApplication"
    }
}
