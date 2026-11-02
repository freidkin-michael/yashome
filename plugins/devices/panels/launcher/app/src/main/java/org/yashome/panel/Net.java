package org.yashome.panel;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** The two requests the panel needs. An error carries the body text: without it
 *  "not bound" on the wall looks the same whatever the cause. */
public class Net {
    public static String post(String url, String body) throws Exception {
        return post(url, body, "");
    }

    public static String post(String url, String body, String token) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod("POST");
        c.setConnectTimeout(4000);
        c.setReadTimeout(8000);
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json");
        if (token != null && token.length() > 0) {
            c.setRequestProperty("Authorization", "Bearer " + token);
        }
        OutputStream os = c.getOutputStream();
        os.write(body.getBytes("UTF-8"));
        os.close();
        return read(c);
    }

    public static String get(String url) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(4000);
        c.setReadTimeout(8000);
        return read(c);
    }

    private static String read(HttpURLConnection c) throws Exception {
        int code = c.getResponseCode();
        InputStream is = (code >= 400) ? c.getErrorStream() : c.getInputStream();
        ByteArrayOutputStream bo = new ByteArrayOutputStream();
        if (is != null) {
            byte[] b = new byte[4096];
            int n;
            while ((n = is.read(b)) > 0) bo.write(b, 0, n);
            is.close();
        }
        c.disconnect();
        String s = bo.toString("UTF-8");
        if (code >= 400) throw new Exception("HTTP " + code + ": " + s);
        return s;
    }
}
