package com.yash.home

import android.Manifest
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.media.ToneGenerator
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.io.File
import kotlin.concurrent.thread

/** "Voice recording": samples of the wake phrase (positive) and of anything else (negative) for training. One press = a beep, then 3 s of the
 *  phone's microphone (16 kHz mono) go up whole and are saved as they are on the house; nothing is recognised and nothing is acted on. */
class RecordActivity : AppCompatActivity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var spk: Spinner
    private lateinit var newSpk: EditText
    private lateinit var pos: RadioButton
    private lateinit var hint: TextView
    private lateinit var count: TextView
    private lateinit var msg: TextView
    private lateinit var recBtn: Button
    private var speakers = JSONObject()
    private var slugs = listOf<String>()
    private var target = 100
    private var lastFile: String? = null
    @Volatile private var busy = false

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun label() = if (pos.isChecked) "positive" else "negative"
    private fun speaker() = newSpk.text.toString().trim().ifEmpty { slugs.getOrNull(spk.selectedItemPosition) ?: "" }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.rec_title)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(12), dp(16), dp(12)) }
        fun row(vararg v: android.view.View) = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; v.forEach { addView(it) } }
        spk = Spinner(this)
        newSpk = EditText(this).apply { hint = getString(R.string.rec_new); setSingleLine() }
        root.addView(TextView(this).apply { text = getString(R.string.rec_speaker) })
        root.addView(spk, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        root.addView(newSpk, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        pos = RadioButton(this).apply { text = getString(R.string.rec_pos); isChecked = true; id = 1001 }
        val neg = RadioButton(this).apply { text = getString(R.string.rec_neg); id = 1002 }
        root.addView(RadioGroup(this).apply { orientation = RadioGroup.HORIZONTAL; addView(pos); addView(neg) })
        hint = TextView(this).apply { textSize = 17f; setPadding(0, dp(8), 0, dp(8)); minLines = 3 }
        root.addView(hint, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        recBtn = Button(this).apply { text = getString(R.string.rec_record); textSize = 22f; setOnClickListener { record() } }
        count = TextView(this).apply { textSize = 22f; setPadding(dp(16), 0, 0, 0) }
        root.addView(row(recBtn, count))
        msg = TextView(this).apply { setPadding(0, dp(8), 0, dp(8)) }
        root.addView(msg, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        root.addView(row(Button(this).apply { text = getString(R.string.rec_play); setOnClickListener { play() } },
                         Button(this).apply { text = getString(R.string.rec_del); setOnClickListener { delete() } }))
        setContentView(ScrollView(this).apply { addView(root) })
        pos.setOnCheckedChangeListener { _, _ -> showCount(); showHint() }
        newSpk.addTextChangedListener(object : android.text.TextWatcher {
            override fun afterTextChanged(s: android.text.Editable?) = showCount()
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
        })
        spk.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: android.widget.AdapterView<*>?, v: android.view.View?, i: Int, id: Long) = showCount()
            override fun onNothingSelected(p: android.widget.AdapterView<*>?) {}
        }
        refresh()
        showHint()
        thread { if (RecQueue.pending(this) > 0) { RecQueue.flush(this); ui.post { refresh() } } }
    }

    private fun refresh() = thread {
        try {
            val st = HomeApi.recordState(this)
            ui.post {
                speakers = st.optJSONObject("speakers") ?: JSONObject()
                target = st.optInt("target", 100)
                val keep = slugs.getOrNull(spk.selectedItemPosition)
                slugs = speakers.keys().asSequence().toList()
                spk.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, slugs.map { speakers.getJSONObject(it).optString("name", it) })
                keep?.let { k -> slugs.indexOf(k).takeIf { it >= 0 }?.let { spk.setSelection(it) } }
                showCount()
            }
        } catch (e: Exception) { ui.post { msg.text = e.message ?: e.javaClass.simpleName } }
    }

    private fun showCount() {
        val typed = newSpk.text.toString().trim()
        val e = if (typed.isEmpty()) speakers.optJSONObject(slugs.getOrNull(spk.selectedItemPosition) ?: "") else null
        val wait = RecQueue.pending(this)
        count.text = "${e?.optInt(label(), 0) ?: 0} / $target" + if (wait > 0) "  " + getString(R.string.rec_pending, wait) else ""
    }

    private fun showHint() {
        if (pos.isChecked) { hint.text = getString(R.string.rec_say); return }
        hint.text = ""
        thread {
            try {
                val a = HomeApi.recordPhrases(this, 3)
                ui.post { hint.text = getString(R.string.rec_read) + ":\n" + (0 until a.length()).joinToString("\n") { a.getString(it) } }
            } catch (_: Exception) { }
        }
    }

    private fun record() {
        if (busy) return
        if (speaker().isEmpty()) { msg.text = getString(R.string.rec_pick); return }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) { requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 11); return }
        busy = true; recBtn.isEnabled = false; msg.text = getString(R.string.rec_recording)
        val who = speaker(); val lbl = label(); val typed = newSpk.text.toString().trim()
        thread {
            var ar: AudioRecord? = null
            try {
                val tone = ToneGenerator(AudioManager.STREAM_MUSIC, 70)
                try { tone.startTone(ToneGenerator.TONE_PROP_BEEP, 400); Thread.sleep(600) } finally { tone.release() }   // one per press: not released, ~100 presses exhaust them
                val rate = 16000
                val min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
                ar = AudioRecord(MediaRecorder.AudioSource.MIC, rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min, 8192))
                if (ar.state != AudioRecord.STATE_INITIALIZED) throw IllegalStateException("microphone busy")
                val pcm = ByteArray(rate * 2 * 3)                         // 3 s: the whole phrase fits
                ar.startRecording()
                var got = 0
                while (got < pcm.size) { val n = ar.read(pcm, got, pcm.size - got); if (n <= 0) break; got += n }
                ar.stop()
                val sample = pcm.copyOf(got)
                var peak = 0
                for (i in 0 until sample.size - 1 step 2) peak = maxOf(peak, Math.abs(((sample[i + 1].toInt() shl 8) or (sample[i].toInt() and 0xff)).toShort().toInt()))
                if (peak < 800) { ui.post { msg.text = getString(R.string.rec_nospeech) }; return@thread }          // nobody spoke: nothing is kept or sent
                try {
                    val r = HomeApi.recordSample(this, who, lbl, typed, sample)
                    lastFile = r.optString("file").ifEmpty { null }
                    ui.post {
                        msg.text = if (r.optBoolean("quiet")) getString(R.string.rec_quiet) else getString(R.string.rec_saved) + " (" + r.optDouble("secs", 0.0) + " s)"
                        refresh()
                        if (!pos.isChecked) showHint()
                    }
                    thread { RecQueue.flush(this); ui.post { showCount() } }          // the way is open: what waited goes up too
                } catch (e: Exception) {                                               // no network, VPN down, the house down: keep it, send later
                    RecQueue.add(this, who, lbl, typed, sample)
                    ui.post { msg.text = getString(R.string.rec_kept); showCount(); if (!pos.isChecked) showHint() }
                }
            } catch (e: Exception) { ui.post { msg.text = e.message ?: e.javaClass.simpleName } }
            finally { try { ar?.release() } catch (_: Exception) { }; busy = false; ui.post { recBtn.isEnabled = true } }
        }
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, res: IntArray) {
        super.onRequestPermissionsResult(code, perms, res)
        if (code == 11 && res.firstOrNull() == PackageManager.PERMISSION_GRANTED) record()
    }

    private fun delete() {
        val who = speaker(); if (who.isEmpty()) return
        thread {
            try { HomeApi.recordDelete(this, who, label()); lastFile = null; ui.post { refresh() } }
            catch (e: Exception) { ui.post { msg.text = e.message ?: e.javaClass.simpleName } }
        }
    }

    private fun play() {
        val f = lastFile ?: run { msg.text = getString(R.string.rec_first); return }
        thread {
            try {
                val tmp = File(cacheDir, "sample.wav").apply { writeBytes(HomeApi.recordFile(this@RecordActivity, f)) }
                ui.post { MediaPlayer().apply { setDataSource(tmp.path); setOnCompletionListener { release() }; prepare(); start() } }
            } catch (e: Exception) { ui.post { msg.text = e.message ?: e.javaClass.simpleName } }
        }
    }
}
