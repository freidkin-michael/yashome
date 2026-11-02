package com.yash.home

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Context
import org.json.JSONObject

/** House-pushed state for the home-screen widgets, and the list of channels the house should push. */
object WidgetState {
    /** values = {"<dev>": {"<code>": value, ..., "online": bool}}: redraws every widget showing those devices. */
    fun apply(ctx: Context, values: JSONObject, t: Long = 0L) {
        val app = ctx.applicationContext
        if (t > 0L) {                               // the house's time: a late, older push never overwrites newer
            val sp = app.getSharedPreferences("device_widgets", Context.MODE_PRIVATE)
            val old = values.keys().asSequence().filter { sp.getLong("t.$it", 0L) > t }.toList()
            old.forEach { values.remove(it) }
            val e = sp.edit()
            values.keys().forEach { e.putLong("t.$it", t) }
            e.apply()
        }
        for (id in DeviceWidget.ids(app)) {
            if (DeviceWidget.isBusy(id)) continue
            val pk = DeviceWidget.pick(app, id) ?: continue
            val upd = values.optJSONObject(pk.dev) ?: continue
            if (DeviceWidget.merge(app, id, pk, upd)) DeviceWidget.draw(app, id)
        }
        val lock = Lock.cachedDev(app) ?: return
        if (!values.has(lock)) return
        val am = AppWidgetManager.getInstance(app)
        val ids = am.getAppWidgetIds(ComponentName(app, LockWidget::class.java))
        if (ids.isNotEmpty()) LockWidget.update(app, am, ids)
    }

    /** PUT the watched channels to the house; a failure is fine, the periodic update stays.
     *  force = false (a process start, e.g. woken by a push) skips the same list sent within an hour. */
    fun publish(ctx: Context, force: Boolean = true) {
        val app = ctx.applicationContext
        Thread {
            try {
                if (!HomeApi.configured(app)) return@Thread
                val items = DeviceWidget.watched(app)
                val sp = app.getSharedPreferences("device_widgets", Context.MODE_PRIVATE)
                val now = System.currentTimeMillis()
                if (!force && sp.getString("watch_sent", null) == items.toString() && now - sp.getLong("watch_at", 0L) < 3_600_000L) return@Thread
                HomeApi.putWidgets(app, items)
                sp.edit().putString("watch_sent", items.toString()).putLong("watch_at", now).apply()
            } catch (e: Exception) {
                android.util.Log.w("YashWidgets", "watched channels not sent: $e")
                Prefs.logEvent(app, "widgets: not sent to the house (${e.javaClass.simpleName})")
            }
        }.start()
    }
}
