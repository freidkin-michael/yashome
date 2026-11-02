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
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
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

    private var devices = linkedMapOf<String, JSONObject>()
    private var rooms = JSONArray()
    private var favorites = JSONArray()
    private var state = JSONObject()
    private var tab = "fav"
    private var ws: WebSocket? = null
    private var started = false
    private val binders = mutableListOf<(JSONObject) -> Unit>()
    private val touchedAt = mutableMapOf<String, Long>()
    private var meKnown = false                                     // the server knows us as a family phone
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
        menu.add(0, 6, 1, R.string.rec_title).setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER)
        menu.add(0, 5, 0, R.string.ask_title).setIcon(android.R.drawable.ic_btn_speak_now).setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        menu.add(0, 4, 0, R.string.mouse_title).setIcon(R.drawable.ic_mouse).setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        menu.add(0, 7, 0, R.string.films_title).setIcon(android.R.drawable.ic_media_play).setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        menu.add(0, 1, 0, R.string.refresh)
        menu.add(0, 2, 1, R.string.settings)
        return true
    }

    private var recAvail = false                       // the house has the recognizer service that keeps voice samples

    override fun onPrepareOptionsMenu(menu: Menu): Boolean {
        menu.findItem(6)?.isVisible = recAvail
        menu.removeItem(3)
        if (meKnown) menu.add(0, 3, 0, if (editing) R.string.edit_done else R.string.edit_screen)
            .setShowAsAction(MenuItem.SHOW_AS_ACTION_IF_ROOM)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean = when (item.itemId) {
        1 -> { load(); true }
        2 -> { startActivity(Intent(this, SetupActivity::class.java)); true }
        4 -> { startActivity(Intent(this, MouseActivity::class.java)); true }
        7 -> { startActivity(Intent(this, FilmsActivity::class.java)); true }
        5 -> { startActivity(Intent(this, AskActivity::class.java)); true }
        6 -> { startActivity(Intent(this, RecordActivity::class.java)); true }
        3 -> { editing = !editing; invalidateOptionsMenu(); render(); true }
        else -> super.onOptionsItemSelected(item)
    }

    private fun load() {
        setStatus(getString(R.string.connecting))
        io.execute { val a = HomeApi.recordAvailable(this); if (a != recAvail) { recAvail = a; runOnUiThread { invalidateOptionsMenu() } } }
        io.execute {
            try {
                val devs = HomeApi.devices(this)
                val r = HomeApi.rooms(this)
                val f = HomeApi.favorites(this)
                val me = try { HomeApi.me(this) } catch (_: Exception) { null }
                main.post {
                    meKnown = me != null
                    myItems.clear()
                    me?.optJSONArray("items")?.let { a -> for (i in 0 until a.length()) a.getJSONObject(i).let { myItems += it.optString("dev") to it.optString("code") } }
                    invalidateOptionsMenu()
                    devices = linkedMapOf<String, JSONObject>().apply {
                        for (i in 0 until devs.length()) devs.getJSONObject(i).let {
                            if (!it.optBoolean("unsupported")) put(it.getString("id"), it)
                        }
                    }
                    rooms = r; favorites = f
                    render()
                    connectLive()
                }
            } catch (e: Exception) {
                main.post { setStatus(errorText(e)); retryLater { load() } }
            }
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

    // ---- tabs ----------------------------------------------------------------
    private fun tabs(): List<Pair<String, String>> {
        val out = mutableListOf<Pair<String, String>>()
        if (meKnown) out += "my" to myTabLabel()
        if (devices.values.any(::isSecurity)) out += "sec" to getString(R.string.tab_security)
        if (favorites.length() > 0) out += "fav" to getString(R.string.tab_fav)
        for (i in 0 until rooms.length()) rooms.getJSONObject(i).let { out += ("room:" + it.getString("id")) to it.optString("name") }
        out += "all" to getString(R.string.tab_all)
        return out
    }

    private fun render() {
        val t = tabs()
        if (t.none { it.first == tab }) tab = t.first().first
        tabsRow.removeAllViews()
        for ((id, name) in t) tabsRow.addView(Button(this, null, android.R.attr.borderlessButtonStyle).apply {
            text = name; isAllCaps = false; tag = id
            typeface = if (id == tab) heading else android.graphics.Typeface.SANS_SERIF
            setTextColor(getColor(if (id == tab) R.color.brand_primary else R.color.brand_muted))
            setOnClickListener { tab = id; getPreferences(MODE_PRIVATE).edit().putString("tab", id).apply(); render() }
        })
        list.removeAllViews(); binders.clear()
        if (editing) list.addView(TextView(this).apply {
            text = getString(if (tab == "my") R.string.edit_hint_my else R.string.edit_hint)
            textSize = 14f; setTextColor(android.graphics.Color.WHITE); setPadding(dp(14), dp(10), dp(14), dp(10))
            background = android.graphics.drawable.GradientDrawable().apply { cornerRadius = dp(14).toFloat(); setColor(getColor(R.color.brand_fill)) }
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(8) }
        })
        when {
            tab == "my" -> {
                myItems.forEachIndexed { i, (dev, code) ->
                    val d = devices[dev] ?: return@forEachIndexed
                    if (code == "*ac" && isClimate(d)) { list.addView(climateCard(d, i)); return@forEachIndexed }
                    val ctls = if (code == "*ir") irCtls(d) else listOfNotNull(findCtl(d, code))
                    if (ctls.isEmpty()) return@forEachIndexed
                    list.addView(card(d.optString("name"), d, ctls, single = code != "*ir", myIndex = i))
                }
                if (myItems.isEmpty()) list.addView(TextView(this).apply { text = getString(R.string.my_empty); setPadding(dp(8), dp(16), dp(8), 0) })
            }
            tab == "sec" -> devices.values.filter(::isSecurity).forEach { list.addView(deviceCard(it)) }
            tab == "fav" -> for (i in 0 until favorites.length()) favorites.getJSONObject(i).let { f ->
                val d = devices[f.optString("device")] ?: return@let
                val ctl = findCtl(d, f.optString("code")) ?: return@let
                list.addView(card(f.optString("alias").ifEmpty { d.optString("name") }, d, listOf(ctl), single = true))
            }
            tab.startsWith("room:") -> {
                val room = (0 until rooms.length()).map { rooms.getJSONObject(it) }.firstOrNull { "room:" + it.getString("id") == tab }
                val ids = room?.optJSONArray("devices") ?: JSONArray()
                for (i in 0 until ids.length()) devices[ids.getString(i)]?.let { list.addView(deviceCard(it)) }
            }
            else -> {
                val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
                list.addView(android.widget.EditText(this).apply {
                    hint = getString(R.string.search_hint); setText(query); isSingleLine = true
                    addTextChangedListener(object : android.text.TextWatcher {
                        override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                        override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                        override fun afterTextChanged(s: android.text.Editable?) {
                            query = s?.toString() ?: ""; binders.clear(); fillAll(box); applyState()
                        }
                    })
                })
                list.addView(box)
                fillAll(box)
            }
        }
        if (list.childCount == 0) list.addView(TextView(this).apply { text = getString(R.string.empty); setPadding(dp(8), dp(16), 0, 0) })
        applyState()
    }

    private fun fillAll(box: LinearLayout) {
        box.removeAllViews()
        val q = query.trim().lowercase()
        devices.values.filter { q.isEmpty() || it.optString("name").lowercase().contains(q) || it.optString("id").lowercase().contains(q) }
            .sortedBy { it.optString("name").lowercase() }.forEach { box.addView(deviceCard(it)) }
    }

    /** Locks and doors: a lock-style switch, a door/contact channel, or "lock"/"door" in the name (English or Russian). */
    private fun isSecurity(d: JSONObject): Boolean {
        val c = d.optJSONArray("controls") ?: JSONArray()
        val ctls = (0 until c.length()).map { c.getJSONObject(it) }
        val words = (d.optString("id") + " " + d.optString("name") + " " + d.optString("model")).lowercase()
        return ctls.any { it.optString("payload_on") == "LOCKED" || it.optString("code") in setOf("locked", "lock", "door", "contact") } ||
            listOf("lock", "\u0437\u0430\u043c\u043e\u043a", "door", "\u0434\u0432\u0435\u0440").any { it in words }   // i18n: Russian words
    }

    /** Edit mode: a checkbox per channel (or per whole remote / AC); checked = on My screen. */
    private fun pickBox(pair: Pair<String, String>, label: String?) = androidx.appcompat.widget.AppCompatCheckBox(this).apply {
        if (label != null) { text = label; textSize = 15f }
        isChecked = pair in myItems
        buttonTintList = android.content.res.ColorStateList.valueOf(getColor(R.color.brand_primary))
        setOnCheckedChangeListener { _, on ->
            if (on && pair !in myItems) myItems += pair else if (!on) myItems.remove(pair)
            saveScreen(); updateMyTabLabel()
        }
    }

    private fun myTabLabel() = getString(R.string.tab_my) + if (myItems.isNotEmpty()) " \u00B7 ${myItems.size}" else ""

    private fun updateMyTabLabel() {
        for (i in 0 until tabsRow.childCount) (tabsRow.getChildAt(i) as? Button)?.let { if (it.tag == "my") it.text = myTabLabel() }
    }

    private fun saveScreen() {
        val items = JSONArray().apply { myItems.forEach { (d, c) -> put(JSONObject().put("dev", d).put("code", c)) } }
        io.execute {
            try { HomeApi.putScreen(this, items) }
            catch (e: Exception) { main.post { Toast.makeText(this, errorText(e), Toast.LENGTH_LONG).show() } }
        }
    }

    private fun isClimate(d: JSONObject) = d.optString("category") == "climate" &&
        findCtl(d, "power") != null && findCtl(d, "temp") != null

    // ---- air conditioner, drawn like the dashboard AC card ------------------------------
    private fun climateCard(d: JSONObject, myIndex: Int): View {
        val id = d.getString("id")
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(12))
            setBackgroundResource(R.drawable.card_bg)
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(8) }
        }
        val head = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        head.addView(TextView(this).apply { text = d.optString("name"); textSize = 16f; typeface = heading },
            LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        val room = TextView(this).apply { textSize = 13f; setTextColor(getColor(R.color.brand_muted)) }
        head.addView(room); box.addView(head)
        if (editing && myIndex < 0) {
            box.addView(pickBox(id to "*ac", getString(R.string.ac_whole)))
        }

        fun chip(onClick: () -> Unit) = android.widget.ImageButton(this).apply {
            scaleType = android.widget.ImageView.ScaleType.CENTER_INSIDE
            setPadding(dp(10), dp(10), dp(10), dp(10)); setOnClickListener { onClick() }
        }
        fun paint(v: View, sel: Boolean, fill: Int = getColor(R.color.brand_fill)) {
            v.background = android.graphics.drawable.GradientDrawable().apply {
                cornerRadius = dp(14).toFloat(); setColor(if (sel) fill else getColor(R.color.rm_key)) }
            when (v) {
                is android.widget.ImageButton -> if (sel) v.setColorFilter(android.graphics.Color.WHITE) else v.clearColorFilter()
                is Button -> v.setTextColor(if (sel) android.graphics.Color.WHITE else getColor(R.color.brand_text))
            }
        }
        fun rowOf(views: List<View>, h: Int) = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            views.forEach { addView(it, LinearLayout.LayoutParams(0, dp(h), 1f).apply { setMargins(dp(3), dp(3), dp(3), dp(3)) }) }
        }
        fun caption(t: String) = TextView(this).apply {
            text = t.uppercase(); textSize = 12f; setTextColor(getColor(R.color.brand_muted)); setPadding(dp(3), dp(10), 0, dp(2)) }

        // power
        val on = Button(this, null, android.R.attr.borderlessButtonStyle).apply { isAllCaps = false; setOnClickListener { send(id, "power", true) } }
        val off = Button(this, null, android.R.attr.borderlessButtonStyle).apply { isAllCaps = false; setOnClickListener { send(id, "power", false) } }
        box.addView(rowOf(listOf(on, off), 50))

        // temperature
        val range = findCtl(d, "temp")?.optJSONArray("range")?.let { r -> (0 until r.length()).map { r.getString(it) } } ?: emptyList()
        val tRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val big = TextView(this).apply { textSize = 34f; typeface = heading; setPadding(dp(8), 0, 0, 0) }
        val bar = SeekBar(this).apply { max = (range.size - 1).coerceAtLeast(0) }
        var dragging = false
        bar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(s: SeekBar, p: Int, user: Boolean) { if (user && p < range.size) big.text = "${range[p]}\u00b0C" }
            override fun onStartTrackingTouch(s: SeekBar) { dragging = true }
            override fun onStopTrackingTouch(s: SeekBar) { dragging = false; range.getOrNull(s.progress)?.let { send(id, "temp", it) } }
        })
        if (range.isNotEmpty()) {
            box.addView(caption(getString(R.string.ac_temp)))
            tRow.addView(bar, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f)); tRow.addView(big); box.addView(tRow)
        }

        // fan and mode: icon chips like the dashboard
        val groups = listOf("fan", "mode").mapNotNull { code ->
            val ctl = findCtl(d, code) ?: return@mapNotNull null
            val r = ctl.optJSONArray("range") ?: return@mapNotNull null
            val opts = (0 until r.length()).map { r.getString(it) }
            val labels = ctl.optJSONObject("labels")
            box.addView(caption(getString(if (code == "fan") R.string.ac_fan else R.string.ac_mode)))
            val chips = opts.map { v ->
                val res = resources.getIdentifier("ac_$v", "drawable", packageName)
                val b: View = if (res != 0) chip { send(id, code, v) }.apply { setImageResource(res) }
                              else Button(this, null, android.R.attr.borderlessButtonStyle).apply { text = labels?.optString(v, v) ?: v; isAllCaps = false; setOnClickListener { send(id, code, v) } }
                b.contentDescription = labels?.optString(v, v) ?: v
                v to b
            }
            box.addView(rowOf(chips.map { it.second }, 46))
            code to chips
        }

        binders += { st ->
            val v = st.optJSONObject(id)?.optJSONObject("values") ?: JSONObject()
            val pw = truthy(v.opt("power"))
            if (!recentlyTouched("$id/power")) {
                on.text = getString(if (pw) R.string.ac_is_on else R.string.ac_turn_on); paint(on, pw)
                off.text = getString(if (pw) R.string.ac_turn_off else R.string.ac_is_off); paint(off, !pw, getColor(R.color.ac_off_fill))
            }
            room.text = (v.opt("room_temp") as? Number)?.let { getString(R.string.ac_room, String.format(java.util.Locale.US, "%.1f", it.toDouble())) } ?: ""
            val t = (v.opt("temp") as? Number)?.toInt()?.toString()
            if (!dragging && !recentlyTouched("$id/temp") && t != null) { range.indexOf(t).takeIf { it >= 0 }?.let { bar.progress = it }; big.text = "$t\u00b0C" }
            for ((code, chips) in groups) if (!recentlyTouched("$id/$code")) for ((opt, b) in chips) paint(b, v.optString(code) == opt)
            tRow.alpha = if (pw) 1f else 0.55f
            tint(box, pw)
            box.alpha = if (st.optJSONObject(id)?.optBoolean("online") == true) 1f else 0.45f
        }
        if (editing && myIndex >= 0) box.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            fun btn(label: String, act: () -> Unit) = addView(Button(this@DashboardActivity).apply { text = label; setOnClickListener { act(); saveScreen(); render() } },
                LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            btn("\u2191") { if (myIndex > 0) java.util.Collections.swap(myItems, myIndex, myIndex - 1) }
            btn("\u2193") { if (myIndex < myItems.size - 1) java.util.Collections.swap(myItems, myIndex, myIndex + 1) }
            btn("\u2715") { myItems.removeAt(myIndex) }
        })
        return box
    }

    private fun irCtls(d: JSONObject): List<JSONObject> {
        val c = d.optJSONArray("controls") ?: return emptyList()
        return (0 until c.length()).map { c.getJSONObject(it) }.filter { it.optString("kind") == "ir_press" }
    }

    private fun findCtl(d: JSONObject, code: String): JSONObject? {
        val c = d.optJSONArray("controls") ?: return null
        return (0 until c.length()).map { c.getJSONObject(it) }.firstOrNull { it.optString("code") == code }
    }

    private fun deviceCard(d: JSONObject): View {
        if (isClimate(d)) return climateCard(d, -1)
        val c = d.optJSONArray("controls") ?: JSONArray()
        return card(d.optString("name"), d, (0 until c.length()).map { c.getJSONObject(it) }, single = false)
    }

    // ---- cards ---------------------------------------------------------------
    private fun card(title: String, d: JSONObject, ctls: List<JSONObject>, single: Boolean, myIndex: Int = -1): View {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(10), dp(14), dp(10))
            setBackgroundResource(R.drawable.card_bg)
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(8) }
        }
        val head = TextView(this).apply { text = title; textSize = 16f; typeface = heading }
        box.addView(head)
        val id = d.getString("id")
        binders += { st -> box.alpha = if (st.optJSONObject(id)?.optBoolean("online") == true) 1f else 0.45f }
        val switches = ctls.filter { it.optString("kind") == "switch" && !it.optBoolean("ro") }
        if (switches.isNotEmpty()) binders += { st ->
            val v = st.optJSONObject(id)?.optJSONObject("values")
            tint(box, v != null && switches.any { truthy(v.opt(it.optString("code"))) })
        }
        // IR buttons are one remote, not a column of rows; in edit mode it is starred as a whole ("*ir")
        val irKeys = ctls.filter { it.optString("kind") == "ir_press" }
        val plain = ctls.filter { it.optString("kind") != "ir_press" }
        for (ctl in plain) controlRow(id, ctl, if (single) "" else ctl.optString("label", ctl.optString("code")))?.let { row ->
            if (!editing || myIndex >= 0) { box.addView(row); return@let }
            val star = pickBox(id to ctl.optString("code"), null)
            box.addView(LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
                addView(star); addView(row, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            })
        }
        if (irKeys.isNotEmpty()) {
            if (editing && myIndex < 0) {
                box.addView(pickBox(id to "*ir", getString(R.string.remote_whole)))
            }
            box.addView(Remote.build(this, irKeys.map { Remote.Key(it.optString("code"), it.optString("label", it.optString("code"))) }) { code, v ->
                v.alpha = 0.45f; v.postDelayed({ v.alpha = 1f }, 350)
                send(id, code, true)
            })
        }
        if (editing && myIndex >= 0) box.addView(LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            fun btn(label: String, act: () -> Unit) = addView(Button(this@DashboardActivity).apply { text = label; setOnClickListener { act(); saveScreen(); render() } },
                LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            btn("\u2191") { if (myIndex > 0) java.util.Collections.swap(myItems, myIndex, myIndex - 1) }
            btn("\u2193") { if (myIndex < myItems.size - 1) java.util.Collections.swap(myItems, myIndex, myIndex + 1) }
            btn("\u2715") { myItems.removeAt(myIndex) }
        })
        return box
    }

    private fun controlRow(dev: String, ctl: JSONObject, label: String): View? {
        val code = ctl.optString("code")
        val key = "$dev/$code"
        val ro = ctl.optBoolean("ro")
        return when (ctl.optString("kind")) {
            "switch" -> if (ro) textRow(dev, ctl, label) else {
                val base = label.ifEmpty { ctl.optString("label").ifEmpty { getString(R.string.power) } }
                val textOn = ctl.optString("text_on"); val textOff = ctl.optString("text_off")
                val isLock = ctl.optString("payload_on") == "LOCKED"
                val sw = SwitchCompat(this).apply { text = base; minHeight = dp(44) }
                var binding = false
                fun caption() { if (textOn.isNotEmpty()) sw.text = "$base: ${if (sw.isChecked) textOn else textOff}" }
                sw.setOnCheckedChangeListener { _, on ->
                    if (binding) return@setOnCheckedChangeListener
                    caption()
                    if (isLock && !on) {
                        // opening a door is the one action that must not happen by a stray tap
                        androidx.appcompat.app.AlertDialog.Builder(this)
                            .setTitle(R.string.unlock_title).setMessage(getString(R.string.unlock_text, devices[dev]?.optString("name") ?: dev))
                            .setPositiveButton(R.string.unlock_ok) { _, _ -> send(dev, code, false) }
                            .setNegativeButton(android.R.string.cancel) { _, _ -> binding = true; sw.isChecked = true; caption(); binding = false }
                            .setOnCancelListener { binding = true; sw.isChecked = true; caption(); binding = false }
                            .show()
                    } else send(dev, code, on)
                }
                binders += { st ->
                    if (!recentlyTouched(key)) {
                        binding = true; sw.isChecked = truthy(value(st, dev, code)); caption(); binding = false
                    }
                }
                sw
            }
            "bright" -> {
                val row = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
                val cap = TextView(this).apply { textSize = 13f }
                val bar = SeekBar(this).apply { max = 100; minimumHeight = dp(36) }
                val lo = ctl.optDouble("min", 0.0); val hi = ctl.optDouble("max", 100.0)
                var dragging = false
                bar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                    override fun onProgressChanged(s: SeekBar, p: Int, user: Boolean) { if (user) cap.text = "$label $p%".trim() }
                    override fun onStartTrackingTouch(s: SeekBar) { dragging = true }
                    override fun onStopTrackingTouch(s: SeekBar) { dragging = false; send(dev, code, s.progress) }
                })
                binders += { st ->
                    val raw = value(st, dev, code)
                    val pct = (raw as? Number)?.let { (((it.toDouble() - lo) / ((hi - lo).takeIf { d -> d != 0.0 } ?: 1.0)) * 100).toInt().coerceIn(0, 100) }
                    if (!dragging && !recentlyTouched(key) && pct != null) { bar.progress = pct; cap.text = "$label $pct%".trim() }
                }
                row.addView(cap); row.addView(bar); row
            }
            else -> textRow(dev, ctl, label)
        }
    }

    private fun textRow(dev: String, ctl: JSONObject, label: String): View {
        val code = ctl.optString("code")
        val unit = ctl.optString("unit")
        val isBool = ctl.optString("vtype") == "bool"
        val tv = TextView(this).apply { textSize = 15f; gravity = Gravity.START; setPadding(0, dp(4), 0, dp(4)) }
        binders += { st ->
            val v = value(st, dev, code)
            val shown = when {
                v == null || v == JSONObject.NULL -> "-"
                isBool || v is Boolean -> getString(if (truthy(v)) R.string.yes else R.string.no)
                v is Double -> String.format(java.util.Locale.US, "%.1f", v)
                else -> v.toString()
            }
            tv.text = listOf(label, "$shown ${if (shown != "-") unit else ""}".trim()).filter { it.isNotEmpty() }.joinToString(": ")
        }
        return tv
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
