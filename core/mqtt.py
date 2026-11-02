# The core: the MQTT bridge: publish, subscribe, the state mirror, commands.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# MQTT bridge -- LAN clients (displays, scripts) talk to the dashboard over mosquitto:
# retained home/state/<dev>/<code> (+ _pct for brightness, + online) and commands on
# home/cmd/<dev>/<code>, re-posted to our own /control so REST and MQTT validate alike.
# Payloads and the brightness window: docs/mqtt-contract.md.
# ---------------------------------------------------------------------------
try:
    import paho.mqtt.client as _paho
except ImportError:
    _paho = None

_mqtt_ready = threading.Event()   # set on CONNACK; publishes are dropped before it


def _mqtt_pub(cli, topic, payload=None, retain=False):
    """Publish and FAIL LOUDLY if the broker link was down. paho does not queue a
    QoS-0 publish while the socket is closed -- it returns rc=MQTT_ERR_NO_CONN and
    drops the message. Every command path used to ignore that, so /control still
    answered ok:True and wrote the value nobody applied into the optimistic cache.
    Only command paths use this; the retained-mirror republishers stay best-effort
    because the 30 s refresh loop covers them."""
    info = cli.publish(topic, payload, retain=retain)
    rc = getattr(info, "rc", 0)
    if rc != 0:
        raise RuntimeError(f"mqtt publish to {topic} failed (rc={rc})")
    return info


MQTT_HOST = os.environ.get("MQTT_HOST") or "mosquitto"
MQTT_PORT = int(os.environ.get("MQTT_PORT") or "1883")


def _mqtt_auth(cli):
    """Broker credentials from the environment; anonymous when unset."""
    user = os.environ.get("MQTT_USER")
    if user:
        cli.username_pw_set(user, os.environ.get("MQTT_PASSWORD", ""))
    return cli
_mqtt_client = None
_mqtt_last_pub: dict = {}    # topic -> last payload, dedup against poller spam
_sensor_last: dict = {}      # f"{name}/{code}" -> last LIVE sensor value (fire bindings on change); empty after a start until the device reports
_QUIET: dict = {}            # device -> (until, why): a config write made its sensors say things that are not so (a radar's phantom target): a true edge is not trusted until then
_quiet_pending: set = set()  # (device, code): a true edge that arrived in the quiet time: judged again when it ends
_sensor_since: dict = {}     # f"{name}/{code}" -> when a live binary sensor last changed (or was first heard)
_bind_origin: dict = {}      # (target, code) -> {src, code, g, ts}: the rule that last switched the channel; any other change or a hand clears it
_sensor_arm: dict = {}       # f"{name}/{code}/{gesture}" -> armed (hysteresis for numeric)

def _mqtt_payload(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


_unmirrored: set = set()


def _mqtt_publish_state(dev_id: str):
    cli = _mqtt_client
    if cli is None:
        return
    if dev_id not in DEVICES:                       # a device that is gone is not mirrored again: its retained branch was cleared
        if dev_id not in _unmirrored:
            _unmirrored.add(dev_id)
            log.info(f"[mirror] {dev_id} is no device: not mirrored (state stored by {sys._getframe(1).f_code.co_name})")
        return
    # every device, whatever it speaks, is mirrored to home/state/<id>/<code>: that
    # is the one contract MQTT clients read (docs/mqtt-contract.md)
    st = _status_copy(dev_id, {})
    msgs = {f"home/state/{dev_id}/online": _mqtt_payload(bool(st.get("online")))}
    values = st.get("values") or {}
    for code, val in values.items():
        msgs[f"home/state/{dev_id}/{code}"] = _mqtt_payload(val)
    cfg = DEVICES.get(dev_id)
    if cfg:
        # The percent mirror always carries the LEVEL, including while the channel
        # is off: an off tile reads what it will come back at, not 0%. On/off is
        # therefore NOT encoded in this number: a client takes it from the switch's
        # own state topic and the dashboard from the pill's .on class. Only an ON light parked at the device
        # floor is clamped up to 1%, so it never reads as a dark 0%.
        switches = [c for c in cfg["_ctl"].values()
                    if c.get("kind") == "switch"]
        for ctl in cfg["_ctl"].values():
            if ctl.get("kind") == "bright" and ctl["code"] in values:
                # Resolve "is it on" from the switch on the SAME gang: a multi-gang
                # dimmer has one per channel. This used to read a hardcoded
                # "switch_led" code, which no current device has, so power_on
                # always defaulted to True and an OFF channel kept publishing its
                # last brightness -- an MQTT client then could not tell off from
                # on-at-minimum.
                want = _chan_of(str(ctl.get("code") or ""))
                sw = next((s for s in switches
                           if _chan_of(str(s.get("code") or "")) == want), None)
                if sw is None and len(switches) == 1:
                    sw = switches[0]
                power_on = bool(values.get(sw["code"], True)) if sw else True
                pct = _raw_to_pct(ctl, values.get(ctl["code"]))
                if pct is not None:
                    # window-relative when the channel has a [floor, ceil]: the client's
                    # bar then spans the usable travel, like the dashboard pill
                    pct = _ui_to_win(_dim_window(dev_id, ctl["code"]), pct)
                    if power_on and pct < 1:
                        pct = 1
                    msgs[f"home/state/{dev_id}/{ctl['code']}_pct"] = str(pct)
    for topic, payload in msgs.items():
        if _mqtt_last_pub.get(topic) != payload:
            try:
                info = cli.publish(topic, payload, retain=True)
            except Exception as e:                             # noqa: BLE001
                log.debug(f"[mirror] {topic}: {e}")
                continue
            if getattr(info, "rc", 0) == 0:                    # queued; else the next call tries again
                _mqtt_last_pub[topic] = payload


def _shared_read(topic: str) -> bool:
    return any(_filter_hit(f, topic) for f in MQTT_SHARED_READ)


def _mqtt_on_message(cli, userdata, msg):
    try:
        hits = _MQTT_TOPIC_IDX.get(msg.topic)   # generic-MQTT DIY devices (exact topic)
        if hits:
            for _did, _role, _code in hits:
                _handle_mqtt_state(_did, _role, _code, msg.payload, msg.retain)
            if msg.topic.split("/", 1)[0] not in MQTT_NAMESPACES:   # a shared one is its plug-in's frame too
                return
        parts = msg.topic.split("/")
        if len(parts) >= 3 and parts[1] in NODE_SUBTOPICS and parts[0] != "home" and parts[0] not in MQTT_NAMESPACES:
            NODE_SUBTOPICS[parts[1]](parts, msg.payload, msg.retain)    # <node>/<name>/...: a plug-in reads what a node says of itself
            return
        for pre, fn in MQTT_HANDLERS:            # plug-in topics
            if tuple(parts[:len(pre)]) == tuple(pre):
                fn(parts, msg.payload, msg.retain)
                return
        if len(parts) != 4 or parts[:2] != ["home", "cmd"]:
            return
        if msg.retain:            # a command is an event; a retained one would re-run on every reconnect
            log.info(f"[mqtt] ignoring retained command {msg.topic}")
            return
        mqtt_command(parts[2], parts[3], msg.payload, msg.topic)
    except Exception as e:
        log.warning(f"[mqtt] cmd error {msg.topic}: {e}")


def _bright_step(dev_id: str, code: str, way: str):
    """One tenth of the dim window up or down: (code, full-range percent), or (switch code, False) when
    "down" at the floor switches the gang off; None when there is nothing to do (an off gang, down)."""
    cfg = DEVICES[dev_id]
    ctl = cfg["_ctl"][code]
    sw = _switch_for(cfg, code)
    if sw and not _value_now(dev_id, sw["code"], True):   # an off gang: "up" switches it on, "down" leaves it off
        return None if way == "down" else (sw["code"], True)
    win = _dim_window(dev_id, code)
    nxt = _dim_step(_ui_to_win(win, _raw_to_pct(ctl, _value_now(dev_id, code, way=way)) or 0), way)
    if nxt is not None:
        return code, _win_to_ui(win, nxt)
    if sw:                                  # already at the floor: the step switches the gang off instead
        return sw["code"], False
    return code, 0                          # no switch: 0 is off, whatever the dim window


def mqtt_command(dev_id: str, code: str, payload: bytes, topic: str = ""):
    """One command in the home/cmd payload language (true/false/on/off/toggle/up/down/number),
    handed to control() on the event loop: the core's own bus and a plug-in's relay alike."""
    if dev_id not in DEVICES:
        log.info(f"[mqtt] cmd for unknown device {dev_id}")
        return
    if code.endswith("_pct") and code not in DEVICES[dev_id]["_ctl"]:   # a real control so named keeps it
        code = code[:-4]
    raw = payload.decode("utf-8", "replace").strip()
    cfg = DEVICES[dev_id]
    ctl = cfg["_ctl"].get(code)
    low = raw.lower()
    if low == "toggle":
        value = not bool(_value_now(dev_id, code, False))
    elif low in ("true", "on"):
        value = True
    elif low in ("false", "off"):
        value = False
    elif low in ("up", "down") and ctl and ctl.get("kind") == "bright":
        step = _bright_step(dev_id, code, low)
        if step is None:
            return
        code, value = step
    else:
        try:
            value = int(raw)
        except ValueError:
            try:
                value = float(raw)
            except ValueError:
                value = raw
        if ctl and ctl.get("kind") == "bright" and isinstance(value, (int, float)):
            # a client's percent is window-relative; control() wants full-range
            value = _win_to_ui(_dim_window(dev_id, code), value)
    # Hand the command to the event loop instead of HTTP-posting to ourselves:
    # this is paho's only network thread, and a blocking round-trip here stalls
    # every MQTT frame and the keepalive for its whole duration.
    if _MAIN_LOOP is None:
        log.warning(f"[mqtt] cmd {topic} arrived before startup, dropped")
        return
    fut = asyncio.run_coroutine_threadsafe(
        control(dev_id, ControlBody(code=code, value=value)), _MAIN_LOOP)
    fut.add_done_callback(lambda f, t=topic, v=raw: _log_cmd_result(f, t, v))


_cmd_refused: dict = {}    # topic -> [first logged at, refused since]: a flood is one line per 10 s


def _log_cmd_result(fut, topic, raw):
    try:
        fut.result()
    except HTTPException as e:
        if e.status_code == 409:
            now, seen = time.time(), _cmd_refused.get(topic)
            if seen and now - seen[0] < 10:
                seen[1] += 1
                return
            more = f" (and {seen[1]} more in 10 s)" if seen and seen[1] else ""
            _cmd_refused[topic] = [now, 0]
            log.warning(f"[mqtt] cmd {topic}={raw} -> 409 {e.detail}{more}")
            return
        log.warning(f"[mqtt] cmd {topic}={raw} -> {e.status_code} {e.detail}")
    except Exception as e:
        log.warning(f"[mqtt] cmd {topic}={raw} failed: {e}")


def _mqtt_on_connect(cli, userdata, flags, rc, properties=None):
    if getattr(rc, "is_failure", False):
        log.error(f"[mqtt] broker refused the connection: {rc}")
        _mqtt_ready.clear()
        return
    log.info(f"[mqtt] connected to {MQTT_HOST}:{MQTT_PORT} rc={rc}")
    global _STARTED
    _STARTED = time.time()             # the grace and the silence count from the broker's replay
    try:
        _mqtt_subscribe_all(cli)
    except Exception:
        log.exception("[mqtt] on_connect failed; the link stays up, subscriptions may be partial")
    for fn in list(ON_MQTT_CONNECT):
        try:
            fn(cli)
        except Exception as e:                    # noqa: BLE001  one plug-in must not stop the others
            log.error(f"[plugins] on-connect {getattr(fn, '__name__', fn)}: {e}")
    _mqtt_ready.set()          # startup waits on this before replaying timers


def _mqtt_on_disconnect(cli, userdata, flags, rc, properties=None):
    _mqtt_ready.clear()
    log.warning(f"[mqtt] disconnected: {rc}")


def _mqtt_subscribe_all(cli):
    global _MQTT_TOPIC_IDX
    cli.subscribe("home/cmd/#")
    _MQTT_TOPIC_IDX = _mqtt_topic_index()     # generic-MQTT DIY ESP devices
    for _t in _MQTT_TOPIC_IDX:
        cli.subscribe(_t)
    if _MQTT_TOPIC_IDX:
        log.info(f"[mqtt] subscribed {len(_MQTT_TOPIC_IDX)} generic-mqtt topics")
    # plug-in topics: last, one bad filter hurts nobody else. In list order: the broker replays
    # retained messages in subscription order (a device registry before its state frames)
    for name in list(NODE_SUBTOPICS):
        try:
            cli.subscribe(f"+/{name}/#")
        except Exception as e:
            log.error(f"[plugins] subscribe +/{name}/# failed: {e}")
    for t in MQTT_SUBSCRIPTIONS:
        try:
            cli.subscribe(t)
        except Exception as e:
            log.error(f"[plugins] subscribe {t!r} failed: {e}")
    # fresh retained snapshot for every device (a client may hold stale state)
    _mqtt_last_pub.clear()
    for did in list(DEVICES):
        _mqtt_publish_state(did)


def _mqtt_start():
    global _mqtt_client
    if _paho is None:
        log.warning("[mqtt] paho-mqtt not installed, bridge disabled")
        return
    cli = _paho.Client(_paho.CallbackAPIVersion.VERSION2, client_id=f"home-dashboard-{secrets.token_hex(3)}")   # two backends on one broker must not kick each other
    cli.on_connect = _mqtt_on_connect
    cli.on_disconnect = _mqtt_on_disconnect
    cli.on_message = _mqtt_on_message
    cli.suppress_exceptions = True    # a bug in a callback is logged, the network loop lives on
    cli.reconnect_delay_set(min_delay=1, max_delay=30)
    _mqtt_auth(cli)
    _mqtt_client = cli

    def _connect_when_reachable():
        # A fresh install starts the broker and the backend together: the first connect
        # may fail on DNS or a refused socket, and paho's network loop does not retry
        # THAT failure - only lost connections. So knock until the broker answers.
        delay = 1
        while True:
            try:
                cli.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
                break
            except (OSError, ValueError) as e:        # ValueError: empty MQTT_HOST
                log.warning(f"[mqtt] {MQTT_HOST}:{MQTT_PORT} not reachable ({e}); retry in {delay}s")
                time.sleep(delay)
                delay = min(delay * 2, 30)
        cli.loop_start()

    threading.Thread(target=_connect_when_reachable, daemon=True, name="mqtt-connect").start()


# Gang helpers: 'state_l2' and 'brightness_l2' are the same channel of a multi-gang device.
def _chan_of(code: str) -> str:
    """Gang suffix of a control code: 'state_l2' -> 'l2', 'brightness' -> ''."""
    for base in ("state", "brightness"):
        if code.startswith(base):
            return code[len(base):].lstrip("_")
    return ""


def _switch_for(cfg, code):
    """The on/off switch on the SAME gang as a brightness code: 'brightness_l2' ->
    'state_l2', 'brightness' -> 'state'. None when the device has no such switch."""
    want = _chan_of(str(code or ""))
    for c in cfg.get("controls", []):
        if c.get("kind") == "switch" and _chan_of(str(c.get("code") or "")) == want:
            return c
    return None


# A plug-in whose devices come from outside (a coordinator's retained list) says so at import
# with registry_pending() and calls registry_ready() once the list is in. Until then the core
# does nothing that treats an unknown id as gone: no mirror prune, no cache pruning, no audit.
_registry_cv = threading.Condition()


def registry_pending(name: "str | None" = None):
    """Mark the device list of a plug-in as not known yet (default: the calling plug-in)."""
    with _registry_cv:
        REGISTRY_PENDING.add(name or _calling_plugin() or "?")


def registry_ready(name: "str | None" = None):
    """The plug-in's device list is in: pruning and the reference audit may run."""
    with _registry_cv:
        REGISTRY_PENDING.discard(name or _calling_plugin() or "?")
        _registry_cv.notify_all()


def _registry_known() -> bool:
    return not REGISTRY_PENDING


def _wait_registry(timeout: float) -> bool:
    with _registry_cv:
        return _registry_cv.wait_for(_registry_known, timeout)


def _prune_orphan_state_mirror(window: float = 5.0):
    """Clear retained home/state/<dev>/* branches of devices that no longer exist.

    The mirror is only ever written, never cleaned, so every device removed or
    renamed since the first boot left a frozen retained branch behind, and every
    retained consumer keeps reading those forever.

    Runs on a short-lived client so the ingest path is untouched, and refuses to
    run until every plug-in's device list is in (registry_ready) -- otherwise its devices
    would look unknown and their mirror would be wiped."""
    if _paho is None:
        return 0
    if _MQTT_DEVICES_BROKEN:
        log.warning(f"[mirror] prune skipped: mqtt_devices.json did not load ({_MQTT_DEVICES_BROKEN})")
        return 0
    if not _wait_registry(60):
        log.info(f"[mirror] prune skipped: no device list from {sorted(REGISTRY_PENDING)} within 60 s")
        return 0
    topics: set = set()
    cli = _paho.Client(_paho.CallbackAPIVersion.VERSION2, client_id=f"home-mirror-prune-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: topics.add(m.topic)
    try:
        _mqtt_auth(cli).connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        cli.subscribe("home/state/#")
        cli.loop_start()
        time.sleep(window)                 # retained backlog arrives in one burst
    except Exception as e:
        log.warning(f"[mirror] prune skipped: {e}")
        return 0
    finally:
        try:
            cli.loop_stop(); cli.disconnect()
        except Exception:                         # noqa: BLE001  closing a one-off client: what it says no longer matters
            pass
    orphans = {}
    for t in topics:
        parts = t.split("/")               # home/state/<dev>/<code>; dev may contain no /
        if len(parts) < 4 or parts[0] != "home" or parts[1] != "state":
            continue
        dev = parts[2]
        if dev not in DEVICES:
            orphans.setdefault(dev, []).append(t)
    if not orphans:
        return 0
    pub = _mqtt_client
    if pub is None:
        return 0
    n = 0
    for dev, ts in orphans.items():
        for t in ts:
            pub.publish(t, None, retain=True)   # empty payload = delete retained
            n += 1
    log.info(f"[mirror] cleared {n} retained topics of {len(orphans)} gone devices: "
          + ", ".join(sorted(orphans)))
    return n
