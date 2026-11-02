package org.yashome.panel;

import android.app.ActivityManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.hardware.Sensor;
import android.hardware.SensorManager;
import android.os.Build;
import android.util.DisplayMetrics;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;

/** The device passport: what this device has. The dashboard makes a panel for this
 *  screen from it and decides which widgets this hardware can show. */
public class Caps {
    public static final String APP_VER = "4.0";

    public static JSONObject caps(Context c) throws Exception {
        JSONObject o = new JSONObject();
        DisplayMetrics dm = new DisplayMetrics();
        android.view.WindowManager wm =
                (android.view.WindowManager) c.getSystemService(Context.WINDOW_SERVICE);
        // The real screen size, not the window: in immersive mode the window is shorter
        // by the hidden bars, and the panel canvas would come out 1024x552, not 1024x600
        if (wm != null) wm.getDefaultDisplay().getRealMetrics(dm);
        else dm = c.getResources().getDisplayMetrics();
        JSONObject sc = new JSONObject();
        sc.put("w", dm.widthPixels);
        sc.put("h", dm.heightPixels);
        sc.put("dpi", dm.densityDpi);
        double inch = Math.sqrt(Math.pow(dm.widthPixels / dm.xdpi, 2)
                              + Math.pow(dm.heightPixels / dm.ydpi, 2));
        sc.put("inch", Math.round(inch * 10) / 10.0);
        o.put("screen", sc);

        JSONArray cams = new JSONArray();
        try {
            int n = android.hardware.Camera.getNumberOfCameras();
            for (int i = 0; i < n; i++) {
                android.hardware.Camera.CameraInfo ci = new android.hardware.Camera.CameraInfo();
                android.hardware.Camera.getCameraInfo(i, ci);
                JSONObject cam = new JSONObject();
                cam.put("id", i);
                cam.put("facing", ci.facing == android.hardware.Camera.CameraInfo.CAMERA_FACING_FRONT
                        ? "front" : "back");
                cams.put(cam);
            }
        } catch (Throwable t) {
            cams = new JSONArray();
        }
        o.put("cameras", cams);

        PackageManager pm = c.getPackageManager();
        o.put("mic", false);            // no voice in this app yet: the panel offers no microphone widget
        o.put("speaker", pm.hasSystemFeature("android.hardware.audio.output"));
        o.put("bt", pm.hasSystemFeature(PackageManager.FEATURE_BLUETOOTH));
        o.put("multitouch", pm.hasSystemFeature(PackageManager.FEATURE_TOUCHSCREEN_MULTITOUCH));

        JSONArray sens = new JSONArray();
        SensorManager sm = (SensorManager) c.getSystemService(Context.SENSOR_SERVICE);
        if (sm != null) {
            List<Sensor> all = sm.getSensorList(Sensor.TYPE_ALL);
            for (int i = 0; i < all.size() && i < 12; i++) sens.put(all.get(i).getName());
        }
        o.put("sensors", sens);

        Intent bi = c.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (bi != null) {
            int lvl = bi.getIntExtra("level", -1), scale = bi.getIntExtra("scale", 100);
            if (lvl >= 0 && scale > 0) o.put("battery", lvl * 100 / scale);
            o.put("charging", bi.getIntExtra("plugged", 0) != 0);
        }
        o.put("charge_ctl", Charge.mode());
        o.put("hold", Charge.holding());

        ActivityManager am = (ActivityManager) c.getSystemService(Context.ACTIVITY_SERVICE);
        ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
        if (am != null) { am.getMemoryInfo(mi); o.put("ram", mi.totalMem / 1048576L); }

        o.put("android", Build.VERSION.RELEASE);
        o.put("sdk", Build.VERSION.SDK_INT);
        o.put("model", Build.MANUFACTURER + " " + Build.MODEL);
        o.put("ip", ip());
        o.put("app", APP_VER);
        JSONArray f = new JSONArray();
        f.put("panel"); f.put("pair"); f.put("settings"); f.put("camera");
        o.put("cam_port", CamServer.PORT);
        o.put("features", f);
        return o;
    }

    /** The register body: flat fields for the old parser, caps for the full passport. */
    public static JSONObject passport(Context c, String id, String code) throws Exception {
        JSONObject caps = caps(c);
        JSONObject o = new JSONObject();
        o.put("id", id);
        o.put("code", code);
        o.put("name", Prefs.name(c));
        o.put("model", caps.optString("model"));
        o.put("android", caps.optString("android"));
        JSONObject sc = caps.getJSONObject("screen");
        o.put("w", sc.getInt("w"));
        o.put("h", sc.getInt("h"));
        JSONArray flat = new JSONArray();
        JSONArray cams = caps.getJSONArray("cameras");
        for (int i = 0; i < cams.length(); i++) flat.put(cams.getJSONObject(i).optString("facing"));
        o.put("cams", flat);
        o.put("mic", caps.optBoolean("mic"));
        o.put("caps", caps);
        return o;
    }

    public static String ip() {
        try {
            java.util.Enumeration<java.net.NetworkInterface> ns =
                    java.net.NetworkInterface.getNetworkInterfaces();
            while (ns.hasMoreElements()) {
                java.net.NetworkInterface n = ns.nextElement();
                if (n.isLoopback() || !n.isUp()) continue;
                java.util.Enumeration<java.net.InetAddress> as = n.getInetAddresses();
                while (as.hasMoreElements()) {
                    java.net.InetAddress a = as.nextElement();
                    if (a instanceof java.net.Inet4Address) return a.getHostAddress();
                }
            }
        } catch (Exception e) {
            return "";
        }
        return "";
    }
}
