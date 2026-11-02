package com.yash.home

import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.os.Bundle
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import okhttp3.FormBody
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** A door card tapped on the phone: the link the card carries is posted back to the house (the link
 *  proves itself: a fresh signature on every tap) and the house's answer is shown as a notification.
 *  Only links to the house this app is paired with are posted. */
class DoorTapActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val link = intent?.data?.toString()
        finish()
        if (link.isNullOrEmpty()) return
        val app = applicationContext
        thread {
            val (head, note) = tap(app, link)
            notify(app, head, note)
        }
    }

    private fun tap(ctx: Context, link: String): Pair<String, String> {
        return try {
            val url = link.toHttpUrl()
            val house = HomeApi.url(ctx).toHttpUrl()
            if (!HomeApi.configured(ctx) || url.host != house.host) return Pair(ctx.getString(R.string.door_foreign), url.host)
            val form = FormBody.Builder()
            for (i in 0 until url.querySize) form.add(url.queryParameterName(i), url.queryParameterValue(i) ?: "")
            form.add("fmt", "json")
            val req = Request.Builder().url(url.newBuilder().query(null).build())
                .header("Accept", "application/json").post(form.build()).build()
            client.newCall(req).execute().use { resp ->
                val o = JSONObject(resp.body?.string() ?: "{}")
                Pair(o.optString("head", ctx.getString(R.string.door_title)), o.optString("note", ""))
            }
        } catch (e: Exception) {
            Pair(ctx.getString(R.string.door_not_delivered), e.message ?: ctx.getString(R.string.lock_offline))
        }
    }

    private fun notify(ctx: Context, head: String, note: String) {
        val mgr = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            mgr.createNotificationChannel(NotificationChannel(CHANNEL, ctx.getString(R.string.door_title), NotificationManager.IMPORTANCE_HIGH))
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_lock_idle_lock)
            .setContentTitle(head).setContentText(note)
            .setPriority(NotificationCompat.PRIORITY_HIGH).setAutoCancel(true).setTimeoutAfter(120_000L).build()
        try { NotificationManagerCompat.from(ctx).notify(NOTIFY_ID, n) }
        catch (e: SecurityException) { android.util.Log.w("DoorTap", "notifications not granted: " + e.message) }
    }

    private companion object {
        const val CHANNEL = "door"
        const val NOTIFY_ID = 4242
        // the house confirms a lock after up to 20 s: wait for it
        val client: OkHttpClient = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()
    }
}
