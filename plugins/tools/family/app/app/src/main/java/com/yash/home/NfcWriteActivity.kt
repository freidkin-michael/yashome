package com.yash.home

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.nfc.NfcAdapter
import android.nfc.Tag
import android.nfc.TagLostException
import android.nfc.tech.IsoDep
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import java.io.IOException

/** Writing a door card: the phone is only the antenna. The house holds the keys and makes every command; the phone sends the card's
 *  answer back (POST /api/nfc/write/{sid}/step) and gets the next command, until the house says done. A card moved away ends the
 *  session: Try again starts a new one with the same name. */
class NfcWriteActivity : AppCompatActivity(), NfcAdapter.ReaderCallback {
    companion object {
        const val EXTRA_SID = "nfcw_sid"
        const val EXTRA_NAME = "nfcw_name"
        private const val CH = "nfc_write"
        private const val NOTIF = 45
        private val SID_RE = Regex("[A-Za-z0-9_-]{8,64}")

        fun open(ctx: Context, sid: String, name: String) {
            if (!SID_RE.matches(sid)) return
            ctx.startActivity(Intent(ctx, NfcWriteActivity::class.java).putExtra(EXTRA_SID, sid).putExtra(EXTRA_NAME, name)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }

        /** {cmd: nfc_write, sid}: the house waits for a card; a tap on the notification opens the "hold the card" screen. */
        fun notify(ctx: Context, j: JSONObject) {
            val sid = j.optString("sid")
            if (!SID_RE.matches(sid)) return
            if (Build.VERSION.SDK_INT >= 33 &&
                ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                Prefs.logEvent(ctx, "nfc write: notifications not allowed")
                return
            }
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CH, ctx.getString(R.string.ch_nfc_write), NotificationManager.IMPORTANCE_HIGH).apply {
                lockscreenVisibility = Notification.VISIBILITY_PRIVATE
            })
            val open = Intent(ctx, NfcWriteActivity::class.java).putExtra(EXTRA_SID, sid).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            val tap = PendingIntent.getActivity(ctx, NOTIF, open, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            nm.notify(NOTIF, NotificationCompat.Builder(ctx, CH).setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle(ctx.getString(R.string.nfcw_title)).setContentText(ctx.getString(R.string.nfcw_hold))
                .setContentIntent(tap).setAutoCancel(true).setTimeoutAfter(120_000L)
                .setPriority(NotificationCompat.PRIORITY_HIGH).setCategory(NotificationCompat.CATEGORY_MESSAGE).build())
            Prefs.logEvent(ctx, "nfc write: the house waits for a card")
        }

        fun cancelNotice(ctx: Context) = ctx.getSystemService(NotificationManager::class.java).cancel(NOTIF)

        private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }
        private fun unhex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
    }

    private var nfc: NfcAdapter? = null
    @Volatile private var sid = ""
    @Volatile private var name = ""
    @Volatile private var busy = false
    @Volatile private var finished = false
    private lateinit var head: TextView
    private lateinit var step: TextView
    private lateinit var retry: Button

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.nfcw_title)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(24), dp(20), dp(20)) }
        head = TextView(this).apply { textSize = 22f; gravity = Gravity.CENTER }
        step = TextView(this).apply { textSize = 15f; gravity = Gravity.CENTER; setPadding(0, dp(12), 0, dp(12)) }
        retry = Button(this).apply { text = getString(R.string.nfcw_retry); visibility = View.GONE; setOnClickListener { restart() } }
        root.addView(head, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        root.addView(step, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        root.addView(retry, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        root.addView(Button(this).apply { text = getString(R.string.nfcw_close); setOnClickListener { close() } },
            LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(8) })
        setContentView(root)
        nfc = NfcAdapter.getDefaultAdapter(this)
        take(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        take(intent)
        enableReader()
    }

    /** A session from the notification or the menu: its id, and its name (asked of the house when only the id came). */
    private fun take(i: Intent) {
        sid = i.getStringExtra(EXTRA_SID).orEmpty()
        name = i.getStringExtra(EXTRA_NAME).orEmpty()
        finished = false
        cancelNotice(this)
        if (sid.isEmpty()) { finish(); return }
        show(getString(R.string.nfcw_hold), "", false)
        when {
            nfc == null -> show(getString(R.string.nfcw_no_nfc), "", false)
            nfc?.isEnabled != true -> show(getString(R.string.nfcw_nfc_off), "", false)
        }
        if (name.isEmpty()) {
            val app = applicationContext
            val s = sid
            Thread { try { val n = HomeApi.nfcWriteState(app, s).optString("name"); if (sid == s) name = n } catch (_: Exception) { } }.start()
        }
    }

    override fun onResume() {
        super.onResume()
        enableReader()
    }

    override fun onPause() {
        nfc?.disableReaderMode(this)
        super.onPause()
    }

    private fun enableReader() {
        if (finished || nfc?.isEnabled != true) return
        nfc?.enableReaderMode(this, this,
            NfcAdapter.FLAG_READER_NFC_A or NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK or NfcAdapter.FLAG_READER_NO_PLATFORM_SOUNDS, null)
    }

    private fun show(title: String, sub: String, canRetry: Boolean) = runOnUiThread {
        head.text = title
        step.text = sub
        retry.visibility = if (canRetry) View.VISIBLE else View.GONE
    }

    /** A card is near (a reader thread, not the main one): the whole exchange runs here, one command at a time. */
    override fun onTagDiscovered(tag: Tag) {
        if (busy || finished) return
        busy = true
        val app = applicationContext
        val s = sid
        val iso = IsoDep.get(tag)
        if (iso == null) { show(getString(R.string.nfcw_not_ours), "", false); busy = false; return }
        try {
            iso.connect()
            iso.timeout = 5000
            show(getString(R.string.nfcw_writing), "", false)
            var resp: String? = null
            while (true) {
                val r = HomeApi.nfcWriteStep(app, s, resp)
                if (r.optBoolean("done")) { result(r.optJSONObject("result") ?: JSONObject()); break }
                show(getString(R.string.nfcw_writing), getString(R.string.nfcw_step, r.optString("step")), false)
                resp = hex(iso.transceive(unhex(r.optString("apdu_hex"))))
            }
        } catch (e: TagLostException) {
            show(getString(R.string.nfcw_lost), "", true)
        } catch (e: HomeApi.ApiError) {
            show(HomeApi.detail(e) ?: getString(R.string.nfcw_failed, "HTTP ${e.code}"), "", true)
        } catch (e: IOException) {
            show(getString(if (iso.isConnected) R.string.nfcw_offline else R.string.nfcw_lost), "", true)
        } catch (e: Exception) {
            show(getString(R.string.nfcw_failed, e.javaClass.simpleName), "", true)
        } finally {
            try { iso.close() } catch (_: Exception) { }
            busy = false
        }
    }

    private fun result(r: JSONObject) {
        finished = true
        runOnUiThread { nfc?.disableReaderMode(this) }
        if (r.optBoolean("ok")) show(getString(R.string.nfcw_done, r.optString("listed").ifEmpty { name }), r.optString("note"), false)
        else show(getString(R.string.nfcw_failed, r.optString("error").ifEmpty { r.optString("step") }), "", true)
    }

    /** A new session with the same name: the old one is cancelled first (a card moved away mid-way needs a fresh start). */
    private fun restart() {
        val app = applicationContext
        val old = sid
        val n = name
        if (n.isEmpty()) { show(getString(R.string.nfcw_failed, getString(R.string.nfcw_offline)), "", true); return }
        retry.visibility = View.GONE
        Thread {
            try { HomeApi.nfcWriteCancel(app, old) } catch (_: Exception) { }
            try {
                val r = HomeApi.nfcWriteStart(app, n)
                sid = r.optString("sid")
                finished = false
                cancelNotice(app)
                show(getString(R.string.nfcw_hold), "", false)
                runOnUiThread { enableReader() }
            } catch (e: Exception) {
                show(HomeApi.detail(e) ?: getString(R.string.nfcw_offline), "", true)
            }
        }.start()
    }

    /** Close: a session still waiting is cancelled, so the house does not wait 60 s for a card. */
    private fun close() {
        if (!finished && sid.isNotEmpty()) {
            val app = applicationContext
            val s = sid
            Thread { try { HomeApi.nfcWriteCancel(app, s) } catch (_: Exception) { } }.start()
        }
        finish()
    }
}
