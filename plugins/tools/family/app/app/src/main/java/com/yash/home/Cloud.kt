package com.yash.home

import android.content.Context
import com.google.firebase.FirebaseApp
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.database.FirebaseDatabase
import com.google.firebase.database.ServerValue
import com.google.firebase.messaging.FirebaseMessaging

/** Firebase side of the phone: who we are (uid from the pairing QR) and what we write. An APK built
 *  without google-services.json has no Firebase at all: then every call here is a no-op. */
object Cloud {
    const val QR_PREFIX = "yashfam1:"

    @Volatile var enabled = false
        private set

    fun init(ctx: Context) { enabled = FirebaseApp.getApps(ctx).isNotEmpty() }

    fun uid(): String? = if (enabled) FirebaseAuth.getInstance().currentUser?.uid else null

    private fun device(uid: String) = FirebaseDatabase.getInstance().getReference("devices").child(uid)

    const val QR2_PREFIX = "yash2:"

    fun isPairingQr(qr: String) = qr.startsWith(QR_PREFIX) || qr.startsWith(QR2_PREFIX) || pairingJson(qr) != null

    /** The yash2 JSON from any carrier: "yash2:{...}", ".../pair.html#<base64url>" or "yash://pair?d=<base64url>".
     *  With "ct" it signs in to Firebase too; without, it is a house without Firebase (srv + tok only). */
    fun pairingJson(text: String): org.json.JSONObject? {
        val raw = when {
            text.startsWith(QR2_PREFIX) -> text.removePrefix(QR2_PREFIX)
            text.startsWith("yash://pair") -> android.net.Uri.parse(text).getQueryParameter("d")?.let(::b64)
            (text.startsWith("https://") || text.startsWith("http://")) && '#' in text -> b64(text.substringAfter('#'))
            else -> null
        } ?: return null
        return try { org.json.JSONObject(raw).takeIf { it.has("ct") || (it.has("srv") && it.has("tok")) } } catch (_: Exception) { null }
    }

    private fun b64(s: String): String? = try {
        String(android.util.Base64.decode(s, android.util.Base64.URL_SAFE or android.util.Base64.NO_WRAP or android.util.Base64.NO_PADDING))
    } catch (_: Exception) { null }

    /** uid inside the custom token (JWT payload), to show it before the user confirms a link. */
    fun pairingUid(j: org.json.JSONObject): String? = if (!j.has("ct")) j.optString("name").ifEmpty { null } else try {
        org.json.JSONObject(b64(j.getString("ct").split('.')[1].replace('+', '-').replace('/', '_').trimEnd('='))!!).optString("uid")
    } catch (_: Exception) { null }

    /** yash2:{"ct": custom token, "srv"?: url, "tok"?: dashboard token} - one QR for cloud and server access (srv/tok come from the dashboard's pairing). */
    fun signIn(ctx: Context, qr: String, done: (String?) -> Unit) {
        val j = pairingJson(qr)
        val srv = j?.optString("srv").orEmpty()
        val tok = j?.optString("tok").orEmpty()
        if (srv.isNotEmpty() && tok.startsWith(HomeApi.CLAIM_PREFIX)) {
            // The QR holds a one-time code: the house swaps it for this phone's own token (network, not here)
            Thread {
                val err = try {
                    val r = HomeApi.claim(ctx, srv, tok)
                    HomeApi.save(ctx, srv, r.getString("tok"))
                    r.optString("ct").takeIf { it.isNotEmpty() }?.let { j!!.put("ct", it) }   // sign in with it below
                    if (r.optBoolean("no_cloud")) Prefs.logEvent(ctx, "the house could not hand over the Firebase sign-in")
                    null
                } catch (e: Exception) { e.message ?: "claim failed" }
                android.os.Handler(android.os.Looper.getMainLooper()).post {
                    if (err != null) { Prefs.logEvent(ctx, "claim failed: $err"); done(err) }
                    else { Prefs.logEvent(ctx, "server set from QR"); finishSignIn(ctx, j, qr, done) }
                }
            }.start()
            return
        }
        if (srv.isNotEmpty() && tok.isNotEmpty()) { HomeApi.save(ctx, srv, tok); Prefs.logEvent(ctx, "server set from QR") }
        finishSignIn(ctx, j, qr, done)
    }

    private fun finishSignIn(ctx: Context, j: org.json.JSONObject?, qr: String, done: (String?) -> Unit) {
        val custom = when {
            j != null -> {
                if (!j.has("ct") || !enabled) {           // no Firebase: the phone reports its network and refreshes widgets by timer, no rings
                    Link.start(ctx)
                    Prefs.logEvent(ctx, "paired with the house directly")
                    return done(null)
                }
                Link.start(ctx)                           // it tells the house about its network
                j.getString("ct")
            }
            qr.startsWith(QR_PREFIX) && enabled -> qr.removePrefix(QR_PREFIX)
            else -> return done(ctx.getString(R.string.qr_foreign))
        }
        FirebaseAuth.getInstance().signInWithCustomToken(custom)
            .addOnSuccessListener {
                Prefs.logEvent(ctx, "paired as ${uid()}")
                LiveService.start(ctx)                    // signed in to Firebase: the link service (started before the sign-in) is not needed
                FirebaseMessaging.getInstance().token.addOnSuccessListener { publishToken(ctx, it) }
                done(null)
            }
            .addOnFailureListener {
                Prefs.logEvent(ctx, "sign-in failed: ${it.message}")
                done(it.message ?: "sign-in failed")
            }
    }

    fun publishToken(ctx: Context, token: String) {
        Prefs.setToken(ctx, token)
        val uid = uid() ?: return
        device(uid).child("token").setValue(token)
            .addOnSuccessListener { Prefs.logEvent(ctx, "token published") }
            .addOnFailureListener { Prefs.logEvent(ctx, "token write denied: ${it.message}") }
    }

    fun ack(ctx: Context) {
        val uid = uid() ?: return
        device(uid).child("ack").setValue(ServerValue.TIMESTAMP)
            .addOnFailureListener { Prefs.logEvent(ctx, "ack denied: ${it.message}") }
    }
}
