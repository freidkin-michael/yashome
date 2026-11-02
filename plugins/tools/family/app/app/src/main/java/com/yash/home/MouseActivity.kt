package com.yash.home

import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import kotlin.math.hypot
import kotlin.math.min

/** Touchpad mouse for a player (a media player, a PC): the same UDP receivers as the wall panels; the list of players
 *  comes from /api/touchmouse (plug-in touchmouse). The pad ("m dx dy", "b l|r|m 1|0", "w n") fills the screen; under it
 *  one row: rewind, Play/Pause, fast forward (the arrow keys: a player seeks) and, on the right, the computer's own volume
 *  ("k voldown|mute|volup"). */
class MouseActivity : AppCompatActivity() {
    private val gain = 2.5f
    private val accel = 8f
    private val scrollPx = 24f
    private var targets = listOf<JSONObject>()
    private var target: JSONObject? = null
    private lateinit var picker: Spinner
    private lateinit var hint: TextView
    private val net = HandlerThread("mouse").apply { start() }
    private val h = Handler(net.looper)
    private var sock: DatagramSocket? = null

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.mouse_title)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(12), dp(8), dp(12), dp(12)) }
        root.addView(TextView(this).apply { text = getString(R.string.mouse_player); textSize = 13f; setTextColor(Color.parseColor("#8A93A3")) })
        picker = Spinner(this)
        root.addView(picker, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        hint = TextView(this).apply {
            text = getString(R.string.mouse_loading); gravity = Gravity.CENTER; textSize = 16f
            setTextColor(Color.parseColor("#8A93A3"))
            background = GradientDrawable().apply { cornerRadius = dp(20).toFloat(); setColor(Color.parseColor("#1A1F29")) }
        }
        root.addView(hint, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f).apply { topMargin = dp(10); bottomMargin = dp(10) })
        fun key(icon: Int, k: String, mirror: Boolean = false) = ImageButton(this).apply {
            setImageResource(icon); contentDescription = k; if (mirror) scaleX = -1f; setOnClickListener { send("k $k") }
        }
        val bar = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val cell = { LinearLayout.LayoutParams(0, dp(56), 1f).apply { marginStart = dp(3); marginEnd = dp(3) } }
        for (b in listOf(key(R.drawable.ir_rew, "left"), key(R.drawable.ir_play, "playpause"), key(R.drawable.ir_ff, "right")))   // the arrows: a player seeks
            bar.addView(b, cell())
        bar.addView(View(this), LinearLayout.LayoutParams(dp(16), 1))            // the playback keys | the volume
        for (b in listOf(key(R.drawable.ir_vol_down, "voldown"), key(R.drawable.ir_mute, "mute"), key(R.drawable.ir_vol_up, "volup")))
            bar.addView(b, cell())
        root.addView(bar, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        setContentView(root)
        hint.setOnTouchListener(Pad())
        picker.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: AdapterView<*>?, v: View?, pos: Int, id: Long) = choose(targets.getOrNull(pos))
            override fun onNothingSelected(p: AdapterView<*>?) {}
        }
        Thread {
            val list = try {
                val a = HomeApi.touchmouse(this)
                (0 until a.length()).map { a.getJSONObject(it) }
            } catch (e: Exception) { null }
            runOnUiThread { show(list) }
        }.start()
    }

    private fun show(list: List<JSONObject>?) {
        targets = list ?: emptyList()
        if (targets.isEmpty()) { hint.text = getString(R.string.mouse_none); return }
        val names = targets.map { it.optString("name") + " \u00b7 " + if (it.optString("os") == "linux") "Linux" else "Android" }
        picker.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, names)
        val last = getPreferences(MODE_PRIVATE).getString("target", "")
        picker.setSelection(targets.indexOfFirst { it.optString("id") == last }.coerceAtLeast(0))
    }

    private fun choose(t: JSONObject?) {
        target = t ?: return
        getPreferences(MODE_PRIVATE).edit().putString("target", t.optString("id")).apply()
        hint.text = getString(R.string.mouse_hint)
    }

    private fun send(msg: String) {
        val t = target ?: return
        val b = msg.toByteArray(Charsets.US_ASCII)
        h.post {
            try {
                val s = sock ?: DatagramSocket().also { sock = it }
                s.send(DatagramPacket(b, b.size, InetAddress.getByName(t.optString("host")), t.optInt("port")))
            } catch (_: Exception) {
            }
        }
    }

    private inner class Pad : View.OnTouchListener {
        private var lx = 0f; private var ly = 0f; private var fx = 0f; private var fy = 0f; private var sy = 0f
        private var path = 0f; private var fingers = 0; private var t0 = 0L

        override fun onTouch(v: View, e: MotionEvent): Boolean {
            val den = resources.displayMetrics.density
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    t0 = SystemClock.uptimeMillis(); path = 0f; fx = 0f; fy = 0f; sy = 0f; fingers = 1
                    lx = e.x; ly = e.y; v.alpha = 0.85f
                }
                MotionEvent.ACTION_POINTER_DOWN -> {
                    fingers = maxOf(fingers, e.pointerCount)
                    ly = (0 until e.pointerCount).map { e.getY(it) }.average().toFloat()
                }
                MotionEvent.ACTION_MOVE -> if (e.pointerCount >= 2) {
                    // scroll: the content follows the fingers (the mean of all of them)
                    val y = (0 until e.pointerCount).map { e.getY(it) }.average().toFloat()
                    val d = (y - ly) / den
                    ly = y; path += Math.abs(d); sy += d / scrollPx
                    val n = sy.toInt()
                    if (n != 0) { sy -= n; send("w $n") }
                } else {
                    val dx = (e.x - lx) / den; val dy = (e.y - ly) / den
                    lx = e.x; ly = e.y
                    val d = hypot(dx, dy); val g = gain * (1 + min(2f, d / accel))
                    path += d; fx += dx * g; fy += dy * g
                    val ix = fx.toInt(); val iy = fy.toInt()
                    if (ix != 0 || iy != 0) { fx -= ix; fy -= iy; send("m $ix $iy") }
                }
                MotionEvent.ACTION_POINTER_UP -> {
                    val rest = (0 until e.pointerCount).filter { it != e.actionIndex }
                    lx = e.getX(rest[0]); ly = rest.map { e.getY(it) }.average().toFloat()
                }
                MotionEvent.ACTION_UP -> {
                    v.alpha = 1f
                    if (SystemClock.uptimeMillis() - t0 < 250 && path < 12f) {
                        val b = if (fingers >= 3) "m" else if (fingers == 2) "r" else "l"
                        send("b $b 1"); send("b $b 0")
                    }
                }
            }
            return true
        }
    }

    override fun onDestroy() {
        h.post { sock?.close(); net.quitSafely() }
        super.onDestroy()
    }
}
