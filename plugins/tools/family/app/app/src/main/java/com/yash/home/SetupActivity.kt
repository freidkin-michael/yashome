package com.yash.home

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.firebase.messaging.FirebaseMessaging
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions

/** Settings: one pairing QR (cloud + server for parents), test ring, battery, language; manual server under Advanced. */
class SetupActivity : AppCompatActivity() {
    private lateinit var statusView: TextView
    private lateinit var eventsView: TextView
    private lateinit var pairBtn: Button
    private lateinit var batteryBtn: Button
    private lateinit var urlEdit: EditText
    private lateinit var tokenEdit: EditText
    private lateinit var serverStatus: TextView

    // Sign-in may finish after the scanner rotated us into a new instance: refresh on the auth event
    private val authListener = com.google.firebase.auth.FirebaseAuth.AuthStateListener { runOnUiThread { refresh(); checkServer() } }

    override fun onStart() { super.onStart(); if (Cloud.enabled) com.google.firebase.auth.FirebaseAuth.getInstance().addAuthStateListener(authListener) }
    override fun onStop() { if (Cloud.enabled) com.google.firebase.auth.FirebaseAuth.getInstance().removeAuthStateListener(authListener); super.onStop() }

    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); handleLink(intent) }

    /** A yash://pair link could come from any app: show whose login it is and apply only on OK. */
    private fun handleLink(intent: Intent?) {
        val link = intent?.data?.toString() ?: return
        if (!link.startsWith("yash://pair")) return
        intent.data = null
        val j = Cloud.pairingJson(link) ?: return toast(getString(R.string.qr_foreign))
        confirmHouse(j.optString("srv"), getString(if (j.has("tok")) R.string.link_text_parent else R.string.link_text_child,
            Cloud.pairingUid(j) ?: "?")) { pairWith(link) }
    }

    /** Every pairing - a link, a scanned QR - says which house it connects to, and that it replaces another. */
    private fun confirmHouse(srv: String, what: String, ok: () -> Unit) {
        if (srv.isNotEmpty() && !HomeApi.safeUrl(srv)) return toast(getString(R.string.link_unsafe, HomeApi.shown(srv)))
        val current = HomeApi.url(this)
        val other = srv.isNotEmpty() && current.isNotEmpty() && HomeApi.shown(current) != HomeApi.shown(srv)
        val text = what + (if (srv.isNotEmpty()) "\n\n" + getString(R.string.link_house, HomeApi.shown(srv)) else "") +
            (if (other) "\n\n" + getString(R.string.link_other_house, HomeApi.shown(current)) else "")
        androidx.appcompat.app.AlertDialog.Builder(this)
            .setTitle(R.string.link_title)
            .setMessage(text)
            .setPositiveButton(R.string.link_ok) { _, _ -> ok() }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun pairWith(text: String) {
        pairBtn.isEnabled = false
        Cloud.signIn(this, text) { err ->
            pairBtn.isEnabled = true
            toast(err ?: getString(R.string.paired))
            fillServer(); refresh(); checkServer()
        }
    }

    private val scan = registerForActivityResult(ScanContract()) { res ->
        val text = res.contents ?: return@registerForActivityResult
        if (text.startsWith(HomeApi.QR_PREFIX)) {
            val (url, tok) = HomeApi.parseHomeQr(text) ?: run { toast(getString(R.string.qr_foreign)); return@registerForActivityResult }
            confirmHouse(url, getString(R.string.link_text_token)) { HomeApi.save(this, url, tok); fillServer(); checkServer() }
            return@registerForActivityResult
        }
        val j = Cloud.pairingJson(text)
        if (j == null) pairWith(text)
        else confirmHouse(j.optString("srv"), getString(if (j.has("tok")) R.string.link_text_parent else R.string.link_text_child,
            Cloud.pairingUid(j) ?: "?")) { pairWith(text) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)
        statusView = findViewById(R.id.status)
        eventsView = findViewById(R.id.events)
        pairBtn = findViewById(R.id.pair)
        batteryBtn = findViewById(R.id.battery)

        urlEdit = findViewById(R.id.server_url)
        tokenEdit = findViewById(R.id.server_token)
        serverStatus = findViewById(R.id.server_status)
        fillServer()
        findViewById<Button>(R.id.server_save).setOnClickListener {
            HomeApi.save(this, urlEdit.text.toString(), tokenEdit.text.toString()); checkServer()
        }
        val box = findViewById<android.view.View>(R.id.advanced_box)
        val arrow = findViewById<TextView>(R.id.advanced_arrow)
        findViewById<android.view.View>(R.id.advanced_row).setOnClickListener {
            val open = box.visibility != android.view.View.VISIBLE
            box.visibility = if (open) android.view.View.VISIBLE else android.view.View.GONE
            arrow.rotation = if (open) 90f else 0f
        }
        findViewById<Button>(R.id.open_dashboard).setOnClickListener {
            startActivity(Intent(this, DashboardActivity::class.java)); finish()
        }
        pairBtn.setOnClickListener { launchScan() }
        setupLanguage()
        findViewById<Button>(R.id.test).setOnClickListener { RingService.ring(this, 10) }
        findViewById<Button>(R.id.stop).setOnClickListener { RingService.stop(this) }
        findViewById<Button>(R.id.check_update).setOnClickListener { checkUpdate() }
        batteryBtn.visibility = android.view.View.GONE                    // the app no longer asks to be exempt from battery optimisation

        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)

        // Re-publish on every start: cheap, and heals a token write that was denied or lost
        if (Cloud.enabled) FirebaseMessaging.getInstance().token.addOnCompleteListener { t ->
            if (t.isSuccessful) t.result?.let { Cloud.publishToken(this, it) }
            else Prefs.logEvent(this, "token error: ${t.exception?.message}")
            refresh()
        }
        checkServer()
        handleLink(intent)
    }

    override fun onResume() { super.onResume(); refresh() }

    private fun setupLanguage() {
        val current = androidx.appcompat.app.AppCompatDelegate.getApplicationLocales().toLanguageTags()
        val tags = listOf("") + Langs.supported(this)
        val names = tags.map { Langs.name(this, it) }
        findViewById<TextView>(R.id.lang_value).text = Langs.name(this, current.substringBefore(','))
        findViewById<android.view.View>(R.id.lang_row).setOnClickListener {
            val sel = tags.indexOfFirst { it.isNotEmpty() && current.startsWith(it) }.coerceAtLeast(0)
            androidx.appcompat.app.AlertDialog.Builder(this)
                .setTitle(R.string.lang_label)
                .setSingleChoiceItems(names.toTypedArray(), sel) { d, i ->
                    d.dismiss()
                    if (tags[i] != current) androidx.appcompat.app.AppCompatDelegate.setApplicationLocales(
                        androidx.core.os.LocaleListCompat.forLanguageTags(tags[i]))
                }
                .setNegativeButton(android.R.string.cancel, null)
                .show()
        }
    }

    private fun launchScan() = scan.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
        .setPrompt(getString(R.string.scan_prompt)).setBeepEnabled(false).setOrientationLocked(false))

    private fun fillServer() {
        urlEdit.setText(HomeApi.url(this))
        tokenEdit.setText(HomeApi.token(this))
    }

    private fun checkServer() {
        if (!HomeApi.configured(this)) { serverStatus.text = getString(R.string.server_none); return }
        serverStatus.text = getString(R.string.connecting)
        Thread {
            val msg = try { getString(R.string.server_ok, HomeApi.devices(this).length()) }
                      catch (e: HomeApi.ApiError) { if (e.code == 401) getString(R.string.bad_token) else e.message }
                      catch (e: Exception) { getString(R.string.offline, e.message ?: e.javaClass.simpleName) }
            runOnUiThread { serverStatus.text = msg }
        }.start()
    }

    private fun refresh() {
        val uid = Cloud.uid()
        statusView.text = when {
            uid == null && HomeApi.configured(this) -> getString(R.string.paired_direct, HomeApi.url(this))
            uid == null -> getString(R.string.not_paired)
            Prefs.token(this) == null -> getString(R.string.paired_no_token, uid)
            else -> getString(R.string.paired_as, uid)
        }
        pairBtn.text = getString(if (uid == null && !HomeApi.configured(this)) R.string.pair else R.string.repair)
        findViewById<Button>(R.id.open_dashboard).visibility =
            if (HomeApi.configured(this) || uid != null) android.view.View.VISIBLE else android.view.View.GONE
        eventsView.text = Prefs.events(this)
        val nm = getSystemService(android.app.NotificationManager::class.java)
        val warn = when {
            !androidx.core.app.NotificationManagerCompat.from(this).areNotificationsEnabled() -> getString(R.string.warn_notifications)
            nm.currentInterruptionFilter == android.app.NotificationManager.INTERRUPTION_FILTER_NONE -> getString(R.string.warn_silence)
            else -> null
        }
        if (warn != null) statusView.text = statusView.text.toString() + "\n" + warn
    }


    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_LONG).show()

    /** Asks the house which app it has built: a newer one is fetched and installed (AppUpdate, as when the house sends it). */
    private fun checkUpdate() {
        toast(getString(R.string.update_checking))
        Thread {
            val msg = try {
                val app = HomeApi.app(this)
                when {
                    app == null -> getString(R.string.update_none_here)
                    app.optLong("code") <= AppUpdate.installedCode(this) -> getString(R.string.update_latest, app.optString("name"))
                    else -> { AppUpdate.start(this, app); getString(R.string.update_fetching, app.optString("name")) }
                }
            } catch (e: Exception) { getString(R.string.update_failed, e.message ?: e.javaClass.simpleName) }
            runOnUiThread { toast(msg) }
        }.start()
    }
}
