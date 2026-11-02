package org.yashome.panel;

/** The camera stays open only while somebody watches it: otherwise its LED is on
 *  and the battery drains for nothing. */
public class Idle extends Thread {
    private final CamServer srv;

    public Idle(CamServer srv) { this.srv = srv; }

    @Override public void run() {
        while (!isInterrupted()) {
            try { Thread.sleep(3000); } catch (InterruptedException e) { return; }
            if (srv.idleTooLong()) srv.closeAsync();
        }
    }
}
