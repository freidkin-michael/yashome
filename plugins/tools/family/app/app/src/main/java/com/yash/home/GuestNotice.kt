package com.yash.home

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import org.json.JSONObject

/** A browser asks to come in (the house's guest access): {cmd: guest, id, code, ua} from the live link or a pull. An administrator's
 *  phone gets it (the house sends it to no other) and shows it; a tap opens GuestActivity to allow or deny. Only the request id, the code and the browser come: no secret. */
object GuestNotice {
    private const val CH = "guest"
    private const val WAIT_MS = 300_000L                 // the house drops an unanswered request after 5 minutes
    private val ID_RE = Regex("[A-Za-z0-9_-]{8,64}")

    /** One notification for all requests: a newer one replaces it, only the first sounds. */
    fun notifId(id: String) = NOTIF_ID
    const val NOTIF_ID = 1999

    fun show(ctx: Context, j: JSONObject) {
        val id = j.optString("id")
        val pausedUntil = j.optLong("paused_until", 0L)
        if (!ID_RE.matches(id) && pausedUntil <= 0L) return
        val waiting = j.optInt("waiting", 1).coerceAtLeast(1)
        val code = j.optString("code").filter { it.isDigit() }.take(6)
        val ua = j.optString("ua").filter { it >= ' ' }.take(40)
        if (Build.VERSION.SDK_INT >= 33 &&
            ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            Prefs.logEvent(ctx, "guest: notifications not allowed")
            return
        }
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH, ctx.getString(R.string.ch_guest), NotificationManager.IMPORTANCE_HIGH).apply {
            lockscreenVisibility = Notification.VISIBILITY_PRIVATE
        })
        val n = NOTIF_ID                                       // one notice for every request: a flood cannot pile them up
        if (pausedUntil > 0L) {                                // a flood: the house stopped taking requests for a while
            val hhmm = java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault()).format(java.util.Date(pausedUntil * 1000))
            nm.notify(n, NotificationCompat.Builder(ctx, CH).setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle(ctx.getString(R.string.guest_flood_title))
                .setContentText(ctx.getString(R.string.guest_flood_text, hhmm))
                .setAutoCancel(true).setOnlyAlertOnce(true).setCategory(NotificationCompat.CATEGORY_STATUS).build())
            Prefs.logEvent(ctx, "guest: requests paused (a flood)")
            return
        }
        val open = Intent(ctx, GuestActivity::class.java).putExtra(GuestActivity.EXTRA_ID, id)
            .putExtra(GuestActivity.EXTRA_CODE, code).putExtra(GuestActivity.EXTRA_UA, ua).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val tap = PendingIntent.getActivity(ctx, n, open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        nm.notify(n, NotificationCompat.Builder(ctx, CH).setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(ctx.getString(R.string.guest_title))
            .setContentText(if (waiting > 1) ctx.getString(R.string.guest_text_many, waiting, code)
                            else ctx.getString(R.string.guest_text, code, ua.ifEmpty { "?" }))
            .setContentIntent(tap).setAutoCancel(true).setTimeoutAfter(WAIT_MS).setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH).setCategory(NotificationCompat.CATEGORY_MESSAGE).build())
        Prefs.logEvent(ctx, "guest: a browser asks to come in")
    }

    fun cancel(ctx: Context, id: String) = ctx.getSystemService(NotificationManager::class.java).cancel(notifId(id))
}
