# The core: deferred actions (for-timers, auto-off, missed schedules) and the scheduler.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

def _fire_deferred(token: str, timer=None):
    tok = _CAUSE.set("timer")
    try:
        return _fire_deferred_inner(token, timer)
    finally:
        _CAUSE.reset(tok)


def _fire_deferred_inner(token: str, timer=None):
    with _pending_lock:
        if timer is _REPLAY:
            if token in _pending_handles:
                return                 # armed again since start: the new timer owns the token
        elif timer is not None and _pending_handles.get(token) is not timer:
            return                     # re-armed or cancelled meanwhile: this timer is stale
        spec = _pending.get(token)
        if spec is None:
            _pending_handles.pop(token, None)
            return
        st = _status_cache.get(spec["device"]) or {}
        starting = st.get("waiting") == "start"           # at a start: not known yet, ends within the grace
        away = _is_offline(spec["device"]) or starting
        due = float(spec.get("due") or spec.get("run_at") or time.time())
        if away and (starting or time.time() - due < _OFFLINE_KEEP_S.get(token.split("|", 1)[0], 0)):
            t = threading.Timer(START_RETRY_S if starting else OFFLINE_RETRY_S, lambda: _fire_deferred(token, t))
            t.daemon = True
            _pending_handles[token] = t                  # in memory only: the file keeps its time
            t.start()
            if token not in _waiting_shots:
                _waiting_shots.add(token)
                log.info(f"[timer] {token}: {spec['device']} is away, the shot waits for it")
            return
        _pending.pop(token, None)
        _pending_handles.pop(token, None)
    _waiting_shots.discard(token)
    _pending_save()
    today = time.strftime("%Y-%m-%d")
    if away or (token.startswith("sched|") and spec.get("due") and time.strftime("%Y-%m-%d", time.localtime(due)) != today):
        log.warning(f"[timer] {token}: {spec['device']} " + ("away since its time" if away else "back only after its day")
                    + ", dropped")
        if token.startswith("sched|"):
            _sched_missed(spec["device"], spec["code"], due)
        return
    try:
        if spec["device"] in DEVICES and spec["code"] not in DEVICES[spec["device"]]["_ctl"]:
            log.warning(f"[timer] {token}: channel {spec['code']} is gone, dropped")   # B-3
            return
        _set_switch(spec["device"], spec["code"], bool(spec["value"]))
        if token.startswith(("sched|", "for|")) and spec["value"]:
            # as the scheduler does for a direct ON; a `for` revert to ON is a turn-on too, and
            # the cache set by _set_switch leaves the device's echo no edge to arm on
            _arm_inching(spec["device"], spec["code"])
        log.info(f"[timer] {token} -> {spec['device']}/{spec['code']}="
              f"{spec['value']} ({spec.get('label', '')})")
    except Exception as e:
        # Losing this means an inching auto-off never happens (worst case: a
        # device stays on), so re-arm instead of just logging. Bounded, or
        # a permanently missing device would retry until the process dies.
        tries = int(spec.get("tries") or 0) + 1
        if spec["device"] not in DEVICES and tries > 20:
            log.warning(f"[timer] fire {token} failed: {e} -- the device is gone, giving up after {tries}")
            return
        # a broker outage can last hours: keep trying (15 s, then up to every 10 min), never drop an OFF
        delay = min(600, 15 * (2 ** min(tries - 1, 6)))
        log.warning(f"[timer] fire {token} failed: {e} -- retry {tries} in {delay}s")
        _schedule_deferred(token, delay, spec["device"], spec["code"],
                           bool(spec["value"]), spec.get("label", ""), tries=tries)


OFFLINE_RETRY_S = 15.0
START_RETRY_S = 2.0         # at a start the device becomes known within the grace: look again soon
# how long a shot due while its device is offline waits for it to come back
_OFFLINE_KEEP_S = {"inch": 1800.0, "for": 600.0, "sched": 600.0}
_missed_sched: list = []          # [device, code, due]: a schedule its device was away for (kept a day)
_waiting_shots: set = set()       # tokens whose wait was logged
MISSED_PATH = STATUS_CACHE_PATH.with_name("missed_schedules.json")


def _sched_missed(dev: str, code: str, due: float):
    """A schedule entry its device was away for: shown in Health for a day, kept across a restart
    (the file is written only when one is missed)."""
    day = time.time() - 86400
    _missed_sched[:] = [m for m in _missed_sched if m[2] > day] + [[dev, code, due]]
    try:
        _atomic_json_dump(MISSED_PATH, _missed_sched)
    except OSError as e:
        log.warning(f"[sched] missed list not saved: {e}")


def _load_missed():
    try:
        data = json.loads(MISSED_PATH.read_text()) if MISSED_PATH.exists() else []
        _missed_sched[:] = [m for m in data if isinstance(m, list) and len(m) == 3 and m[2] > time.time() - 86400]
    except (OSError, ValueError) as e:
        log.warning(f"[sched] missed list not read: {e}")


def _cancel_deferred(token: str):
    with _pending_lock:
        h = _pending_handles.pop(token, None)
        existed = _pending.pop(token, None) is not None
        _waiting_shots.discard(token)
    if h:
        h.cancel()
    if existed:
        _pending_save()


def _deferred_active(token: str) -> bool:
    with _pending_lock:
        return token in _pending


def _schedule_deferred(token, secs, device, code, value, label="", *, run_at=None,
                       tries=0, due=None):
    """Arm a one-shot: set device/code=value after `secs`s (or exactly at the
    absolute epoch `run_at`). Replaces any existing timer for the same token.
    due: when it first came due (a shot that waits for its device keeps it)."""
    when = run_at if run_at is not None else (time.time() + max(0, secs))
    spec = {"run_at": when, "device": device, "code": code,
            "value": bool(value), "label": label}
    if tries:
        spec["tries"] = int(tries)
    if due:
        spec["due"] = float(due)
    # replace under one lock: two arms of the same token cannot leave an orphan timer behind
    with _pending_lock:
        old = _pending_handles.pop(token, None)
        _waiting_shots.discard(token)
        t = threading.Timer(max(0.0, when - time.time()), lambda: _fire_deferred(token, t))
        t.daemon = True
        _pending[token] = spec
        _pending_handles[token] = t
    if old:
        old.cancel()
    _pending_save()
    t.start()


def _load_pending():
    """Read pending_timers.json into _pending before the broker connects: an ON that arms a
    timer early then saves the file WITH the other channels' deadlines, not over them. An
    unreadable file is renamed aside (kept for a look), never silently replaced by {}."""
    if not _PENDING_PATH.exists():
        return
    try:
        data = json.load(open(_PENDING_PATH))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except Exception as e:
        aside = _PENDING_PATH.with_name(f"pending_timers.corrupt-{int(time.time())}.json")
        try:
            os.replace(_PENDING_PATH, aside)
        except OSError:
            pass
        log.error(f"[timer] {_PENDING_PATH.name} unreadable ({e}); moved to {aside.name}, timers start empty")
        return
    with _pending_lock:
        for token, spec in data.items():
            if isinstance(spec, dict) and token not in _pending:
                _pending[token] = spec


def _replay_pending_on_boot():
    """Arm the deadlines loaded by _load_pending. Elapsed ones fire immediately; live ones
    re-arm for their remaining time. Tokens armed since then keep their newer timer."""
    with _pending_lock:
        data = {k: dict(v) for k, v in _pending.items() if k not in _pending_handles}
    now = time.time()
    fired = armed = 0
    for token, spec in list(data.items()):
        if not isinstance(spec, dict):
            log.warning(f"[timer] {token}: not a timer in pending_timers.json, dropped")
            continue
        if token.startswith("sched|") and now - float(spec.get("run_at") or 0) > 1800:
            _sched_missed(spec.get("device", "?"), spec.get("code", "?"), float(spec.get("due") or spec.get("run_at") or 0))
            _cancel_deferred(token)
            log.info(f"[timer] {token}: schedule retry from {int((now - float(spec.get('run_at') or 0)) / 60)} min ago dropped")
            data.pop(token)
            continue
        try:
            dev, code = spec["device"], spec["code"]
            val = bool(spec.get("value"))
            run_at = float(spec.get("run_at") or 0)
            label = spec.get("label", "")
        except (KeyError, TypeError, ValueError) as e:
            log.warning(f"[timer] {token}: unreadable in pending_timers.json, dropped: {e!r}")
            continue
        if run_at <= now:
            _fire_deferred(token, _REPLAY)
            fired += 1
        else:
            _schedule_deferred(token, 0, dev, code, val, label, run_at=run_at)
            armed += 1
    if fired or armed:
        log.info(f"[timer] replayed {armed} pending, fired {fired} elapsed")

try:
    from zoneinfo import ZoneInfo
    _LOCAL_TZ = ZoneInfo(os.environ.get("TZ") or "UTC")
except Exception:
    _LOCAL_TZ = None


def _valid_hm(t) -> bool:
    return _norm_hm(t) is not None


def _clean_autos(raw):
    for k in [k for k, a in raw.items() if not isinstance(a, dict)]:
        log.warning(f"[settings] automation {k!r} skipped: not an object")
        raw.pop(k)
    for a in raw.values():
        if not isinstance(a.get("schedule"), list):
            a["schedule"] = []
        good = []
        for e in a["schedule"]:
            days = e.get("days") if isinstance(e, dict) else None
            if (isinstance(e, dict) and _norm_hm(e.get("time")) and e.get("action", "on") in ("on", "off")
                    and (days is None or (isinstance(days, list) and days
                                          and all(isinstance(d, int) and 1 <= d <= 7 for d in days)))):
                good.append(e)
            else:
                log.warning(f"[settings] schedule row {e!r} dropped: needs HH:MM, days 1..7, on|off")
        a["schedule"] = good
    for a in raw.values():                      # older files may hold "7:00": the scheduler wants "07:00"
        for e in a["schedule"]:
            if _norm_hm(e.get("time")):
                e["time"] = _norm_hm(e.get("time"))
    return raw


def _migrate_autos(v, old, new, lost):
    n = 0
    for key in [k for k in v if k.split("/", 1)[0] == old]:
        if new + key[len(old):] in v:
            lost.add(f"a|{new}{key[len(old):]}")
        v.setdefault(new + key[len(old):], v.pop(key))
        n += 1
    return {"automations": n}


_store("automations", "_autos", {}, clean=_clean_autos, migrate=_migrate_autos,
       refs=lambda v: [("automation", k.split("/", 1)[0], k) for k in v])


def _clean_rules_disabled(raw):
    """A disabled "a|dev/code|sched|7:00|on" keeps matching its row once that is "07:00"."""
    out = set()
    for rid in (r for r in raw if isinstance(r, str)):
        p = rid.split("|")
        if len(p) == 5 and p[2] == "sched" and _norm_hm(p[3]):
            p[3] = _norm_hm(p[3])
        out.add("|".join(p))
    return out


def _migrate_rules_disabled(v, old, new, lost):
    n = 0
    for rid in list(v):                         # b|<src>|... and a|<dev>/<code>|...
        parts = rid.split("|")
        if len(parts) > 1 and parts[1].split("/", 1)[0] == old:
            parts[1] = new + parts[1][len(old):]
            v.discard(rid)
            if not _lost_rule("|".join(parts), lost):
                v.add("|".join(parts))
            n += 1
    return {"rules_disabled": n}


# Former ids of a device merged into it (one physical thing, docs/devices.md): {id: [old ids]}; the history reads them.
_aliases: dict = {}


def _migrate_aliases(v, old, new, lost):
    n = 0
    if old in v:
        v[new] = list(dict.fromkeys((v.get(new) or []) + v.pop(old) + [old]))
        n += 1
    return {"aliases": n}


_store("aliases", "_aliases", {}, clean=lambda raw: {str(k): [str(x) for x in v] for k, v in raw.items() if isinstance(v, list)},
       migrate=_migrate_aliases)


def aliases(dev_id: str) -> list:
    """The ids this device had before a merge (its history keeps them)."""
    with _settings_lock:
        return list(_aliases.get(dev_id) or [])


_store("rules_disabled", "_rules_disabled", [], clean=_clean_rules_disabled, save=sorted,
       migrate=_migrate_rules_disabled,
       refs=lambda v: [("disabled-rule", r.split("|")[1].split("/", 1)[0], r) for r in sorted(v) if r.count("|") > 1])


def _inching_sec(dev_id: str, code: str) -> int:
    """Total auto-off seconds for a channel (0 = off). Reads inching_sec; falls
    back to legacy inching_min*60."""
    a = _autos.get(f"{dev_id}/{code}") or {}
    try:
        if a.get("inching_sec") is not None:
            return max(0, int(a.get("inching_sec") or 0))
        return max(0, int(a.get("inching_min") or 0)) * 60
    except (TypeError, ValueError):                 # a hand-edited value that is no number: no auto-off
        return 0


def _cancel_inching(key: str):
    _cancel_deferred(f"inch|{key}")


def _arm_inching(dev_id: str, code: str):
    """Auto-off N seconds after ANY turn-on (manual/scheduled/binding). Re-arms.
    Runs on the persistent deferred registry, so it survives an app restart."""
    key = f"{dev_id}/{code}"
    _cancel_inching(key)
    if not _rule_enabled(f"a|{key}|inch"):
        return
    secs = _inching_sec(dev_id, code)
    if secs <= 0:
        return
    _schedule_deferred(f"inch|{key}", secs, dev_id, code, False, "inching")
    log.info(f"[inching] {key} armed off in {secs}s")


_for_echo: dict = {}       # (device, code) -> until when: the next ON echo is a `for` rule's, not an auto-off's


def _mark_for_echo(b: dict, dev_id: str, code: str):
    """A rule's ON with a `for`, BEFORE it is sent: its echo must not arm the auto-off too."""
    if b.get("for") and b.get("action") == "on":
        _for_echo[(dev_id, code)] = time.time() + CONFIRM_WAIT_S


def _inching_hook(dev_id: str, code: str, on: bool):
    if on and _for_echo.pop((dev_id, code), 0) > time.time():   # the rule's `for` decides when it ends
        return
    if on:
        _arm_inching(dev_id, code)
    else:
        _for_echo.pop((dev_id, code), None)         # an OFF ends the rule's ON: a later ON is someone's own
        _cancel_inching(f"{dev_id}/{code}")


def _rearm_inching_on_boot():
    """Gap-filler after _replay_pending_on_boot(): if a channel is ON at boot but
    has no restored inching timer (e.g. switched on physically while we were
    down), arm a fresh window. Restored timers keep their exact remaining time."""
    for key in list(_autos.keys()):
        if "/" not in key:
            continue
        dev, code = key.split("/", 1)
        if _inching_sec(dev, code) > 0 and not _deferred_active(f"inch|{key}"):
            st = _status_copy(dev, {})
            if (st.get("values") or {}).get(code):
                _arm_inching(dev, code)


def _scheduler_loop():
    """Once a minute, fire the schedule entries that came due since the previous tick.
    One bad entry must not kill the thread: that would silently stop every
    schedule in the house."""
    log.info("[sched] scheduler thread started")
    prev = None
    while True:
        now = datetime.now(_LOCAL_TZ) if _LOCAL_TZ else datetime.now()
        try:
            _scheduler_tick(now, prev)
        except Exception:
            log.exception("[sched] tick failed")
        prev = now
        time.sleep(max(1, 61 - now.second))        # land once per minute


_sched_fired: dict = {}     # (key, time, action) -> date it last fired: once a day, whatever the clock does


def _scheduler_tick(now, prev=None):
    """Due = local time HH:MM in (previous tick, now], at most once per calendar day. The window
    catches what a skipped hour would miss (spring DST: 02:30 does not exist, it fires at 03:00),
    the per-day guard stops a repeated hour from firing twice (autumn DST: 01:30 comes twice)."""
    cur = now.hour * 60 + now.minute
    if prev is None or prev.date() != now.date():
        lo = cur                                # first tick of the day / of the process: exact minute
    else:
        lo = min(cur, prev.hour * 60 + prev.minute + 1)
        lo = max(lo, cur - 90)                  # a stalled thread must not replay the whole day
    today = now.date()
    wd = now.isoweekday()                      # 1=Mon .. 7=Sun
    with _autos_lock:
        items = [(k, list(a.get("schedule") or [])) for k, a in _autos.items()]
    due = []
    for key, sched in items:
        if "/" not in key:
            continue
        for e in sched:
            hm = _norm_hm(e.get("time"))
            if hm is None:
                continue
            m = int(hm[:2]) * 60 + int(hm[3:])
            if lo <= m <= cur:
                due.append((m, key, hm, e))
    # after a jump several rows are due at once: run them in clock order, so the last one wins
    for m, key, hm, e in sorted(due, key=lambda x: x[0]):
        dev, code = key.split("/", 1)
        if wd not in (e.get("days") or [1, 2, 3, 4, 5, 6, 7]):
            continue
        act = e.get("action")
        if _sched_fired.get((key, hm, act)) == today:
            continue
        if not _rule_enabled(f"a|{key}|sched|{hm}|{act}"):
            continue
        _sched_fired[(key, hm, act)] = today
        with _pending_lock:
            waiting = dict(_pending.get(f"sched|{key}") or {})
        if waiting.get("due"):                 # a catch-up still waiting for its device: missed
            _sched_missed(waiting["device"], waiting["code"], float(waiting["due"]))
        _cancel_deferred(f"sched|{key}")   # a newer row supersedes a retry still waiting
        if _is_offline(dev):                       # caught up if it is back within 10 min
            log.warning(f"[sched] {key} {act} @ {hm}: {dev} offline, waits for it")
            _schedule_deferred(f"sched|{key}", OFFLINE_RETRY_S, dev, code, act == "on", "schedule", due=time.time())
            continue
        try:
            if act == "on":
                _set_switch(dev, code, True)
                _arm_inching(dev, code)
            elif act == "off":
                _cancel_inching(key)
                _set_switch(dev, code, False)
            log.info(f"[sched] {key} {act} @ {hm}")
        except Exception as ex:
            # fired for today, but not lost: the deferred registry retries it until it lands
            log.warning(f"[sched] {key} {act} failed: {ex} -- retrying")
            _schedule_deferred(f"sched|{key}", 15, dev, code, act == "on", "schedule retry")


def _conditions_pass(conds) -> bool:
    """AND-evaluate a rule's optional conditions against the latest cached state.
    Each cond: {device, code, op, value}. op in <,>,<=,>=,==,!=,on,off.
    'on'/'off' check truthiness; comparisons coerce to float when possible.
    Missing/offline data -> condition fails (safe default: don't fire)."""
    for c in (conds or []):
        dev, ccode, op = c.get("device"), c.get("code"), c.get("op")
        st = _status_copy(dev, {})
        cur = (st.get("values") or {}).get(ccode)
        if cur is None or not st.get("online"):
            return False                       # unknown is not "off": a dead sensor must not fire rules
        if op == "on":
            if not bool(cur):
                return False
            continue
        if op == "off":
            if bool(cur):
                return False
            continue
        target = c.get("value")
        if target is None:                     # empty value box in the UI
            return False
        try:
            a, b2 = float(cur), float(target)
        except (TypeError, ValueError):
            a, b2 = cur, target
        # Evaluate ONLY the chosen comparison: building the whole table first made
        # any mixed pair (text sensor vs number) raise TypeError, which bubbled out
        # of _execute_binding and left the binding silently dead forever.
        cmp_fn = {"<": operator.lt, ">": operator.gt, "<=": operator.le,
                  ">=": operator.ge, "==": operator.eq, "!=": operator.ne}.get(op)
        if cmp_fn is None:
            return False
        try:
            if not cmp_fn(a, b2):
                return False
        except TypeError:                      # not comparable -> condition fails
            log.warning(f"[cond] {dev}/{ccode} {op} {target!r}: incomparable with {cur!r}")
            return False
    return True


def _in_time_window(tw) -> bool:
    """True if now falls inside the rule's optional time window. Window:
    {from:'HH:MM', to:'HH:MM', days:[1..7]}. Handles overnight ranges
    (from>to, e.g. 22:00->07:00); for those, the morning part (cur<to) is
    attributed to the weekday the window started on (the previous day). from == to: the whole day."""
    if not tw:
        return True
    f, t = _norm_hm(tw.get("from")), _norm_hm(tw.get("to"))
    if not (f and t):
        return True
    days = tw.get("days") or _WEEKDAYS_ALL
    now = datetime.now(_LOCAL_TZ) if _LOCAL_TZ else datetime.now()
    cur = now.strftime("%H:%M")
    wd = now.isoweekday()
    if f == t:                                   # from == to: the whole day
        return wd in days
    if f <= t:                                   # same-day window
        return (f <= cur < t) and wd in days
    if cur >= f:                                 # overnight, evening part
        return wd in days
    if cur < t:                                  # overnight, morning part
        return (wd - 1 or 7) in days
    return False


def _norm_time_window(tw):
    """Validate + normalize a {from,to,days} window for storage, or None.
    Raises HTTPException on malformed input (used by request handlers)."""
    if tw is None:
        return None
    if not isinstance(tw, dict):
        raise HTTPException(400, "time_window must be an object {from, to, days}")
    if not (_valid_hm(tw.get("from")) and _valid_hm(tw.get("to"))):
        raise HTTPException(400, "time_window needs valid from/to (HH:MM)")
    days = tw.get("days") or _WEEKDAYS_ALL
    if not isinstance(days, list) or not all(isinstance(d, int) and 1 <= d <= 7 for d in days):
        raise HTTPException(400, "time_window days must be 1..7")
    return {"from": _norm_hm(tw["from"]), "to": _norm_hm(tw["to"]), "days": days}


def _apply_for_timer(token: str, b: dict, dev_id: str, code: str) -> bool:
    """If the binding action carries a `for` duration, schedule the revert and
    return True (caller then skips device-level inching for this fire). Honors
    `retrigger`: restart (default) re-arms from now; extend keeps the later
    deadline; ignore is handled by the caller before re-applying the action."""
    fs = b.get("for")
    try:
        fs = int(fs or 0)
    except (TypeError, ValueError):
        fs = 0
    if fs <= 0:
        return False
    revert = b.get("action") != "on"            # on->off ; off->on
    pol = (b.get("retrigger") or "restart").lower()
    tk = f"for|{token}"
    if pol == "extend" and _deferred_active(tk):
        with _pending_lock:
            cur_at = (_pending.get(tk) or {}).get("run_at", 0)
        _schedule_deferred(tk, 0, dev_id, code, revert, "for",
                           run_at=max(cur_at, time.time() + fs))
    else:
        _schedule_deferred(tk, fs, dev_id, code, revert, "for")
    return True
