package org.yashome.panel;

import android.webkit.WebView;
import android.webkit.WebViewClient;

/** Tells the activity honestly whether the page came up. A top-level class, not an
 *  anonymous one: d8 fails on anonymous classes with an internal error. */
public class PanelClient extends WebViewClient {
    private final Launcher owner;
    private boolean failed = false;
    public PanelClient(Launcher owner) { this.owner = owner; }

    // onPageFinished fires on an error page too, so on its own it proves nothing:
    // a load counts only if no onReceivedError came between start and finish.
    @Override public void onPageStarted(WebView v, String url, android.graphics.Bitmap fav) {
        failed = false;
    }

    // Only the house's own pages (and about:blank): a link elsewhere, a redirect away, a data: or
    // file: page never loads here, so the Panel bridge is never handed to another origin.
    @Override public boolean shouldOverrideUrlLoading(WebView v, android.webkit.WebResourceRequest req) {
        return !sameOrigin(req.getUrl().toString());
    }

    @SuppressWarnings("deprecation")
    @Override public boolean shouldOverrideUrlLoading(WebView v, String url) {
        return !sameOrigin(url);
    }

    private boolean sameOrigin(String url) {
        String home = Prefs.url(owner);
        return url.startsWith("about:") || (!home.isEmpty() && (url.equals(home) || url.startsWith(home + "/")));
    }

    @Override public void onReceivedError(WebView v, int code, String descr, String url) {
        failed = true;
        owner.loaded = false;
        owner.waiting = false;
        owner.showWaiting();
        owner.retryLoad();
    }

    // The "waiting for the network" screen is a page too; counting it as success stopped
    // the retries. Success is only the panel's own address.
    @Override public void onPageFinished(WebView v, String url) {
        if (failed || url == null || !url.startsWith(Prefs.url(owner))) return;
        owner.loaded = true; owner.waiting = false;
    }
}
