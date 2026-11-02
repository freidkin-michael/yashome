package org.yashome.panel;

import android.app.Activity;
import android.content.ContentResolver;
import android.provider.Settings;
import android.view.Surface;

/** Rotates the whole system, not only our activity (else Recents, settings and boot follow
 *  the sensor). Auto-rotate off, user_rotation from the layout; written with WRITE_SETTINGS
 *  (appops over adb), without it through root. */
public class Rotation {
    public static void lock(Activity a, String o) {
        int want = surface(a, o);
        ContentResolver cr = a.getContentResolver();
        if (Settings.System.getInt(cr, Settings.System.ACCELEROMETER_ROTATION, 1) == 0
                && Settings.System.getInt(cr, Settings.System.USER_ROTATION, -1) == want) return;
        try {
            if (Settings.System.canWrite(a)) {
                Settings.System.putInt(cr, Settings.System.ACCELEROMETER_ROTATION, 0);
                Settings.System.putInt(cr, Settings.System.USER_ROTATION, want);
                return;
            }
        } catch (SecurityException e) { /* no permission: try root */ }
        if (Charge.rooted()) {
            Charge.su("settings put system accelerometer_rotation 0; settings put system user_rotation " + want);
        }
    }

    /** Surface.ROTATION_* counts from the screen's natural orientation: portrait on a
     *  phone, sometimes landscape on a tablet. */
    static int surface(Activity a, String o) {
        android.view.Display d = a.getWindowManager().getDefaultDisplay();
        android.graphics.Point p = new android.graphics.Point();
        d.getRealSize(p);
        int r = d.getRotation();
        boolean sideways = r == Surface.ROTATION_90 || r == Surface.ROTATION_270;
        boolean naturalPortrait = (p.x < p.y) != sideways;
        if (naturalPortrait) {
            if ("portrait".equals(o)) return Surface.ROTATION_0;
            if ("reverse_portrait".equals(o)) return Surface.ROTATION_180;
            if ("reverse_landscape".equals(o)) return Surface.ROTATION_270;
            return Surface.ROTATION_90;
        }
        if ("portrait".equals(o)) return Surface.ROTATION_270;
        if ("reverse_portrait".equals(o)) return Surface.ROTATION_90;
        if ("reverse_landscape".equals(o)) return Surface.ROTATION_180;
        return Surface.ROTATION_0;
    }
}
