package com.yash.home

import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.inputmethod.EditorInfo
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import org.json.JSONObject

/** Films (the house's player plug-in): search its sources or paste a film's page from a site; the film's translation, season, episode,
 *  quality and the player (a computer at a TV) to watch it on; Continue; each player with its buttons. The house turns the TV on and
 *  onto the computer's input and sends the stream there: the phone only asks. */
class FilmsActivity : AppCompatActivity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var body: LinearLayout
    private lateinit var playersBox: LinearLayout
    private lateinit var field: EditText
    private lateinit var status: TextView
    private var players = JSONArray()
    private var filmOpen = false
    private val grey = Color.parseColor("#8A93A3")
    private val tick = object : Runnable {
        override fun run() { if (!filmOpen) loadPlayers(); ui.postDelayed(this, 3000) }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun isUrl(s: String) = s.startsWith("http://") || s.startsWith("https://")
    private fun time(s: Double): String {
        if (s.isNaN() || s < 0) return "--:--"
        val t = s.toLong(); val h = t / 3600; val m = t / 60 % 60; val x = t % 60
        return if (h > 0) "%d:%02d:%02d".format(h, m, x) else "%d:%02d".format(m, x)
    }
    private fun err(e: Exception): String {
        val m = e.message ?: e.toString()
        return Regex("\"detail\"\\s*:\\s*\"([^\"]+)\"").find(m)?.groupValues?.get(1) ?: m
    }

    /** In the background; the answer (or the error, as a toast) back on the screen. */
    private fun <T> bg(work: () -> T, done: (T) -> Unit) {
        Thread {
            try { val r = work(); ui.post { if (!isFinishing) done(r) } }
            catch (e: Exception) { ui.post { if (!isFinishing) { status.text = ""; Toast.makeText(this, err(e), Toast.LENGTH_LONG).show() } } }
        }.start()
    }

    private fun label(text: String, size: Float = 15f, bold: Boolean = false, color: Int = Color.WHITE) = TextView(this).apply {
        this.text = text; textSize = size; setTextColor(color); if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD)
        setPadding(0, dp(4), 0, dp(4))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.films_title)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(12), dp(8), dp(12), dp(8)) }
        field = EditText(this).apply {
            hint = getString(R.string.films_hint); setSingleLine(); imeOptions = EditorInfo.IME_ACTION_SEARCH
            setTextColor(Color.WHITE); setHintTextColor(grey)
        }
        field.setOnEditorActionListener { _, a, _ -> if (a == EditorInfo.IME_ACTION_SEARCH) { find(); true } else false }
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        row.addView(field, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        row.addView(Button(this).apply { text = getString(R.string.films_find); setOnClickListener { find() } })
        root.addView(row)
        status = label("", 13f, color = grey)
        root.addView(status)
        body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        playersBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val all = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; addView(body); addView(playersBox) }
        root.addView(ScrollView(this).apply { addView(all, MATCH_PARENT, WRAP_CONTENT) }, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        setContentView(root)
        intent?.getStringExtra(android.content.Intent.EXTRA_TEXT)?.let { field.setText(it); find() }
        showRecent()
    }

    override fun onResume() { super.onResume(); ui.post(tick) }
    override fun onPause() { super.onPause(); ui.removeCallbacks(tick) }

    @Deprecated("Back closes a film's window first")
    override fun onBackPressed() {
        if (filmOpen) { filmOpen = false; showRecent(); loadPlayers() } else super.onBackPressed()
    }

    private fun find() {
        val v = field.text.toString().trim()
        if (v.isEmpty()) return
        status.text = getString(R.string.films_looking)
        if (isUrl(v)) bg({ HomeApi.playerPost(this, "/resolve", JSONObject().put("url", v)) }) { status.text = ""; film(it) }
        else bg({ HomeApi.playerGet(this, "/search", mapOf("q" to v)) }) { r ->
            status.text = r.optJSONObject("errors")?.let { e -> e.keys().asSequence().joinToString("; ") { "$it: ${e.optString(it)}" } } ?: ""
            filmOpen = false
            body.removeAllViews()
            val res = r.optJSONArray("results") ?: JSONArray()
            val searched = r.optJSONArray("searched")
            if (searched != null && searched.length() == 0) body.addView(label(getString(R.string.films_nowhere), color = Color.parseColor("#F87171")))
            else if (res.length() == 0) body.addView(label(getString(R.string.films_nothing), color = grey))
            for (i in 0 until res.length()) {
                val x = res.getJSONObject(i)
                val sub = listOf(x.optString("year"), x.optString("note"), x.optString("source_name")).filter { it.isNotEmpty() && it != "0" }.joinToString(" \u00b7 ")
                body.addView(Button(this).apply {
                    text = x.optString("title") + if (sub.isNotEmpty()) "\n$sub" else ""; isAllCaps = false; gravity = Gravity.START or Gravity.CENTER_VERTICAL
                    setOnClickListener { bg({ HomeApi.playerGet(this@FilmsActivity, "/info", mapOf("source" to x.optString("source"), "id" to x.optString("id"))) }) { film(it) } }
                })
            }
        }
    }

    private fun showRecent() {
        bg({ HomeApi.playerGet(this, "/recent") }) { r ->
            if (filmOpen) return@bg
            body.removeAllViews()
            val rec = r.optJSONArray("recent") ?: JSONArray()
            if (rec.length() == 0) return@bg
            body.addView(label(getString(R.string.films_continue), 17f, true))
            for (i in 0 until minOf(rec.length(), 12)) {
                val x = rec.getJSONObject(i)
                val ep = if (x.has("season") && !x.isNull("season")) "S${x.optInt("season")}E${x.optInt("episode")} \u00b7 " else ""
                val pos = x.optDouble("pos", 0.0); val dur = x.optDouble("duration", Double.NaN)
                body.addView(Button(this).apply {
                    text = x.optString("title") + "\n" + ep + time(pos) + if (!dur.isNaN()) " / " + time(dur) else ""
                    isAllCaps = false; gravity = Gravity.START or Gravity.CENTER_VERTICAL
                    setOnClickListener {
                        bg({ HomeApi.playerGet(this@FilmsActivity, "/info", mapOf("source" to x.optString("source"), "id" to x.optString("id"),
                            "translation" to x.optString("translation").takeIf { it != "null" }.orEmpty())) }) { film(it, x) }
                    }
                })
            }
        }
    }

    /** A film's window: the choice, the player, Watch / Continue; [rec] picks what was watched last. */
    private fun film(r: JSONObject, rec: JSONObject? = null) {
        filmOpen = true
        val src = r.optString("source"); val id = r.optString("id")
        var info = r.optJSONObject("info") ?: JSONObject()
        val pick = mutableMapOf<String, String>()
        rec?.let { x -> listOf("translation", "season", "episode", "quality").forEach { k -> if (x.has(k) && !x.isNull(k)) pick[k] = x.optString(k) } }
        fun draw() {
            body.removeAllViews()
            body.addView(label(info.optString("title", id) + (info.optString("year").let { if (it.isNotEmpty() && it != "0") " ($it)" else "" }), 18f, true))
            body.addView(label(r.optString("source_name", src), 13f, color = grey))
            info.optString("description").takeIf { it.isNotEmpty() }?.let { body.addView(label(it, 14f, color = grey)) }
            fun spinner(key: String, title: Int, items: List<Pair<String, String>>, changed: (String) -> Unit = {}) {
                if (items.isEmpty()) { pick.remove(key); return }
                if (items.none { it.first == pick[key] }) pick[key] = items[0].first
                body.addView(label(getString(title), 13f, color = grey))
                body.addView(Spinner(this).apply {
                    adapter = ArrayAdapter(this@FilmsActivity, android.R.layout.simple_spinner_dropdown_item, items.map { it.second })
                    setSelection(items.indexOfFirst { it.first == pick[key] })
                    onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                        override fun onItemSelected(p: AdapterView<*>?, v: View?, pos: Int, l: Long) {
                            val nv = items[pos].first
                            if (nv != pick[key]) { pick[key] = nv; changed(nv) }
                        }
                        override fun onNothingSelected(p: AdapterView<*>?) {}
                    }
                })
            }
            val trs = info.optJSONArray("translations") ?: JSONArray()
            spinner("translation", R.string.films_translation, (0 until trs.length()).map { trs.getJSONObject(it).let { t -> t.optString("id") to t.optString("name", t.optString("id")) } }) { t ->
                bg({ HomeApi.playerGet(this, "/info", mapOf("source" to src, "id" to id, "translation" to t)) }) { info = it.optJSONObject("info") ?: info; draw() }
            }
            val seasons = info.optJSONArray("seasons") ?: JSONArray()
            spinner("season", R.string.films_season, (0 until seasons.length()).map { seasons.getJSONObject(it).optString("n").let { n -> n to "${getString(R.string.films_season)} $n" } }) { pick.remove("episode"); draw() }
            val season = (0 until seasons.length()).map { seasons.getJSONObject(it) }.firstOrNull { it.optString("n") == pick["season"] }
            val eps = season?.optJSONArray("episodes") ?: JSONArray()
            spinner("episode", R.string.films_episode, (0 until eps.length()).map { eps.getJSONObject(it).let { e ->
                e.optString("n") to (if (e.optString("title").isNotEmpty()) "${e.optString("n")}. ${e.optString("title")}" else "${getString(R.string.films_episode)} ${e.optString("n")}") } })
            val qs = info.optJSONArray("qualities") ?: JSONArray()
            spinner("quality", R.string.films_quality, (0 until qs.length()).map { qs.getString(it).let { q -> q to q } })
            val last = getSharedPreferences("yash", MODE_PRIVATE).getString("films_player", "") ?: ""
            if (pick["player"] == null && last.isNotEmpty()) pick["player"] = last
            if (players.length() == 0) body.addView(label(getString(R.string.films_no_players), 14f, color = Color.parseColor("#F87171")))
            spinner("player", R.string.films_where, (0 until players.length()).map { players.getJSONObject(it).let { p ->
                p.optString("id") to (if (p.optString("tv_name").isNotEmpty() && p.optString("tv_name") != "null") "${p.optString("tv_name")} (${p.optString("name")})" else p.optString("name")) } })
            val pos = rec?.optDouble("pos", 0.0) ?: 0.0
            val btns = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            if (pos > 60) btns.addView(Button(this).apply { text = getString(R.string.films_from, time(pos)); isAllCaps = false; setOnClickListener { watch(src, id, info, pick, pos) } })
            btns.addView(Button(this).apply { text = getString(if (pos > 60) R.string.films_start else R.string.films_watch); isAllCaps = false; setOnClickListener { watch(src, id, info, pick, 0.0) } })
            btns.addView(Button(this).apply { text = getString(R.string.films_back); isAllCaps = false; setOnClickListener { filmOpen = false; showRecent(); loadPlayers() } })
            body.addView(btns)
        }
        if (players.length() == 0) bg({ HomeApi.playerGet(this, "") }) { players = it.optJSONArray("players") ?: JSONArray(); draw() } else draw()
        playersBox.removeAllViews()
    }

    private fun watch(src: String, id: String, info: JSONObject, pick: Map<String, String>, start: Double) {
        val pid = pick["player"] ?: return
        getSharedPreferences("yash", MODE_PRIVATE).edit().putString("films_player", pid).apply()
        val b = JSONObject().put("source", src).put("id", id).put("title", info.optString("title")).put("poster", info.optString("poster"))
        pick["translation"]?.let { b.put("translation", it) }
        pick["season"]?.toIntOrNull()?.let { b.put("season", it) }
        pick["episode"]?.toIntOrNull()?.let { b.put("episode", it) }
        pick["quality"]?.let { b.put("quality", it) }
        if (start > 0) b.put("start", start)
        status.text = getString(R.string.films_turning_on)
        bg({ HomeApi.playerPost(this, "/" + android.net.Uri.encode(pid) + "/watch", b) }) { p ->
            status.text = ""
            Toast.makeText(this, getString(R.string.films_playing, p.optString("tv_name").takeIf { it.isNotEmpty() && it != "null" } ?: p.optString("name")), Toast.LENGTH_LONG).show()
            filmOpen = false; showRecent(); loadPlayers()
        }
    }

    private fun loadPlayers() {
        bg({ HomeApi.playerGet(this, "") }) { r ->
            players = r.optJSONArray("players") ?: JSONArray()
            if (filmOpen) return@bg
            drawPlayers()
        }
    }

    private fun cmd(pid: String, b: JSONObject, path: String = "/cmd") =
        bg({ HomeApi.playerPost(this, "/" + android.net.Uri.encode(pid) + path, b) }) { loadPlayers() }

    private fun drawPlayers() {
        playersBox.removeAllViews()
        if (players.length() == 0) return
        playersBox.addView(label(getString(R.string.films_players), 17f, true))
        for (i in 0 until players.length()) {
            val p = players.getJSONObject(i); val pid = p.optString("id")
            val st = p.optJSONObject("state"); val on = st != null && !st.optBoolean("idle", true)
            val tv = p.optString("tv_name").takeIf { it.isNotEmpty() && it != "null" }
            playersBox.addView(label(p.optString("name") + (tv?.let { " \u00b7 $it" } ?: ""), 15f, true))
            if (!p.isNull("error")) { playersBox.addView(label(p.optString("error"), 13f, color = Color.parseColor("#F87171"))); continue }
            if (!on) { playersBox.addView(label(getString(R.string.films_waiting), 13f, color = grey)); continue }
            playersBox.addView(label(st!!.optString("title"), 14f))
            val dur = st.optDouble("duration", 0.0); val pos = st.optDouble("pos", 0.0)
            playersBox.addView(SeekBar(this).apply {
                max = maxOf(1, dur.toInt()); progress = pos.toInt(); isEnabled = dur > 0
                setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                    override fun onProgressChanged(s: SeekBar?, v: Int, user: Boolean) {}
                    override fun onStartTrackingTouch(s: SeekBar?) { ui.removeCallbacks(tick) }
                    override fun onStopTrackingTouch(s: SeekBar?) { cmd(pid, JSONObject().put("cmd", "seek").put("to", s!!.progress)); ui.postDelayed(tick, 3000) }
                })
            })
            playersBox.addView(label(time(pos) + " / " + time(dur), 12f, color = grey))
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            fun btn(t: String, act: () -> Unit) = row.addView(Button(this).apply { text = t; isAllCaps = false; setOnClickListener { act() } },
                LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            btn("\u221230") { cmd(pid, JSONObject().put("cmd", "seek").put("by", -30)) }
            btn(getString(if (st.optBoolean("pause")) R.string.films_go_on else R.string.films_pause)) { cmd(pid, JSONObject().put("cmd", "pause")) }
            btn("+30") { cmd(pid, JSONObject().put("cmd", "seek").put("by", 30)) }
            btn(getString(R.string.films_stop)) { cmd(pid, JSONObject().put("cmd", "stop")) }
            playersBox.addView(row)
            val row2 = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
            row2.addView(Button(this).apply { text = "\u2212"; setOnClickListener { cmd(pid, JSONObject().put("cmd", "volume").put("by", -5)) } })
            row2.addView(label(getString(R.string.films_volume, st.optDouble("volume", 0.0).toInt().toString()), 13f, color = grey).apply { setPadding(dp(8), 0, dp(8), 0) })
            row2.addView(Button(this).apply { text = "+"; setOnClickListener { cmd(pid, JSONObject().put("cmd", "volume").put("by", 5)) } })
            val w = p.optJSONObject("watching")
            if (w != null && w.has("episode") && !w.isNull("episode"))
                row2.addView(Button(this).apply { text = getString(R.string.films_next); isAllCaps = false; setOnClickListener { cmd(pid, JSONObject(), "/next") } })
            playersBox.addView(row2)
        }
    }
}
