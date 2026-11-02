package com.yash.home

import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** A browser asks to come in: its code and browser, for how long and with which rights, Allow or Deny. The answer goes to the house
 *  with this phone's token (only an administrator's phone may answer); a request already answered or expired says so and the screen stays. */
class GuestActivity : AppCompatActivity() {
    companion object {
        const val EXTRA_ID = "guest_id"
        const val EXTRA_CODE = "guest_code"
        const val EXTRA_UA = "guest_ua"
        private val HOURS = intArrayOf(1, 2, 8, 24)
    }

    private var rid = ""
    private var hours = 2
    private var scope = "no_locks"
    private var busy = false
    private val actions = mutableListOf<Button>()
    private val grey = Color.parseColor("#8A93A3")

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.guest_title)
        rid = intent.getStringExtra(EXTRA_ID).orEmpty()
        if (rid.isEmpty()) { finish(); return }
        hours = savedInstanceState?.getInt("hours", 2) ?: 2
        scope = savedInstanceState?.getString("scope") ?: "no_locks"
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(16), dp(20), dp(20)) }

        root.addView(TextView(this).apply { text = getString(R.string.guest_code); textSize = 14f; setTextColor(grey); gravity = Gravity.CENTER })
        root.addView(TextView(this).apply {
            text = intent.getStringExtra(EXTRA_CODE).orEmpty().ifEmpty { "------" }
            textSize = 44f; typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD); letterSpacing = 0.15f; gravity = Gravity.CENTER
        }, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        root.addView(TextView(this).apply {
            text = intent.getStringExtra(EXTRA_UA).orEmpty().ifEmpty { "?" }; textSize = 16f; gravity = Gravity.CENTER
        }, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        root.addView(TextView(this).apply { text = getString(R.string.guest_hint); textSize = 13f; setTextColor(grey); setPadding(0, dp(8), 0, 0) })

        root.addView(heading(R.string.guest_hours))
        val hg = RadioGroup(this).apply { orientation = RadioGroup.HORIZONTAL }
        for (h in HOURS) {
            val b = RadioButton(this).apply { id = View.generateViewId(); text = getString(R.string.guest_h, h); setOnClickListener { hours = h } }
            hg.addView(b, RadioGroup.LayoutParams(0, WRAP_CONTENT, 1f))
            if (h == hours) hg.check(b.id)
        }
        root.addView(hg, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))

        root.addView(heading(R.string.guest_rights))
        val sg = RadioGroup(this).apply { orientation = RadioGroup.VERTICAL }
        for ((s, label) in listOf("no_locks" to R.string.guest_no_locks, "view" to R.string.guest_view)) {
            val b = RadioButton(this).apply { id = View.generateViewId(); text = getString(label); setOnClickListener { scope = s } }
            sg.addView(b, RadioGroup.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
            if (s == scope) sg.check(b.id)
        }
        root.addView(sg, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))

        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        for ((label, allow) in listOf(R.string.guest_deny to false, R.string.guest_allow to true)) {
            val b = Button(this).apply { text = getString(label); setOnClickListener { send(allow) } }
            actions += b
            row.addView(b, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { marginStart = dp(4); marginEnd = dp(4) })
        }
        root.addView(row, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(20) })
        setContentView(ScrollView(this).apply { addView(root, MATCH_PARENT, WRAP_CONTENT) })
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putInt("hours", hours)
        outState.putString("scope", scope)
    }

    private fun heading(res: Int) = TextView(this).apply {
        text = getString(res); textSize = 15f; typeface = Typeface.DEFAULT_BOLD; setPadding(0, dp(18), 0, dp(4))
    }

    /** The house's own words of a refusal ({"detail": ...} after "HTTP 403: "), else null. */
    private fun houseText(e: Exception): String? =
        try { JSONObject(e.message.orEmpty().substringAfter(": ")).optString("detail").ifEmpty { null } } catch (_: Exception) { null }

    private fun send(allow: Boolean) {
        if (busy) return
        busy = true
        actions.forEach { it.isEnabled = false }
        val app = applicationContext
        val id = rid
        val h = hours
        val s = scope
        Thread {
            val err: String? = try { HomeApi.guestAnswer(app, id, allow, h, s); null }
                catch (e: HomeApi.ApiError) {
                    when (e.code) {
                        404, 409 -> getString(R.string.guest_gone)
                        401, 403 -> houseText(e) ?: getString(R.string.guest_forbidden)
                        else -> getString(R.string.guest_failed, e.code)
                    }
                }
                catch (e: IOException) { getString(R.string.guest_offline) }
                catch (e: Exception) { getString(R.string.guest_offline) }
            runOnUiThread {
                if (err == null) {
                    val until = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(System.currentTimeMillis() + h * 3_600_000L))
                    Toast.makeText(app, if (allow) getString(R.string.guest_allowed, until) else getString(R.string.guest_denied), Toast.LENGTH_LONG).show()
                    GuestNotice.cancel(app, id)
                    finish()
                } else {
                    Toast.makeText(app, err, Toast.LENGTH_LONG).show()
                    busy = false
                    actions.forEach { it.isEnabled = true }
                }
            }
        }.start()
    }
}
