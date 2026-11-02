package org.yashome.panel;

import android.os.Handler;
import android.os.HandlerThread;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;

/** Touch mouse for a media box: the touchpad on the panel page sends ready moves here.
 *  Sent as UDP to the receiver: "m dx dy", "b l|r|m 1|0", "w n",
 *  "k back|forward|playpause|voldown|mute|volup|home|kbd|up|down|left|right|enter", "t text". */
public class TouchMouse {
    private static final java.util.Set<String> KEYS = new java.util.HashSet<>(java.util.Arrays.asList(
            "back", "forward", "playpause", "voldown", "mute", "volup", "home", "kbd", "up", "down", "left", "right", "enter"));
    private HandlerThread th;
    private Handler h;
    private DatagramSocket sock;
    private InetAddress addr;
    private int port;

    public synchronized void start(String host, int p) {
        stop();
        th = new HandlerThread("touchmouse");
        th.start();
        h = new Handler(th.getLooper());
        port = p;
        h.post(() -> {
            try {
                addr = InetAddress.getByName(host);
                sock = new DatagramSocket();
            } catch (Exception e) {
                sock = null;
            }
        });
    }

    public synchronized void stop() {
        if (th == null) return;
        final HandlerThread t = th;
        h.post(() -> {
            if (sock != null) sock.close();
            sock = null;
            t.quitSafely();
        });
        th = null;
        h = null;
    }

    public void move(int dx, int dy) {
        if (dx != 0 || dy != 0) send("m " + dx + " " + dy);
    }

    public void click(boolean right) {
        String b = right ? "r" : "l";
        send("b " + b + " 1");
        send("b " + b + " 0");
    }

    public void middle() {
        send("b m 1");
        send("b m 0");
    }

    public void scroll(int n) {
        if (n != 0) send("w " + n);
    }

    public void key(String k) {
        if (KEYS.contains(k)) send("k " + k);
    }

    /** Text goes as "t <chunk>" datagrams of at most 190 bytes of UTF-8, cut between characters, in order. */
    public void text(String t) {
        if (t == null) return;
        int start = 0;
        while (start < t.length()) {
            int end = start, bytes = 0;
            while (end < t.length()) {
                int cp = t.codePointAt(end);
                int n = new String(Character.toChars(cp)).getBytes(StandardCharsets.UTF_8).length;
                if (bytes + n > 190) break;
                bytes += n;
                end += Character.charCount(cp);
            }
            if (end == start) break;
            sendBytes(("t " + t.substring(start, end)).getBytes(StandardCharsets.UTF_8));
            start = end;
        }
    }

    private void send(String msg) {
        sendBytes(msg.getBytes(StandardCharsets.US_ASCII));
    }

    private void sendBytes(byte[] b) {
        Handler hh = h;
        if (hh == null) return;
        hh.post(() -> {
            try {
                if (sock == null) return;
                sock.send(new DatagramPacket(b, b.length, addr, port));
            } catch (Exception ignored) {
            }
        });
    }
}
