package com.yash.home

import android.content.Context
import android.content.Intent
import android.nfc.NfcAdapter
import android.nfc.Tag
import android.nfc.TagLostException
import android.nfc.tech.IsoDep
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.io.IOException

/** Writing a door card: the phone is only the antenna. The house holds the keys and makes every command; the phone sends the card's
 *  answer back (POST /api/nfc/write/{sid}/step) and gets the next command, until the house says done. No name is asked first: a new
 *  card is listed under an automatic name ("Card 1") and this screen offers "Name this card" (Save renames it, Close keeps it).
 *  A listed card keeps its name and rules: the house asks first ({known}), Rewrite restarts the session for it (held again), Cancel
 *  leaves it as it is. A card moved away ends the session: Try again starts a new one. */
class NfcWriteActivity : AppCompatActivity(), NfcAdapter.ReaderCallback {
    companion object {
        fun open(ctx: Context) {
            ctx.startActivity(Intent(ctx, NfcWriteActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }

        private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }
        private fun unhex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
    }

    private var nfc: NfcAdapter? = null
    @Volatile private var sid = ""
    @Volatile private var busy = false
    @Volatile private var finished = true                   // no session yet: a card is ignored until start() has one
    @Volatile private var asking = false
    private var newUid = ""                                 // the card just listed under an automatic name ("Name this card")
    private lateinit var head: TextView
    private lateinit var step: TextView
    private lateinit var nameField: EditText
    private lateinit var save: Button
    private lateinit var retry: Button
    private lateinit var rewrite: Button
    private lateinit var cancel: Button

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.nfcw_title)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(24), dp(20), dp(20)) }
        head = TextView(this).apply { textSize = 22f; gravity = Gravity.CENTER }
        step = TextView(this).apply { textSize = 15f; gravity = Gravity.CENTER; setPadding(0, dp(12), 0, dp(12)) }
        nameField = EditText(this).apply { setSingleLine(); visibility = View.GONE }
        save = Button(this).apply { text = getString(R.string.nfcw_save); visibility = View.GONE; setOnClickListener { saveName() } }
        retry = Button(this).apply { text = getString(R.string.nfcw_retry); visibility = View.GONE; setOnClickListener { start() } }
        rewrite = Button(this).apply { text = getString(R.string.nfcw_rewrite); visibility = View.GONE; setOnClickListener { rewriteIt() } }
        cancel = Button(this).apply { text = getString(R.string.nfcw_cancel); visibility = View.GONE; setOnClickListener { cancelIt() } }
        for (v in listOf(head, step, nameField, save, retry, rewrite, cancel)) root.addView(v, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        root.addView(Button(this).apply { text = getString(R.string.nfcw_close); setOnClickListener { close() } },
            LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(8) })
        setContentView(root)
        nfc = NfcAdapter.getDefaultAdapter(this)
        when {
            nfc == null -> show(getString(R.string.nfcw_no_nfc), "", false)
            nfc?.isEnabled != true -> show(getString(R.string.nfcw_nfc_off), "", false)
            else -> start()
        }
    }

    override fun onResume() {
        super.onResume()
        enableReader()
    }

    override fun onDestroy() {
        asking = false
        super.onDestroy()
    }

    override fun onPause() {
        nfc?.disableReaderMode(this)
        super.onPause()
    }

    /** Reader mode stays on from onResume to onPause, also after a result, during the Rewrite question and while naming: a card left
     *  at the phone must never fall through to the normal tap path, which would act on its lock. onTagDiscovered ignores it then. */
    private fun enableReader() {
        if (nfc?.isEnabled != true) return
        nfc?.enableReaderMode(this, this,
            NfcAdapter.FLAG_READER_NFC_A or NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK or NfcAdapter.FLAG_READER_NO_PLATFORM_SOUNDS, null)
    }

    private fun show(title: String, sub: String, canRetry: Boolean, ask: Boolean = false, naming: String? = null) = runOnUiThread {
        head.text = title
        step.text = sub
        retry.visibility = if (canRetry) View.VISIBLE else View.GONE
        rewrite.visibility = if (ask) View.VISIBLE else View.GONE
        cancel.visibility = if (ask) View.VISIBLE else View.GONE
        nameField.visibility = if (naming != null) View.VISIBLE else View.GONE
        save.visibility = nameField.visibility
        if (naming != null) { nameField.setText(naming); nameField.setSelection(naming.length) }
    }

    /** A new session (the first, or Try again): the old one, if any, is cancelled first. */
    private fun start() {
        val app = applicationContext
        val old = sid
        finished = true
        asking = false
        newUid = ""
        show(getString(R.string.nfcw_hold), "", false)
        Thread {
            if (old.isNotEmpty()) try { HomeApi.nfcWriteCancel(app, old) } catch (_: Exception) { }
            try {
                sid = HomeApi.nfcWriteStart(app).optString("sid")
                if (isFinishing || isDestroyed) { HomeApi.nfcWriteCancel(app, sid); return@Thread }   // closed meanwhile
                finished = false
                runOnUiThread { enableReader() }
            } catch (e: Exception) {
                show(HomeApi.detail(e) ?: getString(R.string.nfcw_offline), "", true)
            }
        }.start()
    }

    /** A card is near (a reader thread, not the main one): the whole exchange runs here, one command at a time. */
    override fun onTagDiscovered(tag: Tag) {
        if (busy || finished || sid.isEmpty()) return
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

    /** The house's answer: a listed card asks first; a new one is named here; the name shown is always the house's. */
    private fun result(r: JSONObject) {
        finished = true                                    // reader mode stays on: later taps are ignored, not dispatched
        val listed = r.optString("listed")
        if (r.optString("known").isNotEmpty()) ask(r.optString("known"))
        else if (r.optString("error") == "cancelled") show(getString(R.string.nfcw_cancelled), "", false)
        else if (r.optBoolean("ok") && r.optBoolean("new") && listed.isNotEmpty()) {
            newUid = r.optString("uid")
            show(getString(R.string.nfcw_done, listed), getString(R.string.nfcw_name_this), false, naming = listed)
        }
        else if (r.optBoolean("ok")) show(getString(R.string.nfcw_done, listed.ifEmpty { r.optString("uid") }), r.optString("note"), false)
        else show(getString(R.string.nfcw_failed, r.optString("error").ifEmpty { r.optString("step") }), "", true)
    }

    /** "Name this card": the tags list's rename; the automatic name stays when Close is pressed instead. */
    private fun saveName() {
        val app = applicationContext
        val uid = newUid
        val n = nameField.text.toString().trim()
        if (uid.isEmpty() || n.isEmpty()) return
        save.isEnabled = false
        Thread {
            try {
                HomeApi.nfcRename(app, uid, n)
                newUid = ""
                show(getString(R.string.nfcw_done, n), "", false)
            } catch (e: Exception) {
                runOnUiThread { step.text = HomeApi.detail(e) ?: getString(R.string.nfcw_offline) }
            } finally {
                runOnUiThread { save.isEnabled = true }
            }
        }.start()
    }

    /** The house asks before rewriting a listed card: Rewrite or Cancel on this screen. */
    private fun ask(known: String) {
        asking = true
        show(getString(R.string.nfcw_known, known), "", false, ask = true)
    }

    /** The session runs again (Rewrite): the same card is held to the phone once more. */
    private fun holdAgain() {
        asking = false
        finished = false
        show(getString(R.string.nfcw_hold_again), "", false)
        runOnUiThread { enableReader() }
    }

    private fun rewriteIt() {
        val app = applicationContext
        val s = sid
        show(head.text.toString(), "", false)
        Thread {
            try { HomeApi.nfcWriteRewrite(app, s); holdAgain() }
            catch (e: Exception) { asking = false; show(HomeApi.detail(e) ?: getString(R.string.nfcw_offline), "", true) }
        }.start()
    }

    private fun cancelIt() {
        asking = false
        val app = applicationContext
        val s = sid
        Thread { try { HomeApi.nfcWriteCancel(app, s) } catch (_: Exception) { } }.start()
        finish()
    }

    /** Close: a session still waiting (or asking about a listed card) is cancelled, so the house does not wait for a card. */
    private fun close() {
        if ((!finished || asking) && sid.isNotEmpty()) {
            asking = false
            val app = applicationContext
            val s = sid
            Thread { try { HomeApi.nfcWriteCancel(app, s) } catch (_: Exception) { } }.start()
        }
        finish()
    }
}
