package com.yash.home

import android.graphics.drawable.Icon
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import androidx.appcompat.app.AlertDialog

/** Quick-settings tile for the lock. Unlocking needs the phone unlocked first (unlockAndRun) and a confirm. */
class LockTileService : TileService() {
    @Volatile private var target: Lock.Target? = null
    @Volatile private var locked: Boolean? = null

    override fun onStartListening() {
        draw(getString(R.string.lock_title), "\u2026", null, Tile.STATE_UNAVAILABLE)
        Thread {
            try {
                val t = Lock.target(this) ?: return@Thread draw(getString(R.string.lock_title), getString(R.string.lock_none), null, Tile.STATE_UNAVAILABLE)
                val s = Lock.state(this, t)
                target = t; locked = s.locked
                draw(t.name, Lock.words(this, s), s.locked, if (s.locked == true) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE)
            } catch (e: Exception) {
                draw(getString(R.string.lock_title), getString(R.string.lock_offline), null, Tile.STATE_UNAVAILABLE)
            }
        }.start()
    }

    override fun onClick() {
        val t = target ?: return
        if (locked == false) return send(t, true)
        unlockAndRun {
            showDialog(AlertDialog.Builder(this, R.style.Theme_Yash)
                .setTitle(R.string.unlock_title).setMessage(getString(R.string.unlock_text, t.name))
                .setPositiveButton(R.string.unlock_ok) { _, _ -> send(t, false) }
                .setNegativeButton(android.R.string.cancel, null).create())
        }
    }

    private fun send(t: Lock.Target, lock: Boolean) = Thread {
        try { Lock.set(this, t, lock) } catch (_: Exception) {}
        Thread.sleep(2500)
        Lock.refreshSurfaces(this)
        onStartListening()
    }.start()

    private fun draw(label: String, sub: String, isLocked: Boolean?, st: Int) {
        val tile = qsTile ?: return
        tile.label = label
        if (android.os.Build.VERSION.SDK_INT >= 29) tile.subtitle = sub
        tile.icon = Icon.createWithResource(this, if (isLocked == false) R.drawable.ic_lock_open else R.drawable.ic_lock_closed)
        tile.state = st
        tile.updateTile()
    }
}
