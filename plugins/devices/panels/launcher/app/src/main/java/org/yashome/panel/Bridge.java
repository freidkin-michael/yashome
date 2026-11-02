package org.yashome.panel;

import android.webkit.JavascriptInterface;

/** The bridge for the panel page: a browser page cannot reach the tablet's camera,
 *  the kiosk settings or the touch mouse on its own. */
public class Bridge {
    private final Launcher owner;

    public Bridge(Launcher owner) { this.owner = owner; }

    // Voice comes with the voice plug-in later: kept as no-ops so the page does not break
    @JavascriptInterface public void voiceStart() { }
    @JavascriptInterface public void voiceStop()  { }
    @JavascriptInterface public boolean voiceBusy() { return false; }
    @JavascriptInterface public void settings()   { owner.settingsSoon(); }
    @JavascriptInterface public int camPort()     { return CamServer.PORT; }
    @JavascriptInterface public String status()   { return Status.json(owner); }
    @JavascriptInterface public void touchmouseStart(String host, int port) { owner.touchMouse.start(host, port); }
    @JavascriptInterface public void touchmouseStop() { owner.touchMouse.stop(); }
    @JavascriptInterface public void tmMove(int dx, int dy) { owner.touchMouse.move(dx, dy); }
    @JavascriptInterface public void tmClick(boolean right) { owner.touchMouse.click(right); }
    @JavascriptInterface public void tmKey(String k) { owner.touchMouse.key(k); }
    @JavascriptInterface public void tmText(String t) { owner.touchMouse.text(t); }
    @JavascriptInterface public void tmScroll(int n) { owner.touchMouse.scroll(n); }
    @JavascriptInterface public void tmMiddle() { owner.touchMouse.middle(); }

    // cutover-compat: older names, still called by panel pages from before the touchmouse rename
    @JavascriptInterface public void airmouseStart(String host, int port) { touchmouseStart(host, port); }
    @JavascriptInterface public void airmouseStop() { touchmouseStop(); }
    @JavascriptInterface public void amMove(int dx, int dy) { tmMove(dx, dy); }
    @JavascriptInterface public void amClick(boolean right) { tmClick(right); }
    @JavascriptInterface public void amKey(String k) { tmKey(k); }

    @JavascriptInterface public String caps() {
        try { return Caps.caps(owner).toString(); } catch (Exception e) { return "{}"; }
    }
}
