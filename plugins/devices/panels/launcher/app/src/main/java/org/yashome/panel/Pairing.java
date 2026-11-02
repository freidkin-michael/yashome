package org.yashome.panel;

import android.content.Context;
import android.net.wifi.WifiManager;

import org.json.JSONObject;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;

/** Pairing: the server calls, we answer. It broadcasts an invitation with its address and
 *  the session's magic word; we answer with the same word, then come over HTTP with the
 *  passport and wait until a human checks our code on the dashboard. Only then the panel
 *  gets its token. */
public class Pairing extends Thread {
    public static final int PORT = 51823;
    public static final String PROTO = "pwpanel/1";

    /** Register again this often (the server drops a re-pair request it has not seen for its key window) and ask
     *  whether a human bound us this often: protocol pacing with the server, nothing a user tunes. */
    static final long REGISTER_EVERY_MS = 5000, POLL_EVERY_MS = 2000;

    private final Launcher owner;
    private volatile boolean stop = false;
    private String serverUrl = "";
    private String lastError = "";

    public Pairing(Launcher owner) { this.owner = owner; }

    public void stopSoon() { stop = true; interrupt(); }

    /** Stopped: a late pairing screen of it must not cover the panel. */
    public boolean stopped() { return stop; }

    @Override public void run() {
        String id = Prefs.deviceId(owner);
        String code = Prefs.code(owner);
        // a panel of a house already knows the way: no UDP invitation needed, straight over HTTP
        if (Prefs.configured(owner)) serverUrl = Prefs.url(owner);
        WifiManager.MulticastLock lock = null;
        DatagramSocket sock = null;
        try {
            WifiManager wm = (WifiManager) owner.getApplicationContext()
                    .getSystemService(Context.WIFI_SERVICE);
            if (wm != null) { lock = wm.createMulticastLock("panel-pair"); lock.acquire(); }
            sock = new DatagramSocket(null);
            sock.setReuseAddress(true);
            sock.setBroadcast(true);
            sock.bind(new InetSocketAddress(PORT));
            sock.setSoTimeout(2000);
        } catch (Exception e) {
            if (sock != null) sock.close();
            sock = null;
            lastError = owner.getString(R.string.pair_port_busy, PORT, String.valueOf(e));
            if (serverUrl.length() == 0) {
                owner.post(lastError, "");
                if (lock != null && lock.isHeld()) lock.release();
                return;
            }
        }

        byte[] buf = new byte[8192];
        long lastPoll = 0, lastReg = 0;
        show(code);
        while (!stop) {
            try {
                if (sock == null) { Thread.sleep(2000); throw new java.net.SocketTimeoutException(); }
                DatagramPacket p = new DatagramPacket(buf, buf.length);
                sock.receive(p);
                JSONObject j = new JSONObject(new String(p.getData(), 0, p.getLength(), "UTF-8"));
                if (!PROTO.equals(j.optString("proto")) || !"server".equals(j.optString("role"))) continue;
                serverUrl = j.optString("url");
                JSONObject r = new JSONObject();
                r.put("proto", PROTO);
                r.put("role", "panel");
                r.put("id", id);
                r.put("magic", j.optString("magic"));
                r.put("name", Prefs.name(owner));
                r.put("model", android.os.Build.MODEL);
                byte[] out = r.toString().getBytes("UTF-8");
                sock.send(new DatagramPacket(out, out.length, p.getAddress(),
                        j.optInt("port", PORT)));
                if (System.currentTimeMillis() - lastReg > REGISTER_EVERY_MS) {
                    lastReg = System.currentTimeMillis();
                    register(id, code);
                    show(code);
                }
            } catch (java.net.SocketTimeoutException t) {
                if (serverUrl.length() > 0 && System.currentTimeMillis() - lastReg > REGISTER_EVERY_MS) {
                    lastReg = System.currentTimeMillis();
                    register(id, code);
                }
                show(code);
            } catch (InterruptedException e) {
                break;
            } catch (Exception e) {
                lastError = String.valueOf(e.getMessage());
            }
            if (serverUrl.length() > 0 && System.currentTimeMillis() - lastPoll > POLL_EVERY_MS) {
                lastPoll = System.currentTimeMillis();
                if (poll(id)) break;
            }
        }
        if (sock != null) sock.close();
        if (lock != null && lock.isHeld()) lock.release();
    }

    private void register(String id, String code) {
        try {
            Net.post(serverUrl + "/api/panels/register",
                     Caps.passport(owner, id, code).toString());
            lastError = "";
        } catch (Exception e) {
            lastError = String.valueOf(e.getMessage());
        }
    }

    private boolean poll(String id) {
        try {
            JSONObject j = new JSONObject(Net.get(serverUrl + "/api/panels/register/" + id));
            if (!"bound".equals(j.optString("status"))) return false;
            String url = j.optString("url");
            // only an http(s) address of a house; the pairing screen showed the server it talks to
            if (!(url.startsWith("http://") || url.startsWith("https://"))) url = serverUrl;
            String tok = j.optString("token");
            // only a NEW key ends pairing: an old record says "bound" with no key (no re-pair asked yet, or not from
            // this address), and keeping the saved key would send it to whatever server answered
            if (tok.isEmpty()) return false;
            Prefs.save(owner, url, tok, j.optString("panel"));
            owner.post(owner.getString(R.string.pair_bound, j.optString("panel")), "");
            owner.openPanelSoon();
            return true;
        } catch (Exception e) {
            lastError = String.valueOf(e.getMessage());
            return false;
        }
    }

    private void show(String code) {
        if (stop) return;
        String st;
        if (serverUrl.length() == 0) {
            st = owner.getString(R.string.pair_waiting);
        } else {
            st = owner.getString(R.string.pair_server, serverUrl);
        }
        if (lastError.length() > 0) st += "\n" + lastError;
        owner.post(st, code);
    }
}
