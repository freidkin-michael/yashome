package org.yashome.panel;

import android.content.Intent;
import android.view.MotionEvent;
import android.view.View;

/** The brand corner: three taps in a row open settings, a 10 s hold starts pairing.
 *  No buttons on the screen on purpose: the wall panel shows only the house. */
public class TapCorner implements View.OnTouchListener {
    static final long PAIR_HOLD_MS = 10000;
    private final Launcher owner;
    private int taps = 0;
    private long first = 0;
    private final Runnable pair;

    public TapCorner(Launcher owner) {
        this.owner = owner;
        this.pair = owner::pairSoon;
    }

    @Override public boolean onTouch(View v, MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                v.postDelayed(pair, PAIR_HOLD_MS);
                return true;
            case MotionEvent.ACTION_UP:
                v.removeCallbacks(pair);
                if (e.getEventTime() - e.getDownTime() < 1000) tap();
                return true;
            case MotionEvent.ACTION_CANCEL:
                v.removeCallbacks(pair);
                return true;
            default:
                return true;
        }
    }

    private void tap() {
        long now = System.currentTimeMillis();
        if (now - first > 2500) { first = now; taps = 0; }
        if (++taps >= 3) {
            taps = 0;
            owner.startActivity(new Intent(owner, SettingsActivity.class));
        }
    }
}
