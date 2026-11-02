package org.yashome.panel;

import android.app.Activity;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.WindowManager;

/** Brightness from the device's own sensors: nobody near - minimum, a hand at the screen -
 *  middle, a hand and a bright room - maximum. "Near" is the proximity sensor (a few cm),
 *  so after the hand leaves the level holds HOLD_MS more, or the screen would blink.
 *  Without a proximity sensor (most tablets) brightness is left alone. */
public class Ambient implements SensorEventListener, Runnable {
    static final float MIN = 0.04f, MID = 0.5f, MAX = 1.0f;
    static final float BRIGHT_LUX = 250f;     // a bright room, daylight
    static final long HOLD_MS = 30000;
    private final Activity act;
    private final SensorManager sm;
    private final Sensor prox, light;
    private final Handler h = new Handler(Looper.getMainLooper());
    private boolean near = false;
    private float lux = -1;
    private long lastNear = 0;
    private float applied = -2;

    public Ambient(Activity a) {
        act = a;
        sm = (SensorManager) a.getSystemService(Activity.SENSOR_SERVICE);
        prox = sm == null ? null : sm.getDefaultSensor(Sensor.TYPE_PROXIMITY);
        light = sm == null ? null : sm.getDefaultSensor(Sensor.TYPE_LIGHT);
    }

    public boolean available() { return prox != null; }

    public void start() {
        if (!available()) return;
        sm.registerListener(this, prox, SensorManager.SENSOR_DELAY_NORMAL);
        if (light != null) sm.registerListener(this, light, SensorManager.SENSOR_DELAY_NORMAL);
        apply();
    }

    public void stop() {
        if (!available()) return;
        sm.unregisterListener(this);
        h.removeCallbacks(this);
        set(WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE);
    }

    @Override public void onSensorChanged(SensorEvent e) {
        if (e.sensor.getType() == Sensor.TYPE_PROXIMITY) {
            // binary sensors report 0/5, rangers centimetres: the threshold is half the range
            near = e.values[0] < Math.max(1f, prox.getMaximumRange() * 0.5f);
            if (near) lastNear = System.currentTimeMillis();
        } else if (e.sensor.getType() == Sensor.TYPE_LIGHT) {
            lux = e.values[0];
        }
        apply();
    }

    @Override public void onAccuracyChanged(Sensor s, int a) { }

    /** On the timer: the hold after the hand left has run out. */
    @Override public void run() { apply(); }

    private void apply() {
        long since = System.currentTimeMillis() - lastNear;
        boolean present = near || since < HOLD_MS;
        set(!present ? MIN : (lux >= BRIGHT_LUX ? MAX : MID));
        h.removeCallbacks(this);
        if (present && !near) h.postDelayed(this, HOLD_MS - since + 50);
    }

    private void set(float v) {
        if (v == applied) return;
        applied = v;
        WindowManager.LayoutParams lp = act.getWindow().getAttributes();
        lp.screenBrightness = v;
        act.getWindow().setAttributes(lp);
        Log.i("panelamb", "brightness " + v + " near=" + near + " lux=" + lux);
    }
}
