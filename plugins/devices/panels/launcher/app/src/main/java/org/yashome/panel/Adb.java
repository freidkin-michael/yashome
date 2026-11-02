package org.yashome.panel;

import android.content.Context;
import android.content.Intent;
import android.provider.Settings;

/** Wireless debugging for a kiosk without root: Android 11 turns it off on every boot and
 *  Wi-Fi change; the flag is written with WRITE_SECURE_SETTINGS (`pm grant` over adb). On an
 *  untrusted network the system shows a dialog instead: one try per connection, then the
 *  panel comes back on top. With root, adb lives on 5555. */
public class Adb {
    private static final String WIFI = "adb_wifi_enabled";
    private static boolean tried = false;

    public static synchronized void rearm() { tried = false; }

    public static synchronized void keep(Context c) {
        if (Charge.rooted()) return;
        try {
            if (Settings.Global.getInt(c.getContentResolver(), WIFI, 0) == 1) { tried = false; return; }
            if (tried || !Link.why(c).isEmpty()) return;
            tried = true;
            Settings.Global.putInt(c.getContentResolver(), WIFI, 1);
            Thread.sleep(3000);
            if (Settings.Global.getInt(c.getContentResolver(), WIFI, 0) == 1) return;
            Intent home = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            c.startActivity(home);
        } catch (Exception e) {
            // no permission, or an old Android without wireless debugging
        }
    }
}
