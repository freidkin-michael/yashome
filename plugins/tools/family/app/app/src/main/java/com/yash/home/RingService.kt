package com.yash.home

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.VibrationEffect
import android.os.Vibrator
import android.provider.Settings
import androidx.core.app.NotificationCompat

/** Alarm on STREAM_ALARM at max volume (sounds on silent and DND); auto-stops, restores volume. */
class RingService : Service() {
    companion object {
        private const val CH = "ring_alert"
        private const val NOTIF = 42
        private const val ACTION_RING = "com.yash.home.RING"
        private const val ACTION_STOP = "com.yash.home.STOP"
        private const val EXTRA_SECS = "secs"

        fun ring(ctx: Context, secs: Int) {
            val i = Intent(ctx, RingService::class.java).setAction(ACTION_RING)
                .putExtra(EXTRA_SECS, secs.coerceIn(5, 300))
            ctx.startForegroundService(i)
        }

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, RingService::class.java).setAction(ACTION_STOP))
        }
    }

    private var player: MediaPlayer? = null
    private var vibrator: Vibrator? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var prevVol = -1
    private val main = Handler(Looper.getMainLooper())
    private val autoStop = Runnable { finish() }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { finish(); return START_NOT_STICKY }
        createChannel()
        val n = alertNotif()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
            startForeground(NOTIF, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
        else startForeground(NOTIF, n)
        val secs = intent?.getIntExtra(EXTRA_SECS, 60) ?: 60
        startRinging()
        main.removeCallbacks(autoStop)
        main.postDelayed(autoStop, secs * 1000L)   // a repeated ring extends the timer
        return START_NOT_STICKY
    }

    override fun onDestroy() { release(); super.onDestroy() }

    private fun startRinging() {
        if (player != null) return
        wakeLock = (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "yash:ring").also { it.acquire(310_000L) }

        val am = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        prevVol = am.getStreamVolume(AudioManager.STREAM_ALARM)
        am.setStreamVolume(AudioManager.STREAM_ALARM, am.getStreamMaxVolume(AudioManager.STREAM_ALARM), 0)

        val uri = RingtoneManager.getActualDefaultRingtoneUri(this, RingtoneManager.TYPE_ALARM)
            ?: RingtoneManager.getActualDefaultRingtoneUri(this, RingtoneManager.TYPE_RINGTONE)
            ?: Settings.System.DEFAULT_ALARM_ALERT_URI
        try {
            player = MediaPlayer().apply {
                setAudioAttributes(AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                setDataSource(this@RingService, uri)
                isLooping = true
                prepare()
                start()
            }
        } catch (e: Exception) { Prefs.logEvent(this, "player error: ${e.message}") }

        vibrator = (getSystemService(Context.VIBRATOR_SERVICE) as Vibrator).also {
            it.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 700, 500), 0))
        }
        Prefs.logEvent(this, "ringing")
    }

    private fun finish() {
        main.removeCallbacks(autoStop)
        release()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun release() {
        val p = player; player = null
        val v = vibrator; vibrator = null
        val wl = wakeLock; wakeLock = null
        try { p?.stop(); p?.release() } catch (_: Exception) {}
        try { v?.cancel() } catch (_: Exception) {}
        if (prevVol >= 0) {
            try { (getSystemService(Context.AUDIO_SERVICE) as AudioManager)
                .setStreamVolume(AudioManager.STREAM_ALARM, prevVol, 0) } catch (_: Exception) {}
            prevVol = -1
        }
        try { wl?.let { if (it.isHeld) it.release() } } catch (_: Exception) {}
    }

    private fun createChannel() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(NotificationChannel(CH, getString(R.string.ch_ring),
            NotificationManager.IMPORTANCE_HIGH).apply {
                setSound(null, null)
                enableVibration(false)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            })
    }

    private fun alertNotif(): Notification {
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val stop = PendingIntent.getService(this, 1,
            Intent(this, RingService::class.java).setAction(ACTION_STOP), flags)
        val open = PendingIntent.getActivity(this, 2, Intent(this, DashboardActivity::class.java), flags)
        return NotificationCompat.Builder(this, CH)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(getString(R.string.ring_title))
            .setContentText(getString(R.string.ring_text))
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setOngoing(true)
            .setFullScreenIntent(open, true)
            .addAction(0, getString(R.string.stop), stop)
            .build()
    }
}
