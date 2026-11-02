package com.yash.home

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiInfo
import android.os.Build
import org.json.JSONObject
import java.net.Inet4Address
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/** What the app tells the house about its network: the gateway and own address of the Wi-Fi network (a VPN is skipped), its name and the
 *  app version. Read on a network change, never by a timer. */
object NetInfo {
    fun gateway(ctx: Context): String {
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        for (n in cm.allNetworks) {
            val caps = cm.getNetworkCapabilities(n) ?: continue
            if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue
            val gw = cm.getLinkProperties(n)?.routes?.firstOrNull { it.isDefaultRoute && it.gateway is Inet4Address }?.gateway
            if (gw != null) return gw.hostAddress ?: ""
        }
        return ""
    }

    fun wifiIp(ctx: Context): String {
        val cm = ctx.getSystemService(ConnectivityManager::class.java)
        for (n in cm.allNetworks) {
            val caps = cm.getNetworkCapabilities(n) ?: continue
            if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue
            val a = cm.getLinkProperties(n)?.linkAddresses?.firstOrNull { it.address is Inet4Address }?.address
            if (a != null) return a.hostAddress ?: ""
        }
        return ""
    }

    private fun clean(raw: String) = if (raw == "<unknown ssid>" || raw == "0x") "" else raw.trim('"')

    /** The Wi-Fi name: Android 12+ gives it only to a callback made with FLAG_INCLUDE_LOCATION_INFO, so a short-lived one is registered here
     *  (a callback delivered by a PendingIntent cannot carry the flag); older Android and a failure fall back to WifiManager. */
    @Suppress("DEPRECATION")
    fun ssid(ctx: Context): String = wifi(ctx).first

    private fun cleanBssid(raw: String?) = if (raw == null || raw.startsWith("02:00:00:00:")) "" else raw.lowercase()

    /** The Wi-Fi name and BSSID (the access point's address; "" when unreadable) of the current connection. */
    @Suppress("DEPRECATION")
    fun wifi(ctx: Context): Pair<String, String> {
        if (ctx.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) return Pair("", "")
        var s = ""
        var b = ""
        if (Build.VERSION.SDK_INT >= 31) {
            val cm = ctx.getSystemService(ConnectivityManager::class.java)
            val latch = CountDownLatch(1)
            val got = AtomicReference("")
            val raw = AtomicReference<String?>(null)
            val bs = AtomicReference("")
            val cb = object : ConnectivityManager.NetworkCallback(FLAG_INCLUDE_LOCATION_INFO) {
                override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                    val wi = caps.transportInfo as? WifiInfo ?: return
                    val r = wi.ssid ?: return
                    raw.set(r); got.set(clean(r)); bs.set(cleanBssid(wi.bssid)); latch.countDown()
                }
            }
            try {
                cm.registerNetworkCallback(NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build(), cb)
                latch.await(2500, TimeUnit.MILLISECONDS)
            } catch (e: Exception) { Prefs.logEvent(ctx, "ssid: callback failed (${e.javaClass.simpleName})") }
            finally { try { cm.unregisterNetworkCallback(cb) } catch (_: Exception) { } }
            s = got.get()
            b = bs.get()
            raw.get()?.let { Prefs.logEvent(ctx, "ssid: raw '$it' -> ${if (s.isEmpty()) "-" else "set"}") }
        }
        if (s.isEmpty() || b.isEmpty()) try {
            val wm = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as android.net.wifi.WifiManager
            val ci = wm.connectionInfo
            if (s.isEmpty()) s = clean(ci?.ssid ?: "")
            if (b.isEmpty()) b = cleanBssid(ci?.bssid)
        } catch (e: Exception) { }
        return Pair(s, b)
    }

    /** ssid_known false = the name could not be read: the house then judges by the address alone. A name that was read is kept with its
     *  gateway and BSSID ("yash" prefs, ssid_gw = gateway|bssid|ssid); when a later read fails the kept name is reported only if the
     *  BSSID is readable and equal to the kept one, so a foreign router with the same gateway does not borrow the house name. */
    fun report(ctx: Context): JSONObject {
        val gw = gateway(ctx)
        var (s, bssid) = wifi(ctx)
        val sp = ctx.getSharedPreferences("yash", Context.MODE_PRIVATE)
        if (s.isNotEmpty() && gw.isNotEmpty() && bssid.isNotEmpty()) sp.edit().putString("ssid_gw", "$gw|$bssid|$s").apply()
        else if (s.isEmpty() && gw.isNotEmpty() && bssid.isNotEmpty()) {
            val kept = (sp.getString("ssid_gw", "") ?: "").split("|", limit = 3)
            if (kept.size == 3 && kept[0] == gw && kept[1] == bssid) s = kept[2]
        }
        return JSONObject().put("gw", gw).put("wifi_ip", wifiIp(ctx)).put("ssid", s).put("ssid_known", s.isNotEmpty()).put("v", AppUpdate.installedCode(ctx))
    }
}
