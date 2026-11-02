# The core: mqtt_devices.json: generic MQTT devices, their names and channels.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Devices come from mqtt_devices.json (generic MQTT: ESPHome, Tasmota, your own firmware)
# and from plug-ins (register_device).
DEVICES: dict = {}

# Each mqtt_devices.json entry is a normal device dict with transport="mqtt"; see _handle_mqtt_state.
def _read_mqtt_devices(path: Path) -> tuple:
    """(entries, why it did not load or ""): an unreadable file gives no entries and says why."""
    try:
        with open(path, encoding="utf-8") as f:
            mds = json.load(f) or []
        if not isinstance(mds, list):
            raise ValueError("expected a list of devices")
        return mds, ""
    except Exception as e:                                   # noqa: BLE001
        log.warning(f"[mqtt] failed to load {path}: {e}")
        return [], str(e) or type(e).__name__


_MQTT_DEVICES_BROKEN = ""    # why the file did not load: its devices are unknown, not gone (no prune)
if MQTT_DEVICES_PATH.exists():
    _mds, _MQTT_DEVICES_BROKEN = _read_mqtt_devices(MQTT_DEVICES_PATH)
    for _i, _md in enumerate(_mds):
        # one bad entry is skipped, it does not take the others (or the whole backend) down
        if not isinstance(_md, dict) or not isinstance(_md.get("id"), str) or not _md["id"]:
            log.warning(f"[mqtt] mqtt_devices.json entry #{_i} skipped: an object with a string \"id\" is needed")
            continue
        if not isinstance(_md.get("name"), str) or not _md["name"].strip():
            _md["name"] = _md["id"]
        if not isinstance(_md.get("controls"), list):
            _md["controls"] = []
        _md["transport"] = "mqtt"
        DEVICES[_md["id"]] = _md

# Channel codes come from outside (a plug-in, ESPHome discovery, a hand-written JSON) and end
# up in MQTT topics, rule ids and the dashboard's markup, so only plain names are accepted.
_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_CAT_RE = re.compile(r"^[a-z_]{1,32}$")
_MAX_DELAY = 7 * 86400     # longest auto-off / rule duration: a threading.Timer overflows far beyond


def _own_topic(t) -> bool:
    """home/state/# and home/cmd/# are the backend's own bus: a device may not live there."""
    return isinstance(t, str) and (t.startswith("home/state/") or t.startswith("home/cmd/"))


def _safe_controls(dev_id: str, controls) -> list:
    out = []
    for c in controls or []:
        if isinstance(c, dict) and (_own_topic(c.get("state_topic")) or _own_topic(c.get("command_topic"))):
            log.warning(f"[devices] {dev_id}/{c.get('code')}: topics under home/state/ or home/cmd/ are the "
                        "backend's own mirror; channel skipped")
            continue
        if isinstance(c, dict) and _CODE_RE.match(str(c.get("code", ""))):
            out.append(c)
        else:
            code = c.get("code") if isinstance(c, dict) else c
            log.warning(f"[devices] {dev_id}: channel code {str(code)[:40]!r} "
                        "skipped (allowed: letters, digits, _ . -)")
    return out


def _mark_dimmers(d: dict):
    """A brightness channel and the switch it dims wait for the device's own report (confirm), when
    the device reports them: <x>_bright dims <x> (ESPHome), brightness[_g] dims state[_g]. A generic
    channel with no state topic of its own nor of its device never reports; `confirm` set in the
    entry wins."""
    d["controls"] = ctls = [dict(c) if isinstance(c, dict) else c for c in d.get("controls") or []]   # never the entry's own
    sw = {c.get("code"): c for c in ctls if c.get("kind") == "switch"}
    brights = [c for c in ctls if c.get("kind") == "bright" and not c.get("ro")]
    for b in brights:
        code = str(b.get("code") or "")
        pair = sw.get(code[:-len("_bright")]) if code.endswith("_bright") else \
            sw.get("state" + code[len("brightness"):]) if code.startswith("brightness") else None
        if pair is None and len(sw) == 1 and len(brights) == 1:   # one switch, one brightness: a lamp
            pair = next(iter(sw.values()))
        for c in (b, pair):
            if c is not None and (d.get("transport", "mqtt") != "mqtt" or c.get("state_topic") or d.get("state_topic")):
                c.setdefault("confirm", True)


for _bad in [k for k, d in list(DEVICES.items()) if not (isinstance(k, str) and _CODE_RE.match(k))]:
    log.warning(f"[devices] mqtt_devices.json id {str(_bad)[:40]!r} skipped (allowed: letters, digits, _ . -)")
    DEVICES.pop(_bad)
for _bad in [k for k, d in list(DEVICES.items())
             if any(_own_topic(d.get(t)) for t in ("state_topic", "command_topic", "availability_topic"))]:
    log.warning(f"[devices] {_bad}: a device-level topic under home/state/ or home/cmd/ (the backend's own bus); skipped")
    DEVICES.pop(_bad)
for d in list(DEVICES.values()):
    if not _CAT_RE.match(str(d.get("category") or "other")):
        d["category"] = "other"
    d["controls"] = _safe_controls(d["id"], d.get("controls"))
    _mark_dimmers(d)
    for _c in d["controls"]:
        _c.setdefault("_label0", _c.get("label", ""))
    d["_ctl"] = {c["code"]: c for c in d["controls"]}   # channel code -> control

# Device-name overrides (settings.json "names"), applied on top at startup so
# they survive a plug-in registering its devices again.
def _migrate_names(v, old, new, lost):
    if old not in v:
        return {"overrides": 0}
    label = v.pop(old)
    if label and label != new:                 # a friendly_name equal to the display name needs none
        v.setdefault(new, label)
    return {"overrides": 1}


_name_overrides: dict = {}
_store("names", "_name_overrides", {}, migrate=_migrate_names,
       refs=lambda v: [("name-override", d, d) for d in v])


def _apply_name_overrides():
    """Re-apply the saved display names to whatever is in DEVICES right now.
    Plug-in devices get theirs in register_device, which runs later."""
    for _did, _nm in _name_overrides.items():
        if _did in DEVICES and isinstance(_nm, str) and _nm:
            DEVICES[_did]["name"] = _nm
            BY_ID_NAME[_did] = _nm


def _migrate_channel_names(v, old, new, lost):
    if old not in v:
        return {"channel_names": 0}
    v[new] = {**v.pop(old), **(v.get(new) or {})}
    return {"channel_names": 1}


_channel_names: dict = {}                       # {device_id: {code: custom_label}}
_store("channel_names", "_channel_names", {}, migrate=_migrate_channel_names,
       refs=lambda v: [("channel-names", d, d) for d in v])


def _apply_channel_names(d: dict):
    """Saved channel renames on top of a device's controls, whatever its transport."""
    ov = _channel_names.get(d.get("id")) or {}
    for c in d.get("controls") or []:
        c.setdefault("_label0", c.get("label", ""))
        if ov.get(c.get("code")):
            c["label"] = ov[c["code"]]


for _d in list(DEVICES.values()):
    _apply_channel_names(_d)
# Per-dimmer-channel usable brightness window, in UI percent (0..100): the LED
# wired to a gang may only light from ~15% and hit full by ~60%, so the pill maps
# its 0..100 travel into [floor, ceil]. Default (no entry) = full 0..100 range.
def _migrate_dimrange(v, old, new, lost):
    if old not in v:
        return {"channel_dimrange": 0}
    v[new] = {**v.pop(old), **(v.get(new) or {})}   # a channel the target has keeps its own window
    return {"channel_dimrange": 1}


_channel_dimrange: dict = {}                    # {device_id: {bright_code: [floor, ceil]}}
_store("channel_dimrange", "_channel_dimrange", {}, migrate=_migrate_dimrange,
       refs=lambda v: [("channel-dimrange", d, d) for d in v])

# dev_id -> human name (for log/journal output)
BY_ID_NAME: dict[str, str] = {did: d["name"] for did, d in DEVICES.items()}
