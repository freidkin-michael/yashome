package com.yash.home

import android.content.Context
import org.json.JSONObject
import java.io.File

/** Wake-word samples that could not go up (no network, VPN down): kept in the app's own storage and sent later, oldest first.
 *  Tried when the screen opens, when the network is back (RecUploadWorker) and by a house without Firebase on its link's connect and tick. A file is removed only after the house took it. */
object RecQueue {
    private fun dir(ctx: Context) = File(ctx.applicationContext.filesDir, "rec_queue").apply { mkdirs() }

    fun add(ctx: Context, speaker: String, label: String, name: String, pcm: ByteArray) {
        val id = System.currentTimeMillis().toString()
        val d = dir(ctx)
        File(d, "$id.pcm").writeBytes(pcm)
        File(d, "$id.json").writeText(JSONObject().put("speaker", speaker).put("label", label).put("name", name).toString())
        RecUploadWorker.enqueue(ctx)                              // sent when the network allows, retried with backoff
    }

    fun pending(ctx: Context): Int = dir(ctx).listFiles { f -> f.name.endsWith(".pcm") }?.size ?: 0

    /** Sends what is waiting; stops at the first failure (the network is still down). -> how many went up. */
    @Synchronized
    fun flush(ctx: Context): Int {
        if (!HomeApi.configured(ctx)) return 0
        var sent = 0
        for (pcm in dir(ctx).listFiles { f -> f.name.endsWith(".pcm") }?.sortedBy { it.name } ?: return 0) {
            val meta = File(pcm.path.removeSuffix(".pcm") + ".json")
            try {
                val m = JSONObject(meta.readText())
                HomeApi.recordSample(ctx, m.getString("speaker"), m.getString("label"), m.optString("name"), pcm.readBytes())
                pcm.delete(); meta.delete(); sent++
            } catch (e: HomeApi.ApiError) {
                if (e.code in 400..499 && e.code != 408) { pcm.delete(); meta.delete() }        // the house refuses it for good: not kept forever
                else break
            } catch (e: Exception) { break }
        }
        return sent
    }
}
