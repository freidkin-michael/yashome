package org.yashome.panel;

import java.io.OutputStream;
import java.net.Socket;

/** One HTTP request to the tablet's camera. Android has no server of its own, and a
 *  library for three paths is not worth it. */
public class Conn extends Thread {
    private final CamServer srv;
    private final Socket sock;

    public Conn(CamServer srv, Socket sock) { this.srv = srv; this.sock = sock; }

    private static final int MAX_LINE = 8192, MAX_HEADERS = 50;

    /** One request line, at most MAX_LINE bytes: an endless line from the LAN is cut, not buffered. */
    private static String line(java.io.InputStream in) throws java.io.IOException {
        java.io.ByteArrayOutputStream b = new java.io.ByteArrayOutputStream();
        int c = in.read();
        if (c == -1) return null;
        for (; c != -1 && c != '\n'; c = in.read()) {
            if (b.size() >= MAX_LINE) throw new java.io.IOException("request line too long");
            if (c != '\r') b.write(c);
        }
        return b.toString("UTF-8");
    }

    @Override public void run() {
        try {
            sock.setSoTimeout(15000);
            java.io.InputStream in = new java.io.BufferedInputStream(sock.getInputStream());
            String line = line(in);
            OutputStream os = sock.getOutputStream();
            if (line == null) { sock.close(); return; }
            String path = line.split(" ").length > 1 ? line.split(" ")[1] : "/";
            String key = "";
            int n = 0;
            for (String h = line(in); h != null && !h.isEmpty() && n++ < MAX_HEADERS; h = line(in)) {
                int c = h.indexOf(':');
                if (c > 0 && h.substring(0, c).trim().equalsIgnoreCase("X-Cam-Key")) key = h.substring(c + 1).trim();
            }
            // only the house's camera proxy knows the key (heartbeat answer): the LAN at large sees nothing
            String want = Prefs.camKey(srv.ctx);
            if (want.isEmpty() || !java.security.MessageDigest.isEqual(want.getBytes("UTF-8"), key.getBytes("UTF-8"))) {
                byte[] b = "camera key".getBytes("UTF-8");
                CamServer.header(os, "403 Forbidden", "text/plain", b.length);
                os.write(b);
                os.flush();
                sock.close();
                return;
            }
            String facing = path.contains("front") ? "front" : "back";
            if (path.startsWith("/snapshot")) {
                byte[] j = srv.jpeg(facing, 70);
                CamServer.header(os, "200 OK", "image/jpeg", j.length);
                os.write(j);
            } else if (path.startsWith("/stream")) {
                stream(os, facing);
            } else {
                byte[] b = ("{\"paths\":[\"/snapshot?cam=front\",\"/stream?cam=back\"]}").getBytes("UTF-8");
                CamServer.header(os, "200 OK", "application/json", b.length);
                os.write(b);
            }
            os.flush();
        } catch (Exception e) {
            try {
                OutputStream os = sock.getOutputStream();
                byte[] b = String.valueOf(e.getMessage()).getBytes("UTF-8");
                CamServer.header(os, "500 Error", "text/plain; charset=utf-8", b.length);
                os.write(b);
                os.flush();
            } catch (Exception ignore) { }
        }
        try { sock.close(); } catch (Exception e) { }
    }

    private void stream(OutputStream os, String facing) throws Exception {
        String h = "HTTP/1.0 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=fr\r\n"
                 + "Cache-Control: no-cache\r\nConnection: close\r\n\r\n";
        os.write(h.getBytes("UTF-8"));
        while (true) {
            byte[] j = srv.jpeg(facing, 60);
            os.write(("--fr\r\nContent-Type: image/jpeg\r\nContent-Length: " + j.length
                    + "\r\n\r\n").getBytes("UTF-8"));
            os.write(j);
            os.write("\r\n".getBytes("UTF-8"));
            os.flush();
            Thread.sleep(120);
        }
    }
}
