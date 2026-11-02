package com.yash.home

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.app.NotificationCompat
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** The house says "update": fetch the built APK from the house, check its sha256 and hand it to the
 *  system installer. Android refuses any APK not signed with this app's key. From Android 12 an app
 *  that installed the previous version updates without a tap; otherwise a notification asks for it.
 *  "Install unknown apps" must be allowed for this app once. */
object AppUpdate {
    const val ACTION = "com.yash.home.UPDATE_RESULT"
    private const val CH = "app_update"
    private const val NOTIF = 44
    private const val PATH = "/plugins/family/yash.apk"
    private const val MAX_BYTES = 150L * 1024 * 1024
    private val running = AtomicBoolean(false)
    private val http = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS).build()

    @Suppress("DEPRECATION")
    fun installedCode(ctx: Context): Long {
        val p = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
        return if (Build.VERSION.SDK_INT >= 28) p.longVersionCode else p.versionCode.toLong()
    }

    fun start(ctx: Context, cmd: JSONObject) {
        if (cmd.optLong("code") <= installedCode(ctx) || cmd.optString("path") != PATH) return
        if (!running.compareAndSet(false, true)) return
        Thread {
            try { run(ctx, cmd) } catch (e: Exception) { Prefs.logEvent(ctx, "update: ${e.javaClass.simpleName}: ${e.message}") }
            finally { running.set(false) }
        }.start()
    }

    private fun run(ctx: Context, cmd: JSONObject) {
        if (!ctx.packageManager.canRequestPackageInstalls()) {
            val i = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + ctx.packageName))
            notify(ctx, R.string.update_allow_title, R.string.update_allow_text, i)
            Prefs.logEvent(ctx, "update: installs from this app are not allowed")
            return
        }
        val size = cmd.optLong("size")
        if (size <= 0 || size > MAX_BYTES) { Prefs.logEvent(ctx, "update: bad size"); return }
        val file = File(ctx.cacheDir, "update.apk")
        val sha = MessageDigest.getInstance("SHA-256")
        val req = Request.Builder().url(HomeApi.url(ctx) + PATH).header("Authorization", "Bearer " + HomeApi.token(ctx)).build()
        http.newCall(req).execute().use { r ->
            if (!r.isSuccessful) { Prefs.logEvent(ctx, "update: HTTP ${r.code}"); return }
            r.body!!.byteStream().use { inp ->
                file.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    var total = 0L
                    while (true) {
                        val n = inp.read(buf)
                        if (n < 0) break
                        total += n
                        if (total > size) { file.delete(); Prefs.logEvent(ctx, "update: more than announced"); return }
                        sha.update(buf, 0, n); out.write(buf, 0, n)
                    }
                }
            }
        }
        val got = sha.digest().joinToString("") { "%02x".format(it) }
        if (got != cmd.optString("sha256").lowercase() || file.length() != size) {
            file.delete(); Prefs.logEvent(ctx, "update: checksum differs"); return
        }
        install(ctx, file)
        file.delete()
    }

    private fun install(ctx: Context, file: File) {
        val pi = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        params.setAppPackageName(ctx.packageName)
        if (Build.VERSION.SDK_INT >= 31) params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        val id = pi.createSession(params)
        pi.openSession(id).use { s ->
            s.openWrite("yash.apk", 0, file.length()).use { out ->
                file.inputStream().use { it.copyTo(out) }
                s.fsync(out)
            }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            val result = Intent(ctx, UpdateReceiver::class.java).setAction(ACTION).setPackage(ctx.packageName)
            s.commit(PendingIntent.getBroadcast(ctx, id, result, flags).intentSender)
        }
        Prefs.logEvent(ctx, "update: handed to the installer")
    }

    /** The system wants a tap (the confirm screen): a notification that opens it. */
    fun askUser(ctx: Context, confirm: Intent) {
        confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        notify(ctx, R.string.update_title, R.string.update_text, confirm)
    }

    private fun notify(ctx: Context, title: Int, text: Int, target: Intent) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH, ctx.getString(R.string.ch_update), NotificationManager.IMPORTANCE_DEFAULT))
        target.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val tap = PendingIntent.getActivity(ctx, NOTIF, target, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        nm.notify(NOTIF, NotificationCompat.Builder(ctx, CH).setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(ctx.getString(title)).setContentText(ctx.getString(text)).setContentIntent(tap)
            .setAutoCancel(true).build())
    }
}
