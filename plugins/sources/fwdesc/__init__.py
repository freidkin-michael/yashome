"""Device descriptions: what a firmware built by fwgen says of itself (<node>/fwgen/description and /hash, retained),
kept per node and shown in the device window; build records registered by the builder (docs/device-description.md)."""
import base64
import copy
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import zlib

from fastapi import HTTPException, Request
from pydantic import Field

import app as core

PLUGIN = {"name": "Device descriptions", "version": "0.1.0", "icon": "info"}

MAX_WIRE = 24 * 1024            # a retained message, compressed
MAX_DECODED = 64 * 1024         # a description after decompression
MAX_SVG = 200 * 1024
BURST_S = 5.0                   # the two topics of one connect arrive within this
RATE_S = 10.0                   # one message per node per this long, unless the hash changed
HISTORY = 5
NODE_RE = re.compile(r"[a-z0-9_-]{1,64}")
HASH_RE = re.compile(r"[0-9a-f]{12}")
SECRET_KEY = re.compile(r"(?i)(^|[_\-. ])(pass(word|wd)?|token|secret|psk|key|ssid)($|[_\-. ])")
DATA = core.plugin_data("fwgen")
_lock = threading.RLock()
_pending = {}                   # node -> (the last valid description seen, when): it arrives before or after its hash
_want = {}                      # node -> a new hash waiting for its description
_seen = {}                      # node -> time of the last handled message
_warned = set()


def canonical_hash(desc: dict) -> str:
    """sha256 of the canonical JSON without build.hash and build.date, the first 12 hex characters."""
    d = copy.deepcopy(desc)
    b = d.get("build")
    if isinstance(b, dict):
        b.pop("hash", None)
        b.pop("date", None)
    raw = json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def decode(raw: bytes) -> dict:
    """The description object of a message: JSON, or {"enc": "gzip+base64", "data": ...}; ValueError when it is none."""
    if len(raw) > MAX_WIRE:
        raise ValueError("message too large")
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("not JSON")
    if isinstance(obj, dict) and obj.get("enc") == "gzip+base64":
        try:
            packed = base64.b64decode(str(obj.get("data") or ""), validate=True)
            dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
            text = dec.decompress(packed, MAX_DECODED + 1)
        except (ValueError, zlib.error):
            raise ValueError("bad gzip+base64")
        if len(text) > MAX_DECODED or dec.unconsumed_tail:
            raise ValueError("description too large")
        try:
            obj = json.loads(text.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("not JSON")
    if not isinstance(obj, dict):
        raise ValueError("not an object")
    return obj


SECRETS_FILE = "/run/esphome-secrets.yaml"     # the nodes' secrets.yaml, mounted read-only (ESPHOME_SECRETS in .env)


def _secrets_from_file() -> tuple:
    """(secret values of the nodes' secrets.yaml, why it could not be read or ''); only keys named like a secret count,
    a broker address in that file is no secret and may appear in a description."""
    try:
        text = open(SECRETS_FILE, encoding="utf-8").read()
    except OSError as e:
        return set(), f"{SECRETS_FILE}: {type(e).__name__}"
    out = set()
    for line in text.splitlines():
        m = re.match(r"\s*([\w.-]+)\s*:\s*[\"']?(.+?)[\"']?\s*$", line)
        if m and not line.lstrip().startswith("#") and len(m.group(2)) >= 6 and SECRET_KEY.search(m.group(1)):
            out.add(m.group(2))
    return out, ("" if out else f"{SECRETS_FILE}: no secret values in it")


def _house_secrets() -> set:
    out = {v for k, v in os.environ.items() if len(v) >= 6 and SECRET_KEY.search(k.replace("_", " ").lower().replace(" ", "_"))}
    vals, why = _secrets_from_file()
    if why and "secrets" not in _warned:
        _warned.add("secrets")
        core.log.warning(f"[fwdesc] the secret-value check runs on the house environment only: {why}")
    return out | vals


def _health():
    why = _secrets_from_file()[1]
    if not why:
        return [core.health_row("fwdesc", "ok", "Device descriptions", "secret check on the nodes' secrets file")]
    return [core.health_row("fwdesc", "warn", "Device descriptions", "the nodes' secrets file is not readable: {0}",
                            "Point ESPHOME_SECRETS in .env at that file", (why,), cmd="make update")]


def secret_in(obj, secrets=None) -> str:
    """'' when the description holds nothing like a secret; else what was found (never the value)."""
    secrets = _house_secrets() if secrets is None else secrets

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if SECRET_KEY.search(str(k)):
                    return f"key {path}{k}"
                r = walk(v, f"{path}{k}.")
                if r:
                    return r
        elif isinstance(o, list):
            for i, v in enumerate(o):
                r = walk(v, f"{path}{i}.")
                if r:
                    return r
        elif isinstance(o, str) and o in secrets:
            return f"a house secret at {path.rstrip('.')}"
        return ""
    return walk(obj, "")


def _prefix_ok(dev, node: str) -> bool:
    """The topic's prefix is the device's: its topic_prefix; with mac_suffix (one image for many boards, the runtime prefix is
    <name>-<6 lowercase hex of the MAC>, which a compile-time description cannot carry) its name or name plus that suffix."""
    if not isinstance(dev, dict):
        return False
    if dev.get("mac_suffix") is True:
        name = dev.get("name")
        return isinstance(name, str) and bool(name) and (node == name or re.fullmatch(re.escape(name) + "-[0-9a-f]{6}", node) is not None)
    return dev.get("topic_prefix") == node


def check(desc: dict, node: str = None) -> None:
    """ValueError unless this is a description the house keeps: an integer schema, its own prefix, no secrets."""
    if not isinstance(desc.get("schema"), int) or isinstance(desc.get("schema"), bool):
        raise ValueError("no integer schema")
    dev = desc.get("device")
    if node is not None and not _prefix_ok(dev, node):
        raise ValueError("device.topic_prefix is not the topic's prefix")
    if isinstance(dev, dict) and dev.get("mac_suffix") is True:       # the runtime prefix is not known at compile time: topics start with ~/
        for e in desc.get("entities") or []:
            t = e.get("state_topic") if isinstance(e, dict) else None
            if t is not None and not (isinstance(t, str) and t.startswith("~/")):
                raise ValueError("with mac_suffix a state_topic starts with ~/")
    why = secret_in(desc)
    if why:
        raise ValueError("it holds " + why)


def _path(kind: str, name: str):
    return DATA / kind / f"{name}.json"


def _load(kind: str, name: str):
    try:
        return json.loads(_path(kind, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(kind: str, name: str, obj: dict) -> None:
    p = _path(kind, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    core.atomic_json_dump(p, obj, ensure_ascii=False, indent=2)


def _warn(node: str, why: str) -> None:
    if (node, why) not in _warned:
        _warned.add((node, why))
        core.log.warning(f"[fwdesc] {node}: description refused - {why}")


def _topics_of(desc: dict, node: str = "") -> set:
    """The entities' state topics; a leading ~ (mac_suffix builds) is the prefix the description came from."""
    out = set()
    for e in desc.get("entities") or []:
        t = e.get("state_topic") if isinstance(e, dict) else None
        if isinstance(t, str) and t:
            out.add(node + t[1:] if node and t.startswith("~/") else t)
    return out


def _card_gap(node: str, desc: dict) -> dict:
    """The device's channels its card does not show (an offer, not an alarm): the card may be hand-written."""
    dev = next((d for d in list(core.DEVICES.values()) if node in _nodes_of(d)), None)
    if dev is None:
        return {}
    have = {c.get("state_topic") for c in dev.get("controls", []) if c.get("state_topic")}
    return {"missing": sorted(_topics_of(desc, node) - have), "device": dev.get("id")}


def _changes(node: str, old: dict, desc: dict) -> dict:
    """A real firmware change: the channels the new description has and the previous one did not, and the reverse."""
    if not old:
        return {}
    was, now = _topics_of(old, node), _topics_of(desc, node)
    return {"added": sorted(now - was), "removed": sorted(was - now)}


def _paired_here(node: str) -> bool:
    """The plug-in pair has a record of this node: the house paired it (its account and OTA password are the house's)."""
    pm = sys.modules.get("yashome_plugins.pair")
    try:
        return bool(pm and pm.ota_password(node))
    except Exception:                                        # noqa: BLE001 - no pair plug-in data: not paired here
        return False


def process(node: str, desc: dict, h: str) -> None:
    """A description and the hash that came with it (cases 1 and 2 of docs/device-description.md)."""
    now = time.time()
    with _lock:
        cur = _load("devices", node)
        if cur and cur.get("hash") == h:                       # case 1: nothing to read again
            cur["seen"] = now
            _save("devices", node, cur)
            return
        rec = {"node": node, "state": "yash", "hash": h, "description": desc, "received": now, "seen": now,
               "mismatch": canonical_hash(desc) != h, "build_known": _load("builds", h) is not None,
               "history": list((cur or {}).get("history") or []), "v": 2}
        if cur:
            rec["history"] = ([{"hash": cur.get("hash"), "received": cur.get("received"), "known": cur.get("build_known", False)}]
                              + rec["history"])[:HISTORY]
        if not rec["build_known"] and not rec["mismatch"] and _paired_here(node):
            # this house paired the device itself (no code, no secrets in its firmware): its own description is the
            # build record, the builder need not know the house
            _save("builds", h, {"hash": h, "date": (desc.get("build") or {}).get("date", ""), "fwgen": desc.get("fwgen") or {},
                                "description": desc, "svg": "", "registered": now, "from": "paired device"})
            rec["build_known"] = True
        rec["changes"] = _changes(node, (cur or {}).get("description"), desc)
        rec["card_gap"] = _card_gap(node, desc)
        _save("devices", node, rec)
        _pending.pop(node, None)
    core.log.info(f"[fwdesc] {node}: build {h} stored" + (" (hash does not match its description)" if rec["mismatch"] else "")
                  + ("" if rec["build_known"] else " (no build record)"))


def on_node(parts, payload, retain):
    """<node>/fwgen/description and <node>/fwgen/hash (the core hands over every <node>/fwgen/...)."""
    if len(parts) != 3 or parts[2] not in ("hash", "description") or not NODE_RE.fullmatch(parts[0]) or not payload:
        return
    node, what = parts[0], parts[2]
    now = time.time()
    try:
        if what == "description":
            desc = decode(bytes(payload))
            check(desc, node)
            with _lock:
                _pending[node] = (desc, now)
                h = _want.pop(node, None)
            if h:
                process(node, desc, h)
            return
        h = bytes(payload).decode("utf-8", "replace").strip()
        if not HASH_RE.fullmatch(h):
            _warn(node, "the hash is not 12 hex characters")
            return
        with _lock:
            cur = _load("devices", node)
            if now - _seen.get(node, 0) < RATE_S and cur and cur.get("hash") == h:
                return
            _seen[node] = now
            desc, got = _pending.get(node, (None, 0))
            same = bool(cur and cur.get("hash") == h)
            # a description of this burst (or one that matches) goes with this hash; an old one never does
            if not same and (desc is None or (canonical_hash(desc) != h and now - got > BURST_S)):
                _want[node] = h                                # its description follows
                return
        if cur and cur.get("hash") == h:
            process(node, cur["description"], h)
        else:
            process(node, desc, h)
    except ValueError as e:
        _warn(node, str(e))


def _nodes_of(dev: dict) -> list:
    """The topic prefixes a device card uses: its esphome_node marker and the first level of its topics."""
    out = []
    if dev.get("esphome_node"):
        out.append(str(dev["esphome_node"]))
    topics = [dev.get("availability_topic")] + [c.get(k) for c in dev.get("controls", []) for k in ("state_topic", "command_topic")]
    for t in topics:
        if isinstance(t, str) and "/" in t:
            out.append(t.split("/", 1)[0])
    return list(dict.fromkeys(out))


def _node_of(dev: dict):
    nodes = _nodes_of(dev)
    for n in nodes:
        if _load("devices", n):
            return n
    return nodes[0] if nodes else None


def describe(dev: dict) -> dict:
    """What the device window shows: case 1 (known hash), 2 (a new one was just read) or 3 (no hash topic ever seen)."""
    node = _node_of(dev)
    rec = _load("devices", node) if node else None
    if not rec:
        return {"case": 3, "state": "foreign", "node": node}
    build = _load("builds", rec["hash"])
    shown = copy.deepcopy(rec["description"])          # the window sees the real topics, not "~/..." (mac_suffix builds)
    for e in shown.get("entities") or []:
        if isinstance(e, dict) and isinstance(e.get("state_topic"), str) and e["state_topic"].startswith("~/"):
            e["state_topic"] = node + e["state_topic"][1:]
    fresh = time.time() - float(rec.get("received") or 0) < 86400
    return {"case": 2 if fresh else 1, "state": rec.get("state", "yash"), "node": node, "hash": rec["hash"],
            "description": shown, "received": rec.get("received"), "history": rec.get("history", []),
            "mismatch": bool(rec.get("mismatch")),
            "changes": (rec.get("changes") or {}) if rec.get("v") == 2 else {},   # older records compared with the card: no real change
            "card_gap": _card_gap(node, rec["description"]),
            "build": ({"known": True, "date": build.get("date"), "fwgen": build.get("fwgen"), "svg": build.get("svg")}
                      if build else {"known": False})}


@core.app.get("/api/devices/{dev_id}/description")
def device_description(dev_id: str, request: Request):
    dev = core.DEVICES.get(dev_id)
    if dev is None or dev.get("transport") != "mqtt":
        raise HTTPException(404, "no such generic-MQTT device")
    return describe(dev)


def on_removed(dev: dict) -> None:
    """The device left the house: its copy goes and the retained topics are cleared, else they would come back."""
    for node in _nodes_of(dev):
        if not NODE_RE.fullmatch(node) or not _path("devices", node).exists():
            continue
        for sub in ("description", "hash"):
            try:
                core.mqtt_pub(core.mqtt_client, f"{node}/fwgen/{sub}", None, retain=True)
            except Exception as e:                              # noqa: BLE001  the broker may be down: the copy goes anyway
                core.log.warning(f"[fwdesc] {node}: could not clear {sub}: {e}")
        for t in core.retained_topics((f"homeassistant/+/{node}/+/config",)):          # its HA discovery configs
            parts = t.split("/")
            if len(parts) == 5 and parts[0] == "homeassistant" and parts[2] == node and parts[4] == "config":
                try:
                    core.mqtt_pub(core.mqtt_client, t, None, retain=True)
                except Exception as e:                              # noqa: BLE001
                    core.log.warning(f"[fwdesc] {node}: could not clear {t}: {e}")
        with _lock:
            _path("devices", node).unlink(missing_ok=True)
            _pending.pop(node, None)
            _want.pop(node, None)


# ---- build records: the builder registers every build when it makes it ----
BUILDER_PATHS = ("/api/fwgen/builds",)


def _builder_token(token, method, path):
    want = os.environ.get("FWGEN_BUILDER_TOKEN", "")
    return bool(want) and bool(token) and hmac.compare_digest(str(token).encode(), want.encode()) \
        and method in ("POST", "GET") and (path == "/api/fwgen/builds" or path.startswith("/api/fwgen/builds/"))


class BuildBody(core.Body):
    hash: str
    date: str = ""
    fwgen: dict = {}
    description: dict
    svg: str = Field("", max_length=MAX_SVG)        # checked in bytes by _svg_ok


def _svg_ok(svg: str) -> bool:
    low = svg.lower()
    return (low.lstrip().startswith(("<svg", "<?xml")) and len(svg.encode("utf-8")) <= MAX_SVG
            and not re.search(r"<script|<foreignobject|javascript:|\bon\w+\s*=|(?:xlink:)?href\s*=\s*[\"']?(?!#)|<image|<iframe|@import|url\s*\(\s*[\"']?(?!#)", low))


def _builder_only(request: Request):
    if not (core.is_house_token(request.headers) or _builder_token(
            core.request_token(request.headers), request.method, request.url.path)):
        raise HTTPException(403, "only the dashboard or the builder does this")


@core.app.post("/api/fwgen/builds")
def register_build(body: BuildBody, request: Request):
    _builder_only(request)
    if not HASH_RE.fullmatch(body.hash):
        raise HTTPException(400, "hash: 12 hex characters")
    try:
        check(body.description)
    except ValueError as e:
        raise HTTPException(400, f"description refused: {e}")
    if len(json.dumps(body.description, ensure_ascii=False).encode("utf-8")) > MAX_DECODED:
        raise HTTPException(400, "description too large")
    if canonical_hash(body.description) != body.hash:
        raise HTTPException(400, "the hash is not the description's own")
    if body.svg and not _svg_ok(body.svg):
        raise HTTPException(400, "svg: too large, or it holds a script or a reference")
    with _lock:
        known = _load("builds", body.hash) is not None
        if not known:
            _save("builds", body.hash, {"hash": body.hash, "date": body.date, "fwgen": body.fwgen,
                                        "description": body.description, "svg": body.svg, "registered": time.time()})
    return {"ok": True, "known": known}


@core.app.get("/api/fwgen/builds/{h}")
def get_build(h: str, request: Request):
    _builder_only(request)
    rec = _load("builds", h) if HASH_RE.fullmatch(h) else None
    if rec is None:
        raise HTTPException(404, "no such build")
    return rec


core.NODE_SUBTOPICS["fwgen"] = on_node
def discovery_ignore(names: set) -> bool:
    """A node with a description of its own is a Yash node: HA discovery does not make a second card of it."""
    return any(NODE_RE.fullmatch(n) and _load("devices", n) for n in names)


core.DEVICE_REMOVED.append(on_removed)
core.DISCOVERY_IGNORE.append(discovery_ignore)
core.TOKEN_CHECKS.append(_builder_token)
core.HEALTH_CHECKS.append(_health)
