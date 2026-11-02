package com.yash.home

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings

/** First-run asks (notifications, location) - never an exemption from battery optimisation. */
object Perms {
    const val REQ_NOTIF = 7

    private fun needsNotif(a: Activity) = Build.VERSION.SDK_INT >= 33 &&
        a.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED

    fun firstRun(a: Activity) {
        val p = a.getSharedPreferences("yash", Context.MODE_PRIVATE)
        if (p.getBoolean("first_run_done", false)) return
        if (needsNotif(a) && !p.getBoolean("notif_asked", false)) {
            p.edit().putBoolean("notif_asked", true).apply()
            a.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIF)
            return   // the first-run mark follows from onRequestPermissionsResult
        }
        p.edit().putBoolean("first_run_done", true).apply()
    }

    const val REQ_LOC = 8

    /** Once: why the app wants the location permission (the house reads the Wi-Fi name with it, to know the phone is home), then the ask.
     *  The location itself is never read or sent. Declining is fine: the house then trusts the other presence signals. */
    fun askLocationOnce(a: Activity) {
        val p = a.getSharedPreferences("yash", Context.MODE_PRIVATE)
        if (!p.getBoolean("first_run_done", false) || p.getBoolean("loc_asked", false)) return
        if (a.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) return
        p.edit().putBoolean("loc_asked", true).apply()
        androidx.appcompat.app.AlertDialog.Builder(a)
            .setTitle(R.string.loc_title).setMessage(R.string.loc_text)
            .setPositiveButton(R.string.loc_ok) { _, _ -> a.requestPermissions(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION), REQ_LOC) }
            .setNegativeButton(R.string.loc_later, null).show()
    }

    const val REQ_BGLOC = 9

    /** Once, after the foreground location is granted: the Wi-Fi name is read once a minute with the app closed too, and Android
     *  gives that only as "Allow all the time" (on Android 11+ the system opens the settings page for it). Declining is fine. */
    fun askBackgroundOnce(a: Activity) {
        val p = a.getSharedPreferences("yash", Context.MODE_PRIVATE)
        if (Build.VERSION.SDK_INT < 29 || !p.getBoolean("first_run_done", false) || p.getBoolean("bgloc_asked", false)) return
        if (a.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED ||
            a.checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION) == PackageManager.PERMISSION_GRANTED) return
        p.edit().putBoolean("bgloc_asked", true).apply()
        androidx.appcompat.app.AlertDialog.Builder(a)
            .setTitle(R.string.bgloc_title).setMessage(R.string.bgloc_text)
            .setPositiveButton(R.string.loc_ok) { _, _ -> a.requestPermissions(arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION), REQ_BGLOC) }
            .setNegativeButton(R.string.loc_later, null).show()
    }

    fun openAppSettings(a: Activity) =
        a.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).setData(Uri.parse("package:${a.packageName}")))
}
