package com.yash.home

import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.provider.Settings

/** TEMPORARY diagnostic (revert this commit when the culprit is found): something turns auto-rotate back on.
 *  Off by default; turned on from adb only (ShellReceiver needs the DUMP permission: shell, not apps).
 *  On each change of accelerometer_rotation: set it back to locked (when WRITE_SETTINGS is granted) and tell the house
 *  when it happened and which app was on the screen (when usage access is granted). */
object RotationGuard {
    private const val JOB_ID = 7301
    private fun p(ctx: Context) = ctx.getSharedPreferences("yash", Context.MODE_PRIVATE)
    fun on(ctx: Context) = p(ctx).getBoolean("rot_guard", false)

    fun set(ctx: Context, on: Boolean) {
        p(ctx).edit().putBoolean("rot_guard", on).apply()
        if (on) schedule(ctx) else ctx.getSystemService(JobScheduler::class.java).cancel(JOB_ID)
        Prefs.logEvent(ctx, "rotation guard " + if (on) "on" else "off")
    }

    /** A content-trigger job fires once: it is scheduled again after every run. */
    fun schedule(ctx: Context) {
        if (!on(ctx)) return
        val uri = Settings.System.getUriFor(Settings.System.ACCELEROMETER_ROTATION)
        val job = JobInfo.Builder(JOB_ID, ComponentName(ctx, Job::class.java))
            .addTriggerContentUri(JobInfo.TriggerContentUri(uri, 0))
            .setTriggerContentUpdateDelay(0).setTriggerContentMaxDelay(500).build()
        ctx.getSystemService(JobScheduler::class.java).schedule(job)
    }

    /** The apps that came to the screen in the last 15 s, newest last. */
    private fun recentApps(ctx: Context): String {
        val um = ctx.getSystemService(UsageStatsManager::class.java) ?: return "?"
        val now = System.currentTimeMillis()
        val ev = um.queryEvents(now - 15_000, now) ?: return "no usage access"
        val e = UsageEvents.Event()
        val seen = mutableListOf<String>()
        while (ev.hasNextEvent()) {
            ev.getNextEvent(e)
            if (e.eventType == UsageEvents.Event.ACTIVITY_RESUMED) {
                val s = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.US).format(java.util.Date(e.timeStamp)) + " " + e.packageName
                if (seen.lastOrNull()?.endsWith(e.packageName) != true) seen.add(s)
            }
        }
        return if (seen.isEmpty()) "none (or no usage access)" else seen.takeLast(4).joinToString(", ")
    }

    class Job : JobService() {
        override fun onStartJob(params: JobParameters): Boolean {
            val ctx = applicationContext
            Thread {
                try {
                    val v = Settings.System.getInt(ctx.contentResolver, Settings.System.ACCELEROMETER_ROTATION, 0)
                    if (v == 1) {
                        val fixed = Settings.System.canWrite(ctx) &&
                            Settings.System.putInt(ctx.contentResolver, Settings.System.ACCELEROMETER_ROTATION, 0)
                        val note = "auto-rotate turned ON; " + (if (fixed) "locked again" else "NOT locked (no WRITE_SETTINGS)") +
                            "; apps on screen: " + recentApps(ctx)
                        Prefs.logEvent(ctx, note.take(120))
                        try { HomeApi.note(ctx, note) } catch (e: Exception) { Prefs.logEvent(ctx, "note not sent: ${e.javaClass.simpleName}") }
                    }
                } finally {
                    schedule(ctx)
                    jobFinished(params, false)
                }
            }.start()
            return true
        }

        override fun onStopJob(params: JobParameters): Boolean { schedule(applicationContext); return false }
    }
}

/** adb shell am broadcast -n com.yash.home/.ShellReceiver --ez rot_guard true|false */
class ShellReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.hasExtra("rot_guard")) RotationGuard.set(ctx, intent.getBooleanExtra("rot_guard", false))
    }
}
