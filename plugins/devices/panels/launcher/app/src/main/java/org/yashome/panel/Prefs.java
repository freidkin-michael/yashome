package org.yashome.panel;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;

/** Everything the panel remembers between starts: who it is and where it goes. */
public class Prefs {
    public static SharedPreferences p(Context c) {
        return c.getSharedPreferences("panel", 0);
    }

    /** The device's own id: the full ANDROID_ID (it survives a reinstall signed with the same key), so a
     *  re-paired tablet replaces its old record on the server. A random one only without ANDROID_ID. */
    public static String deviceId(Context c) {
        String a = android.provider.Settings.Secure.getString(
                c.getContentResolver(), android.provider.Settings.Secure.ANDROID_ID);
        if (a != null && a.matches("[0-9a-fA-F]{8,32}")) return "and-" + a.toLowerCase();
        String id = p(c).getString("id", "");
        if (id.length() == 0) {
            id = "and-" + Long.toHexString(new java.security.SecureRandom().nextLong());
            p(c).edit().putString("id", id).apply();
        }
        return id;
    }

    /** A human checks the code by eye, so it lives until the pairing ends. */
    public static String code(Context c) {
        String v = p(c).getString("code", "");
        if (v.length() != 6) {
            v = String.format("%06d", new java.security.SecureRandom().nextInt(1000000));
            p(c).edit().putString("code", v).apply();
        }
        return v;
    }

    public static String name(Context c) {
        return p(c).getString("name", c.getString(R.string.default_name, Build.MODEL));
    }

    public static void setName(Context c, String v) {
        p(c).edit().putString("name", v).apply();
    }

    public static boolean configured(Context c) {
        return url(c).length() > 0 && panel(c).length() > 0;
    }

    public static String url(Context c)   { return p(c).getString("url", ""); }
    public static String token(Context c) { return p(c).getString("token", ""); }
    public static String panel(Context c) { return p(c).getString("panel", ""); }
    public static void setPanel(Context c, String v) { p(c).edit().putString("panel", v).apply(); }
    public static String orientation(Context c) { return p(c).getString("orientation", "landscape"); }
    public static String camKey(Context c) { return p(c).getString("cam_key", ""); }
    public static void setCamKey(Context c, String v) { p(c).edit().putString("cam_key", v).apply(); }
    public static void setOrientation(Context c, String v) { p(c).edit().putString("orientation", v).apply(); }

    public static String panelUrl(Context c) {
        return url(c) + "/panel-view.html?panel=" + Uri.encode(panel(c))
             + "&token=" + Uri.encode(token(c));
    }

    public static void save(Context c, String url, String token, String panel) {
        while (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        p(c).edit().putString("url", url).putString("token", token)
                   .putString("panel", panel).apply();
    }

    /** Reset pairing: the code is new too, the old one may still be on somebody's screen. */
    public static void reset(Context c) {
        p(c).edit().remove("url").remove("token").remove("panel").remove("code").remove("cam_key").apply();
    }
}
