package org.yashome.panel;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;

import org.json.JSONObject;

/** The panel page's status bar: battery, charging, Wi-Fi and why the link is down.
 *  Caps.caps() walks cameras and sensors, too heavy for a poll every few seconds,
 *  so only three cheap reads here. */
public class Status {
    public static String json(Context c) {
        JSONObject o = new JSONObject();
        try {
            Intent bi = c.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
            if (bi != null) {
                int lvl = bi.getIntExtra("level", -1), scale = bi.getIntExtra("scale", 100);
                if (lvl >= 0 && scale > 0) o.put("battery", lvl * 100 / scale);
                o.put("charging", bi.getIntExtra("plugged", 0) != 0);
            }
            o.put("hold", Charge.holding());
            o.put("link", Link.why(c));
            WifiManager wm = (WifiManager) c.getApplicationContext()
                    .getSystemService(Context.WIFI_SERVICE);
            WifiInfo wi = wm == null ? null : wm.getConnectionInfo();
            if (wi != null && wi.getRssi() > -127) o.put("rssi", wi.getRssi());
        } catch (Exception e) {
            // without the sensors the line is just shorter
        }
        return o.toString();
    }
}
