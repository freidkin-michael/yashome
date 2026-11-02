package com.yash.home

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews

/** 1x1 home-screen lock widget: the icon and colour are the state, a tap opens LockActionActivity (lock at once, unlock after a confirm). */
class LockWidget : AppWidgetProvider() {
    override fun onUpdate(ctx: Context, am: AppWidgetManager, ids: IntArray) = update(ctx, am, ids)

    companion object {
        fun update(ctx: Context, am: AppWidgetManager, ids: IntArray) {
            val tap = PendingIntent.getActivity(ctx, 0, Intent(ctx, LockActionActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            fun draw(title: String, locked: Boolean?) {
                val v = RemoteViews(ctx.packageName, R.layout.widget_lock).apply {
                    setTextViewText(R.id.w_title, title)
                    setImageViewResource(R.id.w_icon, if (locked == false) R.drawable.ic_lock_open else R.drawable.ic_lock_closed)
                    setInt(R.id.w_root, "setBackgroundResource", when (locked) {
                        true -> R.drawable.widget_bg; false -> R.drawable.widget_bg_open; null -> R.drawable.widget_bg_off })
                    setOnClickPendingIntent(R.id.w_root, tap)
                }
                am.updateAppWidget(ids, v)
            }
            draw(ctx.getString(R.string.lock_title), null)
            Thread {
                try {
                    val t = Lock.target(ctx) ?: return@Thread draw(ctx.getString(R.string.lock_none), null)
                    val s = Lock.state(ctx, t)
                    draw(t.name, if (s.online) s.locked else null)
                } catch (e: Exception) {
                    Lock.markStale(ctx, true)
                    draw(ctx.getString(R.string.lock_offline), null)
                }
            }.start()
        }
    }
}
