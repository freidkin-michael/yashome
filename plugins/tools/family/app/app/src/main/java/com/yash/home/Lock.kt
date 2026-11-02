package com.yash.home

import android.content.ComponentName
import android.content.Context
import android.service.quicksettings.TileService
import org.json.JSONObject

/** The house lock for the quick tile: found once (a switch with payload LOCKED), then cached. */
object Lock {
    data class Target(val dev: String, val code: String, val name: String)
    data class State(val locked: Boolean?, val door: String?, val online: Boolean)

    private fun p(ctx: Context) = ctx.getSharedPreferences("yash", Context.MODE_PRIVATE)

    fun cachedDev(ctx: Context): String? = p(ctx).getString("lock_dev", null)

    fun target(ctx: Context): Target? {
        p(ctx).getString("lock_dev", null)?.let { return Target(it, p(ctx).getString("lock_code", "locked")!!, p(ctx).getString("lock_name", it)!!) }
        if (!HomeApi.configured(ctx)) return null
        val devs = HomeApi.devices(ctx)
        for (i in 0 until devs.length()) {
            val d = devs.getJSONObject(i)
            val c = d.optJSONArray("controls") ?: continue
            for (j in 0 until c.length()) {
                val ctl = c.getJSONObject(j)
                if (ctl.optString("kind") == "switch" && ctl.optString("payload_on") == "LOCKED") {
                    p(ctx).edit().putString("lock_dev", d.getString("id")).putString("lock_code", ctl.optString("code"))
                        .putString("lock_name", d.optString("name")).apply()
                    return Target(d.getString("id"), ctl.optString("code"), d.optString("name"))
                }
            }
        }
        return null
    }

    fun state(ctx: Context, t: Target): State {
        val st = JSONObject(HomeApi.status(ctx, t.dev))
        val v = st.optJSONObject("values") ?: JSONObject()
        val locked = when (val x = v.opt(t.code)) { is Boolean -> x; is String -> x.equals("LOCKED", true); else -> null }
        return State(locked, v.optString("door").ifEmpty { null }, st.optBoolean("online"))
            .also { p(ctx).edit().putString("lock_last", if (locked == null) "" else locked.toString()).apply() }
    }

    fun set(ctx: Context, t: Target, lock: Boolean) = HomeApi.control(ctx, t.dev, t.code, lock)

    /** A widget that could not reach the house says so; the next pull, tap or 15-minute refresh reads the house again until one draw works (the house may have restarted). */
    fun markStale(ctx: Context, stale: Boolean) = p(ctx).edit().putBoolean("widgets_stale", stale).apply()
    fun stale(ctx: Context) = p(ctx).getBoolean("widgets_stale", false)

    /** Ask the widgets and the tile to redraw after any change we made. */
    fun refreshSurfaces(ctx: Context) {
        markStale(ctx, false)                               // a draw that fails again sets it
        DeviceWidget.refreshAll(ctx)
        TileService.requestListeningState(ctx, ComponentName(ctx, LockTileService::class.java))
    }

    fun words(ctx: Context, s: State): String {
        val main = when (s.locked) { true -> ctx.getString(R.string.lock_locked); false -> ctx.getString(R.string.lock_open); null -> "?" }
        // bolt only: without the lock's door sensor set up its door bit stays "open"; the dashboard still shows it
        return if (!s.online) ctx.getString(R.string.lock_offline) else main
    }
}
