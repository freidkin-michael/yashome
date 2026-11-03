"""Discovery module of the panels plug-in (run by the core's `discover` container, host network): invites
Android panels while pairing is open on the dashboard (UDP broadcast, port 51823). The invitation carries the
dashboard's address and the session's word; a tablet answers with the word and then registers over HTTP itself.
Files in plugin-state/panels/: pairing.json (written by the plug-in), discovered.json and arp.json (written here)."""
import json
import os
import socket
import tempfile
import time

PORT = 51823
PROTO = "pwpanel/1"
TTL = 60
BCAST = "255.255.255.255"
DASH_PORT = int(os.environ.get("DASHBOARD_PORT") or 8765)


def pairing(state):
    try:
        with open(os.path.join(state, "pairing.json")) as f:
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


def _write(state, name: str, body: dict):
    fd, tmp = tempfile.mkstemp(dir=state, prefix=f".{name}.")
    with os.fdopen(fd, "w") as f:
        json.dump(body, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, os.path.join(state, name))


def flush(seen: dict, state):
    now = time.time()
    fresh = sorted((dict(v, age=round(now - v["seen"], 1)) for v in seen.values() if now - v["seen"] < TTL),
                   key=lambda d: str(d.get("name", "")))
    _write(state, "discovered.json", {"devices": fresh, "updated": now})


def arp(state, source="/proc/net/arp"):
    """The host's neighbour table, IPv4 -> MAC (flat, lower case): the backend flashes a panel only at the
    address its bound MAC really has, not at whatever address a broker client claims."""
    out = {}
    try:
        with open(source) as f:
            for line in f.read().splitlines()[1:]:
                p = line.split()
                if len(p) >= 4 and p[2] != "0x0" and p[3].replace(":", "").strip("0"):
                    out[p[0]] = p[3].replace(":", "").lower()
    except OSError:
        return
    _write(state, "arp.json", {"updated": time.time(), "arp": out})


def answer(data: bytes, magic: str):
    """A tablet's answer to the invitation with this session's word: {id, name, model}, else None."""
    try:
        p = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return None
    if (isinstance(p, dict) and p.get("proto") == PROTO and p.get("role") != "server"
            and isinstance(p.get("id"), str) and magic and p.get("magic") == magic):
        return {"id": p["id"][:64], "name": str(p.get("name", ""))[:64], "model": str(p.get("model", ""))[:64]}
    return None


def run(ctx):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("", PORT))
        s.settimeout(2)
        seen, last_bcast, last_flush = {}, 0.0, 0.0
        ctx.log(f"port {PORT}, broadcast {BCAST}")
        while not ctx.stop.is_set():
            now = time.time()
            until, magic = pairing(ctx.state)
            if now < until and magic and now - last_bcast >= 2:
                msg = {"proto": PROTO, "role": "server", "magic": magic, "port": PORT, "url": server_url(), "pairing": True}
                try:
                    s.sendto(json.dumps(msg).encode(), (BCAST, PORT))
                except OSError as e:
                    ctx.log(f"broadcast failed: {e}")
                last_bcast = now
            try:
                data, addr = s.recvfrom(4096)
                p = answer(data, magic)
                if p:
                    seen[p["id"]] = dict(p, ip=addr[0], seen=time.time())
                    if len(seen) > 50:
                        seen.pop(min(seen, key=lambda k: seen[k]["seen"]))
                    last_flush = 0.0
            except socket.timeout:
                pass
            for k in [k for k, v in seen.items() if time.time() - v["seen"] > TTL]:
                seen.pop(k)
            if time.time() - last_flush > 4:
                try:
                    flush(seen, ctx.state)
                    arp(ctx.state)
                except OSError as e:
                    ctx.log(f"discovered.json: {e}")
                last_flush = time.time()
