package org.yashome.panel;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.util.Log;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;

/** Charge window for a kiosk on the charger for years: above max charging stops, below
 *  min it runs again. Needs root (Magisk); the first existing sysfs node of the list is
 *  used. Without root it stays quiet and the server switches the charger's smart plug.
 *  The kernel resets the node on a replug, so the hold is rewritten on every tick. */
public class Charge {
    private static final String TAG = "Charge";
    // {node, "stop" value, "charge" value}
    private static final String[][] NODES = {
        {"/sys/class/power_supply/battery/batt_slate_mode", "1", "0"},
        {"/sys/class/power_supply/battery/battery_charging_enabled", "0", "1"},
        {"/sys/class/power_supply/battery/charging_enabled", "0", "1"},
        {"/sys/class/power_supply/battery/input_suspend", "1", "0"},
    };
    // Magisk does not always put its directory into PATH (some devices lack /debug_ramdisk there)
    private static final String[] SU = {"su", "/debug_ramdisk/su", "/sbin/su", "/system/xbin/su", "/system/bin/su"};
    private static String suBin = "su";
    private static String[] node = null;
    private static boolean probed = false, root = false, hold = false;

    public static synchronized String mode() { probe(); return node == null ? "" : node[0].substring(node[0].lastIndexOf('/') + 1); }
    public static synchronized boolean holding() { return hold; }
    public static synchronized boolean rooted() { probe(); return root; }

    private static void probe() {
        if (probed) return;
        probed = true;
        for (String s : SU) { suBin = s; if (su("id").contains("uid=0")) { root = true; break; } }
        if (!root) { Log.i(TAG, "no root, charge window left to the server"); return; }
        Log.i(TAG, "su at " + suBin);
        for (String[] n : NODES) {
            if (su("ls " + n[0]).trim().equals(n[0])) { node = n; break; }
        }
        Log.i(TAG, node == null ? "no known sysfs node" : "node " + node[0]);
    }

    public static synchronized void apply(Context c, int lo, int hi) {
        probe();
        if (node == null) return;
        Intent bi = c.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (bi == null) return;
        int lvl = bi.getIntExtra("level", -1), scale = bi.getIntExtra("scale", 100);
        if (lvl < 0 || scale <= 0) return;
        int pct = lvl * 100 / scale;
        if (pct >= hi) hold = true;
        else if (pct <= lo) hold = false;
        String want = hold ? node[1] : node[2];
        String cur = su("cat " + node[0]).trim();
        if (!cur.equals(want)) {
            su("echo " + want + " > " + node[0]);
            Log.i(TAG, pct + "% -> " + node[0] + "=" + want + " (was " + cur + ")");
        }
    }

    /** One command through Magisk su; an empty string on any trouble. */
    static String su(String cmd) {
        try {
            final Process p = Runtime.getRuntime().exec(new String[]{suBin, "-c", cmd});
            Thread killer = new Thread(new Runnable() { public void run() {
                try { Thread.sleep(8000); } catch (InterruptedException e) { return; }
                p.destroy();
            }});
            killer.setDaemon(true); killer.start();
            InputStream is = p.getInputStream();
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            byte[] b = new byte[512]; int n;
            while ((n = is.read(b)) > 0) bo.write(b, 0, n);
            p.waitFor(); killer.interrupt();
            return bo.toString("UTF-8");
        } catch (Exception e) {
            return "";
        }
    }
}
