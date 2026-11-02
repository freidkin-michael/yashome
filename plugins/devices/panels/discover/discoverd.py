#!/usr/bin/env python3
"""Invites Android panels while pairing is open on the dashboard (UDP broadcast, port 51823).

Runs in the host's network (a broadcast from the dashboard's bridge network reaches no tablet).
The invitation carries the dashboard's address and the session's word; a tablet answers with
the word and then registers over HTTP itself. Who answered goes to discovered.json.
Files: STATE_DIR/pairing.json (written by the plug-in), STATE_DIR/discovered.json (written here).
"""
import json
import os
import socket
import tempfile
import time

PORT = 51823
PROTO = "pwpanel/1"
TTL = 60
STATE = os.environ.get("STATE_DIR", "/state")
FLAG = os.path.join(STATE, "pairing.json")
OUT = os.path.join(STATE, "discovered.json")
ARP = os.path.join(STATE, "arp.json")
BCAST = "255.255.255.255"
DASH_PORT = int(os.environ.get("DASHBOARD_PORT") or 8765)


def pairing():
    try:
        with open(FLAG) as f:
            d = json.load(f)
        return float(d.get("until", 0)), str(d.get("magic", ""))
    except (OSError, ValueError):
        return 0.0, ""


def server_url() -> str:
    """This host's LAN address (the one of the default route) and the dashboard port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return f"http://{s.getsockname()[0]}:{DASH_PORT}"
    except OSError:
        return ""
    finally:
        s.close()


def flush(seen: dict):
    now = time.time()
    fresh = sorted((dict(v, age=round(now - v["seen"], 1)) for v in seen.values() if now - v["seen"] < TTL),
                   key=lambda d: str(d.get("name", "")))
    fd, tmp = tempfile.mkstemp(dir=STATE, prefix=".discovered.")
    with os.fdopen(fd, "w") as f:
        json.dump({"devices": fresh, "updated": now}, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, OUT)


def arp():
    """The host's neighbour table, IPv4 -> MAC (flat, lower case): the backend flashes a panel only at the
    address its bound MAC really has, not at whatever address a broker client claims."""
    out = {}
    try:
        with open("/proc/net/arp") as f:
            for line in f.read().splitlines()[1:]:
                p = line.split()
                if len(p) >= 4 and p[2] != "0x0" and p[3].replace(":", "").strip("0"):
                    out[p[0]] = p[3].replace(":", "").lower()
    except OSError:
        return
    fd, tmp = tempfile.mkstemp(dir=STATE, prefix=".arp.")
    with os.fdopen(fd, "w") as f:
        json.dump({"updated": time.time(), "arp": out}, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ARP)


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(("", PORT))
    s.settimeout(2)
    seen, last_bcast, last_flush = {}, 0.0, 0.0
    print(f"panels-discover: port {PORT}, broadcast {BCAST}, state {STATE}", flush=True)
    while True:
        now = time.time()
        until, magic = pairing()
        if now < until and magic and now - last_bcast >= 2:
            msg = {"proto": PROTO, "role": "server", "magic": magic, "port": PORT, "url": server_url(), "pairing": True}
            try:
                s.sendto(json.dumps(msg).encode(), (BCAST, PORT))
            except OSError as e:
                print(f"panels-discover: broadcast failed: {e}", flush=True)
            last_bcast = now
        try:
            data, addr = s.recvfrom(4096)
            p = json.loads(data.decode("utf-8", "replace"))
            if (isinstance(p, dict) and p.get("proto") == PROTO and p.get("role") != "server"
                    and isinstance(p.get("id"), str) and magic and p.get("magic") == magic):
                seen[p["id"][:64]] = {"id": p["id"][:64], "name": str(p.get("name", ""))[:64],
                                      "model": str(p.get("model", ""))[:64], "ip": addr[0], "seen": time.time()}
                if len(seen) > 50:
                    seen.pop(min(seen, key=lambda k: seen[k]["seen"]))
                last_flush = 0.0
        except (socket.timeout, ValueError):
            pass
        for k in [k for k, v in seen.items() if time.time() - v["seen"] > TTL]:
            seen.pop(k)
        if time.time() - last_flush > 4:
            try:
                flush(seen)
                arp()
            except OSError as e:
                print(f"panels-discover: {OUT}: {e}", flush=True)
            last_flush = time.time()


if __name__ == "__main__":
    main()
