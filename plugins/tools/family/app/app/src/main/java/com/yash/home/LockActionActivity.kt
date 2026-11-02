package com.yash.home

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity

/** Invisible host for a widget tap: reads the lock, locks at once or asks before unlocking.
 *  A channel widget names its lock in the extras; without them it is the house lock. */
class LockActionActivity : AppCompatActivity() {
    companion object {
        const val EXTRA_DEV = "lock_dev"
        const val EXTRA_CODE = "lock_code"
        const val EXTRA_NAME = "lock_name"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val dev = intent.getStringExtra(EXTRA_DEV)
        val given = if (dev.isNullOrEmpty()) null else Lock.Target(dev,
            intent.getStringExtra(EXTRA_CODE).orEmpty().ifEmpty { "locked" }, intent.getStringExtra(EXTRA_NAME).orEmpty().ifEmpty { dev })
        Thread {
            try {
                val t = given ?: Lock.target(this) ?: return@Thread done(getString(R.string.lock_none))
                val s = Lock.state(this, t)
                runOnUiThread {
                    if (s.locked == false) act(t, true)
                    else AlertDialog.Builder(this)
                        .setTitle(R.string.unlock_title).setMessage(getString(R.string.unlock_text, t.name))
                        .setPositiveButton(R.string.unlock_ok) { _, _ -> act(t, false) }
                        .setNegativeButton(android.R.string.cancel) { _, _ -> finish() }
                        .setOnCancelListener { finish() }
                        .show()
                }
            } catch (e: Exception) { done(getString(R.string.lock_offline)) }
        }.start()
    }

    private fun act(t: Lock.Target, lock: Boolean) = Thread {
        val app = applicationContext
        val ok = try { Lock.set(app, t, lock); true } catch (e: Exception) { false }
        done(getString(if (!ok) R.string.lock_offline else if (lock) R.string.lock_sent_lock else R.string.lock_sent_unlock))
        // the lock reports about 3 s after the command: poll until it does instead of one fixed wait
        for (i in 0 until 10) {
            Thread.sleep(1500)
            if (!ok || (try { Lock.state(app, t).locked } catch (e: Exception) { null }) == lock) break
        }
        Lock.refreshSurfaces(app)
    }.start()

    private fun done(msg: String) = runOnUiThread { Toast.makeText(this, msg, Toast.LENGTH_SHORT).show(); finish() }
}
