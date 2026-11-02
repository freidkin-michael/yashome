package org.yashome.panel;

/** The panel checks in once a minute: the server updates its address (DHCP may move
 *  it) and a fresh passport - battery, cameras, launcher version. */
public class Heartbeat extends Thread {
    private static final long EVERY_MS = 60000;
    private final Launcher owner;

    public Heartbeat(Launcher owner) { this.owner = owner; }

    @Override public void run() {
        while (!isInterrupted()) {
            Adb.keep(owner);
            Link.fix(owner);
            if (Prefs.configured(owner)) {
                try {
                    org.json.JSONObject o = new org.json.JSONObject();
                    o.put("id", Prefs.deviceId(owner));
                    o.put("caps", Caps.caps(owner));
                    String r = Net.post(Prefs.url(owner) + "/api/panels/heartbeat", o.toString(),
                                        Prefs.token(owner));
                    // the panel was renamed on the server: use the new name from now on
                    owner.applyPanel(new org.json.JSONObject(r).optString("panel", ""));
                    org.json.JSONObject ch = new org.json.JSONObject(r).optJSONObject("charge");
                    if (ch != null) Charge.apply(owner, ch.optInt("min", 40), ch.optInt("max", 85));
                    owner.applyOrientation(new org.json.JSONObject(r).optString("orientation", "landscape"));
                    String ck = new org.json.JSONObject(r).optString("cam_key", "");
                    if (!ck.isEmpty()) Prefs.setCamKey(owner, ck);
                } catch (Exception e) {
                    owner.lastBeatError = String.valueOf(e.getMessage());
                }
            }
            try { Thread.sleep(EVERY_MS); } catch (InterruptedException e) { return; }
        }
    }
}
