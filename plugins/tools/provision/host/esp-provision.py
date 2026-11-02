#!/usr/bin/env python3
"""Host relay of the provision plug-in: hands a lost ESPHome node the house Wi-Fi through this host's
spare Wi-Fi card. GET /health, GET /scan, POST /provision {"ap", "ssid", "password", "portal", "bssid"?,
"ap_password"?, "allow_open_setup"?} -> {"ok", "steps", "error"?, "restore_failed"?}, on /run/yashome/provision.sock
(systemd hands the socket over: yashome-provision.socket, its group the house's; who may write to it may ask). Changes go through the root-owned
esp-provision-nmcli gate (sudo; the AP key on its stdin, never in argv); the card always goes back to the
connection it had before, also when the relay fails."""
import ipaddress
import json
import os
import socket
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_FD = 3                                         # the socket systemd passes (sd_listen_fds)
WIFI_IF = os.environ.get("PROVISION_WIFI_IF", "")
GATE = "/usr/local/libexec/yashome/esp-provision-nmcli"
CON_NAME = "yashome-provision"
SUFFIX = "-setup"
OPEN_HOUSE = "an open -setup AP: not one of the house's nodes"
NO_WIFI = "no Wi-Fi interface (set PROVISION_WIFI_IF)"
DEFAULT_PORTAL = {"host": "192.168.4.1", "method": "POST", "path": "/wifisave", "ssid_field": "ssid", "psk_field": "psk"}
PORTAL_NET = ipaddress.ip_network("192.168.4.0/24")
FIELD_RE = re.compile(r"[A-Za-z0-9_]{1,32}")
PATH_RE = re.compile(r"/[A-Za-z0-9/_.?=&-]{0,95}")
BSSID_RE = re.compile(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}")
_relay = threading.Lock()


def run(*args, timeout=45, stdin=None):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           input=stdin if stdin is not None else "")
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def nmcli(*args, timeout=45, stdin=None):
    """A change (or a scan) through the root-owned gate; it refuses anything it does not know."""
    return run("sudo", "-n", GATE, *args, timeout=timeout, stdin=stdin)


def nm_read(*args, timeout=15):
    """Read-only nmcli, no root needed."""
    return run("nmcli", *args, timeout=timeout)


def terse_split(line):
    """One `nmcli -t` line into its fields (\\: is a colon inside a field)."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur, esc = cur + ch, False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out, cur = out + [cur], ""
        else:
            cur += ch
    return out + [cur]


def wifi_if():
    """PROVISION_WIFI_IF when NetworkManager knows it as Wi-Fi, else the first Wi-Fi interface; "" = none."""
    _, out, _ = nm_read("-t", "-f", "DEVICE,TYPE", "dev")
    devs = [f[0] for f in map(terse_split, out.splitlines()) if len(f) == 2 and f[1] == "wifi"]
    if WIFI_IF:
        return WIFI_IF if WIFI_IF in devs else ""
    return devs[0] if devs else ""


def active_connection(dev):
    _, out, _ = nm_read("-t", "-f", "NAME,DEVICE", "con", "show", "--active")
    for f in map(terse_split, out.splitlines()):
        if len(f) == 2 and f[1] == dev:
            return f[0]
    return None


def scan(dev):
    """Open networks (an unprovisioned device) and every "*-setup" one, also a secured one; one entry
    per BSSID, so two APs with one name show up as two."""
    nmcli("dev", "wifi", "rescan", "ifname", dev, timeout=30)
    time.sleep(2)
    _, out, _ = nmcli("dev", "wifi", "list", "ifname", dev)
    aps, seen = [], set()
    for f in map(terse_split, out.splitlines()):
        if len(f) != 4:
            continue
        ssid, bssid, signal, security = f[0], f[1].upper(), f[2], f[3]
        is_open, is_setup = not security.strip() or security.strip() == "--", ssid.endswith(SUFFIX)
        if ssid and (ssid, bssid) not in seen and (is_open or is_setup):
            seen.add((ssid, bssid))
            aps.append({"ssid": ssid, "bssid": bssid if BSSID_RE.fullmatch(bssid) else None,
                        "signal": int(signal) if signal.isdigit() else None, "open": is_open, "setup": is_setup})
    return sorted(aps, key=lambda a: -(a["signal"] or 0))


def portal_ok(portal):
    """An IPv4 address (no name) in 192.168.4.0/24, an optional port; the core's path and field rules."""
    host, _, port = portal["host"].partition(":")
    try:
        inside = ipaddress.IPv4Address(host) in PORTAL_NET and (not port or 0 < int(port) < 65536)
    except ValueError:
        return False
    return inside and bool(PATH_RE.fullmatch(portal["path"])) and all(
        FIELD_RE.fullmatch(portal[k]) for k in ("ssid_field", "psk_field"))


def portal_save(ssid, password, portal, steps):
    """POST the form (falling back to the query string, which some builds read), or GET with a query."""
    query = urllib.parse.urlencode({portal["ssid_field"]: ssid, portal["psk_field"]: password})
    base = f"http://{portal['host']}{portal['path']}"
    sep = "&" if "?" in portal["path"] else "?"
    if portal["method"] == "GET":
        tries = [("GET", urllib.request.Request(base + sep + query))]
    else:
        tries = [("POST body", urllib.request.Request(base, data=query.encode(), headers={
                     "Content-Type": "application/x-www-form-urlencoded"})),
                 ("POST query", urllib.request.Request(base + sep + query, data=b""))]
    for what, req in tries:
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                steps.append(f"{what} {portal['path']} -> HTTP {r.status}")
                if r.status < 400:
                    return True
        except Exception as e:
            steps.append(f"{what} {portal['path']} -> {type(e).__name__}")
    return False


PAIR_PATH = "/yash/pair"
FORM_MAX = 2048


def form_ok(form) -> bool:
    """A pair form: a few plain field names, short text values, 2 KB in all."""
    return (isinstance(form, dict) and 0 < len(form) <= 16
            and all(isinstance(k, str) and FIELD_RE.fullmatch(k) and isinstance(v, str) and len(v) <= 128 for k, v in form.items())
            and sum(len(k) + len(v) for k, v in form.items()) <= FORM_MAX)


def portal_pair(form, portal, steps):
    """POST the pair form to the device (urlencoded; urllib sets the Content-Length, without it ESP-IDF answers 411); its JSON answer.
    The values never go into `steps`: they carry passwords."""
    req = urllib.request.Request(f"http://{portal['host']}{PAIR_PATH}", data=urllib.parse.urlencode(form).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            steps.append(f"pair request -> HTTP {r.status}")
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:                     # the device refuses with JSON ({"ok": false, "error": ...})
        steps.append(f"pair request -> HTTP {e.code}")
        try:
            return {**json.loads(e.read() or b"{}"), "ok": False}
        except ValueError:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:                                  # noqa: BLE001
        steps.append(f"pair request -> {type(e).__name__}")
        return {"ok": False, "error": type(e).__name__}


def restore(dev, previous, steps):
    """The card back where it was; one retry. Returns False when it could not."""
    for attempt in (1, 2):
        nmcli("con", "down", "id", CON_NAME, timeout=30)
        nmcli("con", "delete", "id", CON_NAME, timeout=30)
        if not previous:
            steps.append(f"{dev} left disconnected, as it was")
            return True
        rc, _, err = nmcli("con", "up", "id", previous, timeout=60)
        if rc == 0:
            steps.append(f"{dev} back on {previous}" + (" (second try)" if attempt == 2 else ""))
            return True
        steps.append(f"{dev} NOT back on {previous}: {err[:120]}")
        time.sleep(3)
    return False


def portal_answers(host, attempts=10, pause=1.0) -> int:
    """The try on which the portal answered, 0 when it never did. Any HTTP answer counts, a 404 too: it is up."""
    for attempt in range(1, attempts + 1):
        try:
            urllib.request.urlopen(f"http://{host}/", timeout=3).read(1)
            return attempt
        except urllib.error.HTTPError:
            return attempt
        except Exception:                                        # noqa: BLE001  not yet: the AP's DHCP and web server come up in seconds
            time.sleep(pause)
    return 0


def provision(dev, ap, bssid, ssid, password, portal, ap_password, form=None):
    previous = active_connection(dev)
    steps = [f"{dev} was on: {previous or 'nothing'}"]
    out = {"ok": False, "steps": steps}
    try:
        # "secured": the gate reads the key from stdin (argv is visible to every local user)
        args = ["dev", "wifi", "connect", ap, *(["bssid", bssid] if bssid else []),
                *(["secured"] if ap_password else []), "ifname", dev, "name", CON_NAME]
        rc, _, err = nmcli(*args, timeout=60, stdin=ap_password + "\n" if ap_password else None)
        if rc != 0:
            out["error"] = f"could not join {ap}: {err[:200]}"
            return out
        steps.append(f"joined {ap}" + (f" ({bssid})" if bssid else ""))
        tries = portal_answers(portal["host"])
        if not tries:
            out["error"] = f"the portal {portal['host']} did not answer"
            return out
        steps.append(f"the portal answered (try {tries})")
        if form is not None:
            reply = portal_pair(form, portal, steps)
            out["reply"] = {k: v for k, v in reply.items() if k in ("ok", "node", "hash", "error")}
            out["ok"] = reply.get("ok") is True
            if not out["ok"]:
                out["error"] = str(reply.get("error") or "the device refused the pairing")
            return out
        if not portal_save(ssid, password, portal, steps):
            out["error"] = "the portal did not take the credentials"
            return out
        steps.append("credentials sent; the node restarts into the house network")
        out["ok"] = True
        return out
    finally:
        if not restore(dev, previous, steps):
            out["restore_failed"] = True


def pick(aps, ap, bssid, ap_pwd, allow_open_setup=False):
    """The one AP to join, or (http code, error): one name heard open and secured, or from two MACs
    without a pinned one, is refused; so is a password for an AP heard open, and an open "*-setup"
    AP unless the core allowed it (every house node's setup AP is secured)."""
    named = [a for a in aps if a["ssid"] == ap]
    if len({a["open"] for a in named}) > 1:
        return 409, "two APs with this name: one open, one secured - refusing"
    hits = [a for a in named if not bssid or a["bssid"] == bssid]
    if not hits:
        return 400, f"{ap!r}" + (f" ({bssid})" if bssid else "") + " is not on the air here"
    if len({a["bssid"] for a in hits}) > 1:
        return 409, "two APs with this name (different MACs) - refusing"
    if hits[0]["open"] and ap.endswith(SUFFIX) and not allow_open_setup:
        return 409, OPEN_HOUSE
    if hits[0]["open"] and ap_pwd:
        return 409, f"{ap!r} is open here, but a password was given - refusing"
    if not hits[0]["open"] and not ap_pwd:
        return 400, f"{ap!r} is secured and no ap_password was given"
    return hits[0]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def address_string(self):
        return "house"                                # a socket file has no peer address

    def do_GET(self):
        if self.path not in ("/health", "/scan"):
            return self._send(404, {"ok": False, "error": "unknown path"})
        dev = wifi_if()
        if not dev:
            return self._send(503, {"ok": False, "error": NO_WIFI})
        if self.path == "/health":
            _, out, _ = nm_read("-t", "-f", "DEVICE,STATE", "dev")
            state = next((f[1] for f in map(terse_split, out.splitlines()) if len(f) == 2 and f[0] == dev), "?")
            return self._send(200, {"ok": True, "wifi": dev, "state": state})
        try:
            return self._send(200, {"aps": scan(dev)})
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)[:200]})

    def do_POST(self):
        if self.path != "/provision":
            return self._send(404, {"ok": False, "error": "unknown path"})
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= 4096:
            return self._send(400, {"ok": False, "error": "body: JSON, at most 4 KB"})
        try:
            body = json.loads(self.rfile.read(n))
            ap, ssid, pwd = body.get("ap"), body.get("ssid"), body.get("password")
            ap_pwd, bssid = body.get("ap_password") or "", body.get("bssid") or ""
            form = body.get("form")
            if form is not None:                                    # a pair request: the AP key is the pairing code, no Wi-Fi to hand over
                if not form_ok(form) or not ap_pwd or not isinstance(ap, str) or not ap.endswith(SUFFIX):
                    return self._send(400, {"ok": False, "error": "form: plain fields, a -setup AP and its key (the code)"})
                ssid = pwd = "-"
            elif not all(isinstance(v, str) and v for v in (ap, ssid, pwd)) or not isinstance(ap_pwd, str):
                return self._send(400, {"ok": False, "error": "need ap, ssid, password"})
            bssid = bssid.upper() if isinstance(bssid, str) else "?"
            if bssid and not BSSID_RE.fullmatch(bssid):
                return self._send(400, {"ok": False, "error": "bssid: AA:BB:CC:DD:EE:FF"})
            portal = dict(DEFAULT_PORTAL)
            for k, v in (body.get("portal") or {}).items():
                if k in portal and isinstance(v, str) and v.strip() and form is None:
                    portal[k] = v.strip()
            portal["method"] = "GET" if portal["method"].upper() == "GET" else "POST"
            if not portal_ok(portal):
                return self._send(400, {"ok": False, "error": "the portal: an IPv4 address in 192.168.4.0/24, "
                                                              "path / field names of the allowed characters"})
        except (ValueError, AttributeError) as e:
            return self._send(400, {"ok": False, "error": str(e)[:200]})
        dev = wifi_if()
        if not dev:
            return self._send(503, {"ok": False, "error": NO_WIFI})
        if not _relay.acquire(blocking=False):
            return self._send(409, {"ok": False, "error": "busy with another relay"})
        try:
            hit = pick(scan(dev), ap, bssid, ap_pwd, body.get("allow_open_setup") is True)
            if isinstance(hit, tuple):
                return self._send(hit[0], {"ok": False, "error": hit[1]})
            self._send(200, provision(dev, ap, hit["bssid"] or "", ssid, pwd, portal, ap_pwd, form))
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)[:200]})
        finally:
            _relay.release()

    def log_message(self, fmt, *args):
        sys.stderr.write("[esp-provision] " + (fmt % args) + "\n")


class Server(ThreadingHTTPServer):
    """The HTTP server on the socket systemd passed (a socket file)."""
    address_family = socket.AF_UNIX

    def __init__(self, sock):
        super().__init__("", Handler, bind_and_activate=False)
        self.socket.close()
        self.socket = sock

    def server_bind(self):
        pass


if __name__ == "__main__":
    if os.environ.get("LISTEN_FDS") != "1":
        sys.exit(print("esp-provision: start it through yashome-provision.socket (tools/host-sockets.sh)", file=sys.stderr) or 2)
    # a profile left by a crash mid-relay (none there: the delete just fails)
    nmcli("con", "delete", "id", CON_NAME, timeout=30)
    print(f"esp-provision: on its socket, Wi-Fi {wifi_if() or '(none found)'}", file=sys.stderr)
    Server(socket.socket(fileno=LISTEN_FD)).serve_forever()
