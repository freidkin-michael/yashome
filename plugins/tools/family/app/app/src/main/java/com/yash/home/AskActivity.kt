package com.yash.home

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/** "Ask the house": chat bubbles like the dashboard's box, and push-to-talk. A typed phrase and a spoken one take the same road in the
 *  house (phrase map, the model, the house's checks); a guess or a risky device comes back as a question with Yes / No buttons. When
 *  the text is no command the house chats (streamed text, no actions). Holding the button records 16 kHz mono; on release the recording
 *  goes up in one request and the answer is played on the phone. Works wherever the phone reaches the house (Wi-Fi, a VPN). */
class AskActivity : AppCompatActivity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var log: LinearLayout
    private lateinit var scroll: ScrollView
    private lateinit var field: EditText
    private lateinit var yesNo: LinearLayout
    private lateinit var speakBox: CheckBox
    private lateinit var holdBtn: Button
    private var token = ""
    private var player: MediaPlayer? = null
    private val history = JSONArray()
    @Volatile private var recording = false
    private var rec: AudioRecord? = null

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private val grey = Color.parseColor("#8A93A3")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.ask_title)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(12), dp(8), dp(12), dp(12)) }
        log = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(0, dp(4), 0, dp(4)) }
        scroll = ScrollView(this).apply { addView(log, MATCH_PARENT, WRAP_CONTENT) }
        root.addView(scroll, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))

        yesNo = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; visibility = View.GONE }
        yesNo.addView(Button(this).apply { text = getString(R.string.ask_yes); setOnClickListener { answer(true) } }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        yesNo.addView(Button(this).apply { text = getString(R.string.ask_no); setOnClickListener { answer(false) } }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        root.addView(yesNo, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))

        speakBox = CheckBox(this).apply { text = getString(R.string.ask_speak); setTextColor(grey); textSize = 14f }
        root.addView(speakBox, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))

        field = EditText(this).apply {
            hint = getString(R.string.ask_hint); setSingleLine(); imeOptions = EditorInfo.IME_ACTION_SEND
            setTextColor(Color.WHITE); setHintTextColor(grey)
        }
        field.setOnEditorActionListener { _, a, _ -> if (a == EditorInfo.IME_ACTION_SEND) { send(); true } else false }
        val typing = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        typing.addView(field, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        typing.addView(Button(this).apply { text = getString(R.string.ask_send); setOnClickListener { send() } })
        root.addView(typing, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))

        holdBtn = Button(this).apply {
            text = getString(R.string.ask_hold)
            textSize = 18f
            setOnTouchListener { v, e -> hold(v, e) }
        }
        root.addView(holdBtn, LinearLayout.LayoutParams(MATCH_PARENT, dp(72)).apply { topMargin = dp(8) })
        setContentView(root)
        if (!HomeApi.configured(this)) bubble(false, getString(R.string.ask_not_paired), "")
    }

    override fun onStop() {
        stopRecording(false)
        player?.release(); player = null
        super.onStop()
    }

    // ---- bubbles ----
    private fun bubble(mine: Boolean, text: String, badge: String): TextView {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(12), dp(8), dp(12), dp(8)) }
        box.background = GradientDrawable().apply {
            cornerRadius = dp(14).toFloat()
            setColor(Color.parseColor(if (mine) "#2F6FED" else "#1A1F29"))
        }
        if (badge.isNotEmpty()) box.addView(TextView(this).apply { this.text = badge; textSize = 11f; setTextColor(Color.parseColor("#B8C0D0")) })
        val t = TextView(this).apply { this.text = text; textSize = 16f; setTextColor(Color.WHITE) }
        box.addView(t)
        val lp = LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT).apply {
            gravity = if (mine) Gravity.END else Gravity.START
            topMargin = dp(6)
            if (mine) marginStart = dp(48) else marginEnd = dp(48)
        }
        log.addView(box, lp)
        scroll.post { scroll.fullScroll(View.FOCUS_DOWN) }
        return t
    }

    private fun bg(work: () -> Unit) { Thread { try { work() } catch (e: Throwable) { ui.post { bubble(false, e.message ?: e.toString(), getString(R.string.ask_error)) } } }.start() }

    // ---- typed phrases ----
    private fun send() {
        val t = field.text.toString().trim()
        if (t.isEmpty()) return
        field.setText("")
        yesNo.visibility = View.GONE; token = ""
        bubble(true, t, "")
        val speak = speakBox.isChecked
        bg { val r = HomeApi.askText(this, t); ui.post { show(r, t, speak) } }
    }

    private fun answer(yes: Boolean) {
        val tk = token; token = ""
        yesNo.visibility = View.GONE
        bubble(true, getString(if (yes) R.string.ask_yes else R.string.ask_no), "")
        val speak = speakBox.isChecked
        bg { val r = HomeApi.askConfirm(this, tk, yes); ui.post { show(r, "", speak) } }
    }

    /** What the house answered: a chat when the text is no command, else the answer, a question with buttons, and sound. */
    private fun show(r: JSONObject, phrase: String, speak: Boolean, audio: String = "") {
        if (r.optBoolean("chat")) { chat(r.optString("text", phrase).ifEmpty { phrase }, speak); return }
        val said = r.optString("said")
        bubble(false, said, "")                                   // the engine of the answer is a debug detail, not shown
        token = if (r.optBoolean("ask")) r.optString("token") else ""
        yesNo.visibility = if (token.isNotEmpty()) View.VISIBLE else View.GONE
        if (audio.isNotEmpty()) play(audio) else if (speak && said.isNotEmpty()) bg { play(HomeApi.tts(this, said)) }
    }

    private fun chat(text: String, speak: Boolean) {
        val out = bubble(false, "", getString(R.string.ask_chat_badge))
        val sb = StringBuilder()
        bg {
            try {
                HomeApi.chat(this, text, history) { piece -> sb.append(piece); ui.post { out.text = sb.toString(); scroll.fullScroll(View.FOCUS_DOWN) } }
            } catch (e: Exception) { sb.append(e.message ?: e.toString()); ui.post { out.text = sb.toString() } }
            history.put(JSONObject().put("role", "user").put("content", text))
            history.put(JSONObject().put("role", "assistant").put("content", sb.toString()))
            while (history.length() > 6) history.remove(0)
            if (speak && sb.isNotEmpty()) play(HomeApi.tts(this, sb.toString()))
        }
    }

    private fun play(url: String) {
        if (url.isEmpty()) return
        ui.post {
            try {
                player?.release()
                player = MediaPlayer().apply {
                    setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                    setDataSource(url)
                    setOnPreparedListener { it.start() }
                    setOnCompletionListener { it.release(); if (player === it) player = null }
                    setOnErrorListener { m, _, _ -> m.release(); if (player === m) player = null; true }
                    prepareAsync()
                }
            } catch (e: Exception) { /* no sound is not an error of the phrase */ }
        }
    }

    // ---- push-to-talk ----
    @SuppressLint("ClickableViewAccessibility")
    private fun hold(v: View, e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 11)
                    return true
                }
                v.parent.requestDisallowInterceptTouchEvent(true)
                startRecording()
            }
            MotionEvent.ACTION_UP -> stopRecording(true)
            MotionEvent.ACTION_CANCEL -> stopRecording(false)
        }
        return true
    }

    private fun startRecording() {
        if (recording) return
        val rate = 16000
        val min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val r = try {
            AudioRecord(MediaRecorder.AudioSource.MIC, rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min, 8192))
        } catch (e: SecurityException) { bubble(false, getString(R.string.ask_mic_denied), getString(R.string.ask_error)); return }
        if (r.state != AudioRecord.STATE_INITIALIZED) { r.release(); bubble(false, getString(R.string.ask_mic_busy), getString(R.string.ask_error)); return }
        player?.release(); player = null
        rec = r; recording = true
        holdBtn.text = getString(R.string.ask_release)
        r.startRecording()
        Thread {
            val pcm = ByteArrayOutputStream()
            val buf = ByteArray(3200)
            val limit = rate * 2 * 15                     // 15 s at most; the house takes 20
            while (recording && pcm.size() < limit) {
                val n = r.read(buf, 0, buf.size)
                if (n > 0) pcm.write(buf, 0, n) else if (n < 0) break
            }
            try { r.stop() } catch (_: Exception) { }
            r.release()
            if (rec === r) rec = null
            val send = sendAfterStop
            ui.post { holdBtn.text = getString(R.string.ask_hold) }
            if (send) upload(pcm.toByteArray())
        }.start()
    }

    @Volatile private var sendAfterStop = true

    private fun stopRecording(send: Boolean) {
        sendAfterStop = send
        recording = false
    }

    private fun upload(pcm: ByteArray) {
        if (pcm.size < 16000 * 2 * 3 / 10) return          // under 0.3 s: a tap
        ui.post { yesNo.visibility = View.GONE; token = "" }
        val wait = arrayOfNulls<TextView>(1)
        ui.post { wait[0] = bubble(true, "\u2026", getString(R.string.ask_voice_badge)) }
        bg {
            val r = HomeApi.ptt(this, HomeApi.satelliteId(this), pcm)
            ui.post {
                wait[0]?.text = r.optString("text").ifEmpty { "?" }
                show(r, r.optString("text"), true, r.optString("audio"))
            }
        }
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, res: IntArray) {
        super.onRequestPermissionsResult(code, perms, res)
        if (code == 11 && res.firstOrNull() != PackageManager.PERMISSION_GRANTED) bubble(false, getString(R.string.ask_mic_denied), getString(R.string.ask_error))
    }
}
