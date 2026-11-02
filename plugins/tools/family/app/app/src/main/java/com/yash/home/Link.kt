package com.yash.home

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** The app's link to the house, built to cost almost nothing while idle: no service, no heartbeat. The house wakes the phone with a
 *  data-less Firebase message (a tickle) and the phone asks for what it needs over HTTPS; the phone tells the house about its network
 *  when the network changes; widgets also refresh every 15 minutes (the shortest period Android allows) and when tapped. */
object Link {
    private const val ACTION = "com.yash.home.NET_CHANGED"

    /** One pull: the changed widget channels are applied, the house's commands run, and the answer applied is remembered (sent as `ack` with the
     *  next pull: the house sends only what changed after it, and again what was lost on the way); a new address for the phones is adopted. */
    fun pullApply(ctx: Context, all: Boolean) {
        val r = HomeApi.pull(ctx, all)
        r.optJSONObject("state")?.takeIf { it.length() > 0 }?.let { WidgetState.apply(ctx, it, r.optLong("t", 0L)) }
        val cmds = r.optJSONArray("cmds")
        for (i in 0 until (cmds?.length() ?: 0)) cmds!!.getJSONObject(i).takeIf { it.optString("cmd") == "update" }?.let { AppUpdate.start(ctx, it) }
        ctx.getSharedPreferences("yash", Context.MODE_PRIVATE).edit().putLong("pull_ack", r.optLong("t", 0L)).apply()
        HomeApi.adopt(ctx, r.optString("srv"))                       // the house got another address for the phones: move to it
    }
    private val NET = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()

    /** App start, boot, update: listen for Wi-Fi changes (the system delivers them to NetReceiver even when the app is not running),
     *  schedule the periodic widget refresh and make sure the house knows where the phone is. */
    fun start(ctx: Context) {
        val app = ctx.applicationContext
        if (!HomeApi.configured(app)) return
        if (!LiveService.enabled(app)) try {                       // a phone with Firebase has no link service: its notification channels go
            val nm = app.getSystemService(NotificationManager::class.java)
            nm.deleteNotificationChannel("house_link"); nm.deleteNotificationChannel("house_link_min")
        } catch (_: Exception) { }
        try {
            val pi = PendingIntent.getBroadcast(app, 21, Intent(app, NetReceiver::class.java).setAction(ACTION),
                PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0))
            app.getSystemService(ConnectivityManager::class.java).registerNetworkCallback(
                NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build(), pi)
        } catch (e: Exception) { Prefs.logEvent(app, "link: network listener failed (${e.javaClass.simpleName})") }
        WorkManager.getInstance(app).enqueueUniquePeriodicWork("link-periodic", ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<PullWorker>(15, TimeUnit.MINUTES).setConstraints(NET)
                .setInputData(Data.Builder().putBoolean("all", true).build()).build())
        ChargeWorker.enqueue(app, 0)                                // waits for a charger; cheap, so it is simply always queued
        val sp = app.getSharedPreferences("yash", Context.MODE_PRIVATE)
        if (System.currentTimeMillis() - sp.getLong("report_at", 0L) > 3_600_000L) ReportWorker.enqueue(app)
        LiveService.start(app)                                    // only a house without Firebase: the rings come over the open link
    }
}

/** A Wi-Fi network appeared or went (the system calls this with the app not running): one report to the house. */
class NetReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) = ReportWorker.enqueue(ctx)
}

abstract class LinkWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    // Android < 12 runs expedited work as a foreground service and needs this notification
    override suspend fun getForegroundInfo(): ForegroundInfo {
        val nm = applicationContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(NotificationChannel("sync", "Sync", NotificationManager.IMPORTANCE_MIN))
        val n = NotificationCompat.Builder(applicationContext, "sync").setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(applicationContext.getString(R.string.syncing)).build()
        return ForegroundInfo(44, n)
    }
}

/** The network report: gateway, own address, Wi-Fi name, version. The latest request replaces a waiting one. */
class ReportWorker(ctx: Context, params: WorkerParameters) : LinkWorker(ctx, params) {
    companion object {
        fun enqueue(ctx: Context) {
            val req = OneTimeWorkRequestBuilder<ReportWorker>().setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST).build()
            WorkManager.getInstance(ctx.applicationContext).enqueueUniqueWork("link-report", ExistingWorkPolicy.REPLACE, req)
        }
    }

    override suspend fun doWork(): Result {
        if (!HomeApi.configured(applicationContext)) return Result.success()
        delay(1500)                                              // a network that just came up fills its address a moment later
        return try {
            HomeApi.report(applicationContext, NetInfo.report(applicationContext))
            applicationContext.getSharedPreferences("yash", Context.MODE_PRIVATE).edit().putLong("report_at", System.currentTimeMillis()).apply()
            Result.success()
        } catch (e: Exception) { if (runAttemptCount < 4) Result.retry() else Result.failure() }
    }
}

/** What a tickle (or the 15-minute timer) asks for: the changed widget channels and the house's commands (an app update). `all` = the whole
 *  state, not only what changed since the last pull. */
class PullWorker(ctx: Context, params: WorkerParameters) : LinkWorker(ctx, params) {
    companion object {
        fun enqueue(ctx: Context, high: Boolean, all: Boolean = false) {
            val b = OneTimeWorkRequestBuilder<PullWorker>().setInputData(Data.Builder().putBoolean("all", all).build()).setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            if (high) b.setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST)
            WorkManager.getInstance(ctx.applicationContext).enqueueUniqueWork("link-pull", ExistingWorkPolicy.APPEND_OR_REPLACE, b.build())   // a running pull is not cancelled
        }
    }

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        if (!HomeApi.configured(ctx)) return Result.success()
        return try {
            Link.pullApply(ctx, inputData.getBoolean("all", false))
            if (Lock.stale(ctx)) Lock.refreshSurfaces(ctx)           // a widget that said "no connection" reads the house again
            Result.success()
        } catch (e: Exception) { if (runAttemptCount < 4) Result.retry() else Result.failure() }
    }
}

/** While the phone charges, battery is no concern: a pull every two minutes (the chain only runs with a charger connected; unplugged, the next
 *  link just waits). On battery the 15-minute timer, a tickle and opening the app are the only pulls. */
class ChargeWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    companion object {
        fun enqueue(ctx: Context, delaySec: Long = 120) {
            val req = OneTimeWorkRequestBuilder<ChargeWorker>().setInitialDelay(delaySec, TimeUnit.SECONDS)
                .setConstraints(Constraints.Builder().setRequiresCharging(true).setRequiredNetworkType(NetworkType.CONNECTED).build()).build()
            WorkManager.getInstance(ctx.applicationContext).enqueueUniqueWork("link-charging", ExistingWorkPolicy.KEEP, req)
        }
    }

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        if (HomeApi.configured(ctx)) {
            try {
                Link.pullApply(ctx, false)                            // the pull carries the network too
            } catch (_: Exception) { }                            // the next link in the chain tries again
        }
        WorkManager.getInstance(ctx).enqueueUniqueWork("link-charging", ExistingWorkPolicy.APPEND_OR_REPLACE,
            OneTimeWorkRequestBuilder<ChargeWorker>().setInitialDelay(120, TimeUnit.SECONDS)
                .setConstraints(Constraints.Builder().setRequiresCharging(true).setRequiredNetworkType(NetworkType.CONNECTED).build()).build())
        return Result.success()
    }
}

/** A widget switch that could not reach the house: sent again when the network is back, for two minutes at most. Never a lock, never a
 *  step ("up"/"down"): a late unlock or a double step would be a surprise. */
class ControlRetryWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    companion object {
        fun enqueue(ctx: Context, dev: String, code: String, value: Any) {
            val d = Data.Builder().putString("dev", dev).putString("code", code).putLong("deadline", System.currentTimeMillis() + 120_000L)
            when (value) {
                is Boolean -> d.putString("kind", "b").putBoolean("b", value)
                is Number -> d.putString("kind", "n").putDouble("n", value.toDouble())
                else -> return
            }
            val req = OneTimeWorkRequestBuilder<ControlRetryWorker>().setInputData(d.build())
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.LINEAR, 10, TimeUnit.SECONDS).build()
            WorkManager.getInstance(ctx.applicationContext).enqueueUniqueWork("retry-$dev-$code", ExistingWorkPolicy.REPLACE, req)
        }
    }

    override suspend fun doWork(): Result {
        val d = inputData
        if (System.currentTimeMillis() > d.getLong("deadline", 0L)) return Result.failure()
        val value: Any = if (d.getString("kind") == "b") d.getBoolean("b", false) else d.getDouble("n", 0.0).let { if (it % 1.0 == 0.0) it.toLong() else it }
        return try { HomeApi.control(applicationContext, d.getString("dev") ?: "", d.getString("code") ?: "", value); Result.success() }
        catch (e: Exception) { Result.retry() }
    }
}

/** Voice-recording samples waiting in RecQueue go up when the network allows. */
class RecUploadWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    companion object {
        fun enqueue(ctx: Context) {
            val req = OneTimeWorkRequestBuilder<RecUploadWorker>().setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build()
            WorkManager.getInstance(ctx.applicationContext).enqueueUniqueWork("rec-upload", ExistingWorkPolicy.KEEP, req)
        }
    }

    override suspend fun doWork(): Result {
        RecQueue.flush(applicationContext)
        return if (RecQueue.pending(applicationContext) > 0) Result.retry() else Result.success()
    }
}
