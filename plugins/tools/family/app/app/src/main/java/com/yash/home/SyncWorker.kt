package com.yash.home

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.ForegroundInfo
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.OutOfQuotaPolicy
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.google.firebase.database.FirebaseDatabase
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withTimeout
import org.json.JSONObject

/** One-shot read of /state after a "sync" push; the result is cached for the dashboard. */
class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    companion object {
        fun enqueue(ctx: Context) {
            val req = OneTimeWorkRequestBuilder<SyncWorker>()
                .setExpedited(OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST).build()
            WorkManager.getInstance(ctx).enqueueUniqueWork("state-sync", ExistingWorkPolicy.REPLACE, req)
        }
    }

    override suspend fun doWork(): Result {
        if (Cloud.uid() == null) return Result.success()
        return try {
            val snap = withTimeout(20_000) { FirebaseDatabase.getInstance().getReference("state").get().await() }
            StateCache.save(applicationContext, snap.value)
            Prefs.logEvent(applicationContext, "sync ok, ${snap.childrenCount} item(s)")
            Result.success()
        } catch (e: Exception) {
            Prefs.logEvent(applicationContext, "sync failed: ${e.message}")
            if (runAttemptCount < 3) Result.retry() else Result.failure()
        }
    }

    // Android < 12 runs expedited work as a foreground service and needs this notification
    override suspend fun getForegroundInfo(): ForegroundInfo {
        val nm = applicationContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(NotificationChannel("sync", "Sync", NotificationManager.IMPORTANCE_MIN))
        val n = NotificationCompat.Builder(applicationContext, "sync").setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(applicationContext.getString(R.string.syncing)).build()
        return ForegroundInfo(43, n)
    }
}

object StateCache {
    private fun p(ctx: Context) = ctx.getSharedPreferences("yash", Context.MODE_PRIVATE)

    fun save(ctx: Context, value: Any?) {
        val json = (value as? Map<*, *>)?.let { JSONObject(it).toString() } ?: "{}"
        p(ctx).edit().putString("state", json).putLong("state_at", System.currentTimeMillis()).apply()
    }

    fun load(ctx: Context): JSONObject = try { JSONObject(p(ctx).getString("state", "{}")!!) } catch (_: Exception) { JSONObject() }
    fun savedAt(ctx: Context): Long = p(ctx).getLong("state_at", 0L)
}
