package org.yashome.panel;

/** Camera work runs on the camera's own thread. A class, not an anonymous
 *  Runnable: d8 fails on anonymous classes with an internal error. */
public class CamOp implements Runnable {
    public static final int OPEN = 0, CLOSE = 1;

    private final CamServer srv;
    private final int op, id;

    public CamOp(CamServer srv, int op, int id) { this.srv = srv; this.op = op; this.id = id; }

    @Override public void run() {
        if (op == OPEN) srv.doOpen(id);
        else srv.doClose();
    }
}
