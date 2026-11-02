package com.yash.home

import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.Gravity
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.EditText
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import com.google.firebase.database.DataSnapshot
import com.google.firebase.database.DatabaseError
import com.google.firebase.database.FirebaseDatabase
import com.google.firebase.database.ValueEventListener
import okhttp3.WebSocket
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

/** Native home dashboard: cloud /state, tabs (my screen, security, favourites, rooms, all), live from /ws, control via REST. */
class DashboardActivity : AppCompatActivity() {
    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private lateinit var statusView: TextView
    private lateinit var tabsRow: LinearLayout
    private lateinit var list: LinearLayout
    private lateinit var cloudBox: LinearLayout
    private var cloudListener: ValueEventListener? = null

    private var screen = JSONObject()                             // what the house shows this phone (/api/family/me/ui)
    private var state = JSONObject()
    private var tab = "fav"
    private var ws: WebSocket? = null
    private var started = false
    private val binders = mutableListOf<(JSONObject) -> Unit>()
    private val touchedAt = mutableMapOf<String, Long>()
    private var meKnown = false                                     // the server knows us as a family phone
    private var meAdmin = false                                     // an administrator's phone: it may write door cards
    private val myItems = mutableListOf<Pair<String, String>>()     // my screen: (device, code), stored per name
    private var editing = false
    private var query = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        statusView = TextView(this).apply { setPadding(dp(16), dp(6), dp(16), dp(6)); textSize = 13f }
        tabsRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; setPadding(dp(8), 0, dp(8), 0) }
        list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(12), dp(4), dp(12), dp(24)) }
        root.addView(statusView)
        root.addView(HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; addView(tabsRow) })
        cloudBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(12), 0, dp(12), 0) }
        val content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; addView(cloudBox); addView(list) }
        root.addView(ScrollView(this).apply { addView(content) }, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        setContentView(root)
        supportActionBar?.apply {
            setDisplayShowTitleEnabled(false)
            setDisplayShowCustomEnabled(true)
            customView = android.widget.ImageView(this@DashboardActivity).apply {
                setImageResource(R.drawable.yash_wordmark_header)
                adjustViewBounds = true
                contentDescription = getString(R.string.app_name)
                layoutParams = androidx.appcompat.app.ActionBar.LayoutParams(WRAP_CONTENT, dp(36))
            }
        }
        Perms.firstRun(this)
        Perms.askLocationOnce(this)
        Perms.askBackgroundOnce(this)
        tab = savedInstanceState?.getString("tab") ?: getPreferences(MODE_PRIVATE).getString("tab", "my")!!
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, res: IntArray) {
        super.onRequestPermissionsResult(code, perms, res)
        if (code == Perms.REQ_NOTIF) Perms.firstRun(this)
        if (code == Perms.REQ_LOC && res.firstOrNull() == android.content.pm.PackageManager.PERMISSION_GRANTED) Perms.askBackgroundOnce(this)
    }

    override fun onSaveInstanceState(out: Bundle) { super.onSaveInstanceState(out); out.putString("tab", tab) }

    override fun onStart() {
        super.onStart()
        if (HomeApi.configured(this)) PullWorker.enqueue(this, false, all = true)         // opening the screen refreshes the widgets too
        started = true
        val paired = Cloud.uid() != null
        if (!HomeApi.configured(this) && !paired) {
            startActivity(Intent(this, SetupActivity::class.java)); return
        }
        if (paired) attachCloud()
        if (HomeApi.configured(this)) load()
        else { setStatus(getString(R.string.server_not_set)); tabsRow.removeAllViews(); list.removeAllViews() }
    }

    override fun onStop() {
        started = false
        ws?.close(1000, null); ws = null
        cloudListener?.let { FirebaseDatabase.getInstance().getReference("state").removeEventListener(it) }
        cloudListener = null
        main.removeCallbacksAndMessages(null)
        super.onStop()
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menu.add(0, 5, 0, R.string.ask_title).setIcon(android.R.drawable.ic_btn_speak_now).setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        menu.add(0, 4, 0, R.string.mouse_title).setIcon(R.drawable.ic_mouse).setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        menu.add(0, 1, 0, R.string.refresh)
        menu.add(0, 2, 1, R.string.settings)
        return true
    }

    override fun onPrepareOptionsMenu(menu: Menu): Boolean {
        menu.removeItem(3)
        menu.removeItem(8)
        if (meAdmin) menu.add(0, 8, 2, R.string.nfcw_menu).setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER)
        if (meKnown) menu.add(0, 3, 0, if (editing) R.string.edit_done else R.string.edit_screen)
            .setShowAsAction(MenuItem.SHOW_AS_ACTION_IF_ROOM)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        1 -> { load(); true }
        2 -> { startActivity(Intent(this, SetupActivity::class.java)); true }
        4 -> { startActivity(Intent(this, MouseActivity::class.java)); true }
        5 -> { startActivity(Intent(this, AskActivity::class.java)); true }
        3 -> { editing = !editing; invalidateOptionsMenu(); render(); true }
        8 -> { askCardName(); true }
        else -> super.onOptionsItemSelected(item)
    }

    /** "Write a card": its name, then the house starts a write session and this phone carries it (NfcWriteActivity). */
    private fun askCardName() {
        val field = EditText(this).apply { hint = getString(R.string.nfcw_name_hint); setSingleLine() }
        AlertDialog.Builder(this).setTitle(R.string.nfcw_menu).setView(field)
            .setPositiveButton(R.string.nfcw_start) { _, _ ->
                val name = field.text.toString().trim()
                if (name.isNotEmpty()) io.execute {
                    try {
                        val r = HomeApi.nfcWriteStart(this, name)
                        main.post { NfcWriteActivity.open(this, r.optString("sid"), r.optString("name", name)) }
                    } catch (e: Exception) {
                        val why = HomeApi.detail(e) ?: getString(R.string.nfcw_offline)
                        main.post { Toast.makeText(this, why, Toast.LENGTH_LONG).show() }
                    }
                }
            }
            .setNegativeButton(android.R.string.cancel, null).show()
    }

    private fun load() {
        setStatus(getString(R.string.connecting))
        io.execute {
            try {
                val scr = HomeApi.ui(this, lang())
                val me = try { HomeApi.me(this) } catch (_: Exception) { null }
                main.post {
                    meKnown = me != null
                    meAdmin = me?.optBoolean("admin") == true
                    myItems.clear()
                    me?.optJSONArray("items")?.let { a -> for (i in 0 until a.length()) a.getJSONObject(i).let { myItems += it.optString("dev") to it.optString("code") } }
                    invalidateOptionsMenu()
                    screen = scr
                    render()
                    connectLive()
                }
            } catch (e: Exception) {
                main.post { setStatus(errorText(e)); retryLater { load() } }
            }
        }
    }

    /** The screen again after My screen changed (the house builds its cards). */
    private fun reloadScreen() {
        io.execute {
            val scr = try { HomeApi.ui(this, lang()) } catch (_: Exception) { null } ?: return@execute
            main.post { screen = scr; if (tab == "my" || !editing) render() }
        }
    }

    // ---- cloud /state (works without the home server) -----------------------
    private fun attachCloud() {
        FirebaseDatabase.getInstance().getReference("i18n").child(lang()).get().addOnSuccessListener { snap ->
            @Suppress("UNCHECKED_CAST")
            dict = (snap.value as? Map<String, String>) ?: emptyMap()
            renderCloud(StateCache.load(this))
        }
        renderCloud(StateCache.load(this))
        if (cloudListener != null) return
        cloudListener = object : ValueEventListener {
            override fun onDataChange(snap: DataSnapshot) {
                StateCache.save(this@DashboardActivity, snap.value)
                renderCloud(StateCache.load(this@DashboardActivity))
            }
            override fun onCancelled(err: DatabaseError) { Prefs.logEvent(this@DashboardActivity, "state: ${err.message}") }
        }.also { FirebaseDatabase.getInstance().getReference("state").addValueEventListener(it) }
    }

    private fun renderCloud(st: JSONObject) {
        cloudBox.removeAllViews()
        if (st.length() == 0) return
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(10))
            setBackgroundResource(R.drawable.card_bg)
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(8) }
        }
        box.addView(TextView(this).apply { text = getString(R.string.cloud_title); textSize = 17f; typeface = heading; setTextColor(getColor(R.color.brand_primary)) })
        var newest = 0L
        st.keys().asSequence().mapNotNull { st.optJSONObject(it) }.sortedBy { loc(it, "label") }.forEach { e ->
            newest = maxOf(newest, e.optLong("ts"))
            box.addView(TextView(this).apply { textSize = 15f; setPadding(0, dp(4), 0, dp(4)); text = "${loc(e, "label")}: ${cloudValue(e)}" })
        }
        if (newest > 0) box.addView(TextView(this).apply {
            textSize = 12f; setTextColor(getColor(R.color.brand_muted))
            text = getString(R.string.cloud_age, java.text.SimpleDateFormat("dd.MM HH:mm", java.util.Locale.US).format(java.util.Date(newest)))
        })
        cloudBox.addView(box)
    }

    private fun lang() = resources.configuration.locales[0].language

    /** /state texts are English keys; /i18n/<lang> (published by the bridge from the plug-in i18n.json) translates them. */
    private var dict: Map<String, String> = emptyMap()
    private fun tr(s: String) = dict[s.replace(Regex("[.#$/\\[\\]]"), "_")] ?: s
    private fun loc(e: JSONObject, key: String) = tr(e.optString(key))

    private fun cloudValue(e: JSONObject): String {
        val v = e.opt("v")
        return when (e.optString("kind")) {
            "bool" -> if (truthy(v)) loc(e, "on").ifEmpty { getString(R.string.yes) } else loc(e, "off").ifEmpty { getString(R.string.no) }
            "enum" -> tr(e.optJSONObject("map")?.optString(v.toString(), v.toString()) ?: v.toString())
            "num" -> ((v as? Number)?.let { String.format(java.util.Locale.US, "%.1f", it.toDouble()) } ?: v.toString()) +
                     e.optString("unit").let { if (it.isEmpty()) "" else " $it" }
            else -> v?.toString() ?: "-"
        }
    }

    private fun connectLive() {
        ws?.close(1000, null)
        ws = HomeApi.live(this, { snap -> main.post { state = snap; setStatus(getString(R.string.online)); applyState() } },
            { why -> main.post { setStatus(getString(R.string.offline, why)); retryLater { connectLive() } } })
    }

    private fun retryLater(what: () -> Unit) {
        if (!started) return
        main.removeCallbacksAndMessages(null)
        main.postDelayed({ if (started) what() }, 5000)
    }

    private fun errorText(e: Exception) = when {
        e is HomeApi.ApiError && e.code == 401 -> getString(R.string.bad_token)
        else -> getString(R.string.offline, e.message ?: e.javaClass.simpleName)
    }

    private fun setStatus(s: String) { statusView.text = s }

    // ---- the screen the house made for this phone (/api/family/me/ui): tabs, cards, items --------------------
    private fun tabList(): List<JSONObject> = screen.optJSONArray("tabs")?.let { a -> (0 until a.length()).map { a.getJSONObject(it) } } ?: emptyList()

    private fun cardsOf(t: JSONObject): List<JSONObject> = t.optJSONArray("cards")?.let { a -> (0 until a.length()).map { a.getJSONObject(it) } } ?: emptyList()

    private fun render() {
        val t = tabList()
        tabsRow.removeAllViews(); list.removeAllViews(); binders.clear()
        if (t.isEmpty()) { list.addView(TextView(this).apply { text = getString(R.string.empty); setPadding(dp(8), dp(16), 0, 0) }); return }
        if (t.none { it.optString("id") == tab }) tab = t.first().optString("id")
        for (x in t) {
            val id = x.optString("id")
            tabsRow.addView(Button(this, null, android.R.attr.borderlessButtonStyle).apply {
                text = if (id == "my") myTabLabel() else x.optString("title"); isAllCaps = false; tag = id
                typeface = if (id == tab) heading else android.graphics.Typeface.SANS_SERIF
                setTextColor(getColor(if (id == tab) R.color.brand_primary else R.color.brand_muted))
                setOnClickListener { tab = id; getPreferences(MODE_PRIVATE).edit().putString("tab", id).apply(); render() }
            })
        }
        if (editing) list.addView(TextView(this).apply {
            text = getString(if (tab == "my") R.string.edit_hint_my else R.string.edit_hint)
            textSize = 14f; setTextColor(android.graphics.Color.WHITE); setPadding(dp(14), dp(10), dp(14), dp(10))
            background = android.graphics.drawable.GradientDrawable().apply { cornerRadius = dp(14).toFloat(); setColor(getColor(R.color.brand_fill)) }
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(8) }
        })
        val cards = cardsOf(t.first { it.optString("id") == tab })
        if (tab == "all") {
            val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
            fun fill() {
                box.removeAllViews()
                val q = query.trim().lowercase()
                cards.filter { q.isEmpty() || it.optString("title").lowercase().contains(q) || it.optString("id").lowercase().contains(q) }
                    .forEach { box.addView(cardView(it)) }
            }
            list.addView(EditText(this).apply {
                hint = getString(R.string.search_hint); setText(query); isSingleLine = true
                addTextChangedListener(object : android.text.TextWatcher {
                    override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                    override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                    override fun afterTextChanged(s: android.text.Editable?) { query = s?.toString() ?: ""; binders.clear(); fill(); applyState() }
                })
            })
            list.addView(box); fill()
        } else cards.forEach { list.addView(cardView(it)) }
        if (tab == "my" && cards.isEmpty()) list.addView(TextView(this).apply { text = getString(R.string.my_empty); setPadding(dp(8), dp(16), dp(8), 0) })
        else if (list.childCount == 0) list.addView(TextView(this).apply { text = getString(R.string.empty); setPadding(dp(8), dp(16), 0, 0) })
        applyState()
    }

    /** Edit mode: a checkbox per item (a whole remote is one); checked = on My screen. */
    private fun pickBox(pair: Pair<String, String>, label: String?) = androidx.appcompat.widget.AppCompatCheckBox(this).apply {
        if (label != null) { text = label; textSize = 15f }
        isChecked = pair in myItems
        buttonTintList = android.content.res.ColorStateList.valueOf(getColor(R.color.brand_primary))
        setOnCheckedChangeListener { _, on ->
            if (on && pair !in myItems) myItems += pair else if (!on) myItems.remove(pair)
            saveScreen(); updateMyTabLabel()
        }
    }

    private fun myTabLabel() = getString(R.string.tab_my) + if (myItems.isNotEmpty()) " \u00b7 ${myItems.size}" else ""

    private fun updateMyTabLabel() {
        for (i in 0 until tabsRow.childCount) (tabsRow.getChildAt(i) as? Button)?.let { if (it.tag == "my") it.text = myTabLabel() }
    }

    private fun saveScreen() {
        val items = JSONArray().apply { myItems.forEach { (d, c) -> put(JSONObject().put("dev", d).put("code", c)) } }
        io.execute {
            try { HomeApi.putScreen(this, items); main.post { reloadScreen() } }
            catch (e: Exception) { main.post { Toast.makeText(this, errorText(e), Toast.LENGTH_LONG).show() } }
        }
    }

    private fun cardView(c: JSONObject): View {
        val dev = c.optString("id")
        val title = c.optString("title")
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(12))
            setBackgroundResource(R.drawable.card_bg)
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(8) }
        }
        val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        head.addView(TextView(this).apply { text = title; textSize = 16f; typeface = heading; isSingleLine = true; ellipsize = android.text.TextUtils.TruncateAt.END },
            LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        if (c.optString("sub").isNotEmpty()) head.addView(TextView(this).apply {
            text = c.optString("sub"); textSize = 13f; setTextColor(getColor(R.color.brand_muted)); isSingleLine = true
            ellipsize = android.text.TextUtils.TruncateAt.END; maxWidth = dp(140); setPadding(dp(8), 0, 0, 0) })
        box.addView(head)
        val items = c.optJSONArray("items")?.let { a -> (0 until a.length()).map { a.getJSONObject(it) } } ?: emptyList()
        val switches = items.filter { it.optString("t") == "switch" }
        binders += { st ->
            val s = st.optJSONObject(dev)
            if (switches.isNotEmpty()) tint(box, switches.any { truthy(s?.optJSONObject("values")?.opt(it.optString("code"))) })
            box.alpha = if (s?.optBoolean("online") == true) 1f else 0.45f
        }
        for (it in items) {
            val v = itemView(it, title)
            if (editing && tab != "my") {
                val pair = it.optString("dev", dev) to (if (it.optString("t") == "remote") "*ir" else it.optString("code"))
                box.addView(LinearLayout(this).apply {
                    orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
                    addView(pickBox(pair, null)); addView(v, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
                })
            } else box.addView(v)
        }
        val my = c.optJSONObject("my")
        if (editing && my != null) {
            val pair = my.optString("dev") to my.optString("code")
            box.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                fun btn(label: String, act: (Int) -> Unit) = addView(Button(this@DashboardActivity).apply {
                    text = label; setOnClickListener { val i = myItems.indexOf(pair); if (i >= 0) { act(i); saveScreen() } }
                }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
                btn("\u2191") { i -> if (i > 0) java.util.Collections.swap(myItems, i, i - 1) }
                btn("\u2193") { i -> if (i < myItems.size - 1) java.util.Collections.swap(myItems, i, i + 1) }
                btn("\u2715") { i -> myItems.removeAt(i); updateMyTabLabel() }
            })
        }
        return box
    }

    private fun caption(t: String) = TextView(this).apply {
        text = t.uppercase(); textSize = 12f; isSingleLine = true; ellipsize = android.text.TextUtils.TruncateAt.END; setTextColor(getColor(R.color.brand_muted)); setPadding(dp(3), dp(10), 0, dp(2)) }

    private fun paint(v: View, sel: Boolean) {
        v.background = android.graphics.drawable.GradientDrawable().apply {
            cornerRadius = dp(14).toFloat(); setColor(if (sel) getColor(R.color.brand_fill) else getColor(R.color.rm_key)) }
        when (v) {
            is android.widget.ImageButton -> if (sel) v.setColorFilter(android.graphics.Color.WHITE) else v.clearColorFilter()
            is Button -> v.setTextColor(if (sel) android.graphics.Color.WHITE else getColor(R.color.brand_text))
        }
    }

    /** One item of a card, drawn by its type; a type this app does not know shows its label. */
    private fun itemView(it: JSONObject, title: String): View {
        val dev = it.optString("dev"); val code = it.optString("code"); val label = it.optString("label")
        val ro = it.optBoolean("ro")
        val view = when (it.optString("t")) {
            "switch" -> switchView(dev, code, label, title, it.optBoolean("confirm_off"))
            "slider" -> sliderView(dev, code, label, it)
            "select" -> selectView(dev, code, label, it.optJSONArray("options") ?: JSONArray())
            "value" -> valueView(dev, code, label, it.optString("unit"))
            "remote" -> remoteView(it.optJSONArray("keys") ?: JSONArray())
            else -> TextView(this).apply { text = label; textSize = 15f; setPadding(0, dp(4), 0, dp(4)) }
        }
        if (ro) { view.isEnabled = false; view.alpha = 0.6f; disable(view) }
        return view
    }

    private fun disable(v: View) {
        v.isEnabled = false
        if (v is android.view.ViewGroup) for (i in 0 until v.childCount) disable(v.getChildAt(i))
    }

    private fun switchView(dev: String, code: String, label: String, title: String, confirmOff: Boolean): View {
        val sw = SwitchCompat(this).apply { text = label.ifEmpty { getString(R.string.power) }; minHeight = dp(44); isSingleLine = true; ellipsize = android.text.TextUtils.TruncateAt.END }
        var binding = false
        sw.setOnCheckedChangeListener { _, on ->
            if (binding) return@setOnCheckedChangeListener
            if (confirmOff && !on) {
                // opening a door is the one action that must not happen by a stray tap
                AlertDialog.Builder(this)
                    .setTitle(R.string.unlock_title).setMessage(getString(R.string.unlock_text, title))
                    .setPositiveButton(R.string.unlock_ok) { _, _ -> send(dev, code, false) }
                    .setNegativeButton(android.R.string.cancel) { _, _ -> binding = true; sw.isChecked = true; binding = false }
                    .setOnCancelListener { binding = true; sw.isChecked = true; binding = false }
                    .show()
            } else send(dev, code, on)
        }
        binders += { st -> if (!recentlyTouched("$dev/$code")) { binding = true; sw.isChecked = truthy(value(st, dev, code)); binding = false } }
        return sw
    }

    /** pct: 0..100 sent, min..max the state's raw scale; values: the knob stops at these and sends them; else min..max as is. */
    private fun sliderView(dev: String, code: String, label: String, it: JSONObject): View {
        val vals = it.optJSONArray("values")?.let { a -> (0 until a.length()).map { a.get(it).toString() } }
        val pct = it.optBoolean("pct"); val lo = it.optDouble("min", 0.0); val hi = it.optDouble("max", 100.0)
        val unit = it.optString("unit")
        val row = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val line = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val big = TextView(this).apply { textSize = if (vals != null) 30f else 16f; typeface = heading; setPadding(dp(8), 0, 0, 0) }
        val bar = SeekBar(this).apply { max = vals?.let { (it.size - 1).coerceAtLeast(0) } ?: if (pct) 100 else (hi - lo).toInt().coerceAtLeast(1); minimumHeight = dp(36) }
        fun shown(p: Int) = (vals?.getOrNull(p) ?: if (pct) "$p%" else (lo + p).toInt().toString()) + (if (vals != null) unit else "")
        var dragging = false
        bar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(s: SeekBar, p: Int, user: Boolean) { if (user) big.text = shown(p) }
            override fun onStartTrackingTouch(s: SeekBar) { dragging = true }
            override fun onStopTrackingTouch(s: SeekBar) {
                dragging = false
                val v: Any = vals?.getOrNull(s.progress) ?: if (pct) s.progress else (lo + s.progress).toInt()
                send(dev, code, v)
            }
        })
        binders += { st ->
            val raw = value(st, dev, code)
            val p = when {
                raw == null || raw == JSONObject.NULL -> null
                vals != null -> vals.indexOfFirst { v -> v == raw.toString() || (raw as? Number)?.toDouble() == v.toDoubleOrNull() }.takeIf { it >= 0 }
                pct -> (raw as? Number)?.let { (((it.toDouble() - lo) / ((hi - lo).takeIf { d -> d != 0.0 } ?: 1.0)) * 100).toInt().coerceIn(0, 100) }
                else -> (raw as? Number)?.let { (it.toDouble() - lo).toInt().coerceIn(0, bar.max) }
            }
            if (!dragging && !recentlyTouched("$dev/$code") && p != null) { bar.progress = p; big.text = shown(p) }
        }
        row.addView(caption(label))
        line.addView(bar, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f)); line.addView(big)
        row.addView(line)
        return row
    }

    /** A row of choices: an icon when the app has one for the value (ac_<value>), else its label; the current one lit. */
    private fun selectView(dev: String, code: String, label: String, options: JSONArray): View {
        val row = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        row.addView(caption(label))
        val chips = (0 until options.length()).map { i ->
            val o = options.getJSONArray(i); val v = o.get(0).toString(); val name = o.optString(1, v)
            val res = resources.getIdentifier("ac_" + v.lowercase().replace(Regex("[^a-z0-9_]"), "_"), "drawable", packageName)
            val b: View = if (res != 0) android.widget.ImageButton(this).apply {
                setImageResource(res); scaleType = android.widget.ImageView.ScaleType.CENTER_INSIDE; setPadding(dp(10), dp(10), dp(10), dp(10)) }
            else Button(this, null, android.R.attr.borderlessButtonStyle).apply {
                text = name; isAllCaps = false; Remote.oneLine(this); setPadding(dp(6), 0, dp(6), 0)
            }
            b.contentDescription = name
            b.setOnClickListener { send(dev, code, v) }
            v to b
        }
        // as many in a row as their words allow: a long name gets a wider key; one line always (the type shrinks to fit)
        val longest = (0 until options.length()).maxOfOrNull { options.getJSONArray(it).optString(1, "").length } ?: 0
        val perRow = when { longest > 12 -> 2; longest > 7 -> 3; else -> 4 }
        chips.chunked(perRow).forEach { line ->
            row.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                line.forEach { (_, b) ->
                    addView(b, LinearLayout.LayoutParams(0, dp(46), 1f).apply { setMargins(dp(3), dp(3), dp(3), dp(3)) })
                }
                repeat(perRow - line.size) { addView(View(this@DashboardActivity), LinearLayout.LayoutParams(0, 1, 1f)) }
            })
        }
        binders += { st -> if (!recentlyTouched("$dev/$code")) { val cur = value(st, dev, code)?.toString(); for ((v, b) in chips) paint(b, v == cur) } }
        return row
    }

    private fun valueView(dev: String, code: String, label: String, unit: String): View {
        val tv = TextView(this).apply { textSize = 15f; gravity = Gravity.START; setPadding(0, dp(4), 0, dp(4)); isSingleLine = true; ellipsize = android.text.TextUtils.TruncateAt.END }
        binders += { st ->
            val v = value(st, dev, code)
            val shown = when {
                v == null || v == JSONObject.NULL -> "-"
                v is Boolean -> getString(if (v) R.string.yes else R.string.no)
                v is Double -> String.format(java.util.Locale.US, "%.1f", v)
                else -> v.toString()
            }
            tv.text = listOf(label, "$shown ${if (shown != "-") unit else ""}".trim()).filter { it.isNotEmpty() }.joinToString(": ")
        }
        return tv
    }

    /** The keys sit at the places the house names (slot); a press sends its value (default on) to its own channel. */
    private fun remoteView(keys: JSONArray): View {
        val ks = (0 until keys.length()).map { keys.getJSONObject(it) }
        return Remote.build(this, ks.mapIndexed { i, k -> Remote.Key(i.toString(), k.optString("label"), k.optString("slot")) }) { idx, v ->
            val k = ks.getOrNull(idx.toIntOrNull() ?: -1) ?: return@build
            v.alpha = 0.45f; v.postDelayed({ v.alpha = 1f }, 350)
            send(k.optString("dev"), k.optString("code"), k.opt("value") ?: true)
        }
    }

    private fun value(st: JSONObject, dev: String, code: String): Any? =
        st.optJSONObject(dev)?.optJSONObject("values")?.opt(code)

    private fun truthy(v: Any?): Boolean = when (v) {
        is Boolean -> v
        is Number -> v.toDouble() != 0.0
        is String -> v.lowercase() in setOf("on", "true", "1", "open", "locked")
        else -> false
    }

    // a card of a unit that is on glows warm, as on the dashboard
    private fun tint(box: View, on: Boolean) {
        val surface = getColor(R.color.brand_surface)
        box.background = android.graphics.drawable.GradientDrawable(android.graphics.drawable.GradientDrawable.Orientation.TL_BR,
            intArrayOf(if (on) 0xFF322712.toInt() else surface, surface)).apply {
            cornerRadius = dp(14).toFloat(); if (on) setStroke(dp(1), 0xFF5A4A1E.toInt()) }
    }

    private fun recentlyTouched(key: String) = SystemClock.elapsedRealtime() - (touchedAt[key] ?: 0L) < 3000

    private fun applyState() { for (b in binders) b(state) }

    private fun send(dev: String, code: String, value: Any) {
        touchedAt["$dev/$code"] = SystemClock.elapsedRealtime()
        io.execute {
            try { HomeApi.control(this, dev, code, value) }
            catch (e: Exception) { main.post { Toast.makeText(this, errorText(e), Toast.LENGTH_LONG).show() } }
        }
    }

    private val heading: android.graphics.Typeface = android.graphics.Typeface.create("sans-serif-black", android.graphics.Typeface.NORMAL)

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
}
