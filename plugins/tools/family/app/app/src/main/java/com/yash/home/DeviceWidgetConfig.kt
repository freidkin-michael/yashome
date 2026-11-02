package com.yash.home

import android.appwidget.AppWidgetManager
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.AbsListView
import android.widget.ArrayAdapter
import android.widget.BaseAdapter
import android.widget.EditText
import android.widget.GridView
import android.widget.ImageView
import android.widget.ListView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject

/** Picks the channel, short name and icon of a DeviceWidget: switches, locks, dimmers, air conditioners. */
class DeviceWidgetConfig : AppCompatActivity() {
    private var id = AppWidgetManager.INVALID_APPWIDGET_ID

    private class Entry(val pick: DeviceWidget.Pick, val shown: String)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        id = intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
        setResult(RESULT_CANCELED, Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id))
        if (id == AppWidgetManager.INVALID_APPWIDGET_ID) return finish()
        if (!HomeApi.configured(this)) { Toast.makeText(this, R.string.device_widget_signin, Toast.LENGTH_LONG).show(); return finish() }
        title = getString(R.string.device_widget_pick)
        val list = ListView(this)
        setContentView(list)
        Thread {
            val picks = try { channels() } catch (e: Exception) { null }
            runOnUiThread {
                if (picks.isNullOrEmpty()) {
                    Toast.makeText(this, if (picks == null) R.string.lock_offline else R.string.device_widget_none, Toast.LENGTH_LONG).show()
                    finish(); return@runOnUiThread
                }
                list.adapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, picks.map { it.shown })
                list.setOnItemClickListener { _, _, pos, _ -> caption(picks[pos].pick) }
            }
        }.start()
    }

    private fun channels(): List<Entry> {
        val out = mutableListOf<Entry>()
        val devs = HomeApi.devices(this)
        for (i in 0 until devs.length()) {
            val d = devs.getJSONObject(i)
            val c = d.optJSONArray("controls") ?: continue
            val rw = (0 until c.length()).map { c.getJSONObject(it) }.filter { !it.optBoolean("ro") }
            val dev = d.getString("id")
            val name = d.optString("name").ifEmpty { dev }
            val sw = rw.filter { it.optString("kind") == "switch" }
            val mode = rw.firstOrNull { it.optString("kind") == "setting_enum" && it.optString("code") == "mode" }
            val power = sw.firstOrNull { it.optString("code") == "power" } ?: sw.singleOrNull()
            if (mode != null && power != null) {
                out += Entry(DeviceWidget.Pick(dev, power.optString("code"), name, power.optString("payload_on"),
                    kind = DeviceWidget.KIND_AC, mode = "mode"), "$name \u00b7 ${getString(R.string.device_widget_mark_ac)}")
            }
            val plain = sw.filter { it.optString("payload_on") != "LOCKED" }
            val brights = rw.filter { it.optString("kind") == "bright" }
            for (b in brights) {
                val s = pairSwitch(plain, b.optString("code"))
                val chan = if (brights.size > 1) " \u00b7 ${b.optString("label").ifEmpty { b.optString("code") }}" else ""
                out += Entry(DeviceWidget.Pick(dev, s?.optString("code") ?: "", name, s?.optString("payload_on") ?: "",
                    kind = DeviceWidget.KIND_DIMMER, bright = b.optString("code"),
                    min = b.optDouble("min", 0.0), max = b.optDouble("max", 100.0)),
                    "$name$chan \u00b7 ${getString(R.string.device_widget_mark_dimmer)}")
            }
            for (ctl in sw) {
                val lock = ctl.optString("payload_on") == "LOCKED"
                val chan = if (sw.size > 1) " \u00b7 ${ctl.optString("label").ifEmpty { ctl.optString("code") }}" else ""
                val mark = if (lock) " \u00b7 ${getString(R.string.device_widget_mark_lock)}" else ""
                out += Entry(DeviceWidget.Pick(dev, ctl.optString("code"), name, ctl.optString("payload_on"),
                    kind = if (lock) DeviceWidget.KIND_LOCK else DeviceWidget.KIND_SWITCH), "$name$chan$mark")
            }
        }
        return out.sortedBy { it.shown.lowercase() }
    }

    /** A dimmer's on/off, the house's rule (app.py _switch_for): the switch on the same gang -
     *  "brightness_l2" pairs "state_l2", "brightness" pairs "state"; none = no paired switch. */
    private fun pairSwitch(sw: List<JSONObject>, bright: String): JSONObject? =
        sw.firstOrNull { chanOf(it.optString("code")) == chanOf(bright) }

    private fun chanOf(code: String): String {
        for (base in listOf("state", "brightness")) if (code.startsWith(base)) return code.removePrefix(base).trimStart('_')
        return ""
    }

    private fun caption(pick: DeviceWidget.Pick) {
        val edit = EditText(this).apply { setText(pick.label); setSelection(text.length) }
        AlertDialog.Builder(this).setTitle(R.string.device_widget_caption).setView(edit)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                icon(pick.copy(label = edit.text.toString().trim().ifEmpty { pick.label }))
            }
            .setNegativeButton(android.R.string.cancel, null).show()
    }

    private fun icon(pick: DeviceWidget.Pick) {
        val keys = DeviceWidget.ICONS
        val def = DeviceWidget.defaultIcon(pick.kind, pick.label)
        val grid = GridView(this).apply {
            numColumns = 4; verticalSpacing = dp(8); horizontalSpacing = dp(8); setPadding(dp(16), dp(12), dp(16), dp(4))
        }
        grid.adapter = object : BaseAdapter() {
            override fun getCount() = keys.size
            override fun getItem(pos: Int): Any = keys[pos]
            override fun getItemId(pos: Int) = pos.toLong()
            override fun getView(pos: Int, convert: View?, parent: ViewGroup): View {
                val iv = convert as? ImageView ?: ImageView(this@DeviceWidgetConfig).apply {
                    layoutParams = AbsListView.LayoutParams(dp(56), dp(56))
                    setPadding(dp(14), dp(14), dp(14), dp(14))
                    scaleType = ImageView.ScaleType.FIT_CENTER
                }
                val res = DeviceWidget.iconRes(keys[pos], pick.kind, true)
                iv.setImageResource(res)
                if (res in DeviceWidget.TINTED) iv.setColorFilter(Color.WHITE) else iv.clearColorFilter()
                iv.setBackgroundResource(if (keys[pos] == def) R.drawable.widget_bg_open else R.drawable.widget_bg)
                iv.contentDescription = keys[pos]
                return iv
            }
        }
        val dlg = AlertDialog.Builder(this).setTitle(R.string.device_widget_icon).setView(grid)
            .setOnCancelListener { finishWith(pick.copy(icon = def)) }.create()
        grid.setOnItemClickListener { _, _, pos, _ ->
            dlg.setOnCancelListener(null); dlg.dismiss(); finishWith(pick.copy(icon = keys[pos]))
        }
        dlg.show()
    }

    private fun finishWith(pick: DeviceWidget.Pick) {
        DeviceWidget.save(this, id, pick)
        DeviceWidget.refreshAll(applicationContext, intArrayOf(id))
        WidgetState.publish(this)
        setResult(RESULT_OK, Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id))
        finish()
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
}
