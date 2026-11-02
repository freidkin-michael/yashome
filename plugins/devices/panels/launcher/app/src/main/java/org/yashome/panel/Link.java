package org.yashome.panel;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.net.wifi.SupplicantState;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.provider.Settings;

/** Why the panel has no link, in one line for the wall. A kiosk never needs airplane
 *  mode or Wi-Fi off, so with root they are switched back at once. */
public class Link extends BroadcastReceiver {
    private static long shakeSince = 0;

    /** Empty string: the device is on the network, the server is to blame. */
    public static String why(Context c) {
        try {
            if (Settings.Global.getInt(c.getContentResolver(), Settings.Global.AIRPLANE_MODE_ON, 0) == 1)
                return c.getString(R.string.link_airplane);
            WifiManager wm = (WifiManager) c.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm == null) return "";
            if (!wm.isWifiEnabled()) return c.getString(R.string.link_wifi_off);
            WifiInfo wi = wm.getConnectionInfo();
            SupplicantState s = wi == null ? SupplicantState.DISCONNECTED : wi.getSupplicantState();
            boolean shake = s == SupplicantState.AUTHENTICATING || s == SupplicantState.ASSOCIATING
                    || s == SupplicantState.ASSOCIATED || s == SupplicantState.FOUR_WAY_HANDSHAKE
                    || s == SupplicantState.GROUP_HANDSHAKE;
            if (!shake) shakeSince = 0;
            else if (shakeSince == 0) shakeSince = System.currentTimeMillis();
            if (shake) return c.getString(System.currentTimeMillis() - shakeSince > 30000
                    ? R.string.link_wifi_refused : R.string.link_wifi_connecting);
            if (s != SupplicantState.COMPLETED) return c.getString(R.string.link_no_network);
            if (wi.getIpAddress() == 0) return c.getString(R.string.link_no_dhcp);
        } catch (Exception e) {
            return "";
        }
        return "";
    }

    /** Puts the kiosk's radio back to work; does nothing without root. */
    public static void fix(Context c) {
        if (!Charge.rooted()) return;
        String w = why(c);
        if (w.equals(c.getString(R.string.link_airplane))) {
            Charge.su("cmd connectivity airplane-mode disable 2>/dev/null || { settings put global airplane_mode_on 0;"
                    + " am broadcast -a android.intent.action.AIRPLANE_MODE --ez state false; }");
        } else if (w.equals(c.getString(R.string.link_wifi_off))) {
            Charge.su("svc wifi enable");
        }
        // the airplane tile leaves the quick settings, so a hand does not hit it
        Charge.su("t=$(settings get secure sysui_qs_tiles); case \"$t\" in *airplane*)"
                + " settings put secure sysui_qs_tiles $(echo \"$t\" | sed 's/,*airplane//; s/^,//');; esac");
    }

    // su must not run on the main thread: it may wait up to 8 s
    @Override public void onReceive(Context c, Intent i) {
        final Context app = c.getApplicationContext();
        if (android.net.wifi.WifiManager.NETWORK_STATE_CHANGED_ACTION.equals(i.getAction())) Adb.rearm();
        new Fixer(app).start();
    }

    static class Fixer extends Thread {
        private final Context c;
        Fixer(Context c) { this.c = c; }
        @Override public void run() { Adb.keep(c); fix(c); }
    }
}
