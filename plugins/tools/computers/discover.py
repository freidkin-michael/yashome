"""Discovery module of the computers plug-in (run by the core's `discover` container, host network).
One broadcast tells the house what runs where: "? yashome 1" to UDP 7708 every INTERVAL seconds; every computer's
hello responder (helper/roles/hello-install.sh) answers {"yashome": 1, "name", "servers": [...], "ports": {...}}.
Who answered goes to plugin-state/computers/discovered.json (read by this plug-in and by the plug-ins of those
servers, e.g. touchmouse). Asks from UDP 7707: a host firewall lets the LAN in there."""
import json
import os
import re
import socket
import tempfile
import time

PORT = int(os.environ.get("YASHOME_HELLO_PORT") or 7708)
FROM_PORT = int(os.environ.get("YASHOME_DISCOVER_PORT") or 7707)   # asked from a fixed port: the answers come back to it,
# and a host firewall needs one rule for them (a reply to a broadcast matches no conntrack entry)
INTERVAL = 30
LISTEN_S = 3                       # answers are taken this long after a broadcast
TTL = 300                          # a computer that stopped answering is dropped after this
BCAST = "255.255.255.255"
NAME = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
SERVER = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def parse(data: bytes):
    try:
        p = json.loads(data.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(p, dict) or p.get("yashome") != 1 or not NAME.match(str(p.get("name") or "")):
        return None
    servers = [x for x in p.get("servers") or [] if isinstance(x, str) and SERVER.match(x)][:32]
    ports = {k: v for k, v in (p.get("ports") or {}).items()
             if isinstance(k, str) and SERVER.match(k) and isinstance(v, int) and 0 < v < 65536} if isinstance(p.get("ports"), dict) else {}
    return {"name": p["name"], "servers": sorted(set(servers)), "ports": ports}


def flush(seen: dict, state):
    now = time.time()
    fresh = sorted((dict(v) for v in seen.values() if now - v["seen"] < TTL), key=lambda d: d["host"])
    fd, tmp = tempfile.mkstemp(dir=state, prefix=".discovered.")
    with os.fdopen(fd, "w") as f:
        json.dump({"hosts": fresh, "updated": now}, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(state, "discovered.json"))


def ask(seen: dict):
    """One broadcast and the answers of LISTEN_S seconds into seen, by address."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", FROM_PORT))
        s.sendto(b"? yashome 1", (BCAST, PORT))
        end = time.time() + LISTEN_S
        while (left := end - time.time()) > 0:
            s.settimeout(left)
            try:
                data, addr = s.recvfrom(2048)
            except socket.timeout:
                break
            p = parse(data)
            if p:
                seen[addr[0]] = dict(p, host=addr[0], seen=time.time())


def run(ctx):
    seen = {}
    ctx.log(f"port {PORT}, every {INTERVAL} s")
    while not ctx.stop.is_set():
        try:
            ask(seen)
        except OSError as e:
            ctx.log(str(e))
        for k in [k for k, v in seen.items() if time.time() - v["seen"] > TTL]:
            seen.pop(k)
        if len(seen) > 64:
            for k in sorted(seen, key=lambda k: seen[k]["seen"])[:len(seen) - 64]:
                seen.pop(k)
        try:
            flush(seen, ctx.state)
        except OSError as e:
            ctx.log(f"discovered.json: {e}")
        if ctx.wait(INTERVAL):
            return
