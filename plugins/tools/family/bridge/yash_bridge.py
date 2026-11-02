#!/usr/bin/env python3
"""Home side of the family-phone app: pairing QR, ring, revoke, MQTT bridge.

  yash_bridge.py pair <name> [--role child|parent]   one QR: 1-hour custom token (cloud only; the role is
                                                     a claim for the database rules;
                                                     the server token comes from the dashboard's pairing)
  yash_bridge.py ring <name> [secs] | stop <name>    one FCM push
  yash_bridge.py revoke <name> | list | rules        admin tasks
  yash_bridge.py state <key> <value> | state-clear  write one /state entry by hand (+ sync push)
  yash_bridge.py serve                               family/<name>/ring (MQTT) -> FCM, ack -> MQTT,
                                                     whitelisted topics (state.json) -> /state + sync push,
                                                     HTTP API for the dashboard plug-in on $BRIDGE_PORT
"""
import argparse
import json
import os
import re
import sys
import threading
import time

import firebase_admin
from firebase_admin import auth, credentials, db, messaging

KEY = os.environ.get("YASH_FB_KEY", "/secrets/firebase-key.json")
DB_URL = os.environ.get("YASH_FB_DB", "")
RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.rules.json")
QR_DIR = os.environ.get("YASH_QR_DIR", ".")
QR_PREFIX = "yash2:"
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,19}$")
KEY_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
STATE_CFG = os.environ.get("YASH_STATE_CFG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"))
SYNC_DEBOUNCE = 25


class SyncPusher:
    """At most one 'sync' push per SYNC_DEBOUNCE s to every paired phone; trailing edge wins."""
    def __init__(self):
        self._lock = threading.Lock()
        self._timer = None
        self._last = 0.0

    def kick(self):
        with self._lock:
            if self._timer:
                return
            delay = max(0.0, self._last + SYNC_DEBOUNCE - time.time())
            self._timer = threading.Timer(delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self):
        with self._lock:
            self._timer = None
            self._last = time.time()
        for uid, d in (db.reference("devices").get() or {}).items():
            if isinstance(d, dict) and d.get("token"):
                try:
                    push(uid, {"cmd": "sync"}, collapse="sync")
                except Exception as e:
                    print(f"sync {uid}: FAILED {e}", flush=True)
        print("sync pushed", flush=True)


def load_state_cfg():
    with open(STATE_CFG) as f:
        items = json.load(f).get("items") or []
    for it in items:
        it["_re"] = re.compile("^" + re.escape(it["topic"]).replace(r"\+", "([^/]+)") + "$")
    return items


def match_item(items, topic):
    for it in items:
        m = it["_re"].match(topic)
        if m:
            fill = lambda t: re.sub(r"\{(\d)\}", lambda g: m.group(int(g.group(1))), t)
            return it, fill(it["key"]), fill(it.get("label", it["key"]))
    return None, None, None


def parse_payload(raw):
    text = raw.decode("utf-8", "replace").strip()
    try:
        return json.loads(text)
    except ValueError:
        return text


I18N = os.environ.get("YASH_I18N", "/app/i18n.json")


def publish_i18n():
    """/state labels are English keys; the phones translate them from /i18n/<lang>: the plug-in's
    dictionaries plus the "i18n" of state.json (the house's own labels, such as room names)."""
    try:
        with open(I18N) as f:
            dicts = json.load(f)
    except (OSError, ValueError) as e:
        print(f"i18n: {e}", flush=True)
        dicts = {}
    try:
        with open(STATE_CFG) as f:
            house = json.load(f).get("i18n") or {}
    except (OSError, ValueError, AttributeError):
        house = {}
    for lang, d in house.items():
        if isinstance(d, dict):
            dicts.setdefault(lang, {}).update(d)
    clean = {lang: {re.sub(r"[.#$/\[\]]", "_", k): v for k, v in d.items()} for lang, d in dicts.items() if isinstance(d, dict)}
    db.reference("i18n").set(clean)
    print(f"i18n published: {', '.join(f'{k} {len(v)}' for k, v in clean.items())}", flush=True)


def write_state(key, entry):
    """True when the stored value changed (retained replays after a reconnect are no-ops)."""
    if not KEY_RE.match(key):
        print(f"state key {key!r} skipped: a-z 0-9 _ - only", flush=True)
        return False
    ref = db.reference(f"state/{key}")
    cur = ref.get() or {}
    if cur.get("v") == entry["v"] and cur.get("label") == entry.get("label"):
        return False
    ref.set({**entry, "ts": {".sv": "timestamp"}})
    return True


def init():
    if not DB_URL:
        sys.exit("YASH_FB_DB is not set: the Realtime Database URL of your Firebase project")
    firebase_admin.initialize_app(credentials.Certificate(KEY), {"databaseURL": DB_URL})


def check_name(name):
    if not NAME_RE.match(name):
        sys.exit(f"bad name {name!r}: lowercase latin, 2-20 chars")
    return name


def fcm_token(uid):
    return db.reference(f"devices/{uid}/token").get()


def push(uid, data, collapse=None, priority="high", ttl=60):
    tok = fcm_token(uid)
    if not tok:
        raise RuntimeError(f"{uid}: no FCM token yet (phone not paired?)")
    msg = messaging.Message(token=tok, data=data, android=messaging.AndroidConfig(
        priority=priority, ttl=ttl, collapse_key=collapse))
    return messaging.send(msg)


def make_pairing(uid, role, srv=None, tok=None, link=None, display=None):
    """Create/enable the Firebase user and return the one-QR text (valid 1 h).
    With `link` the QR is <link>#<base64url json>: any phone camera opens the install page."""
    import qrcode
    import io
    import base64
    try:
        auth.get_user(uid)
        auth.update_user(uid, disabled=False, display_name=display or uid)
    except auth.UserNotFoundError:
        auth.create_user(uid=uid, display_name=display or uid)
    auth.set_custom_user_claims(uid, {"role": role})
    db.reference(f"revoked/{uid}").delete()
    payload = {"srv": srv, "tok": tok, "name": display or uid} if srv and tok else {}
    if not tok:            # from the house the QR holds a one-time code: the custom token comes with its claim
        payload["ct"] = auth.create_custom_token(uid, {"role": role}).decode()
    text = QR_PREFIX + json.dumps(payload, separators=(",", ":"))
    if link:
        text = link + "#" + base64.urlsafe_b64encode(text[len(QR_PREFIX):].encode()).decode().rstrip("=")
    buf = io.BytesIO()
    qrcode.make(text, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=5).save(buf, "PNG")
    return text, base64.b64encode(buf.getvalue()).decode()


def revoke(uid):
    """Gone from the list at once; /revoked/<uid> stays so an ID token already issued (<= 1 h) is refused too."""
    db.reference(f"revoked/{uid}").set(True)
    try:
        auth.revoke_refresh_tokens(uid)
        auth.delete_user(uid)
    except auth.UserNotFoundError:
        pass
    db.reference(f"devices/{uid}").delete()


def list_devices():
    devs = db.reference("devices").get() or {}
    revoked = db.reference("revoked").get() or {}
    out = []
    for u in auth.list_users().iterate_all():
        d = devs.get(u.uid, {}) if isinstance(devs.get(u.uid), dict) else {}
        out.append({"uid": u.uid, "name": u.display_name or u.uid, "role": (u.custom_claims or {}).get("role", ""),
                    "token": bool(d.get("token")), "ack": d.get("ack"),
                    "state": "revoked" if revoked.get(u.uid) else ("disabled" if u.disabled else "active")})
    return out


def cmd_pair(a):
    import base64
    uid = check_name(a.name)
    text, png = make_pairing(uid, a.role)
    path = os.path.join(QR_DIR, f"pair-{uid}.png")
    with open(path, "wb") as f:
        f.write(base64.b64decode(png))
    os.chmod(path, 0o600)
    print(f"{uid}: QR {path} ({len(text)} chars, cloud only; valid 1 h, delete after scan)")


def cmd_ring(a):
    uid = check_name(a.name)
    print(push(uid, {"cmd": "ring", "secs": str(a.secs)}))


def cmd_stop(a):
    print(push(check_name(a.name), {"cmd": "stop"}))


def cmd_revoke(a):
    uid = check_name(a.name)
    revoke(uid)
    print(f"{uid}: revoked")


def cmd_list(a):
    devs = db.reference("devices").get() or {}
    revoked = db.reference("revoked").get() or {}
    for u in auth.list_users().iterate_all():
        d = devs.get(u.uid, {})
        ack = d.get("ack")
        ack_s = time.strftime("%d.%m %H:%M:%S", time.localtime(ack / 1000)) if ack else "-"
        print(f"{u.uid:12} role={(u.custom_claims or {}).get('role', '-'):6} "
              f"token={'yes' if d.get('token') else 'no ':3} ack={ack_s} "
              f"{'REVOKED' if revoked.get(u.uid) else ('disabled' if u.disabled else 'active')}")


def cmd_rules(a):
    # Admin SDK has no rules call; use the REST endpoint with the same key
    import requests
    from google.oauth2 import service_account
    import google.auth.transport.requests as gtr
    cred = service_account.Credentials.from_service_account_file(KEY, scopes=[
        "https://www.googleapis.com/auth/firebase.database",
        "https://www.googleapis.com/auth/userinfo.email"])
    cred.refresh(gtr.Request())
    with open(RULES) as f:
        rules = f.read()
    r = requests.put(DB_URL + "/.settings/rules.json", data=rules, timeout=15,
                     headers={"Authorization": "Bearer " + cred.token})
    print(r.status_code, r.text.strip())


def cmd_state(a):
    entry = {"v": parse_payload(a.value.encode()), "label": a.label or a.key, "kind": a.kind}
    if write_state(a.key, entry):
        SyncPusher()._fire()
    else:
        print("unchanged")


def cmd_state_clear(a):
    db.reference("state").delete()
    SyncPusher()._fire()


def cmd_serve(a):
    import paho.mqtt.client as mqtt
    host = os.environ.get("MQTT_HOST", "127.0.0.1")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="yash-family-bridge")
    if os.environ.get("MQTT_USER"):
        cli.username_pw_set(os.environ["MQTT_USER"], os.environ.get("MQTT_PASS", ""))

    items = load_state_cfg() if os.path.exists(STATE_CFG) else []
    print(f"state whitelist: {len(items)} item(s) from {STATE_CFG}", flush=True)
    publish_i18n()
    syncer = SyncPusher()

    def on_connect(c, _u, _f, rc, _p):
        print("mqtt connected", rc, flush=True)
        c.subscribe("family/+/ring")
        c.subscribe("family/+/stop")
        for it in items:
            c.subscribe(it["topic"])

    def on_state(it, key, label, raw):
        v = parse_payload(raw)
        entry = {k: it[k] for k in ("kind", "unit", "on", "off", "map") if k in it}
        entry.update(v=v, label=label)
        try:
            if write_state(key, entry):
                print(f"state {key}={v!r}", flush=True)
                syncer.kick()
        except Exception as e:
            print(f"state {key}: FAILED {e}", flush=True)

    def on_message(_c, _u, m):
        it, key, label = match_item(items, m.topic)
        if it:
            # off the paho thread: an RTDB round trip must not stall the MQTT loop
            threading.Thread(target=on_state, args=(it, key, label, m.payload), daemon=True).start()
            return
        parts = m.topic.split("/")
        if len(parts) != 3 or parts[0] != "family":
            return
        _, name, what = parts
        if not NAME_RE.match(name):
            return
        try:
            secs = int(json.loads(m.payload or b"{}").get("secs", 60)) if what == "ring" else 0
        except (ValueError, AttributeError):
            secs = 60
        data = {"cmd": what, "secs": str(secs)} if what == "ring" else {"cmd": what}
        try:
            print(f"{what} {name}: {push(name, data)}", flush=True)
        except Exception as e:
            print(f"{what} {name}: FAILED {e}", flush=True)

    def on_ack(ev):
        # Path is /<uid>/ack on child updates, or / with the whole tree on the first snapshot
        parts = [p for p in ev.path.split("/") if p]
        if len(parts) == 2 and parts[1] == "ack" and isinstance(ev.data, (int, float)):
            cli.publish(f"family/{parts[0]}/ack", json.dumps({"ts": int(ev.data)}))
            print(f"ack {parts[0]} {int(ev.data)}", flush=True)

    cli.on_connect, cli.on_message = on_connect, on_message
    start_http(cli)
    cli.connect(host, port, 30)
    db.reference("devices").listen(on_ack)
    cli.loop_forever(retry_first_connection=True)


def start_http(cli):
    """Tiny JSON API for the dashboard plug-in, gated by $BRIDGE_KEY; plain HTTP - keep it on the docker
    network or behind a private link (VPN, TLS proxy) when the bridge runs on another host."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import hmac
    key = os.environ.get("BRIDGE_KEY", "")
    if not key:
        print("BRIDGE_KEY not set: HTTP API disabled", flush=True)
        return

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authed(self):
            return hmac.compare_digest(self.headers.get("X-Bridge-Key", "").encode(), key.encode())

        def do_GET(self):
            if not self.authed():
                return self.reply(401, {"error": "key"})
            if self.path == "/list":
                return self.reply(200, list_devices())
            if self.path == "/health":
                return self.reply(200, {"ok": True})
            self.reply(404, {"error": "path"})

        def do_POST(self):
            if not self.authed():
                return self.reply(401, {"error": "key"})
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
                uid = req.get("uid", "")
                if not NAME_RE.match(uid):
                    return self.reply(400, {"error": "bad name"})
                if self.path == "/pair":
                    role = req.get("role", "member")   # per-name rights are decided on the server, later
                    text, png = make_pairing(uid, role, req.get("srv"), req.get("tok"), req.get("link"),
                                             str(req.get("display") or uid)[:30])
                    return self.reply(200, {"png": png, "chars": len(text)})
                if self.path == "/custom_token":       # the house claims a QR code: Firebase sign-in, once
                    role = (auth.get_user(uid).custom_claims or {}).get("role", "member")
                    return self.reply(200, {"ct": auth.create_custom_token(uid, {"role": role}).decode()})
                if self.path == "/revoke":
                    revoke(uid)
                    return self.reply(200, {"ok": True})
                if self.path == "/tickle":               # no data at all: the phone wakes and asks the house itself
                    prio = "high" if req.get("priority") == "high" else "normal"
                    return self.reply(200, {"id": push(uid, {"cmd": "pull"}, collapse="pull", priority=prio, ttl=60 if prio == "high" else 3600)})
                if self.path in ("/ring", "/stop"):
                    data = {"cmd": self.path[1:]}
                    if self.path == "/ring":
                        data["secs"] = str(int(req.get("secs", 60)))
                    return self.reply(200, {"id": push(uid, data)})
                self.reply(404, {"error": "path"})
            except Exception as e:
                self.reply(502, {"error": str(e)[:200]})

    srv = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("BRIDGE_PORT", "8790"))), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"http api on :{srv.server_port}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("pair"); p.add_argument("name"); p.add_argument("--role", default="child", choices=["child", "parent"])
    p = sp.add_parser("ring"); p.add_argument("name"); p.add_argument("secs", nargs="?", type=int, default=60)
    sp.add_parser("stop").add_argument("name")
    sp.add_parser("revoke").add_argument("name")
    sp.add_parser("list"); sp.add_parser("rules"); sp.add_parser("serve")
    p = sp.add_parser("state"); p.add_argument("key"); p.add_argument("value")
    p.add_argument("--label"); p.add_argument("--kind", default="text", choices=["text", "num", "bool", "enum"])
    sp.add_parser("state-clear")
    a = ap.parse_args()
    init()
    globals()["cmd_" + a.cmd.replace("-", "_")](a)


if __name__ == "__main__":
    main()
