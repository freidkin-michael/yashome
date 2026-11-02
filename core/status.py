# The core: dimming windows, the status cache, liveness, events and switching.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

def _pct_to_raw(ctl: dict, pct) -> int:
    """UI percent 0..100 -> raw units of a bright control."""
    lo, hi = ctl["min"], ctl["max"]
    return int(lo + (hi - lo) * max(0, min(100, int(pct))) / 100)


def _raw_to_pct(ctl: dict, raw):
    """Raw units -> UI percent 0..100; None when raw is not a number."""
    lo, hi = ctl.get("min", 0), ctl.get("max", 0)
    span = (hi - lo) or 1
    try:
        return max(0, min(100, round((raw - lo) / span * 100)))
    except TypeError:
        return None


# The [floor, ceil] window of a dimmer channel (settings.json -> channel_dimrange, UI
# percent). The dashboard pill applies it client-side; MQTT clients cannot, so the
# bridge does it for them: a client's 0..100 is the usable travel, exactly like the pill. Without a window both helpers are the identity.
def _dim_window(dev_id: str, code: str):
    dr = _channel_dimrange.get(dev_id)
    dr = dr.get(code) if isinstance(dr, dict) else None
    if not isinstance(dr, (list, tuple)) or len(dr) != 2:
        return None
    try:
        lo, hi = int(dr[0]), int(dr[1])
    except (TypeError, ValueError, IndexError):
        return None
    lo = max(0, min(99, lo))
    hi = max(lo + 1, min(100, hi))
    return None if (lo == 0 and hi == 100) else (lo, hi)


def _win_to_ui(win, pos) -> int:
    """Window-relative 0..100 -> UI percent of the full range (what control() takes)."""
    if not win:
        return max(1, min(100, int(round(pos))))
    lo, hi = win
    return max(1, min(100, int(round(lo + float(pos) / 100 * (hi - lo)))))


def _ui_to_win(win, ui):
    """UI percent -> window-relative 0..100 (what MQTT clients display and step on)."""
    if not win or ui is None:
        return ui
    lo, hi = win
    return max(0, min(100, int(round((ui - lo) / (hi - lo) * 100))))


def _dim_step(pos, direction):
    """One up/down step on the window scale, a tenth from the NEAREST tenth.
    Returns the new window-relative level, or None when a step down is asked at
    the floor: the floor is the dimmest allowed level, so going below it means off."""
    k = int(round((pos or 0) / 10))
    if direction == "down":
        return None if k <= 0 else (k - 1) * 10
    return min(10, k + 1) * 10


def _scale(control: dict, raw_value):
    if raw_value is None:
        return None
    if isinstance(raw_value, bool):       # binary sensors (occupancy, contact...) pass through
        return raw_value
    s = control.get("scale", 1)
    try:
        return round(raw_value * s, 2)
    except Exception:
        return raw_value


# Status cache persists across restarts (bind-mounted dir): after a container
# restart the UI/widget immediately sees last-known states (marked stale)
# instead of everything-offline until the first poll sweep completes.
_status_cache_dirty = threading.Event()


def _load_status_cache():
    if not STATUS_CACHE_PATH.exists():
        return
    try:
        data = json.load(open(STATUS_CACHE_PATH))
    except Exception as e:
        log.warning(f"[status-cache] load failed: {e}")
        return
    now = time.time()
    n = 0
    for did, st in data.items():
        # unknown ids are kept too: a plug-in device may register only once the broker answers
        if not isinstance(did, str) or not isinstance(st, dict):
            continue
        st.pop("stale", None)
        if now - float(st.get("periodic_since") or 0) > PERIODIC_KEEP_S:
            st.pop("periodic", None)            # learned too long ago: learn it again
            st.pop("periodic_since", None)
        if not st.get("offline_since"):         # yesterday's values are not live: wait for the device
            st.update(online=False, waiting="start")
            st.pop("waiting_since", None)       # a start wait counts from the broker's connect
        with _cache_lock:
            _status_cache[did] = st
        _status_ts[did] = now
        n += 1
    log.info(f"[status-cache] restored {n} device states")


def _cache_snapshot() -> dict:
    """The status cache to write: without removed devices once the registry is known, and with all
    of them while mqtt_devices.json did not load (its devices are unknown, not gone)."""
    with _cache_lock:
        snap = copy.deepcopy(_status_cache)
    if _registry_known() and not _MQTT_DEVICES_BROKEN:
        snap = {k: v for k, v in snap.items() if k in DEVICES}
    return snap


def _status_cache_saver_loop():
    while True:
        _status_cache_dirty.wait()
        time.sleep(STATUS_CACHE_SAVE_DEBOUNCE)
        _status_cache_dirty.clear()
        try:
            _atomic_json_dump(STATUS_CACHE_PATH, _cache_snapshot(), ensure_ascii=False)
        except Exception as e:
            log.warning(f"[status-cache] save failed: {e}")


def _status_copy(dev_id: str, default: dict) -> dict:
    """A private copy of a device's cached state to modify and hand back to _store_status.
    Entries in _status_cache are never changed in place: /ws and the snapshot saver read them
    without the lock, and a dict changing size under json.dumps raises."""
    with _cache_lock:
        st = _status_cache.get(dev_id)
        if not st:
            return copy.deepcopy(default)
        out = dict(st)
        for k in ("values", "raw", "last_event"):
            if isinstance(out.get(k), dict):
                out[k] = dict(out[k])
        return out


_report_times: dict = defaultdict(lambda: deque(maxlen=64))   # device -> its last report times


def _periodic(times, now: float):
    """A heartbeat, not events, learned conservatively (a false offline is worse than a false live):
    in the last hour PERIODIC_MIN+ reports (values within 1 s count once) over PERIODIC_SPAN_S+ at
    regular gaps - the longest at most twice the shortest, the median under PERIODIC_GAP_S.
    None: too little to say (keep what is known)."""
    recent = []
    for t in sorted(t for t in times if now - t <= 3600):
        if not recent or t - recent[-1] > 1.0:
            recent.append(t)
    if len(recent) < PERIODIC_MIN:
        return None
    gaps = sorted(b - a for a, b in zip(recent, recent[1:]))
    return (recent[-1] - recent[0] >= PERIODIC_SPAN_S and gaps[-1] <= 2 * gaps[0]
            and gaps[len(gaps) // 2] < PERIODIC_GAP_S)


def _life(dev_id: str, prev: dict, st: dict, report: bool):
    """live / waiting / offline (docs/device-state.md) from the stored change. report: the device
    said something (_store_status); else a word about it as given (availability, a bridge, a probe)."""
    now = time.time()
    for k in ("periodic", "periodic_since", "last_seen"):   # learned / the last report: kept until replaced
        if k in prev and k not in st:
            st[k] = prev[k]
    if st.get("online"):
        if report:
            st["last_seen"] = now
            _report_times[dev_id].append(now)
            p = _periodic(_report_times[dev_id], now)
            if p:
                st.update(periodic=True, periodic_since=now)
            elif p is False:
                st.pop("periodic", None)
                st.pop("periodic_since", None)
            for k in ("waiting", "waiting_since", "offline_since"):
                st.pop(k, None)
        elif prev.get("offline_since") or prev.get("waiting") == "return":   # back: only a report ends it
            st.update(online=False, waiting="return", waiting_since=prev.get("waiting_since") or now)
            st.pop("offline_since", None)
        else:                                   # back at start (its retained availability), or still live
            st.pop("waiting", None)
            st.pop("waiting_since", None)
    elif report and prev.get("waiting") and not st.get("error"):
        st["waiting"] = prev["waiting"]         # a retained frame while it waits: still waiting
        if prev.get("waiting_since"):
            st["waiting_since"] = prev["waiting_since"]
        st.pop("offline_since", None)
    else:                                       # its own word, a bridge, or a poll that failed
        st.pop("waiting", None)
        st.pop("waiting_since", None)
        st["offline_since"] = prev.get("offline_since") or now


_ws_version = 0     # bumped on every status change: a WebSocket frame goes out at once


def _bump():
    global _ws_version
    _ws_version += 1


def _store_status(dev_id: str, st: dict, report: bool = True, codes=None):
    """report=False: a value the core wrote itself (a command's echo); it says nothing about life.
    codes: the channels this report carries (a command in flight on another channel is not answered)."""
    with _cache_lock:
        before = _status_cache.get(dev_id) or {}
        was_away = bool(before.get("offline_since")) or before.get("waiting") == "return"
        if report:
            _store_status_locked(dev_id, st, codes)
        else:
            prev = _status_cache.get(dev_id) or {}
            for k in ("online", "waiting", "offline_since", "last_seen"):
                st.pop(k, None)
                if k in prev:
                    st[k] = prev[k]
            _status_cache[dev_id] = st
    _status_ts[dev_id] = time.time()
    _status_cache_dirty.set()
    _bump()
    try:
        _mqtt_publish_state(dev_id)
    except Exception as e:
        log.warning(f"[state] mirror publish {dev_id} failed: {e}")
    now_st = _status_cache.get(dev_id) or {}
    if report:
        _emit_changes(dev_id, before, now_st)
        vals = now_st.get("values") or {}
        for fn in list(ON_REPORT):
            try:
                fn(dev_id, {c: vals.get(c) for c in (codes or vals)}, time.time())
            except Exception as e:                                               # noqa: BLE001
                log.warning(f"[report] listener {getattr(fn, '__name__', fn)}: {type(e).__name__}: {e}")
    if was_away and now_st.get("online") and not now_st.get("waiting"):
        _rearm_after_outage(dev_id, now_st, before.get("values") or {})


def _rearm_after_outage(dev_id: str, st: dict, before: dict):
    """Back with its own report: a channel it reports ON (and showed ON before: an OFF->ON report is an
    edge the transport arms itself) gets its auto-off window again, unless a shot for it still waits
    (an auto-off or a rule's `for` kept through the outage)."""
    for code, val in (st.get("values") or {}).items():
        if val is True and before.get(code) is True and _inching_sec(dev_id, code) > 0 \
                and not _deferred_active(f"inch|{dev_id}/{code}"):
            with _pending_lock:
                busy = any(t.startswith("for|") and s.get("device") == dev_id and s.get("code") == code
                           for t, s in _pending.items())
            if not busy:
                _arm_inching(dev_id, code)
                log.info(f"[inch] {dev_id}/{code}: back ON after an outage, auto-off armed again")


def _store_status_exact(dev_id: str, st: dict):
    """Store as given, without the grace period of _store_status: for an explicit word from the
    device side (an availability topic), where "offline" means offline now."""
    with _cache_lock:
        before = _status_cache.get(dev_id) or {}
        _life(dev_id, before, st, False)
        _status_cache[dev_id] = st
        _fail_count[dev_id] = 0
    _status_ts[dev_id] = time.time()
    _status_cache_dirty.set()
    _bump()
    try:
        _mqtt_publish_state(dev_id)
    except Exception as e:
        log.warning(f"[state] mirror publish {dev_id} failed: {e}")
    _emit_changes(dev_id, before, st)


def _store_status_locked(dev_id: str, st: dict, codes=None):
    prev = _status_cache.get(dev_id) or {}
    if st.get("online"):
        _fail_count[dev_id] = 0
        _life(dev_id, prev, st, True)
        _status_cache[dev_id] = st
        _cmd_report(dev_id, prev.get("values") or {}, st.get("values") or {}, codes)
    else:
        fc = _fail_count.get(dev_id, 0) + 1
        _fail_count[dev_id] = fc
        if prev and prev.get("online") and fc < OFFLINE_AFTER_N_FAILS:
            kept = dict(prev)
            kept["stale"] = True
            kept["last_error"] = st.get("error", "")
            _status_cache[dev_id] = kept
        else:
            _life(dev_id, prev, st, True)
            _status_cache[dev_id] = st


def get_status(dev_id: str) -> dict:
    st = _status_cache.get(dev_id)
    return st if st is not None else {"online": False, "error": "no data yet"}


def _tag_press(src_id: str, code: str, gesture: str, text: str):
    """The press log line of this press says what the rule did (its newest entry, when it is this press)."""
    with _events_lock:
        evs = _events.get(src_id)
        if evs and evs[-1].get("code") == code and str(evs[-1].get("value")) == gesture:
            evs[-1]["fired"] = text


def _safe_execute_binding(src_id: str, code: str, gesture: str):
    """Run a rule; its result (target, code, value, or error), None when no rule ran. An action that
    raises is a result too ({"error": text}): the press log and a card tap say it did not go out."""
    try:
        result = _execute_binding(src_id, code, gesture)
        if result and result.get("error"):                  # the press log says why nothing happened
            tgt = result.get("target")
            who = BY_ID_NAME.get(tgt, tgt) if tgt else result.get("action")
            if result["error"] == "offline":
                _tag_press(src_id, code, gesture, f"skipped: {who} {result.get('why') or 'offline'}")
            else:
                err = result["error"]
                _tag_press(src_id, code, gesture, f"failed: {who} {err}" if who else f"failed: {err}")
        if result and not result.get("error"):
            tgt = result.get("target")
            what = result.get("value", result.get("action", ""))
            _tag_press(src_id, code, gesture, f"{BY_ID_NAME.get(tgt, tgt)}.{result.get('code', '')}={what}"
                       if tgt else str(result.get("action") or what))
        if result:
            _emit({"kind": "rule", "device": src_id, "code": code, "gesture": gesture, "target": result.get("target"), "tcode": result.get("code"),
                   "value": result.get("value", result.get("action")), "error": result.get("error") or "", "source": f"rule:{src_id}/{code}={gesture}"})
        return result
    except Exception as e:
        err = str(e) or type(e).__name__
        log.warning(f"[binding] {src_id}/{code}/{gesture} -> ERR {err}")
        _tag_press(src_id, code, gesture, f"failed: {err}")
        return {"error": err, "raised": True}      # a text of "offline" is still a failure, not a skip


ORIGIN_ECHO_S = 5          # a change of a channel this soon after a rule's command is the echo of it


def _quiet_device(device: str, seconds: float, why: str) -> None:
    """Do not trust the true edges of this device's binary sensors for `seconds` (a write to the radar's settings makes it see a phantom target for
    10-20 s). An edge that comes in the quiet time is held; when it ends and the sensor still says true, its rule runs then."""
    until = time.time() + seconds
    cur = _QUIET.get(device)
    if cur and cur[0] > until:
        return
    _QUIET[device] = (until, why)
    t = threading.Timer(seconds + 0.5, _after_quiet, (device,))
    t.daemon = True
    t.start()


def _after_quiet(device: str) -> None:
    cur = _QUIET.get(device)
    if cur and cur[0] > time.time():
        return                                                                   # extended meanwhile: the later timer judges
    for dev, code in [p for p in _quiet_pending if p[0] == device]:
        _quiet_pending.discard((dev, code))
        if _sensor_last.get(f"{dev}/{code}"):
            log.info(f"[binding] {dev}/{code}=true held through a config change and still true: rule runs now")
            _bg.submit(_safe_execute_binding, dev, code, "true")


def _seed_sensor_last(name, cfg, values):
    """A retained frame (the device's last word, held by the broker) is the value the next live frame is compared with: without it
    the first live change after a start (a presence going true) looks like a first reading and fires nothing."""
    for c in cfg.get("controls", []):
        if c.get("kind") == "sensor" and c.get("vtype") == "bool" and c.get("code") in values:
            key = f"{name}/{c['code']}"
            if key not in _sensor_last:
                _sensor_last[key] = values[c["code"]]
                _sensor_since[key] = time.time()


def _fire_sensor_triggers(name, cfg, values):
    """Fire bindings on sensor value transitions.
    Binary sensors -> gesture "true"/"false" on change.
    Numeric sensors -> gesture "below"/"above" on entering the threshold zone,
    re-armed only after leaving it past the hysteresis band (no edge chatter,
    no fire on the first reading after boot)."""
    for c in cfg.get("controls", []):
        if c.get("kind") != "sensor" or c.get("code") == "battery":
            continue
        code = c["code"]
        if code not in values:
            continue
        val = values[code]
        key = f"{name}/{code}"
        prev = _sensor_last.get(key)
        _sensor_last[key] = val
        if c.get("vtype") == "bool":
            nb = bool(val)
            if prev is None or bool(prev) != nb:
                _sensor_since[key] = time.time()
            if prev is None or bool(prev) == nb:
                continue                      # no transition / first reading
            g = "true" if nb else "false"
            q = _QUIET.get(name)
            if nb and q and q[0] > time.time():
                log.info(f"[binding] presence after a radar config change ignored: {name}/{code}=true ({q[1]})")
                _quiet_pending.add((name, code))
                continue
            _bg.submit(_safe_execute_binding, name, code, g)
            continue
        # numeric thresholds
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        with _bindings_lock:
            entries = {g: dict(b) for g, b in
                       (_bindings.get(name, {}).get(code, {}) or {}).items()
                       if g in ("below", "above")}
        for g, b in entries.items():
            thr = b.get("threshold")
            if thr is None:
                continue
            h = b.get("hyst") or 0
            ak = f"{name}/{code}/{g}"
            in_zone = (num < thr) if g == "below" else (num > thr)
            if ak not in _sensor_arm:
                _sensor_arm[ak] = not in_zone   # seed; fire only on a future entry
                continue
            if _sensor_arm[ak] and in_zone:
                _sensor_arm[ak] = False
                _bg.submit(_safe_execute_binding, name, code, g)
            elif not _sensor_arm[ak]:
                left = (num > thr + h) if g == "below" else (num < thr - h)
                if left:
                    _sensor_arm[ak] = True


_MAIN_LOOP: "asyncio.AbstractEventLoop | None" = None   # set in _start_pollers
_AFTER_BROKER = None   # keeps the startup task referenced (asyncio holds tasks weakly)


def _apply_value(dev_id: str, code: str, val, kind: str = "switch", timer: bool = False):
    """Push one value to a device over whatever transport it speaks. Single place
    that knows the transport table, shared by /control, bindings and inching.
    timer: the core's own shot (an auto-off, a `for`, a schedule)."""
    cfg = DEVICES.get(dev_id)
    if cfg is None:
        raise RuntimeError(f"unknown device {dev_id}")
    ctl = cfg["_ctl"].get(code) or {}
    if ctl.get("kind") in CONTROL_KINDS:      # a plug-in's stateless channel (an event, no value)
        CONTROL_KINDS[ctl["kind"]](cfg, ctl, val)
        return
    tr = cfg.get("transport")
    if tr == "mqtt":
        _mqtt_generic_publish_set(cfg, code, val, kind)
    elif tr in TRANSPORTS:
        TRANSPORTS[tr](cfg, code, val, kind)
    else:
        raise RuntimeError(f"unsupported transport {tr!r} for {dev_id}")
    if kind == "switch":
        # delivered (a failed publish raised above and keeps the retry): someone's actuation of the
        # channel makes a schedule retry obsolete; a timer's own shot only when it did what it asked
        with _pending_lock:
            waiting = (_pending.get(f"sched|{dev_id}/{code}") or {}).get("value")
        if not timer or waiting is None or bool(waiting) == bool(val):
            _cancel_deferred(f"sched|{dev_id}/{code}")


# Commands sent to a confirm channel and not yet answered: (device, code) -> [(value, sent at), ...],
# oldest first; and the 1 s settle after a report that answers none of them. docs/device-state.md.
_cmd_sent: dict = {}
_cmd_settle: dict = {}     # (device, code) -> (when the settle ends, the report's time, its value)
_cmd_lock = threading.Lock()
SETTLE_S = 1.0


def _cmd_forget(dev_id: str = "", now: float = 0.0):
    """Drop a device's commands in flight (removed, renamed), or with `now` every expired one."""
    with _cmd_lock:
        for key in list(_cmd_sent):
            if (dev_id and key[0] == dev_id) or (now and all(now - t >= CONFIRM_WAIT_S for _v, t in _cmd_sent[key])):
                _cmd_sent.pop(key, None)
                _cmd_settle.pop(key, None)
        for key in [k for k, until in list(_for_echo.items()) if (dev_id and k[0] == dev_id) or (now and until <= now)]:
            _for_echo.pop(key, None)


def _cmd_matches(dev_id: str, code: str, sent, got) -> bool:
    """A report answers a command: a switch equal, a brightness within one step or 2 % of its range."""
    ctl = ((DEVICES.get(dev_id) or {}).get("_ctl") or {}).get(code) or {}
    if ctl.get("kind") == "bright" and isinstance(sent, (int, float)) and isinstance(got, (int, float)):
        span = float(ctl.get("max", 100)) - float(ctl.get("min", 0))
        return abs(float(sent) - float(got)) <= max(1.0, 0.02 * span)
    return sent == got


CMD_KEEP = 8                # commands remembered per channel (a rule firing every minute never echoes)


def _cmd_prune(key, now: float):
    """Expired commands go; at most CMD_KEEP stay. Under _cmd_lock."""
    left = [c for c in _cmd_sent.get(key, []) if now - c[1] < CONFIRM_WAIT_S][-CMD_KEEP:]
    if left:
        _cmd_sent[key] = left
    else:
        _cmd_sent.pop(key, None)
        _cmd_settle.pop(key, None)


def _cmd_report(dev_id: str, before: dict, after: dict, codes=None):
    """A report of the device's channels `codes` (None: the ones whose value changed). An echo of a
    command sent clears the newest such command and every older one (a newer one still waits); a
    report of the channel that answers none of them starts the settle again."""
    now = time.time()
    for key in [k for k in _bind_origin if k[0] == dev_id]:             # the channel changed later than the echo of a rule's command: not that rule's any more
        if now - _bind_origin[key]["ts"] > ORIGIN_ECHO_S and key[1] in after and after.get(key[1]) != before.get(key[1]):
            _bind_origin.pop(key, None)
    with _cmd_lock:
        for key in [k for k in _cmd_sent if k[0] == dev_id]:
            if codes is None and after.get(key[1]) == before.get(key[1]):
                continue
            if codes is not None and key[1] not in codes and not (
                    key[1] in after and _cmd_sent[key] and _cmd_matches(dev_id, key[1], _cmd_sent[key][-1][0], after[key[1]])):
                continue                            # another channel's report says nothing of this one
            got = after.get(key[1])
            hits = [i for i, (v, _t) in enumerate(_cmd_sent[key]) if _cmd_matches(dev_id, key[1], v, got)]
            if hits:
                del _cmd_sent[key][:hits[-1] + 1]
                _cmd_settle.pop(key, None)
            else:
                _cmd_settle[key] = (now + SETTLE_S, now, got)
            _cmd_prune(key, now)


_NEVER_REFUSED = {"webos"}  # an unreachable TV sleeps: power-on (Wake-on-LAN) must still go out


def _not_live_word(dev_id: str) -> str:
    """Why a device does not count now, for a log line: gone (unknown), waiting, or offline."""
    st = _status_cache.get(dev_id)
    return "gone" if dev_id not in DEVICES else "waiting" if st is None or st.get("waiting") else "offline"


def _is_offline(dev_id: str) -> bool:
    """Gone by its own word, its bridge, a failed poll or silence (docs/device-state.md): a command
    to it is refused, a rule skips it. Waiting is not offline."""
    d = DEVICES.get(dev_id) or {}
    if d.get("transport") in _NEVER_REFUSED or d.get("wakes"):      # wakes: a plug-in's device that sleeps, woken by WoL
        return False
    return bool((_status_cache.get(dev_id) or {}).get("offline_since"))


def _value_now(dev_id: str, code: str, default=None, way: str = ""):
    """What a relative command (toggle, a step `way` up/down) builds on: the newest command to a
    confirm channel the device has not answered, else the reported value. While a report that
    answered none settles, a step never goes against it (down: the lower, up: the higher)."""
    now, key = time.time(), (dev_id, code)
    with _cmd_lock:
        settle = _cmd_settle.get(key)
        if settle and now >= settle[0]:                     # settled: what was sent before it is void
            _cmd_sent[key] = [c for c in _cmd_sent.get(key, []) if c[1] > settle[1]]
            _cmd_settle.pop(key, None)
            settle = None
        _cmd_prune(key, now)
        sent = _cmd_sent.get(key)
        if sent:
            v = sent[-1][0]
            if settle and way and sent[-1][1] <= settle[1] and isinstance(v, (int, float)) \
                    and isinstance(settle[2], (int, float)):      # a command older than the report only
                v = min(v, settle[2]) if way == "down" else max(v, settle[2])
            return v
    return ((_status_cache.get(dev_id) or {}).get("values") or {}).get(code, default)


def _cmd_waiting(dev_id: str) -> dict:
    """{code: the newest value sent} for every confirm channel still waiting for the device (the
    rule of _value_now: an echo, a settled other value or 5 s ends it); the screens show it waits."""
    out = {}
    for code in [k[1] for k in list(_cmd_sent) if k[0] == dev_id]:
        _value_now(dev_id, code)                         # applies a settle that ran out, prunes
        with _cmd_lock:
            sent = _cmd_sent.get((dev_id, code))
            if sent:
                out[code] = sent[-1][0]
    return out


def _status_out(dev_id: str) -> dict:
    """The status a screen gets: the cached one, plus cmd_wait while a command waits."""
    st = _status_cache.get(dev_id, {"online": False, "values": {}, "raw": {}})
    waits = _cmd_waiting(dev_id) if any(k[0] == dev_id for k in list(_cmd_sent)) else None
    return {**st, "cmd_wait": waits} if waits else st


_CAUSE: contextvars.ContextVar = contextvars.ContextVar("cause", default="")     # who is acting: app | voice | watchdog | timer | rule:<src>/<code>=<gesture>


def _emit(ev: dict) -> None:
    """Tell the listeners (ON_EVENT) what happened: {kind, device, code, old, new, source, ...}; a listener that raises is logged, never stops the house."""
    if not ON_EVENT:
        return
    ev.setdefault("ts", time.time())
    for fn in list(ON_EVENT):
        try:
            fn(ev)
        except Exception as e:                                                   # noqa: BLE001
            log.warning(f"[event] listener {getattr(fn, '__name__', fn)}: {type(e).__name__}: {e}")


def _emit_changes(dev_id: str, before: dict, after: dict) -> None:
    """A device's report changed something: offline/online, and every channel whose value changed, with who caused it (a command we sent for that value
    in the last 20 s names its cause, else the change came from the device itself: a hand at the wall, a sensor)."""
    if not ON_EVENT:
        return
    if bool(before.get("offline_since")) != bool(after.get("offline_since")):
        _emit({"kind": "offline" if after.get("offline_since") else "online", "device": dev_id})
    if not before or before.get("waiting"):
        return                                                                   # the first report after a start or an outage is no change
    bv, av, now = before.get("values") or {}, after.get("values") or {}, time.time()
    for code, new in av.items():
        old = bv.get(code)
        if old == new:
            continue
        src = "device"
        for d, c, v, _o, ts, cause in reversed(_sent_log):
            if d == dev_id and c == code and now - ts < 20 and v == new:
                src = cause or "app"
                break
        _emit({"kind": "state", "device": dev_id, "code": code, "old": old, "new": new, "source": src})


_sent_log: deque = deque(maxlen=300)    # every command a channel was sent: (device, code, value, the value it reported before, time): the watchdog's evidence


def _optimistic(dev_id: str, code: str, val):
    """The cache after a command was sent. A "confirm" channel (a lock) keeps its real state until the
    device reports it; a device with an availability topic is online only when it says so."""
    cfg = DEVICES[dev_id]
    _last_control_ts[dev_id] = time.time()
    _sent_log.append((dev_id, code, val, ((_status_cache.get(dev_id) or {}).get("values") or {}).get(code), time.time(), _CAUSE.get()))
    _emit({"kind": "command", "device": dev_id, "code": code, "new": val, "old": ((_status_cache.get(dev_id) or {}).get("values") or {}).get(code), "source": _CAUSE.get() or "device"})
    if (cfg["_ctl"].get(code) or {}).get("confirm"):
        with _cmd_lock:
            _cmd_sent.setdefault((dev_id, code), []).append((val, time.time()))
            _cmd_prune((dev_id, code), time.time())
        _bump()                                         # the screens start the pulse now
        return
    cur = _status_copy(dev_id, {"values": {}, "raw": {}})
    cur.setdefault("values", {})[code] = val
    _store_status(dev_id, cur, report=False)         # a command proves nothing about the device's life


def _set_switch(dev_id: str, code: str, on: bool, timer: bool = True):
    """Turn a switch/plug channel on or off, transport-agnostic, and reflect it
    in the cache. Used by server-side inching (pulse)."""
    cfg = DEVICES.get(dev_id)
    if not cfg:
        raise RuntimeError(f"unknown device {dev_id}")
    _apply_value(dev_id, code, on, "switch", timer=timer)
    _optimistic(dev_id, code, on)


# --- Server-side automations: per-channel inching + daily schedule -----------
# Everything runs on OUR server (no device-side inching DP, no vendor-cloud
# schedules). Inching = "after ANY turn-on, auto-off after N minutes" and owns
# the duration for manual / scheduled / binding turn-ons alike. Schedule = just
# on/off times; a scheduled ON inherits the channel's inching window.
_autos: dict = {}            # "<dev>/<code>" -> {"inching_min": int, "schedule": [..]}
_autos_lock = _settings_lock          # one re-entrant config lock
# --- Persistent deferred-action registry ------------------------------------
# One place owns every "do X after N seconds" deadline: inching auto-off and
# rule-action durations (`for`). Each deadline is persisted to disk with its
# absolute run_at, so an app restart replays it with the CORRECT remaining time
# (or fires immediately if it elapsed during downtime) instead of losing it.
_PENDING_PATH = HOUSE / "pending_timers.json"
_pending: dict = {}            # token -> {run_at, device, code, value, label}
_pending_handles: dict = {}    # token -> threading.Timer
_pending_lock = threading.Lock()


_pending_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="timer-save")


def _pending_write():
    with _pending_lock:
        snap = {k: dict(v) for k, v in _pending.items()}
    try:
        _atomic_json_dump(_PENDING_PATH, snap)
    except Exception as e:
        log.warning(f"[timer] persist failed: {e}")


def _pending_save():
    # one writer thread in submission order: the latest snapshot lands last, and a toggle on
    # the event loop never waits for an fsync (slow on SD cards)
    try:
        _pending_writer.submit(_pending_write)
    except RuntimeError:           # the executor is gone (interpreter shutting down)
        _pending_write()


_REPLAY = object()   # _fire_deferred caller: the boot replay of an elapsed deadline
