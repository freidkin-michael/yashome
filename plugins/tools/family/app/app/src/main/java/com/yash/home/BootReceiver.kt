package com.yash.home

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** After a reboot or an app update the network listener and the widget timer come back by themselves. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            Link.start(ctx)
            RotationGuard.schedule(ctx)
        }
    }
}
