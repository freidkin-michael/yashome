package org.yashome.panel;

/** From the pairing thread to the UI. A class of its own, not an anonymous one:
 *  d8 fails on anonymous classes with an internal error. */
public class UiState implements Runnable {
    public static final int SHOW = 0;
    public static final int OPEN = 1;
    public static final int SETTINGS = 2;
    public static final int TOAST = 3;

    private final Launcher owner;
    private final int what;
    private final String status;
    private final String code;

    public UiState(Launcher owner, int what, String status, String code) {
        this.owner = owner; this.what = what; this.status = status; this.code = code;
    }

    @Override public void run() {
        if (what == OPEN) owner.openPanel();
        else if (what == SETTINGS) owner.openSettings();
        else if (what == TOAST) owner.toast(status);
        else owner.showPairing(status, code);
    }
}
