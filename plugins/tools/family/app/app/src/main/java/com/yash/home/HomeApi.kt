package com.yash.home

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Home dashboard backend: REST under /api with a Bearer token, live state over /ws. */
object HomeApi {
    const val DEFAULT_URL = ""
    const val QR_PREFIX = "yashhome1:"
    const val CLAIM_PREFIX = "yfc_"
    private val http = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS).build()

    @Volatile private var sp: SharedPreferences? = null
    private fun prefs(ctx: Context): SharedPreferences = sp ?: synchronized(this) {
        sp ?: try { open(ctx) } catch (e: Exception) {
            // A file whose Keystore key did not come along (a copied phone): start unpaired, not crash
            ctx.deleteSharedPreferences("home_server")
            Prefs.logEvent(ctx, "server settings unreadable, cleared: ${e.javaClass.simpleName}")
            open(ctx)
        }.also { sp = it }
    }

    private fun open(ctx: Context): SharedPreferences = EncryptedSharedPreferences.create(ctx, "home_server",
        MasterKey.Builder(ctx).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM)

    /** https anywhere; plain http only to a private address (LAN, VPN) or a local name: a pairing
     *  link from elsewhere can never point the phone at a house over plain internet. */
    fun safeUrl(url: String): Boolean {
        val u = try { java.net.URI(url) } catch (_: Exception) { return false }
        if (u.rawUserInfo != null) return false               // https://house@elsewhere reads like the house
        val host = u.host?.lowercase() ?: return false
        if (u.scheme == "https") return true
        if (u.scheme != "http") return false
        if (listOf(".local", ".lan", ".home.arpa", ".internal").any { host.endsWith(it) }) return true
        val parts = host.split('.')
        if (parts.size != 4 || parts.any { !Regex("0|[1-9][0-9]{0,2}").matches(it) || it.toInt() > 255 }) return false
        val p = parts.map { it.toInt() }
        return p[0] == 10 || (p[0] == 172 && p[1] in 16..31) || (p[0] == 192 && p[1] == 168) ||
            (p[0] == 100 && p[1] in 64..127) || p[0] == 127
    }

    fun url(ctx: Context): String = (prefs(ctx).getString("url", DEFAULT_URL) ?: DEFAULT_URL).trimEnd('/')
    fun token(ctx: Context): String = prefs(ctx).getString("token", "") ?: ""
    fun configured(ctx: Context) = token(ctx).isNotEmpty() && url(ctx).startsWith("http")
    fun save(ctx: Context, url: String, token: String) =
        prefs(ctx).edit().putString("url", url.trim().ifEmpty { DEFAULT_URL }).putString("token", token.trim()).apply()

    /** QR payload "yashhome1:<url>#<token>" as (url, token); null when it is not ours or the url is not safe. */
    fun parseHomeQr(qr: String): Pair<String, String>? {
        if (!qr.startsWith(QR_PREFIX)) return null
        val body = qr.removePrefix(QR_PREFIX)
        val i = body.lastIndexOf('#')
        if (i <= 0 || i == body.length - 1 || !safeUrl(body.substring(0, i))) return null
        return body.substring(0, i) to body.substring(i + 1)
    }

    /** What a person should read of an address: scheme://host[:port], nothing else. */
    fun shown(url: String): String = try {
        val u = java.net.URI(url); u.scheme + "://" + u.host + (if (u.port > 0) ":" + u.port else "")
    } catch (_: Exception) { "?" }

    /** The pairing QR's one-time code for this phone's own token (POST /api/family/claim, no token yet);
     *  a house with Firebase adds "ct", the one sign-in to it. */
    fun claim(ctx: Context, srv: String, code: String): JSONObject {
        val body = JSONObject().put("code", code).put("device_id", deviceId(ctx)).toString()
            .toRequestBody("application/json".toMediaType())
        val j = JSONObject(call(Request.Builder().url(srv.trimEnd('/') + "/api/family/claim").post(body).build()))
        val tok = j.optString("tok")
        if (!tok.startsWith("yfd_") || tok.length !in 20..256)
            throw IllegalStateException("the house answered something that is not a phone token")
        return j
    }

    /** The phone's own id (ANDROID_ID: it survives a reinstall signed with the same key): pairing the same
     *  phone again replaces its entry in the house instead of adding one. Empty when there is none. */
    fun deviceId(ctx: Context): String {
        val a = android.provider.Settings.Secure.getString(ctx.contentResolver, android.provider.Settings.Secure.ANDROID_ID)
        return if (a != null && Regex("[0-9a-fA-F]{8,32}").matches(a)) "and-" + a.lowercase() else ""
    }

    private fun req(ctx: Context, path: String) = Request.Builder().url(url(ctx) + path)
        .header("Authorization", "Bearer " + token(ctx))

    private fun call(r: Request): String = http.newCall(r).execute().use { resp ->
        val text = resp.body?.string() ?: ""
        if (!resp.isSuccessful) throw ApiError(resp.code, text.take(200))
        text
    }

    class ApiError(val code: Int, msg: String) : Exception("HTTP $code: $msg")

    fun devices(ctx: Context) = JSONArray(call(req(ctx, "/api/devices").build()))
    fun rooms(ctx: Context) = JSONArray(call(req(ctx, "/api/rooms").build()))
    // cutover-compat: a house that still runs the old server answers the players on /api/airmouse
    fun touchmouse(ctx: Context) = try { JSONArray(call(req(ctx, "/api/touchmouse").build())) }
        catch (e: ApiError) { if (e.code == 404) JSONArray(call(req(ctx, "/api/airmouse").build())) else throw e }
    fun favorites(ctx: Context) = JSONArray(call(req(ctx, "/api/favorites").build()))
    fun status(ctx: Context, dev: String): String =
        call(req(ctx, "/api/devices/" + android.net.Uri.encode(dev) + "/status").build())

    /** The app the house has built ({cmd: update, code, name, sha256, size, path}); null when it has none. */
    fun app(ctx: Context): JSONObject? = try { JSONObject(call(req(ctx, "/api/family/me/app").build())) }
        catch (e: ApiError) { if (e.code == 404) null else throw e }

    /** This phone's own screen (plugins/family); null when the server does not know us as a family phone. */
    fun me(ctx: Context): JSONObject? = try { JSONObject(call(req(ctx, "/api/family/me").build())) }
        catch (e: ApiError) { if (e.code == 404) null else throw e }

    fun putScreen(ctx: Context, items: JSONArray) {
        val body = JSONObject().put("items", items).toString().toRequestBody("application/json".toMediaType())
        call(req(ctx, "/api/family/me/screen").put(body).build())
    }

    /** The channels this phone's home-screen widgets show: the house pushes their changes ("state"). */
    fun putWidgets(ctx: Context, items: JSONArray) {
        val body = JSONObject().put("items", items).toString().toRequestBody("application/json".toMediaType())
        call(req(ctx, "/api/family/me/widgets").put(body).build())
    }

    // ---- ask the house and push-to-talk (plug-in voice; the house token and full-access phones only) ----
    private fun jsonBody(o: JSONObject) = o.toString().toRequestBody("application/json".toMediaType())

    fun askText(ctx: Context, text: String) = JSONObject(call(req(ctx, "/api/voice/ask").post(jsonBody(JSONObject().put("text", text))).build()))

    fun askConfirm(ctx: Context, token: String, yes: Boolean) =
        JSONObject(call(req(ctx, "/api/voice/ask/confirm").post(jsonBody(JSONObject().put("token", token).put("yes", yes))).build()))

    /** The speech of a text as an address the phone plays. */
    fun tts(ctx: Context, text: String): String =
        JSONObject(call(req(ctx, "/api/voice/tts").post(jsonBody(JSONObject().put("text", text.take(600)))).build())).optString("url")

    /** The satellite of this phone in the house: "phone_" + its family name; the house makes it at the first talk. */
    fun satelliteId(ctx: Context): String {
        val uid = try { me(ctx)?.optString("uid") ?: "" } catch (_: Exception) { "" }
        val slug = uid.lowercase().replace(Regex("[^a-z0-9]+"), "_").trim('_').take(20)
        return "phone_" + slug.ifEmpty { "phone" }
    }

    private fun enc(s: String) = android.net.Uri.encode(s)

    /** The player plug-in (Films): a GET or a POST of its API, the answer as JSON. */
    fun playerGet(ctx: Context, path: String, query: Map<String, String> = emptyMap()) =
        JSONObject(call(req(ctx, "/api/player$path" + if (query.isEmpty()) "" else "?" + query.entries.joinToString("&") { it.key + "=" + enc(it.value) }).build()))

    fun playerPost(ctx: Context, path: String, body: JSONObject) = JSONObject(call(req(ctx, "/api/player$path").post(jsonBody(body)).build()))

    /** What a tickle asks for: {cmds, state (the widget channels that changed; all of them with all=true), t}. */
    fun pull(ctx: Context, all: Boolean): JSONObject {
        val n = NetInfo.report(ctx)                                 // every pull tells the house where the phone is (percent-encoded: a name may hold any letters)
        val ack = ctx.getSharedPreferences("yash", Context.MODE_PRIVATE).getLong("pull_ack", 0L)   // the answer applied last
        val b = req(ctx, "/api/family/me/pull?v=${AppUpdate.installedCode(ctx)}&ack=$ack" + if (all) "&all=1" else "")
            .header("X-Net-Gw", enc(n.optString("gw"))).header("X-Net-Wifi-Ip", enc(n.optString("wifi_ip"))).header("X-Net-Ssid", enc(n.optString("ssid")))
            .header("X-Net-Ssid-Known", if (n.optBoolean("ssid_known")) "1" else "0")
        return JSONObject(call(b.build()))
    }

    /** The house's address for the phones, from a pull: when it is another one, safe, and this phone's token works there (it asks
     *  /api/family/me), the phone keeps it from now on - a house that got an outside address moves its phones without a new QR.
     *  At most one try an hour while it does not work. */
    fun adopt(ctx: Context, srv: String) {
        val to = srv.trim().trimEnd('/')
        if (to.isEmpty() || to == url(ctx) || !safeUrl(to)) return
        val sp = ctx.getSharedPreferences("yash", Context.MODE_PRIVATE)
        if (System.currentTimeMillis() - sp.getLong("srv_try_at", 0L) < 3_600_000L) return
        sp.edit().putLong("srv_try_at", System.currentTimeMillis()).apply()
        val ok = try {
            JSONObject(call(Request.Builder().url("$to/api/family/me").header("Authorization", "Bearer " + token(ctx)).build())).has("uid")
        } catch (e: Exception) { Prefs.logEvent(ctx, "house address ${shown(to)} not usable yet (${e.javaClass.simpleName})"); false }
        if (!ok) return
        prefs(ctx).edit().putString("url", to).apply()
        Prefs.logEvent(ctx, "house address now ${shown(to)}")
    }

    /** The network report: {gw, wifi_ip, ssid, v}. */
    fun report(ctx: Context, j: JSONObject) { call(req(ctx, "/api/family/me/report").post(jsonBody(j)).build()) }

    /** TEMPORARY (rotation guard): one line for the house log. */
    fun note(ctx: Context, text: String) { call(req(ctx, "/api/family/me/note").post(jsonBody(JSONObject().put("text", text.take(300)))).build()) }

    // ---- wake-word samples (the "Voice recording" screen) ----
    fun recordAvailable(ctx: Context) = try { JSONObject(call(req(ctx, "/api/voice/record/available").build())).optBoolean("available") } catch (e: Exception) { false }
    fun recordState(ctx: Context) = JSONObject(call(req(ctx, "/api/voice/record/state").build()))
    fun recordPhrases(ctx: Context, n: Int) = JSONObject(call(req(ctx, "/api/voice/record/phrases?n=$n").build())).optJSONArray("phrases") ?: JSONArray()
    fun recordSample(ctx: Context, speaker: String, label: String, name: String, pcm: ByteArray): JSONObject {
        val q = "speaker=${enc(speaker)}&label=$label&sat=${enc(satelliteId(ctx))}" + if (name.isNotEmpty()) "&name=${enc(name)}" else ""
        return JSONObject(call(req(ctx, "/api/voice/record/sample?$q").post(pcm.toRequestBody("application/octet-stream".toMediaType())).build()))
    }
    fun recordDelete(ctx: Context, speaker: String, label: String) =
        JSONObject(call(req(ctx, "/api/voice/record/delete").post(jsonBody(JSONObject().put("speaker", speaker).put("label", label))).build()))
    fun recordFile(ctx: Context, f: String): ByteArray = http.newCall(req(ctx, "/api/voice/record/file?f=${enc(f)}").build()).execute().use {
        if (!it.isSuccessful) throw ApiError(it.code, "")
        it.body?.bytes() ?: ByteArray(0)
    }

    /** A held-button recording (16 kHz mono s16le) up; the answer is the house's decision plus the audio to play. */
    fun ptt(ctx: Context, sat: String, pcm: ByteArray): JSONObject {
        val body = pcm.toRequestBody("application/octet-stream".toMediaType())
        return JSONObject(call(req(ctx, "/api/voice/ptt?sat=" + android.net.Uri.encode(sat)).post(body).build()))
    }

    /** The chat answer, piece by piece (plain text, no actions in it). */
    fun chat(ctx: Context, text: String, history: JSONArray, onPiece: (String) -> Unit) {
        val body = jsonBody(JSONObject().put("text", text.take(300)).put("history", history))
        http.newCall(req(ctx, "/api/voice/chat").post(body).build()).execute().use { resp ->
            if (!resp.isSuccessful) throw ApiError(resp.code, resp.body?.string()?.take(200) ?: "")
            val rd = java.io.InputStreamReader(resp.body!!.byteStream(), Charsets.UTF_8)
            val buf = CharArray(256)
            while (true) { val n = rd.read(buf); if (n < 0) break; if (n > 0) onPiece(String(buf, 0, n)) }
        }
    }

    fun control(ctx: Context, dev: String, code: String, value: Any) {
        val body = JSONObject().put("code", code).put("value", value).toString()
            .toRequestBody("application/json".toMediaType())
        call(req(ctx, "/api/devices/" + android.net.Uri.encode(dev) + "/control").post(body).build())
    }

    fun live(ctx: Context, onState: (JSONObject) -> Unit, onDown: (String) -> Unit): WebSocket {
        // Header, not ?token=: URLs get logged, and a login proxy in front may admit only a Bearer header
        val ws = url(ctx).replaceFirst("http", "ws") + "/ws"
        return http.newWebSocket(req(ctx, "").url(ws).build(), object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                try { onState(JSONObject(text)) } catch (_: Exception) {}
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = onDown("closed $code")
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: okhttp3.Response?) =
                onDown(response?.code?.let { "HTTP $it" } ?: (t.message ?: "no connection"))
        })
    }
}
