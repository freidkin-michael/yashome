package org.yashome.panel;

import android.app.Activity;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.FrameLayout;

/** The home screen: shows the pairing code until bound, then the server's layout
 *  (panel-view.html, the same source of truth as the ESP screens).
 *  No anonymous classes on purpose: d8 fails on them with an internal error. */
public class Launcher extends Activity implements Runnable,
        android.view.TextureView.SurfaceTextureListener {
    WebView web;
    boolean loaded = false;
    boolean waiting = false;
    private int tries = 0;
    private String lastUrl = "";
    private Pairing pairing;
    private CamServer cams;
    private Heartbeat beat;
    String lastBeatError = "";
    Ambient ambient;
    TouchMouse touchMouse;
    static boolean forceReload = false;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        setRequestedOrientation(orientationFlag(Prefs.orientation(this)));
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                           | WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        web.setBackgroundColor(0xFF000000);
        web.setWebViewClient(new PanelClient(this));
        web.addJavascriptInterface(new Bridge(this), "Panel");

        FrameLayout root = new FrameLayout(this);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        View corner = new View(this);
        corner.setOnTouchListener(new TapCorner(this));
        root.addView(corner, new FrameLayout.LayoutParams(240, 120, Gravity.TOP | Gravity.LEFT));
        android.view.TextureView tv = new android.view.TextureView(this);
        tv.setSurfaceTextureListener(this);
        root.addView(tv, new FrameLayout.LayoutParams(2, 2, Gravity.BOTTOM | Gravity.RIGHT));
        setContentView(root);
        ambient = new Ambient(this);
        touchMouse = new TouchMouse();
        hideBars();
        ensurePermissions();
        cams = new CamServer(this);
        cams.setDaemon(true);
        cams.start();
        beat = new Heartbeat(this);
        beat.setDaemon(true);
        beat.start();
        IntentFilter f = new IntentFilter(Intent.ACTION_AIRPLANE_MODE_CHANGED);
        f.addAction(WifiManager.WIFI_STATE_CHANGED_ACTION);
        f.addAction(WifiManager.NETWORK_STATE_CHANGED_ACTION);
        registerReceiver(new Link(), f);
        begin();
    }

    /** The orientation comes from the layout with the heartbeat answer; it is kept so a
     *  reboot does not flash a landscape frame. System rotation is re-locked every tick. */
    void applyPanel(String panel) {
        if (panel.length() == 0 || panel.equals(Prefs.panel(this))) return;
        Prefs.setPanel(this, panel);
        runOnUiThread(new Runnable() { public void run() { begin(); } });
    }

    void applyOrientation(final String o) {
        Rotation.lock(this, o);
        if (o.equals(Prefs.orientation(this)) && getRequestedOrientation() == orientationFlag(o)) return;
        Prefs.setOrientation(this, o);
        runOnUiThread(new Runnable() { public void run() { setRequestedOrientation(orientationFlag(o)); } });
    }

    static int orientationFlag(String o) {
        if ("portrait".equals(o)) return android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT;
        if ("reverse_portrait".equals(o)) return android.content.pm.ActivityInfo.SCREEN_ORIENTATION_REVERSE_PORTRAIT;
        if ("reverse_landscape".equals(o)) return android.content.pm.ActivityInfo.SCREEN_ORIENTATION_REVERSE_LANDSCAPE;
        return android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE;
    }

    /** Asked once on the first start: nobody answers a dialog on the wall later. */
    private void ensurePermissions() {
        if (android.os.Build.VERSION.SDK_INT < 23) return;
        java.util.ArrayList<String> need = new java.util.ArrayList<String>();
        if (checkSelfPermission(android.Manifest.permission.CAMERA)
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            need.add(android.Manifest.permission.CAMERA);
        }
        if (!need.isEmpty()) requestPermissions(need.toArray(new String[need.size()]), 7);
    }

    /** The server host of a dashboard URL, empty when it does not parse. */
    static String hostOf(String url) {
        try {
            String h = new java.net.URI(url).getHost();
            return h == null ? "" : h;
        } catch (Exception e) {
            return "";
        }
    }

    void settingsSoon() {
        runOnUiThread(new UiState(this, UiState.SETTINGS, null, null));
    }

    void openSettings() {
        startActivity(new android.content.Intent(this, SettingsActivity.class));
    }

    void toast(String t) {
        android.widget.Toast.makeText(this, t, android.widget.Toast.LENGTH_SHORT).show();
    }

    /** Either we are already somebody's panel, or we go pairing. */
    void begin() {
        web.removeCallbacks(this);
        pairShown = "";
        tries = 0;
        loaded = false;
        if (Prefs.configured(this)) {
            stopPairing();
            run();
        } else {
            showPairing(getString(R.string.pair_waiting), Prefs.code(this));
            startPairing();
        }
    }

    /** Pair-again gives up and shows the panel again after this: a corner held by mistake must not leave the wall
     *  without its panel. A fixed safety limit, not a preference. */
    static final long PAIR_BACK_MS = 300000;
    private final Runnable pairBack = () -> {
        if (Prefs.configured(this) && pairing != null) { stopPairing(); pairShown = ""; openPanel(); }
    };

    /** Long press on the brand corner: pair again; no bind in 5 minutes -> back to the panel. */
    void pairSoon() {
        runOnUiThread(() -> {
            touchMouse.stop();
            web.removeCallbacks(this);
            pairShown = "";
            showPairing(getString(R.string.pair_again), Prefs.code(this));
            startPairing();
            web.removeCallbacks(pairBack);
            web.postDelayed(pairBack, PAIR_BACK_MS);
        });
    }

    void startPairing() {
        if (pairing != null && pairing.isAlive()) return;
        pairing = new Pairing(this);
        pairing.start();
    }

    void stopPairing() {
        if (pairing != null) { pairing.stopSoon(); pairing = null; }
    }

    /** The pairing screen only while the pairing thread lives: a show queued before stopPairing()
     *  used to cover the panel that had just opened, with nothing listening behind it. */
    public void post(String status, String code) {
        Thread from = Thread.currentThread();                 // the pairing thread that asks for the screen
        runOnUiThread(() -> {
            if (pairing == null || pairing != from || pairing.stopped()) return;
            new UiState(this, UiState.SHOW, status, code).run();
        });
    }

    public void openPanelSoon() {
        runOnUiThread(new UiState(this, UiState.OPEN, null, null));
    }

    void openPanel() {
        stopPairing();
        tries = 0;
        loaded = false;
        waiting = false;
        run();
    }

    /** After a reboot Wi-Fi comes up later than the app, so the first tries fail.
     *  PanelClient confirms success: an error page also reaches 100% progress. */
    @Override public void run() {
        if (loaded || !Prefs.configured(this)) return;
        lastUrl = Prefs.panelUrl(this);
        web.loadUrl(lastUrl);
        tries++;                                      // no cap: a wall panel waits for its server as long as it takes
        web.postDelayed(this, tries < 10 ? 8000 : tries < 30 ? 20000 : 60000);
    }

    /** The page failed after it had loaded (the server restarted, the page reloaded into it): the
     *  waiting screen is up, so try again until it is back. The first loop ended with the first load. */
    void retryLoad() {
        web.removeCallbacks(this);
        tries = 0;
        web.postDelayed(this, 8000);
    }

    /** Our own screen instead of the WebView error page: the wall shows that the
     *  panel is alive and why there is no network. Every failure redraws the reason. */
    void showWaiting() {
        if (!Prefs.configured(this)) return;
        String w = Link.why(this);
        if (w.length() == 0) w = getString(R.string.wait_server, hostOf(Prefs.url(this)));
        if (waiting && w.equals(waitShown)) return;
        waiting = true;
        waitShown = w;
        html("<i></i><span>" + getString(R.string.wait_net, w) + "</span>", "");
    }

    private String waitShown = "";

    private String pairShown = "";

    void showPairing(String status, String code) {
        String sig = status + "|" + code;
        if (sig.equals(pairShown)) return;
        pairShown = sig;
        waiting = true;
        String big = (code != null && code.length() > 0)
                ? "<div class=code>" + code + "</div>" : "";
        String st = status == null ? "" : status.replace("&", "&amp;").replace("<", "&lt;")
                                                .replace("\n", "<br>");
        html("<div class=box>" + big + "<div class=st>" + st + "</div></div>",
             "<div class=foot>" + Prefs.deviceId(this) + " &middot; " + Caps.ip() + "</div>");
    }

    private void html(String body, String foot) {
        String h = "<html><head><meta name=viewport content='width=device-width,initial-scale=1'>"
          + "<style>html,body{margin:0;height:100%;background:#0e1116;color:#8a93a3;"
          + "font:16px system-ui,sans-serif;display:flex;align-items:center;"
          + "justify-content:center;gap:12px;text-align:center}"
          + ".box{display:flex;flex-direction:column;gap:14px;align-items:center}"
          + ".code{font-size:64px;letter-spacing:10px;color:#e8ecf3;font-weight:600}"
          + ".st{font-size:15px;line-height:1.5}"
          + ".foot{position:fixed;bottom:10px;left:0;right:0;font-size:12px;color:#4b5566}"
          + "i{width:14px;height:14px;border-radius:50%;background:#ffcf5c;"
          + "animation:b 1.2s infinite}"
          + "@keyframes b{0%,100%{opacity:1}50%{opacity:.15}}</style></head><body>"
          + body + foot + "</body></html>";
        web.loadDataWithBaseURL(null, h, "text/html", "utf-8", null);
    }

    private void hideBars() {
        getWindow().getDecorView().setSystemUiVisibility(
              View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            | View.SYSTEM_UI_FLAG_FULLSCREEN
            | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
    }

    // Back from settings: the layout may have changed, the pairing may be reset
    @Override protected void onResume() {
        super.onResume();
        hideBars();
        ambient.start();
        boolean changed = !Prefs.panelUrl(this).equals(lastUrl);
        if (!Prefs.configured(this) || !loaded || changed || forceReload) {
            forceReload = false;
            begin();
        }
    }

    @Override protected void onPause() {
        super.onPause();
        ambient.stop();
        touchMouse.stop();
    }

    @Override public void onWindowFocusChanged(boolean f) {
        super.onWindowFocusChanged(f);
        if (f) hideBars();
    }

    @Override public void onSurfaceTextureAvailable(android.graphics.SurfaceTexture st, int w, int h) {
        if (cams != null) cams.setTexture(st);
    }

    @Override public void onSurfaceTextureSizeChanged(android.graphics.SurfaceTexture st, int w, int h) { }

    @Override public boolean onSurfaceTextureDestroyed(android.graphics.SurfaceTexture st) {
        if (cams != null) cams.setTexture(null);
        return true;
    }

    @Override public void onSurfaceTextureUpdated(android.graphics.SurfaceTexture st) { }

    // A home screen has nowhere to go back to
    @Override public void onBackPressed() { }
}
