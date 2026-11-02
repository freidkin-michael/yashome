# The core: the devices' REST API: list, status, control, house, automation, renames.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

class ControlBody(Body):
    code: str
    value: object
    source: Optional[str] = None      # who asks, for the history: app (default), voice, watchdog


def _controls_with_dimrange(dev_id, controls):
    """Overlay the per-channel [floor, ceil] window on bright controls for the
    client, without mutating the stored control dicts."""
    out = []
    for c in controls:
        c = {k: v for k, v in c.items() if not k.startswith("_")}   # internal keys stay inside
        # read through _dim_window: a hand-edited entry ([10], "x") is ignored, not a 500
        win = _dim_window(dev_id, c.get("code")) if c.get("kind") == "bright" else None
        if win:
            c = {**c, "floor": win[0], "ceil": win[1]}
        out.append(c)
    return out


@app.get("/api/devices")
def list_devices():
    return [{
        "id": d["id"], "name": d["name"], "category": d.get("category"),
        "transport": d.get("transport"),
        "controls": _controls_with_dimrange(d["id"], d.get("controls", [])),
        "room": d.get("room", ""),
        **{k: d.get(k) for k in DEVICE_FIELDS},   # fields a plug-in needs on its cards
        "ieee": d.get("ieee"), "model": d.get("model"), "vendor": d.get("vendor"), "description": d.get("description"),
        "esphome": bool(d.get("esphome_node") or str(d.get("availability_topic") or "").endswith("/status")),   # an ESPHome node's status topic
        "unsupported": bool(d.get("unsupported")),
        "virtual": d.get("virtual") or "",       # person | presence | tag: no thing in the house, shown where it belongs
        "parts": d.get("parts") or {},             # the other plug-ins' parts of this one thing (an IR remote, a voice satellite)
        "aliases": aliases(d["id"]),               # its ids before a merge: a service keyed by them (the voice glossary) reads them
    } for d in list(DEVICES.values())]


@app.get("/api/devices/{dev_id}/status")
async def status(dev_id: str):
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    # Every transport is push-based (MQTT), so status is always a cache read -- there is nothing left to poll.
    return _status_out(dev_id)


@app.post("/api/resync")
async def resync():
    """Force every surface back in sync in one shot:
      1. let every plug-in transport ask its devices for their current state (RESYNC_HOOKS);
      2. clear the publish-dedup and re-emit the WHOLE home/state/* retained
         mirror (so every retained consumer refreshes).
    The /ws feed then pushes the refreshed cache to every dashboard client on its
    next tick.
    Backend-only primitive on purpose: no UI. The 30 s loop asks the transports and publishes what
    changed; the whole mirror goes again only on a (re)connect and here."""
    cli = _mqtt_client
    asked = await run_in_threadpool(_run_resync_hooks, cli, True) if cli is not None else 0
    _mqtt_last_pub.clear()
    failed = 0
    for did in list(DEVICES):
        try:
            _mqtt_publish_state(did)
        except Exception as e:                      # noqa: BLE001  one device's mirror must not stop the others
            failed += 1
            log.warning(f"[mirror] resync {did}: {type(e).__name__}: {e}")
    if failed:
        log.warning(f"[mirror] resync: {failed} of {len(DEVICES)} devices not mirrored")
    return {"ok": True, "asked": asked, "devices": len(DEVICES)}


@use_route
@app.post("/api/devices/{dev_id}/control")
async def control(dev_id: str, body: ControlBody):
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    _bind_origin.pop((dev_id, body.code), None)                       # a hand (the app, voice): no rule owns the channel now
    _CAUSE.set((body.source or "app")[:32])
    cfg = DEVICES[dev_id]
    ctl = cfg["_ctl"].get(body.code)
    if not ctl:
        raise HTTPException(400, f"Unknown code '{body.code}'")
    if ctl.get("ro") or ctl.get("kind") in ("sensor", "sensor_text", "trigger", "trigger_text"):
        raise HTTPException(400, "Read-only")
    if _is_offline(dev_id) and not ctl.get("_part"):   # every kind of its own; a part (an IR remote) does not need it online
        via = await _part_fallback(dev_id, cfg, ctl, body.value) if ctl.get("_fallback") else None
        if via:
            return {"ok": True, "code": body.code, "value": body.value, "via": via}
        raise HTTPException(409, f"{cfg.get('name') or dev_id} is offline: the command would not arrive")
    if ctl.get("kind") in CONTROL_KINDS:      # a plug-in's stateless channel: no value is stored
        try:
            info = await run_in_threadpool(CONTROL_KINDS[ctl["kind"]], _part_cfg(dev_id, cfg, ctl), ctl, body.value)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"send failed: {e}")
        _last_control_ts[dev_id] = time.time()
        return {"ok": True, "code": body.code, "pressed": info}
    val = body.value
    if ctl["kind"] == "bright" and isinstance(val, str) and val.strip().lower() in ("up", "down"):
        step = _bright_step(dev_id, body.code, val.strip().lower())   # the same step as home/cmd up/down
        if step is None:
            return {"ok": True, "code": body.code, "unchanged": True}
        if step[0] != body.code:
            return await control(dev_id, ControlBody(code=step[0], value=step[1]))
        val = step[1]
    if ctl["kind"] == "switch" and not isinstance(val, bool):
        v = str(val).strip().lower()
        if v in ("1", "true", "on"):
            val = True
        elif v in ("0", "false", "off", ""):
            val = False
        else:
            raise HTTPException(400, f"switch value must be on/off/true/false, got {body.value!r}")
    if ctl["kind"] == "bright":
        try:
            val = _pct_to_raw(ctl, val)
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, f"brightness must be a number 0-100, got {val!r}")
    if ctl["kind"] == "color" and cfg.get("transport") == "mqtt":
        # picked "#rrggbb" -> raw 0-255 to each channel command topic
        hexv = str(val).lstrip("#")
        try:
            r = int(hexv[0:2], 16); g = int(hexv[2:4], 16); b = int(hexv[4:6], 16)
        except ValueError:
            raise HTTPException(400, f"bad color '{val}'")
        ch = ctl.get("channels") or {}
        if _mqtt_client is None or not _mqtt_client.is_connected():
            raise HTTPException(503, "broker not reachable")
        for _nm, _cv in (("r", r), ("g", g), ("b", b)):
            _t = ch.get(_nm)
            if _t:
                _mqtt_client.publish(_t, str(_cv))
        val = "#%02x%02x%02x" % (r, g, b)
        cur = _status_copy(dev_id, {"values": {}, "raw": {}})
        cur.setdefault("values", {})[body.code] = val
        _store_status(dev_id, cur, report=False)     # a colour is never reported back: the value sent
        _last_control_ts[dev_id] = time.time()
        log.info(f"[control] {cfg['name']}/{body.code}={val} via mqtt(color 3ch)")
        return {"ok": True, "code": body.code, "value": val}
    if cfg.get("transport") == "mqtt" and (_mqtt_client is None or not _mqtt_client.is_connected()):
        raise HTTPException(503, "broker not reachable")
    t, pcfg = ctl.get("_transport") or cfg.get("transport"), _part_cfg(dev_id, cfg, ctl)
    try:
        if t in ASYNC_CONTROL or t in TRANSPORTS:
            try:
                if t in ASYNC_CONTROL:
                    await ASYNC_CONTROL[t](pcfg, body.code, val)
                else:
                    await run_in_threadpool(TRANSPORTS[t], pcfg, body.code, val, ctl["kind"])
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(502, f"{t}: {e}")
        elif t == "mqtt":
            try:
                _mqtt_generic_publish_set(cfg, body.code, val, ctl["kind"])
            except Exception as e:
                raise HTTPException(500, f"mqtt error: {e}")
            log.warning(f"[control] {cfg['name']}/{body.code}={val} via mqtt")
        else:
            raise HTTPException(400,
                                f"unsupported transport '{t}'")
    except HTTPException as e:                       # the device's own way failed: a part that maps the code does it
        via = await _part_fallback(dev_id, cfg, ctl, val) if ctl.get("_fallback") and e.status_code in (409, 502, 503, 504) else None
        if via is None:
            raise
        return {"ok": True, "code": body.code, "value": val, "via": via}
    _optimistic(dev_id, body.code, val)
    if ctl["kind"] == "switch":
        _cancel_deferred(f"sched|{dev_id}/{body.code}")   # the user decided: a pending schedule retry is void
        _inching_hook(dev_id, body.code, bool(val))   # server-side auto-off window
    if ctl.get("confirm"):                              # nothing changed yet: the device's report will
        return {"ok": True, "code": body.code, "value": val, "waiting": True}
    return {"ok": True, "code": body.code, "value": val}


@app.get("/api/health")
def health():
    """HTTP is alive since we answered; mqtt = whether paho holds the broker connection now."""
    cli = _mqtt_client
    return {"ok": True, "mqtt": bool(cli is not None and cli.is_connected()), "ts": time.time()}


@app.get("/i18n.js")
def i18n_js():
    """The dashboard's words, led by the house language (the default of a screen where nobody picked one)."""
    head = f"window.HOUSE_LANG = {json.dumps(house_lang())};\nwindow.RESTRICTED = {json.dumps(RESTRICTED)};\n"
    if RESTRICTED:                                    # what runs here: Settings greys a page whose routes are not in it
        head += f"window.RESTRICTED_ROUTES = {json.dumps(sorted(f'{m} {p}' for m, p in RESTRICTED_ALLOW))};\n"
    return Response(head + (ROOT / "i18n.js").read_text(encoding="utf-8"), media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


class HouseBody(Body):
    lang: Optional[str] = None


@app.get("/display.js")
def display_js():
    """How every screen shows a temperature and a humidity (display.js): whole numbers, held."""
    return Response((Path(__file__).parent / "display.js").read_text(encoding="utf-8"), media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


@app.get("/api/house")
def get_house():
    return {**_house, "langs": house_langs()}


@app.put("/api/house")
def put_house(body: HouseBody, request: Request):
    _house_token_only(request)
    if body.lang is not None and body.lang not in house_langs():
        raise HTTPException(400, f"lang: one of {', '.join(house_langs())}")
    with _settings_lock:
        if body.lang is not None:
            _house["lang"] = body.lang
        _save_settings()
    return get_house()


class AutoBody(Body):
    # a field left out keeps its current value: {"inching_sec": 60} must not wipe the schedule
    inching_sec: Optional[int] = None   # total auto-off seconds (0 = off)
    inching_min: Optional[int] = None   # legacy/back-compat; folded into inching_sec
    schedule: Optional[list] = None


@app.get("/api/automation/{dev_id}/{code}")
def get_automation(dev_id: str, code: str):
    a = _autos.get(f"{dev_id}/{code}") or {}
    return {"inching_sec": _inching_sec(dev_id, code),
            "schedule": a.get("schedule") or []}


@use_route
@app.post("/api/automation/{dev_id}/{code}")
def set_automation(dev_id: str, code: str, body: AutoBody):
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    if (DEVICES[dev_id]["_ctl"].get(code) or {}).get("kind") != "switch":
        raise HTTPException(404, "unknown channel")
    key = f"{dev_id}/{code}"
    cur = _autos.get(key) or {}
    sched = []
    for e in ((cur.get("schedule") or []) if body.schedule is None else body.schedule):
        if not isinstance(e, dict):
            raise HTTPException(400, "a schedule row must be an object {time, days, action}")
        t = _norm_hm(e.get("time"))
        if t is None:
            raise HTTPException(400, f"bad time {e.get('time')!r}: use HH:MM")
        raw_days = e.get("days") or [1, 2, 3, 4, 5, 6, 7]
        try:
            days = sorted({int(d) for d in raw_days})
        except (TypeError, ValueError):
            raise HTTPException(400, "days must be numbers 1..7")
        if not days or any(d < 1 or d > 7 for d in days):
            raise HTTPException(400, "days must be numbers 1..7")
        act = e.get("action", "on")
        if act not in ("on", "off"):
            raise HTTPException(400, "action must be on or off")
        # one row per (time, action): the rule id is built from those two, so two rows
        # differing only in days would share an id -- merge their days instead
        same = next((r for r in sched if r["time"] == t and r["action"] == act), None)
        if same:
            same["days"] = sorted(set(same["days"]) | set(days))
        else:
            sched.append({"time": t, "days": days, "action": act})
    if body.inching_sec is None and body.inching_min is None:
        secs = _inching_sec(dev_id, code)
    else:
        secs = max(0, int(body.inching_sec or 0)) or max(0, int(body.inching_min or 0)) * 60
    if secs > _MAX_DELAY:
        raise HTTPException(400, f"auto-off longer than {_MAX_DELAY // 86400} days")
    with _autos_lock:
        if secs <= 0 and not sched:
            _autos.pop(key, None)
        else:
            _autos[key] = {"inching_sec": secs, "schedule": sched}
        _prune_rules_disabled()
        _save_settings()
    _cancel_inching(key)
    st = _status_copy(dev_id, {})
    if secs > 0 and (st.get("values") or {}).get(code):
        _arm_inching(dev_id, code)
    return {"ok": True}


class RenameBody(Body):
    name: str


@app.post("/api/devices/{dev_id}/channel/{code}/rename")
def rename_channel(dev_id: str, code: str, body: RenameBody):
    """Rename one channel (control) of a multi-gang device. Persisted in
    settings.json -> channel_names so it survives container/image rebuilds.
    Empty name reverts the channel to its auto-generated default label."""
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    name = body.name.strip()
    with _settings_lock:
        dev = DEVICES[dev_id]
        ctrl = next((c for c in dev.get("controls", []) if c.get("code") == code), None)
        if ctrl is None:
            raise HTTPException(404, "unknown channel")
        if name:                                       # empty: back to its own label
            name = check_name(name, "channel", [c.get("label") for c in dev.get("controls", []) if c is not ctrl], most=40)
        ov = _channel_names.setdefault(dev_id, {})
        if name:
            ov[code] = name
            ctrl["label"] = name
        else:
            ov.pop(code, None)
            if not ov:
                _channel_names.pop(dev_id, None)
            ctrl["label"] = ctrl.get("_label0", ctrl.get("label", ""))   # the label it registered with
        _save_settings()
    return {"ok": True, "code": code, "label": ctrl["label"]}


class DimRangeBody(Body):
    floor: int
    ceil: int


@app.post("/api/devices/{dev_id}/channel/{code}/dimrange")
def set_dimrange(dev_id: str, code: str, body: DimRangeBody):
    """Set the usable brightness window [floor, ceil] (UI percent 0..100) of one
    dimmer channel. floor>=ceil (or floor 0 & ceil 100) clears it back to the
    default full range. Persisted in settings.json -> channel_dimrange."""
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    lo = max(0, min(100, int(body.floor)))
    hi = max(0, min(100, int(body.ceil)))
    with _settings_lock:
        ctrl = next((c for c in DEVICES[dev_id].get("controls", [])
                     if c.get("code") == code and c.get("kind") == "bright"), None)
        if ctrl is None:
            raise HTTPException(404, "unknown brightness channel")
        dr = _channel_dimrange.get(dev_id)
        if not isinstance(dr, dict):              # a hand-edited settings.json entry
            dr = _channel_dimrange[dev_id] = {}
        if lo >= hi or (lo == 0 and hi == 100):     # nothing to constrain -> clear
            dr.pop(code, None)
            if not dr:
                _channel_dimrange.pop(dev_id, None)
            lo, hi = 0, 100
        else:
            dr[code] = [lo, hi]
        _save_settings()
    return {"ok": True, "code": code, "floor": lo, "ceil": hi}


@app.post("/api/devices/{dev_id}/rename")
async def rename_device(dev_id: str, body: RenameBody):
    """Rename a device (set_device_name: one stored name, shown everywhere)."""
    if dev_id not in DEVICES:
        raise HTTPException(404, "unknown device")
    name = check_name(body.name, "device", [d.get("name") for k, d in list(DEVICES.items()) if k != dev_id])
    await asyncio.to_thread(set_device_name, dev_id, name)
    return {"ok": True, "name": name}


def set_device_name(dev_id: str, name: str) -> None:
    """The one way a display name changes: a plug-in that keeps its devices' names (NAME_OWNERS) stores it
    there, any other device in the overrides ("names"); either way the other store holds none, so the
    tile, the card, voice and the panels read one name."""
    owned = any(fn(dev_id, name) for fn in list(NAME_OWNERS))      # outside the lock: an owner saves under its own
    with _settings_lock:
        if owned:
            _name_overrides.pop(dev_id, None)
        else:
            _name_overrides[dev_id] = name
        if dev_id in DEVICES:
            DEVICES[dev_id]["name"] = name
            BY_ID_NAME[dev_id] = name
        _save_settings()


def take_name_override(dev_id: str):
    """A plug-in that keeps the name itself takes over a rename saved in the overrides: returned, and gone from there."""
    with _settings_lock:
        name = _name_overrides.pop(dev_id, None)
        if name is not None:
            _save_settings()
        return name if isinstance(name, str) and name else None
