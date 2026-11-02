package com.yash.home

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build

/** The installer's answer to AppUpdate: a confirm screen to open, or the end of the update. */
class UpdateReceiver : BroadcastReceiver() {
    @Suppress("DEPRECATION")
    override fun onReceive(ctx: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, -1)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirm = if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                              else intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if (confirm != null) AppUpdate.askUser(ctx, confirm)
            }
            PackageInstaller.STATUS_SUCCESS -> Prefs.logEvent(ctx, "update: installed")
            else -> Prefs.logEvent(ctx, "update: failed " + intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE))
        }
    }
}
