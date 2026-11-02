package com.yash.home

import android.content.Context
import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/** FCM wakes the phone with a command ("pull": ask the house over HTTPS; ring, stop, sync); no home data travels in a push. */
class PushService : FirebaseMessagingService() {
    override fun onMessageReceived(msg: RemoteMessage) {
        val cmd = msg.data["cmd"] ?: return
        if (cmd == "pull") {                       // a tickle: no data in it, the phone asks the house over HTTPS (frequent: not logged)
            PullWorker.enqueue(this, msg.priority == RemoteMessage.PRIORITY_HIGH)
            return
        }
        Log.i(TAG, "push cmd=$cmd prio=${msg.priority}")
        Prefs.logEvent(this, "push $cmd")
        when (cmd) {
            "ring" -> { RingService.ring(this, msg.data["secs"]?.toIntOrNull() ?: 60); Cloud.ack(this) }
            "stop" -> RingService.stop(this)
            "sync" -> SyncWorker.enqueue(this)
        }
    }

    override fun onNewToken(token: String) {
        Prefs.logEvent(this, "new token")
        Cloud.publishToken(this, token)
    }

    companion object { const val TAG = "YashPush" }
}

object Prefs {
    private fun p(ctx: Context) = ctx.getSharedPreferences("yash", Context.MODE_PRIVATE)

    fun token(ctx: Context): String? = p(ctx).getString("fcm_token", null)
    fun setToken(ctx: Context, t: String) = p(ctx).edit().putString("fcm_token", t).apply()

    // Last 10 events, so a missed ring can be diagnosed on the phone itself
    fun logEvent(ctx: Context, what: String) {
        val ts = java.text.SimpleDateFormat("dd.MM HH:mm:ss", java.util.Locale.US).format(java.util.Date())
        val old = p(ctx).getString("events", "") ?: ""
        val lines = ("$ts $what\n$old").lines().filter { it.isNotBlank() }.take(10)
        p(ctx).edit().putString("events", lines.joinToString("\n")).apply()
    }
    fun events(ctx: Context): String = p(ctx).getString("events", "") ?: ""
}
