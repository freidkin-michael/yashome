package com.yash.home

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Keeps /api/family/live open while the phone reaches the house (its network or a
 *  VPN); a ring or a stop comes down it, the ack goes up; every minute it also says which gateway its
 *  Wi-Fi has and its own version ({"gw": ..., "v": ...}), so the house knows the phone is on its Wi-Fi
 *  even through a VPN; an "update" command fetches the newer APK (AppUpdate), a "guest" one shows an entry request (GuestNotice).
 *  Reconnects with a growing pause, at once when a network comes up. Runs only for a house WITHOUT a Firebase sign-in (see enabled): with Firebase the push and the pull do it. */
class LiveService : Service() {
    companion object {
        private const val CH = "house_link_min"            // an existing channel keeps its importance: the quiet one has a new id
        private const val OLD_CH = "house_link"
        private const val NOTIF = 43
        private val REFUSED = setOf("HTTP 401", "HTTP 403")
        private var quietRefusal = false

        // a house without Firebase: the open link to /api/family/live carries the rings; a phone signed in to Firebase gets them by push, no service
        fun enabled(ctx: Context) = HomeApi.configured(ctx) && Cloud.uid() == null

        fun start(ctx: Context) {
            if (!enabled(ctx)) { ctx.stopService(Intent(ctx, LiveService::class.java)); return }
            // A process woken in the background (a widget update) may not start it: the next open does
            try { ctx.startForegroundService(Intent(ctx, LiveService::class.java)) }
            catch (e: IllegalStateException) {
                if (!quietRefusal) Prefs.logEvent(ctx, "live: not started now (${e.javaClass.simpleName})")
                quietRefusal = true                       // once: widget updates would push out every other event
            }
        }
    }

    private val http = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS).pingInterval(25, TimeUnit.SECONDS).build()
    private val main = Handler(Looper.getMainLooper())
    private var ws: WebSocket? = null
    private var pauseMs = 5_000L
    private var refusals = 0
    private var stopped = false
    private var netCb: ConnectivityManager.NetworkCallback? = null
    private val report = object : Runnable {
        override fun run() {
            if (Lock.stale(this@LiveService)) Lock.refreshSurfaces(this@LiveService)
            val w = ws
            if (w != null) Thread { w.send(NetInfo.report(this@LiveService).toString()) }.start()     // the name read may wait: not on the main thread
            main.postDelayed(this, 60_000L)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(NotificationManager::class.java)
        nm.deleteNotificationChannel(OLD_CH)
        nm.createNotificationChannel(NotificationChannel(CH, getString(R.string.ch_live), NotificationManager.IMPORTANCE_MIN).apply {
            setShowBadge(false); enableLights(false); enableVibration(false); setSound(null, null)
        })
        val open = PendingIntent.getActivity(this, 0, Intent(this, SetupActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n: Notification = NotificationCompat.Builder(this, CH).setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(getString(R.string.live_title)).setContentText(getString(R.string.live_text))
            .setContentIntent(open).setOngoing(false).setSilent(true).setShowWhen(false)       // Android 14+: the user may swipe it away, the service goes on
            .setPriority(NotificationCompat.PRIORITY_MIN).setCategory(NotificationCompat.CATEGORY_SERVICE).build()
        try {
            if (Build.VERSION.SDK_INT >= 34) startForeground(NOTIF, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING)
            else startForeground(NOTIF, n)
        } catch (e: IllegalStateException) {          // started in the background (a sticky restart on 12+)
            Prefs.logEvent(this, "live: not allowed now (${e.javaClass.simpleName})")
            stopSelf(); return START_NOT_STICKY
        }
        if (!enabled(this) || !HomeApi.configured(this)) { stopSelf(); return START_NOT_STICKY }
        if (netCb == null) {
            netCb = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) { main.post { pauseMs = 5_000L; if (ws == null) connect() } }
            }.also { getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(it) }
        }
        if (ws == null) connect()
        return START_STICKY
    }

    private fun connect() {
        if (stopped) return
        val url = HomeApi.url(this).replaceFirst("http", "ws") + "/api/family/live"
        val req = try { Request.Builder().url(url).header("Authorization", "Bearer " + HomeApi.token(this)).build() }
                  catch (e: IllegalArgumentException) { Prefs.logEvent(this, "live: bad address"); return }
        ws = http.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                main.post { pauseMs = 5_000L; main.removeCallbacks(report); report.run() }
                Prefs.logEvent(this@LiveService, "live: connected")
                WidgetState.publish(this@LiveService)
                Lock.refreshSurfaces(this@LiveService)           // the house may have restarted: widgets that said "no connection" read it again
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                val j = try { JSONObject(text) } catch (_: Exception) { return }
                when (j.optString("cmd")) {
                    "ring" -> {
                        // the ack means "received and the alarm started", not "someone heard it"
                        val err = try { RingService.ring(this@LiveService, j.optInt("secs", 60)); null }
                                  catch (e: Exception) { e.javaClass.simpleName }
                        webSocket.send(if (err == null) "{\"ack\":1}" else JSONObject().put("ack", 0).put("err", err).toString())
                        Prefs.logEvent(this@LiveService, if (err == null) "live: ring" else "live: ring failed: $err")
                    }
                    "stop" -> RingService.stop(this@LiveService)
                    "update" -> AppUpdate.start(this@LiveService, j)
                    "guest" -> GuestNotice.show(this@LiveService, j)
                    "state" -> j.optJSONObject("values")?.let { WidgetState.apply(this@LiveService, it, j.optLong("t", 0L)) }
                }
            }
            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(1000, null) }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = down(webSocket, "closed $code")
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
                down(webSocket, response?.code?.let { "HTTP $it" } ?: (t.message ?: "no connection"))
        })
    }

    private fun down(socket: WebSocket, why: String) {
        main.post {
            if (ws !== socket) return@post
            ws = null
            main.removeCallbacks(report)
            Prefs.logEvent(this, "live: $why")
            refusals = if (why in REFUSED) refusals + 1 else 0
            if (why == "closed 4401" || refusals >= 3) { stopSelf(); return@post }   // revoked: it runs again after the next start of the app, once paired
            main.postDelayed({ if (ws == null) connect() }, pauseMs)
            pauseMs = (pauseMs * 2).coerceAtMost(120_000L)
        }
    }

    override fun onDestroy() {
        stopped = true
        netCb?.let { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(it) }
        ws?.close(1000, null)
        main.removeCallbacksAndMessages(null)
        super.onDestroy()
    }
}
