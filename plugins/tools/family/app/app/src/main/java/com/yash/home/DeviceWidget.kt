package com.yash.home

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.SystemClock
import android.widget.RemoteViews
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.roundToInt

/** Universal 1x1 "Channel" widget: switch, lock, dimmer or air conditioner, with a picked icon and name. */
class DeviceWidget : AppWidgetProvider() {
    override fun onUpdate(ctx: Context, am: AppWidgetManager, ids: IntArray) = refreshAll(ctx, ids)

    override fun onDeleted(ctx: Context, ids: IntArray) {
        ids.forEach { forget(ctx, it) }
        WidgetState.publish(ctx)
    }

    override fun onReceive(ctx: Context, intent: Intent) {
        super.onReceive(ctx, intent)
        val act = intent.action ?: return
        if (act != ACTION_TOGGLE && act != ACTION_UP && act != ACTION_DOWN) return
        val id = intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
        val pending = goAsync()
        Thread { try { tap(ctx.applicationContext, id, act) } catch (_: Exception) {} finally { pending.finish() } }.start()
    }

    /** code = the on/off switch: lock bolt, the air conditioner's power, the dimmer's paired switch ("" when it has none). */
    data class Pick(val dev: String, val code: String, val label: String, val payloadOn: String,
                    val kind: String = "switch", val icon: String = "", val bright: String = "",
                    val min: Double = 0.0, val max: Double = 100.0, val mode: String = "") {
        fun codes() = listOf(code, bright, mode).filter { it.isNotEmpty() }
    }

    companion object {
        private const val ACTION_TOGGLE = "com.yash.home.DEVICE_TOGGLE"
        private const val ACTION_UP = "com.yash.home.DEVICE_UP"
        private const val ACTION_DOWN = "com.yash.home.DEVICE_DOWN"
        const val KIND_SWITCH = "switch"
        const val KIND_LOCK = "lock"
        const val KIND_DIMMER = "dimmer"
        const val KIND_AC = "ac"
        private const val PI_FLAGS = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT

        val ICONS = listOf("lamp", "plug", "fan", "ac", "lock", "door", "gate", "boiler", "tv", "water", "garage", "power")
        private val PAIRS = mapOf(
            "lamp" to (R.drawable.wi_lamp_on to R.drawable.wi_lamp_off),
            "plug" to (R.drawable.wi_plug_on to R.drawable.wi_plug_off),
            "fan" to (R.drawable.wi_fan_on to R.drawable.wi_fan_off),
            "ac" to (R.drawable.wi_ac_on to R.drawable.wi_ac_off),
            "lock" to (R.drawable.ic_lock_closed to R.drawable.ic_lock_open),
            "door" to (R.drawable.wi_door_open to R.drawable.wi_door_closed),
            "gate" to (R.drawable.wi_gate_open to R.drawable.wi_gate_closed),
            "boiler" to (R.drawable.wi_boiler_on to R.drawable.wi_boiler_off),
            "tv" to (R.drawable.wi_tv_on to R.drawable.wi_tv_off),
            "water" to (R.drawable.wi_water_on to R.drawable.wi_water_off),
            "garage" to (R.drawable.wi_garage_open to R.drawable.wi_garage_closed),
            "power" to (R.drawable.wi_power_on to R.drawable.wi_power_off))
        // pairs are (active, idle); a locked lock shows these closed
        private val OPENISH = setOf("door", "gate", "garage")
        // drawn in the dashboard's dark text colour: tinted white on the widget
        val TINTED = setOf(R.drawable.ac_fan, R.drawable.ac_auto)
        private val MODES = mapOf("cool" to R.drawable.ac_cool, "heat" to R.drawable.ac_heat,
            "dry" to R.drawable.ac_dry, "fan" to R.drawable.ac_fan, "auto" to R.drawable.ac_auto)
        private val HINTS = listOf(listOf("lamp", "light") to "lamp", listOf("boiler", "heater") to "boiler",
            listOf("tv") to "tv", listOf("fan") to "fan", listOf("plug", "socket") to "plug", listOf("gate") to "gate",
            listOf("garage") to "garage", listOf("conditioner", "aircon") to "ac", listOf("door") to "door", listOf("water", "valve", "tap") to "water")

        fun defaultIcon(kind: String, label: String): String = when (kind) {
            KIND_LOCK -> "lock"; KIND_DIMMER -> "lamp"; KIND_AC -> "ac"
            else -> label.lowercase().let { l -> HINTS.firstOrNull { (w, _) -> w.any { it in l } }?.second ?: "power" }
        }

        fun iconRes(key: String, kind: String, on: Boolean): Int {
            val (active, idle) = PAIRS[key] ?: PAIRS.getValue("power")
            return if (if (kind == KIND_LOCK && key in OPENISH) !on else on) active else idle
        }

        private fun p(ctx: Context) = ctx.getSharedPreferences("device_widgets", Context.MODE_PRIVATE)
        private val KEYS = listOf("dev", "code", "label", "on_payload", "last", "kind", "icon", "bright", "bmin", "bmax", "mode", "state")

        fun ids(ctx: Context): IntArray =
            AppWidgetManager.getInstance(ctx).getAppWidgetIds(ComponentName(ctx, DeviceWidget::class.java))

        fun save(ctx: Context, id: Int, pick: Pick) {
            val e = p(ctx).edit()
            for (k in KEYS) e.remove("$id.$k")
            e.putString("$id.dev", pick.dev).putString("$id.code", pick.code).putString("$id.label", pick.label)
                .putString("$id.on_payload", pick.payloadOn).putString("$id.kind", pick.kind).putString("$id.icon", pick.icon)
                .putString("$id.bright", pick.bright).putString("$id.bmin", pick.min.toString())
                .putString("$id.bmax", pick.max.toString()).putString("$id.mode", pick.mode).apply()
        }

        fun pick(ctx: Context, id: Int): Pick? {
            val sp = p(ctx)
            val dev = sp.getString("$id.dev", null) ?: return null
            fun s(k: String, def: String = "") = sp.getString("$id.$k", def) ?: def
            return Pick(dev, s("code"), s("label", dev), s("on_payload"), s("kind", KIND_SWITCH), s("icon"), s("bright"),
                s("bmin").toDoubleOrNull() ?: 0.0, s("bmax").toDoubleOrNull() ?: 100.0, s("mode"))
        }

        private fun forget(ctx: Context, id: Int) {
            val e = p(ctx).edit()
            for (k in KEYS) e.remove("$id.$k")
            e.apply()
        }

        /** {"v": {code: value}, "online": bool, "reach": bool}; widgets placed before it had only "last". */
        private fun cache(ctx: Context, id: Int): JSONObject {
            p(ctx).getString("$id.state", null)?.let { s -> try { return JSONObject(s) } catch (_: Exception) {} }
            val v = JSONObject()
            val last = p(ctx).getString("$id.last", "")
            pick(ctx, id)?.let { if (last == "true" || last == "false") v.put(it.code, last == "true") }
            return JSONObject().put("v", v)
        }

        private fun store(ctx: Context, id: Int, c: JSONObject) = p(ctx).edit().putString("$id.state", c.toString()).apply()

        private fun values(c: JSONObject): JSONObject = c.optJSONObject("v") ?: JSONObject().also { c.put("v", it) }

        /** Merges one device's {"<code>": value, ..., "online": bool}; true when it touched this widget. */
        fun merge(ctx: Context, id: Int, pk: Pick, upd: JSONObject): Boolean {
            val c = cache(ctx, id)
            val v = values(c)
            var hit = false
            if (upd.has("online")) { c.put("online", upd.optBoolean("online", true)); hit = true }
            for (code in pk.codes()) if (upd.has(code)) { v.put(code, upd.get(code)); hit = true }
            if (hit) { c.put("reach", true); store(ctx, id, c) }
            return hit
        }

        private fun local(ctx: Context, id: Int, code: String, value: Any) {
            val c = cache(ctx, id); values(c).put(code, value); store(ctx, id, c)
        }

        private fun reach(ctx: Context, id: Int, ok: Boolean) {
            val c = cache(ctx, id); c.put("reach", ok); store(ctx, id, c)
            if (!ok) Lock.markStale(ctx, true)
        }

        private fun fetch(ctx: Context, dev: String): JSONObject {
            val st = JSONObject(HomeApi.status(ctx, dev))
            val out = JSONObject(st.optJSONObject("values")?.toString() ?: "{}")
            return out.put("online", if (st.has("online")) st.optBoolean("online", true) else true)
        }

        private fun truth(x: Any?, payloadOn: String): Boolean? = when (x) {
            is Boolean -> x
            is Number -> x.toDouble() != 0.0
            is String -> x.equals(payloadOn, true) || x.lowercase() in setOf("on", "true", "1")
            else -> null
        }

        private fun percent(pk: Pick, v: JSONObject): Int? {
            val d = when (val x = v.opt(pk.bright)) { is Number -> x.toDouble(); is String -> x.toDoubleOrNull(); else -> null } ?: return null
            val span = (pk.max - pk.min).takeIf { it != 0.0 } ?: 1.0
            return (((d - pk.min) / span) * 100).roundToInt().coerceIn(0, 100)
        }

        private fun onOf(pk: Pick, v: JSONObject): Boolean? =
            if (pk.kind == KIND_DIMMER && pk.code.isEmpty()) percent(pk, v)?.let { it > 0 } else truth(v.opt(pk.code), pk.payloadOn)

        private fun bcast(ctx: Context, id: Int, act: String) = PendingIntent.getBroadcast(ctx, id,
            Intent(ctx, DeviceWidget::class.java).setAction(act).setData(Uri.parse("yash-widget://$id/$act"))
                .putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id), PI_FLAGS)

        fun draw(ctx: Context, id: Int) {
            val am = AppWidgetManager.getInstance(ctx)
            val pk = pick(ctx, id)
            if (pk == null) {
                val tap = PendingIntent.getActivity(ctx, id, Intent(ctx, DeviceWidgetConfig::class.java)
                    .putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, id).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PI_FLAGS)
                am.updateAppWidget(id, RemoteViews(ctx.packageName, R.layout.widget_device).apply {
                    setTextViewText(R.id.w_label, ctx.getString(R.string.device_widget_pick))
                    setImageViewResource(R.id.w_icon, R.drawable.wi_power_off)
                    setInt(R.id.w_icon, "setColorFilter", 0)
                    setInt(R.id.w_icon, "setImageAlpha", 110)
                    setInt(R.id.w_root, "setBackgroundResource", R.drawable.widget_bg_off)
                    setOnClickPendingIntent(R.id.w_root, tap)
                })
                return
            }
            val c = cache(ctx, id)
            val v = values(c)
            val live = c.optBoolean("online", true) && c.optBoolean("reach", true)
            val on = if (live) onOf(pk, v) else null
            val key = pk.icon.ifEmpty { defaultIcon(pk.kind, pk.label) }
            val icon = if (pk.kind == KIND_AC && key == "ac")
                (if (on == false) R.drawable.wi_ac_off else MODES[v.optString(pk.mode)] ?: R.drawable.wi_ac_on)
            else iconRes(key, pk.kind, on ?: (pk.kind == KIND_LOCK))
            val bg = when {
                on == null -> R.drawable.widget_bg_off
                pk.kind == KIND_LOCK -> if (on) R.drawable.widget_bg else R.drawable.widget_bg_open
                on -> R.drawable.widget_bg
                else -> R.drawable.widget_bg_off
            }
            val dimmer = pk.kind == KIND_DIMMER
            val views = RemoteViews(ctx.packageName, if (dimmer) R.layout.widget_dimmer else R.layout.widget_device).apply {
                setTextViewText(R.id.w_label, pk.label)
                setImageViewResource(R.id.w_icon, icon)
                setInt(R.id.w_icon, "setColorFilter", if (icon in TINTED) Color.WHITE else 0)
                setInt(R.id.w_icon, "setImageAlpha", if (on == null) 110 else 255)
                setInt(R.id.w_root, "setBackgroundResource", bg)
            }
            if (dimmer) {
                val pct = percent(pk, v)
                views.setTextViewText(R.id.w_pct, when (on) {
                    true -> pct?.let { "$it%" } ?: ""; false -> ctx.getString(R.string.device_widget_off); null -> "" })
                views.setOnClickPendingIntent(R.id.w_up, bcast(ctx, id, ACTION_UP))
                views.setOnClickPendingIntent(R.id.w_mid, bcast(ctx, id, ACTION_TOGGLE))
                views.setOnClickPendingIntent(R.id.w_down, bcast(ctx, id, ACTION_DOWN))
            } else if (pk.kind == KIND_LOCK) {
                views.setOnClickPendingIntent(R.id.w_root, PendingIntent.getActivity(ctx, id,
                    Intent(ctx, LockActionActivity::class.java).setData(Uri.parse("yash-widget://$id/lock"))
                        .putExtra(LockActionActivity.EXTRA_DEV, pk.dev).putExtra(LockActionActivity.EXTRA_CODE, pk.code)
                        .putExtra(LockActionActivity.EXTRA_NAME, pk.label).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PI_FLAGS))
            } else views.setOnClickPendingIntent(R.id.w_root, bcast(ctx, id, ACTION_TOGGLE))
            am.updateAppWidget(id, views)
        }

        /** Draws from the cache at once, then reads each shown device once and redraws. */
        fun refreshAll(ctx: Context, which: IntArray = ids(ctx)) {
            val app = ctx.applicationContext
            which.forEach { draw(app, it) }
            Thread {
                val byDev = which.toList().mapNotNull { id -> pick(app, id)?.let { p -> p.dev to id } }.groupBy({ it.first }, { it.second })
                for ((dev, ws) in byDev) {
                    val upd = try { fetch(app, dev) } catch (_: Exception) { null }
                    for (id in ws) {
                        val pk = pick(app, id) ?: continue
                        if (upd == null) reach(app, id, false) else if (!merge(app, id, pk, upd)) reach(app, id, true)
                        draw(app, id)
                    }
                }
            }.start()
        }

        // a tap in flight: a pushed state must not undo the optimistic picture
        private val busy = ConcurrentHashMap<Int, Long>()
        fun isBusy(id: Int) = (busy[id] ?: 0L) > SystemClock.elapsedRealtime()
        private fun hold(id: Int, ms: Long) { busy[id] = SystemClock.elapsedRealtime() + ms }

        private fun num(d: Double): Any = if (d % 1.0 == 0.0) d.toLong() else d

        private fun tap(ctx: Context, id: Int, act: String) {
            val pk = pick(ctx, id) ?: return
            if (act == ACTION_UP || act == ACTION_DOWN) step(ctx, id, pk, act == ACTION_UP) else toggle(ctx, id, pk)
        }

        /** Flip at once on screen, send, then poll until the device reports it. Without a paired
         *  switch a dimmer goes on by "up" and off by 0 (percent, as the house takes it). */
        private fun toggle(ctx: Context, id: Int, pk: Pick) {
            try { merge(ctx, id, pk, fetch(ctx, pk.dev)) } catch (_: Exception) {}
            val want = !(onOf(pk, values(cache(ctx, id))) ?: false)
            hold(id, 12_000)
            val noSwitch = pk.kind == KIND_DIMMER && pk.code.isEmpty()
            val (code, value) = if (noSwitch) pk.bright to (if (want) "up" else 0) else pk.code to want
            if (!noSwitch) local(ctx, id, code, want) else if (!want) local(ctx, id, code, num(pk.min))
            draw(ctx, id)
            try { HomeApi.control(ctx, pk.dev, code, value) } catch (_: Exception) {
                busy.remove(id); reach(ctx, id, false); draw(ctx, id)
                if (pk.kind != KIND_LOCK) ControlRetryWorker.enqueue(ctx, pk.dev, code, value)          // sent again when the network is back
                return
            }
            var last: JSONObject? = null
            for (i in 0 until 8) {
                Thread.sleep(1000)
                last = try { fetch(ctx, pk.dev) } catch (_: Exception) { null } ?: continue
                if (onOf(pk, last) == want) break
            }
            busy.remove(id)
            if (last != null) merge(ctx, id, pk, last) else reach(ctx, id, false)
            draw(ctx, id)
        }

        /** Dimmer top/bottom zone: the house steps a tenth; "down" at the floor switches it off. */
        private fun step(ctx: Context, id: Int, pk: Pick, up: Boolean) {
            if (pk.bright.isEmpty()) return
            val v = values(cache(ctx, id))
            val on = onOf(pk, v)
            val pct = percent(pk, v)
            val next = when {
                up && on == true -> (pct ?: 0) + 10
                up -> maxOf(pct ?: 0, 10)
                on == true -> (pct ?: 0) - 10
                else -> null
            }?.coerceIn(0, 100)
            hold(id, 2500)
            if (next != null) {
                local(ctx, id, pk.bright, num(pk.min + (pk.max - pk.min) * next / 100.0))
                if (pk.code.isNotEmpty()) local(ctx, id, pk.code, up || next > 0)
                draw(ctx, id)
            }
            try { HomeApi.control(ctx, pk.dev, pk.bright, if (up) "up" else "down") } catch (_: Exception) {
                busy.remove(id); reach(ctx, id, false); draw(ctx, id); return
            }
            Thread.sleep(3000)
            try { merge(ctx, id, pk, fetch(ctx, pk.dev)) } catch (_: Exception) {}
            draw(ctx, id)
        }

        /** Every channel the placed widgets need, as [{"dev","code"}], each once. */
        fun watched(ctx: Context): JSONArray {
            val seen = LinkedHashSet<Pair<String, String>>()
            for (id in ids(ctx)) { val pk = pick(ctx, id) ?: continue; pk.codes().forEach { seen += pk.dev to it } }
            return JSONArray().apply { seen.forEach { (d, c) -> put(JSONObject().put("dev", d).put("code", c)) } }
        }
    }
}
