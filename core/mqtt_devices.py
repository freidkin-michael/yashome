# The core: generic MQTT devices: their state, refresh, liveness.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# Generic MQTT devices (ESPHome, Tasmota, own firmware) from mqtt_devices.json, an example in
# README.md and examples/: state as device-level JSON {code: value} on state_topic or a bare
# scalar on a control's own state_topic, mixable; own firmware: retained state, an availability
# LWT, home/<id>/state + home/<id>/set.
# ---------------------------------------------------------------------------
_MQTT_TOPIC_IDX: dict = {}   # topic -> [(dev_id, role, code)]; role: state|avail|ctl


def _mqtt_topic_index() -> dict:
    """Build the exact-topic routing map for every transport=mqtt device.

    One topic can feed SEVERAL cards, so every entry is a LIST of targets: one ESP
    node often fronts more than one device, and all of them take their liveness
    from the node's single `<node>/status`. A one-target-per-topic map silently
    dropped every card but the last one, which then stayed offline while the node
    was plainly publishing."""
    idx = {}

    def add(topic, target):
        if topic.split("/", 1)[0] in MQTT_NAMESPACES and not _shared_read(topic):   # a plug-in's own bus
            log.warning(f"[mqtt] {target[0]}: topic {topic!r} is in a plug-in's namespace; channel ignored")
            return
        idx.setdefault(topic, []).append(target)

    for did, d in list(DEVICES.items()):
        if d.get("transport") != "mqtt":
            continue
        if d.get("state_topic"):
            add(d["state_topic"], (did, "state", None))
        if d.get("availability_topic"):
            add(d["availability_topic"], (did, "avail", None))
        for c in d.get("controls", []):
            if c.get("state_topic"):
                add(c["state_topic"], (did, "ctl", c["code"]))
    return idx


def _json_state(txt: str):
    """json.loads for device state frames: the NaN/Infinity literals Python accepts
    become None, so the status cache stays valid JSON for /ws and GET /api/status."""
    return json.loads(txt, parse_constant=lambda _c: None)


def _mqtt_truthy(rawv, on_str=None) -> bool:
    if isinstance(rawv, bool):
        return rawv
    if isinstance(rawv, (int, float)):
        return bool(rawv)
    s = str(rawv).strip().lower()
    if on_str is not None and s == str(on_str).strip().lower():
        return True
    return s in ("on", "true", "1", "yes", "open", "detected")


def _mqtt_coerce(ctl, rawv):
    """Normalise a raw MQTT value to the type the UI/binding engine expects for
    this control kind: switch/bool -> bool, bright -> int, numeric -> float.
    NaN/inf become None: they are not JSON and would break every /ws frame."""
    kind = ctl.get("kind")
    if kind == "switch" or ctl.get("vtype") == "bool":
        return _mqtt_truthy(rawv, ctl.get("payload_on"))
    if kind == "bright":
        try:
            return int(float(rawv))
        except OverflowError:
            return None
        except (TypeError, ValueError):
            return rawv
    if isinstance(rawv, (int, float)):
        return rawv if math.isfinite(rawv) else None
    try:
        v = float(rawv)
    except (TypeError, ValueError):
        return rawv
    return v if math.isfinite(v) else None


def _handle_mqtt_state(dev_id, role, code, payload, retain=False):
    """Ingest a generic-MQTT frame into the status cache, then drive the same
    sensor-trigger + server-side inching paths as every other device. `retain` is
    the broker's flag: set only for frames replayed from the retained store."""
    cfg = DEVICES.get(dev_id)
    if not cfg:
        return
    if role == "avail":
        s = payload.decode("utf-8", "replace").strip().lower()
        on = str(cfg.get("payload_online", "online")).strip().lower()
        off = str(cfg.get("payload_offline", "offline")).strip().lower()
        online = (s == on) if s in (on, off) else s not in ("offline", "0", "false", "")
        cur = _status_copy(dev_id, {"online": False, "values": {}, "raw": {}})
        cur["online"] = online
        cur.pop("stale", None)
        _store_status_exact(dev_id, cur)     # no sticky-online grace; mirrors home/state at once
        return
    txt = payload.decode("utf-8", "replace")
    if role == "ctl":
        rawd = {code: txt.strip()}
    else:                                    # device-level JSON {code: value}
        try:
            d = _json_state(txt)
        except ValueError:                   # not JSON: a device-level topic carries nothing for us
            return
        if not isinstance(d, dict):
            return
        rawd = d
    values, raw = {}, {}
    for c in cfg["controls"]:
        cc = c["code"]
        if cc not in rawd:
            continue
        v = _mqtt_coerce(c, rawd[cc])
        if c.get("scale") is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
            v = _scale(c, v)   # generic-mqtt sensors honour "scale" too
        values[cc] = v
        raw[cc] = rawd[cc]
    prev = _status_copy(dev_id, {})
    prev_vals = prev.get("values") or {}
    # When a device declares an availability topic, a RETAINED state frame must not
    # assert online: ESPHome publishes its states retained, so on every (re)connect
    # the retained values arrive after the retained LWT "offline" and used to
    # resurrect an unplugged node -- a display node showed hours-old values as live.
    # A LIVE frame is proof of life and heals the stale "offline" a session takeover
    # leaves behind: the broker sends the old session's will AFTER the new birth.
    online = True
    if retain and cfg.get("availability_topic") and "online" in prev:
        online = bool(prev.get("online"))
    _store_status(dev_id, {"online": online,
                           "values": {**prev_vals, **values},
                           "raw": {**(prev.get("raw") or {}), **raw}},
                  # a per-channel topic answers its channel; a device-level frame carries every channel,
                  # so only the ones that changed count as answered
                  codes={code} if role == "ctl" else {c for c in values if values[c] != prev_vals.get(c)})
    if values:
        if not retain:                    # a retained frame on (re)connect is no new reading: no rule fires
            _fire_sensor_triggers(dev_id, cfg, values)
        else:                             # but it is what the device last said: the next live change is an edge from it
            _seed_sensor_last(dev_id, cfg, values)
        for c in cfg["controls"]:
            cc = c["code"]
            if c.get("kind") != "switch" or cc not in values:
                continue
            newv = bool(values[cc])
            if cc in prev_vals and bool(prev_vals[cc]) == newv:
                continue
            _inching_hook(dev_id, cc, newv)   # auto-off window on ANY turn-on


def _mqtt_generic_publish_set(cfg, code, val, kind):
    """Send a command to a generic-MQTT device. Per-control command_topic wins
    (bare scalar payload); else the device-level command_topic gets {code: val}."""
    cli = _mqtt_client
    if cli is None:
        raise RuntimeError("MQTT not connected")
    ctl = next((c for c in cfg.get("controls", []) if c["code"] == code), None)
    if ctl is None:
        raise RuntimeError(f"unknown code {code}")
    if kind == "switch":
        scalar = ctl.get("payload_on", "ON") if val else ctl.get("payload_off", "OFF")
    elif kind == "bright":
        scalar = str(int(val))
    else:
        scalar = val if isinstance(val, str) else str(val)
    ctopic = ctl.get("command_topic")
    if ctopic:
        _mqtt_pub(cli, ctopic, str(scalar))
        return
    dtopic = cfg.get("command_topic")
    if dtopic:
        _mqtt_pub(cli, dtopic, json.dumps({code: scalar}))
        return
    raise RuntimeError("no command_topic configured for this device")


def _run_resync_hooks(cli, force: bool) -> int:
    """Ask every plug-in transport to have its devices report now; returns how many were asked."""
    n = 0
    for fn in list(RESYNC_HOOKS):
        try:
            n += int(fn(cli, force) or 0)
        except Exception as e:                    # noqa: BLE001  one plug-in must not stop the others
            log.warning(f"[plugins] resync {getattr(fn, '__name__', fn)}: {e}")
    return n


def _mqtt_refresh_loop():
    # Every cycle: plug-in transports ask their devices to re-report (RESYNC_HOOKS), then the mirror
    # publishes what changed and was not out yet. Everything again only on a (re)connect or
    # /api/resync: the retained topics hold the rest, a screen does not get the house twice a minute.
    while True:
        time.sleep(30)
        cli = _mqtt_client
        if cli is None:
            continue
        try:
            _run_resync_hooks(cli, False)
            for did in list(DEVICES):
                _mqtt_publish_state(did)
        except Exception as e:
            log.warning(f"[mqtt] refresh cycle failed: {e}")


_STARTED = time.time()


def _silent_limit(did: str, st: dict):
    """Seconds of silence that make this device offline, None when silence says nothing about it.
    offline_after_s in its entry always wins; else only a learned heartbeat without an availability
    signal (that one decides at once) - an event-only device is never gone by silence."""
    cfg = DEVICES.get(did) or {}
    try:
        own = float(cfg.get("offline_after_s") or 0)
    except (TypeError, ValueError):
        own = 0
    if own > 0:
        return own
    if not st.get("periodic") or cfg.get("availability_topic") or "availability" in st:
        return None
    return DEVICE_SILENT_S


ASK_AFTER_S = 5.0                   # a device still waiting this long is asked for its state, once a wait
ASK_PER_TICK = 10                   # at most this many asks a tick (a bridge back with 50 devices: spread)
_asked_wait: dict = {}              # device -> the wait (its start) it was asked in


_bad_request_warned: set = set()   # devices whose state_request was refused (warned once)


def _state_request(cfg: dict):
    """A generic device's state_request as (topic, payload), or None. The topic follows the rules of a
    discovered topic (_node_topic_ok: no $..., no plug-in namespace, no backend-only filter) and never
    the core's home/; the payload is a string, an object or a list goes as JSON."""
    req = cfg.get("state_request")
    if req is None:
        return None
    topic, payload = (req.get("topic"), req.get("payload", "")) if isinstance(req, dict) else (None, None)
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload)
    why = ("not an object {topic, payload}" if not isinstance(req, dict)
           else "its topic: a plain topic outside home/, $..., the plug-ins' namespaces and the backend-only "
                "filters, at most 256 characters" if not (isinstance(topic, str) and _node_topic_ok(topic)
                                                         and topic.split("/", 1)[0] != "home")
           else "its payload: text, an object or a list" if not isinstance(payload, str) else "")
    if why:
        if cfg.get("id") not in _bad_request_warned:
            _bad_request_warned.add(cfg.get("id"))
            log.warning(f"[state] {cfg.get('id')}: state_request refused - {why}")
        return None
    return topic, payload


def _check_state_requests():
    """At start: a bad state_request of mqtt_devices.json is named now, not at its device's first wait."""
    for d in list(DEVICES.values()):
        if d.get("transport") == "mqtt":
            _state_request(d)


def _ask_state(did: str) -> bool:
    """Ask a waiting device once to report: its transport's STATE_ASK, or a generic device's own
    state_request {topic, payload} from its entry; False when it cannot be asked."""
    cfg = DEVICES.get(did) or {}
    fn = STATE_ASK.get(cfg.get("transport"))
    if fn:
        return bool(fn(did))
    req, cli = _state_request(cfg) if cfg.get("transport") == "mqtt" else None, _mqtt_client
    if not req or cli is None:
        return False
    cli.publish(*req)
    return True


def _waits_for_a_report(did: str, st: dict) -> bool:
    """Back after a real outage (its availability said so), a device with a confirm channel (a lock, a
    dimmer) shows only what the device says: no grace, it waits for its report (and can be commanded
    meanwhile). After a start, or without an availability signal, it follows the grace rule."""
    cfg = DEVICES.get(did) or {}
    return (st.get("waiting") == "return" and bool(cfg.get("availability_topic") or "availability" in st)
            and any(c.get("confirm") for c in cfg.get("controls") or []))


def _liveness_tick(now: float):
    """With the broker connected (counted from the last (re)connect): a device waiting since the start,
    or since it came back, is live with its last values after START_GRACE_S when silence is normal for
    it (no heartbeat, no confirm channel); a heartbeat silent past its limit is offline."""
    _cmd_forget(now=now)                                # a command never answered, a device never back
    cli = _mqtt_client
    if cli is None or not cli.is_connected():          # silent because WE are cut off: nothing to learn
        return
    changed = []
    with _cache_lock:                                   # read and write under one lock: no report is lost
        for did, st in list(_status_cache.items()):
            cur, dirty = dict(st), False
            limit, seen = _silent_limit(did, cur), cur.get("last_seen") or 0
            if limit is not None and cur.get("periodic") and not (DEVICES.get(did) or {}).get("offline_after_s") \
                    and _periodic(_report_times.get(did) or [], seen) is False:
                cur.pop("periodic", None)               # its history up to the last report is not a heartbeat
                cur.pop("periodic_since", None)
                limit, dirty = None, True
            grace = limit is None and not _waits_for_a_report(did, cur)
            if limit is not None and (cur.get("online") or cur.get("waiting")) and now - max(seen, _STARTED) > limit:
                cur.update(online=False, offline_since=now)
                cur.pop("waiting", None)
                cur.pop("waiting_since", None)
                log.info(f"[state] {did}: silent for {int(limit)} s, offline")
            elif grace and ((cur.get("waiting") == "start" and now - _STARTED >= START_GRACE_S) or
                            (cur.get("waiting") == "return"
                             and now - max(cur.get("waiting_since") or now, _STARTED) >= START_GRACE_S)):
                cur.pop("waiting", None)
                cur.pop("waiting_since", None)
                cur["online"] = True
            elif not dirty:
                continue
            _status_cache[did] = cur
            changed.append(did)
    for did in changed:
        _bump()
        _status_cache_dirty.set()
        try:
            _mqtt_publish_state(did)
        except Exception as e:                          # noqa: BLE001
            log.warning(f"[state] mirror publish {did} failed: {e}")
    with _cache_lock:                                   # once per wait, ASK_AFTER_S into it
        due = [(d, st.get("waiting_since") or _STARTED) for d, st in _status_cache.items()
               if st.get("waiting") and d in DEVICES]
    waiting = {d for d, _s in due}
    for d in [d for d in _asked_wait if d not in waiting]:
        _asked_wait.pop(d, None)                        # its wait is over
    asks = 0
    for did, since in due:
        if now - since < ASK_AFTER_S or _asked_wait.get(did) == since:
            continue
        if asks >= ASK_PER_TICK:
            break                                       # the rest on the next tick
        _asked_wait[did] = since
        try:
            if _ask_state(did):                         # only a real ask counts against the cap
                asks += 1
                log.info(f"[state] {did}: asked for its state")
        except Exception as e:                          # noqa: BLE001
            log.warning(f"[state] {did}: asking failed: {e}")


def _liveness_loop():
    while True:
        time.sleep(5)
        try:
            _liveness_tick(time.time())
        except Exception as e:                      # noqa: BLE001
            log.warning(f"[state] liveness: {e}")
