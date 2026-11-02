package org.yashome.panel;

import android.content.Context;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.SurfaceTexture;
import android.graphics.YuvImage;
import android.hardware.Camera;
import android.os.Handler;
import android.os.HandlerThread;

import java.io.ByteArrayOutputStream;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.List;

/** The tablet's cameras the way the house already reads IP cameras: /snapshot is a JPEG,
 *  /stream MJPEG. Camera1 is driven ONLY from its own looper thread: it delivers
 *  onPreviewFrame to the opener's looper, and the HTTP thread has none (no frame ever). */
public class CamServer extends Thread implements Camera.PreviewCallback {
    public static final int PORT = 8081;
    private static final long IDLE_MS = 12000;

    final Context ctx;              // Conn reads the camera key from the prefs
    private ServerSocket srv;
    private Handler h;
    private Camera cam;
    private volatile int camId = -1, wantedId = -1;
    private volatile int camW = 640, camH = 480;
    private volatile long lastUse = 0;
    private volatile String camError = "";
    private byte[] frame;
    private int frameId = -1;
    private volatile SurfaceTexture tex;
    private final Object lock = new Object();
    // Camera1 opens one camera at a time: the panel polls the front one, the dashboard
    // may watch the back one; without a queue they kept stealing the open from each other
    private final Object reqLock = new Object();

    /** A real surface from the activity's TextureView: the HAL takes a bare SurfaceTexture
     *  without a GL context but never delivers frames on it. */
    public void setTexture(SurfaceTexture t) { tex = t; }

    public CamServer(Context ctx) { this.ctx = ctx; }

    @Override public void run() {
        HandlerThread ht = new HandlerThread("cam");
        ht.start();
        h = new Handler(ht.getLooper());
        try {
            srv = new ServerSocket(PORT);
        } catch (Exception e) {
            return;
        }
        Idle idle = new Idle(this);
        idle.setDaemon(true);
        idle.start();
        while (!isInterrupted()) {
            try {
                Conn c = new Conn(this, srv.accept());
                c.setDaemon(true);
                c.start();
            } catch (Exception e) {
                break;
            }
        }
    }

    // A frame is tagged with its camera: after a front/back switch the first frame still
    // comes from the old one, and would go out with the wrong size
    @Override public void onPreviewFrame(byte[] data, Camera c) {
        synchronized (lock) {
            frame = data;
            frameId = camId;
            lock.notifyAll();
        }
    }

    int idFor(String facing) {
        int n = Camera.getNumberOfCameras();
        int want = "front".equals(facing) ? Camera.CameraInfo.CAMERA_FACING_FRONT
                                          : Camera.CameraInfo.CAMERA_FACING_BACK;
        for (int i = 0; i < n; i++) {
            Camera.CameraInfo ci = new Camera.CameraInfo();
            Camera.getCameraInfo(i, ci);
            if (ci.facing == want) return i;
        }
        return n > 0 ? 0 : -1;
    }

    private void want(int id) {
        lastUse = System.currentTimeMillis();
        if (wantedId == id && camId == id) return;
        wantedId = id;
        h.post(new CamOp(this, CamOp.OPEN, id));
    }

    void doOpen(int id) {
        if (cam != null && camId == id) return;
        doClose();
        try {
            cam = Camera.open(id);
            camId = id;
            Camera.Parameters p = cam.getParameters();
            List<Camera.Size> sizes = p.getSupportedPreviewSizes();
            Camera.Size best = sizes.get(0);
            for (Camera.Size s : sizes) {
                if (s.width <= 1280 && s.width * s.height > best.width * best.height) best = s;
            }
            p.setPreviewSize(best.width, best.height);
            p.setPreviewFormat(ImageFormat.NV21);
            cam.setParameters(p);
            camW = best.width; camH = best.height;
            cam.setPreviewTexture(tex != null ? tex : new SurfaceTexture(10));
            cam.setPreviewCallback(this);
            cam.startPreview();
            camError = "";
            wantedId = id;
            android.util.Log.i("panelcam", "camera " + id + " open " + camW + "x" + camH);
        } catch (Throwable t) {
            android.util.Log.e("panelcam", "camera " + id + " did not open", t);
            camError = String.valueOf(t.getMessage());
            camId = -1;
            cam = null;
            synchronized (lock) { lock.notifyAll(); }
        }
    }

    void doClose() {
        if (cam == null) return;
        try { cam.setPreviewCallback(null); cam.stopPreview(); cam.release(); } catch (Exception e) { }
        cam = null;
        camId = -1;
        wantedId = -1;
        synchronized (lock) { frame = null; }
    }

    void closeAsync() { if (h != null) h.post(new CamOp(this, CamOp.CLOSE, -1)); }

    boolean idleTooLong() {
        return camId >= 0 && System.currentTimeMillis() - lastUse > IDLE_MS;
    }

    /** Waits for a FRESH frame: the first one after a camera switch is still the old one's. */
    byte[] jpeg(String facing, int quality) throws Exception {
        synchronized (reqLock) { return jpegLocked(facing, quality); }
    }

    private byte[] jpegLocked(String facing, int quality) throws Exception {
        int id = idFor(facing);
        if (id < 0) throw new Exception(ctx.getString(R.string.cam_none));
        camError = "";
        want(id);
        byte[] raw;
        synchronized (lock) {
            frame = null;
            long t0 = System.currentTimeMillis();
            while (frame == null || frameId != id) {
                if (camError.length() > 0) throw new Exception(ctx.getString(R.string.cam_error, camError));
                if (System.currentTimeMillis() - t0 > 10000) {
                    throw new Exception(ctx.getString(R.string.cam_no_frame, camId, wantedId));
                }
                lock.wait(200);
            }
            raw = frame;
        }
        lastUse = System.currentTimeMillis();
        YuvImage y = new YuvImage(raw, ImageFormat.NV21, camW, camH, null);
        ByteArrayOutputStream bo = new ByteArrayOutputStream();
        y.compressToJpeg(new Rect(0, 0, camW, camH), quality, bo);
        return bo.toByteArray();
    }

    static void header(OutputStream os, String status, String type, int len) throws Exception {
        String h = "HTTP/1.0 " + status + "\r\nContent-Type: " + type + "\r\n"
                 + (len >= 0 ? "Content-Length: " + len + "\r\n" : "")
                 + "Connection: close\r\n\r\n";
        os.write(h.getBytes("UTF-8"));
    }
}
