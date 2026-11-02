#!/usr/bin/env python3
"""Wake-on-LAN for every plug-in (app.py: core.wake_on_lan) and the webos CLI. Standard library only.
A docker-bridge container cannot broadcast to the LAN (NAT eats it): the host's relay (plugins/transports/webos/host/
wol-relay.py, its socket /run/yashome/wol.sock: tools/hostsock.py) gets "WOL <mac>" first when it is there; the direct
broadcast goes too, one failing never stops the other."""
import importlib.util
import os
import socket
import sys


def _hostsock():
    if "yashome_hostsock" not in sys.modules:                # the backend loads it first; the webos CLI here
        spec = importlib.util.spec_from_file_location("yashome_hostsock", os.path.join(os.path.dirname(__file__), "hostsock.py"))
        mod = sys.modules["yashome_hostsock"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return sys.modules["yashome_hostsock"]


def wake(mac: str, broadcast="255.255.255.255"):
    """Send a Wake-on-LAN magic packet to the NIC with this MAC (aa:bb:cc:dd:ee:ff or with -)."""
    m = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    pkt = b"\xff" * 6 + m * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        send(s, pkt, mac, broadcast)
    finally:
        s.close()


def send(s, pkt, mac, broadcast):
    hs = _hostsock()
    if hs.there("wol"):
        try:
            hs.datagram("wol", b"WOL " + mac.encode())
        except OSError as e:
            print(f"[wol] the host relay did not take it: {e}", file=sys.stderr, flush=True)
    for port in (9, 7):
        try:
            s.sendto(pkt, (broadcast, port))
        except OSError:
            pass


if __name__ == "__main__":
    wake(sys.argv[1])
