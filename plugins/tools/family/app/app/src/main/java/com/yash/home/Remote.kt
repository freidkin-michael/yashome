package com.yash.home

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject

/**
 * An IR appliance drawn as a real remote: the same slots as remoteHtml() in panel-widgets.js
 * (power row, 3x3 d-pad, volume, channels, media, colours, the rest as chips), icons from IR_ICON.
 */
object Remote {
    data class Key(val code: String, val label: String)

    private var names: Map<String, String>? = null

    private fun names(ctx: Context): Map<String, String> = names ?: run {
        val j = JSONObject(ctx.assets.open("ir_names.i18n.json").bufferedReader().readText())
        val m = mutableMapOf<String, String>()
        for (lang in j.keys()) {
            val t = j.getJSONObject(lang)
            for (k in t.keys()) m[k] = t.getString(k)
        }
        m.also { names = it }
    }

    private fun icon(ctx: Context, key: String): Int =
        ctx.resources.getIdentifier("ir_$key", "drawable", ctx.packageName)

    /** Slot id of a button: by its id (ir_<id>) when we have an icon for it, else by its label. */
    fun key(ctx: Context, b: Key): String {
        val id = b.code.removePrefix("ir_")
        if (icon(ctx, id) != 0 || id == "power" || Regex("^d\\d$").matches(id)) return id
        val l = b.label.trim().lowercase()
        return names(ctx)[l] ?: if (Regex("^\\d$").matches(l)) "d$l" else ""
    }

    private fun dropped(k: String) = Regex("^d\\d$").matches(k) || k == "del"

    fun build(ctx: Context, buttons: List<Key>, press: (String, View) -> Unit): View {
        val by = linkedMapOf<String, Key>(); val rest = mutableListOf<Key>()
        for (b in buttons) {
            val k = key(ctx, b)
            if (dropped(k)) continue
            if (k.isNotEmpty() && k !in by) by[k] = b else rest += b
        }
        val box = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL; setPadding(0, dp(ctx, 6), 0, 0) }
        if (by.size < 4) {                               // too few known keys for a remote: plain chips
            chips(ctx, box, by.values.toList() + rest, press)
            return box
        }
        fun has(vararg ks: String) = ks.any { it in by }
        fun take(k: String) = by.remove(k)
        fun row(vararg views: View?) = box.addView(LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER
            views.filterNotNull().forEach { addView(it, LinearLayout.LayoutParams(0, dp(ctx, 52), 1f).apply { setMargins(dp(ctx, 4), dp(ctx, 4), dp(ctx, 4), dp(ctx, 4)) }) }
        })
        fun k(key: String, power: Boolean = false): View =
            take(key)?.let { keyView(ctx, it, key, power, press) } ?: View(ctx).apply { visibility = View.INVISIBLE }

        if (has("power", "on", "off", "source", "hdmi")) {
            val pw = if (has("on", "off")) listOfNotNull(if ("on" in by) k("on", true) else null, if ("off" in by) k("off", true) else null)
                     else listOfNotNull(if ("power" in by) k("power", true) else null)
            row(*(pw + listOfNotNull(if ("source" in by) k("source") else null, if ("hdmi" in by) k("hdmi") else null)).toTypedArray())
        }
        if (has("up", "down", "left", "right", "ok")) {
            row(k("menu"), k("up"), k("home"))
            row(k("left"), k("ok"), k("right"))
            row(k("back"), k("down"), k(if ("exit" in by) "exit" else "info"))
        }
        if (has("vol_up", "vol_down", "mute")) row(k("vol_down"), k("vol_up"), if ("mute" in by) k("mute") else null)
        if (has("ch_up", "ch_down")) row(TextView(ctx).apply { text = "CH"; gravity = Gravity.CENTER; setTextColor(ctx.getColor(R.color.brand_muted)) }, k("ch_down"), k("ch_up"))
        val media = listOf("prev", "rew", "play", "pause", "stop", "ff", "next").filter { it in by }
        if (media.isNotEmpty()) media.chunked(4).forEach { line -> row(*line.map { k(it) }.toTypedArray()) }
        if (has("red", "green", "yellow", "blue")) row(k("red"), k("green"), k("yellow"), k("blue"))
        chips(ctx, box, by.values.toList() + rest, press)
        return box
    }

    private fun keyView(ctx: Context, b: Key, key: String, power: Boolean, press: (String, View) -> Unit): View {
        val res = icon(ctx, key)
        val v: View = if (res != 0) ImageButton(ctx).apply {
            setImageResource(res); scaleType = android.widget.ImageView.ScaleType.CENTER_INSIDE
            setPadding(dp(ctx, 12), dp(ctx, 12), dp(ctx, 12), dp(ctx, 12))
            if (power) setColorFilter(Color.WHITE)
        } else Button(ctx, null, android.R.attr.borderlessButtonStyle).apply {
            text = b.label; isAllCaps = false; setTextColor(if (power) Color.WHITE else ctx.getColor(R.color.brand_text))
        }
        v.background = keyBg(ctx, power)
        v.contentDescription = b.label
        v.setOnClickListener { press(b.code, it) }
        return v
    }

    private fun chips(ctx: Context, box: LinearLayout, keys: List<Key>, press: (String, View) -> Unit) {
        if (keys.isEmpty()) return
        keys.chunked(2).forEach { pair ->
            box.addView(LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL
                pair.forEach { b ->
                    val res = icon(ctx, key(ctx, b))
                    addView(Button(ctx, null, android.R.attr.borderlessButtonStyle).apply {
                        text = b.label; isAllCaps = false; setTextColor(ctx.getColor(R.color.brand_text))
                        if (res != 0) { setCompoundDrawablesRelativeWithIntrinsicBounds(res, 0, 0, 0); compoundDrawablePadding = dp(ctx, 8) }
                        background = keyBg(ctx, false)
                        setOnClickListener { press(b.code, it) }
                    }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { setMargins(dp(ctx, 4), dp(ctx, 4), dp(ctx, 4), dp(ctx, 4)) })
                }
                if (pair.size == 1) addView(View(ctx), LinearLayout.LayoutParams(0, 1, 1f))
            }, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        }
    }

    private fun keyBg(ctx: Context, power: Boolean) = RippleDrawable(
        ColorStateList.valueOf(ctx.getColor(R.color.brand_primary)),
        GradientDrawable().apply {
            cornerRadius = dp(ctx, 14).toFloat()
            setColor(ctx.getColor(if (power) R.color.brand_fill else R.color.rm_key))
        }, null)

    private fun dp(ctx: Context, v: Int) = (v * ctx.resources.displayMetrics.density).toInt()
}
