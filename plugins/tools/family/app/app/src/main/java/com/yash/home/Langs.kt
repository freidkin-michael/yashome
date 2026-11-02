package com.yash.home

import android.content.Context
import android.content.res.XmlResourceParser
import java.util.Locale

/** App languages come from res/xml/locales_config.xml only: add a line there plus values-xx to add one. */
object Langs {
    fun supported(ctx: Context): List<String> {
        val out = mutableListOf<String>()
        ctx.resources.getXml(R.xml.locales_config).use { p ->
            while (p.next() != XmlResourceParser.END_DOCUMENT)
                if (p.eventType == XmlResourceParser.START_TAG && p.name == "locale")
                    p.getAttributeValue("http://schemas.android.com/apk/res/android", "name")?.let { out += it }
        }
        return out
    }

    /** Autonym ("English", "Deutsch"); empty tag = follow the phone. */
    fun name(ctx: Context, tag: String): String {
        if (tag.isEmpty()) return ctx.getString(R.string.lang_system)
        val l = Locale.forLanguageTag(tag)
        return l.getDisplayName(l).replaceFirstChar { it.titlecase(l) }
    }
}
