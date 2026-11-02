# The core: bindings and the rules surface: list, enable, delete, run.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

# ---------------------------------------------------------------------------
# Bindings CRUD
# ---------------------------------------------------------------------------
class BindingBody(Body):
    model_config = ConfigDict(extra="allow")   # fields of plug-in actions (BINDING_ACTIONS)
    target: str
    code: Optional[str] = None  # target code
    action: str  # on | off | toggle | bright_up | bright_down
    threshold: Optional[float] = None   # numeric-sensor gestures (below/above)
    hyst: float = 0                     # hysteresis band for numeric thresholds
    conditions: Optional[list] = None   # AND guards {device,code,op,value} checked at fire
    for_sec: Optional[int] = None       # action duration: revert after N s (on<->off)
    retrigger: Optional[str] = None     # restart | extend | ignore (default restart)
    time_window: Optional[dict] = None  # {from:'HH:MM', to:'HH:MM', days:[1..7]}


def _store_binding(src: str, src_code: str, gesture: str, body, entry: dict):
    """What every binding shares, core action or plug-in action: conditions, time window,
    threshold, then the save."""
    # common to every action kind: conditions, time window, sensor threshold
    if body.conditions:
        for c in body.conditions:
            if not isinstance(c, dict):
                raise HTTPException(400, "a condition must be an object {device, code, op, value}")
            op = c.get("op")
            if not (c.get("device") and c.get("code") and op):
                raise HTTPException(400, "each condition needs device, code and op")
            # on/off read truthiness; every other operator needs something to
            # compare against, and an empty value box arrives here as null
            if op not in ("on", "off") and c.get("value") in (None, ""):
                raise HTTPException(400, f"condition '{op}' needs a value")
        entry["conditions"] = body.conditions
    tw = _norm_time_window(body.time_window)
    if tw is not None:
        entry["time_window"] = tw
    if gesture in ("below", "above"):                       # numeric-sensor threshold trigger
        if body.threshold is None or not math.isfinite(body.threshold):
            raise HTTPException(400, "below/above gesture needs a threshold (a finite number)")
        if not (body.hyst or 0) >= 0 or not math.isfinite(body.hyst or 0):   # negative/NaN refires
            raise HTTPException(400, "hyst must be a finite number, 0 or more")
        entry["threshold"] = body.threshold
        entry["hyst"] = body.hyst or 0
        _sensor_arm.pop(f"{src}/{src_code}/{gesture}", None)  # re-seed arm on threshold change
    with _bindings_lock:
        _bindings.setdefault(src, {}).setdefault(src_code, {})[gesture] = entry
        _save_settings()
    return {"ok": True}


@use_route
@app.put("/api/bindings/{src}/{src_code}/{gesture}")
def put_binding(src: str, src_code: str, gesture: str, body: BindingBody, request: Request):
    for part, what in ((src, "source"), (src_code, "code"), (gesture, "gesture")):
        _check_topic_name(part, what)          # | would break the rule id b|src|code|gesture
    if src not in DEVICES:
        raise HTTPException(404, "unknown src device")
    _rule_guard(f"b|{src}|{src_code}|{gesture}", request)      # overwriting such a rule = deleting it
    if body.action in BINDING_ACTIONS:              # a plug-in's action checks its own fields
        if BINDING_ACTIONS[body.action].get("house_token_only"):
            _house_token_only(request)
        entry = BINDING_ACTIONS[body.action]["validate"](body.model_dump())
        if not (isinstance(entry, dict) and entry.get("action") == body.action and isinstance(entry.get("target"), str)):
            raise HTTPException(502, f"plug-in action {body.action}: validate() must return {{action, target, ...}}")
        try:       # stored before the save: a value JSON cannot hold would fail every later save
            json.dumps(entry, allow_nan=False)
        except (TypeError, ValueError):
            raise HTTPException(502, f"plug-in action {body.action}: validate() returned a value that is not JSON")
        return _store_binding(src, src_code, gesture, body, entry)
    if body.target not in DEVICES:
        raise HTTPException(400, "unknown target device")
    if body.action not in ("on", "off", "toggle", "bright_up", "bright_down", "press"):
        raise HTTPException(400, "action must be on|off|toggle|bright_up|bright_down|press")
    if not body.code:
        raise HTTPException(400, "target code required for device action")
    tcfg = DEVICES[body.target]
    tctl = tcfg["_ctl"].get(body.code)
    if not tctl:
        raise HTTPException(400, f"target has no code '{body.code}'")
    if tctl.get("ro") or tctl.get("kind") in ("sensor", "sensor_text", "trigger", "trigger_text"):
        raise HTTPException(400, f"target code '{body.code}' is read-only: a rule cannot set it")
    if body.action.startswith("bright_") and tctl.get("kind") != "bright":
        raise HTTPException(400, "bright_* action needs a bright target code")
    if (body.action == "press") != (tctl.get("kind") in CONTROL_KINDS):
        raise HTTPException(400, "press is the one action of a stateless channel, and only of it")
    entry = {"target": body.target, "code": body.code, "action": body.action}
    if body.for_sec is not None:
        if body.action not in ("on", "off"):
            raise HTTPException(400, "for-duration only applies to on/off actions")
        if body.for_sec <= 0 or body.for_sec > _MAX_DELAY:
            raise HTTPException(400, f"for-duration must be 1 s .. {_MAX_DELAY // 86400} days")
        entry["for"] = int(body.for_sec)
        pol = (body.retrigger or "restart").lower()
        if pol not in ("restart", "extend", "ignore"):
            raise HTTPException(400, "retrigger must be restart|extend|ignore")
        entry["retrigger"] = pol
    return _store_binding(src, src_code, gesture, body, entry)



# ---------------------------------------------------------------------------
# Unified automation rules -- one read/manage surface over bindings + autos, each with a
# stable reversible id (b|<src>|<code>|<gesture>, a|<dev>/<code>|sched|<action>, a|<dev>/<code>|inch;
# docs/rules.md). Creation stays on PUT /api/bindings + POST /api/automation; this adds the list,
# the per-device index and uniform enable/disable/delete.
# ---------------------------------------------------------------------------
_WEEKDAYS_ALL = [1, 2, 3, 4, 5, 6, 7]
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _dname(dev_id: str) -> str:
    return (DEVICES.get(dev_id, {}) or {}).get("name") or BY_ID_NAME.get(dev_id) or dev_id


def _gesture_label(gesture: str) -> str:
    return {"single": "press", "true": "triggered", "false": "cleared",
            "below": "below threshold", "above": "above threshold"}.get(
        gesture, gesture.replace("_", " "))


def _binding_rules():
    out = []
    with _bindings_lock:
        snap = {s: {c: dict(g) for c, g in cs.items()} for s, cs in _bindings.items()}
    for src, codes in snap.items():
        for scode, gestures in codes.items():
            for g, b in gestures.items():
                rid = f"b|{src}|{scode}|{g}"
                tgt = b.get("target")
                when = {"type": "button" if scode == "action" else "sensor",
                        "device": src, "device_name": _dname(src),
                        "code": scode, "gesture": g, "label": _gesture_label(g)}
                if "threshold" in b:
                    when["threshold"] = b["threshold"]
                    when["hyst"] = b.get("hyst", 0)
                if b.get("action") in BINDING_ACTIONS:      # a plug-in's action describes itself
                    try:
                        then = dict(BINDING_ACTIONS[b["action"]]["describe"](b))
                    except Exception as e:
                        log.warning(f"[plugins] describe of {b['action']} failed: {e}")
                        then = {"type": b["action"], "action": b["action"], "title": b["action"]}
                    title = f"{_dname(src)}: {_gesture_label(g)} -> {then.get('title', b['action'])}"
                elif b.get("action") not in _CORE_ACTIONS:
                    # a plug-in's action, but the plug-in is not loaded: keep it recognisable (and
                    # the dashboard keeps it out of the editor), never present it as a device rule
                    then = {"type": "plugin-absent", "action": b.get("action"), "device": tgt,
                            "device_name": tgt, "title": f"{b.get('action')} (plug-in not loaded)"}
                    title = f"{_dname(src)}: {_gesture_label(g)} -> {then['title']}"
                else:
                    then = {"type": "device", "device": tgt, "device_name": _dname(tgt),
                            "code": b.get("code", "state"), "action": b.get("action")}
                    fs = b.get("for")
                    if fs:
                        then["for"] = fs
                    title = (f"{_dname(src)}: {_gesture_label(g)} -> "
                             f"{_dname(tgt)} {b.get('action')}")
                    if fs:
                        title += f" for {fs}s"
                out.append({
                    "id": rid, "kind": "binding", "enabled": _rule_enabled(rid),
                    "when": [when], "conditions": b.get("conditions") or [],
                    "then": [then],
                    "retrigger": b.get("retrigger"),
                    "time_window": b.get("time_window"),
                    "title": title,
                })
    return out


def _auto_rules():
    out = []
    with _autos_lock:
        snap = {k: dict(v) for k, v in _autos.items()}
    for key, a in snap.items():
        if "/" not in key:
            continue
        dev, code = key.split("/", 1)
        # one rule per schedule row (per time) -- discrete, individually managed
        for e in (a.get("schedule") or []):
            t = e.get("time")
            act = e.get("action", "on")
            days = e.get("days") or _WEEKDAYS_ALL
            rid = f"a|{key}|sched|{t}|{act}"
            dlabel = "" if len(days) == 7 else " (" + ",".join(
                _DOW[d - 1] for d in days) + ")"
            out.append({
                "id": rid, "kind": "schedule", "enabled": _rule_enabled(rid),
                "when": [{"type": "time", "times": [{"time": t, "days": days}],
                          "label": t + dlabel}],
                "conditions": [],
                "then": [{"type": "device", "device": dev, "device_name": _dname(dev),
                          "code": code, "action": act}],
                "title": f"{_dname(dev)}: {'on' if act == 'on' else 'off'} at {t}{dlabel}",
            })
        secs = _inching_sec(dev, code)
        if secs > 0:
            rid = f"a|{key}|inch"
            out.append({
                "id": rid, "kind": "inching", "enabled": _rule_enabled(rid),
                "when": [{"type": "state", "device": dev, "device_name": _dname(dev),
                          "code": code, "edge": "on", "label": "turned on"}],
                "conditions": [],
                "then": [{"type": "device", "device": dev, "device_name": _dname(dev),
                          "code": code, "action": "off", "after": secs}],
                "title": f"{_dname(dev)}: auto-off {secs}s after turning on",
            })
    return out


def _project_rules():
    return _binding_rules() + _auto_rules()


def _rule_roles_for_device(rule, dev_id) -> list:
    roles = []
    if any(w.get("device") == dev_id for w in rule["when"]):
        roles.append("trigger")
    if any(c.get("device") == dev_id for c in rule["conditions"]):
        roles.append("condition")
    if any(t.get("device") == dev_id for t in rule["then"]):
        roles.append("action")
    return roles


@app.get("/api/rules")
def get_rules():
    return _project_rules()


@app.get("/api/devices/{dev_id}/rules")
def get_device_rules(dev_id: str):
    """Reverse index: rules referencing this device, with the role(s) it plays."""
    out = []
    for r in _project_rules():
        roles = _rule_roles_for_device(r, dev_id)
        if roles:
            out.append(dict(r, roles=roles))   # the full rule: the dashboard titles it from when/then
    return out


class RuleIdBody(Body):
    id: str
    enabled: Optional[bool] = None


def _cancel_sched_retry(rid: str):
    """A schedule rule turned off or deleted takes its retry still waiting for the broker along
    (the retry of the same key with the other action belongs to another row and stays)."""
    parts = rid.split("|")
    if len(parts) < 4 or parts[0] != "a" or parts[2] != "sched":
        return
    tk = f"sched|{parts[1]}"
    with _pending_lock:
        spec = _pending.get(tk)
    if spec and bool(spec.get("value")) == (parts[-1] == "on"):
        _cancel_deferred(tk)


def _rule_guard(rid: str, request: Request):
    """A rule whose plug-in action is house_token_only is stored, run, switched and deleted by the
    dashboard's own token only (a phone with full access may not)."""
    parts = rid.split("|")
    if parts[0] == "b" and len(parts) == 4:
        b = (_bindings.get(parts[1], {}).get(parts[2], {}) or {}).get(parts[3]) or {}
        if BINDING_ACTIONS.get(b.get("action"), {}).get("house_token_only"):
            _house_token_only(request)


@use_route
@app.post("/api/rules/enable")
def rule_enable(body: RuleIdBody, request: Request):
    rid = body.id
    _rule_guard(rid, request)
    if rid not in {r["id"] for r in _project_rules()}:
        raise HTTPException(404, "no such rule")
    with _settings_lock:
        if body.enabled is False:
            _rules_disabled.add(rid)
        else:
            _rules_disabled.discard(rid)
        _save_settings()
    # disabling an inching rule should drop any armed timer immediately
    if rid.startswith("b|") and body.enabled is False:
        _cancel_deferred(f"for|{rid}")              # a disabled rule's pending revert does not land
    if rid.startswith("a|") and rid.endswith("|inch") and body.enabled is False:
        _cancel_inching(rid.split("|", 1)[1].rsplit("|", 1)[0])
    if body.enabled is False:
        _cancel_sched_retry(rid)
    return {"ok": True, "enabled": rid not in _rules_disabled}


@use_route
@app.post("/api/rules/delete")
def rule_delete(body: RuleIdBody, request: Request):
    rid = body.id
    _rule_guard(rid, request)
    parts = rid.split("|")
    if parts[0] == "b" and len(parts) == 4:
        _, src, scode, g = parts
        with _bindings_lock:
            try:
                del _bindings[src][scode][g]
                if not _bindings[src][scode]:
                    del _bindings[src][scode]
                if not _bindings[src]:
                    del _bindings[src]
            except KeyError:
                raise HTTPException(404, "no such rule")
            _rules_disabled.discard(rid)
            _save_settings()
        _cancel_deferred(f"for|{rid}")              # its "for N min" revert goes with it
        return {"ok": True}
    if parts[0] == "a" and len(parts) >= 3:
        key = parts[1]
        with _autos_lock:
            a = _autos.get(key)
            if not a:
                raise HTTPException(404, "no such rule")
            if parts[2] == "sched":
                if len(parts) >= 5:                  # a|key|sched|<time>|<action>
                    t, act = parts[3], parts[4]
                    a["schedule"] = [e for e in (a.get("schedule") or [])
                                     if not (e.get("time") == t and e.get("action") == act)]
                else:                                 # legacy a|key|sched|<action>
                    act = parts[3] if len(parts) > 3 else None
                    a["schedule"] = [e for e in (a.get("schedule") or [])
                                     if e.get("action") != act]
            elif parts[2] == "inch":
                a.pop("inching_sec", None)
                a.pop("inching_min", None)
                _cancel_inching(key)
            if not (a.get("schedule") or _inching_sec(*key.split("/", 1)) > 0):
                _autos.pop(key, None)
            _rules_disabled.discard(rid)
            _save_settings()
        _cancel_sched_retry(rid)
        return {"ok": True}
    raise HTTPException(400, "bad rule id")


async def _run_then_action(then: dict):
    """Execute one projected `then` action (used by the manual rule-run test)."""
    dev = then.get("device")
    code = then.get("code") or "state"
    action = then.get("action")
    if dev not in DEVICES:
        raise HTTPException(404, f"unknown target device {dev}")
    if action == "toggle":
        cur = (_status_cache.get(dev) or {}).get("values", {}).get(code, False)
        await control(dev, ControlBody(code=code, value=not bool(cur)))
    elif action in ("on", "off"):
        await control(dev, ControlBody(code=code, value=(action == "on")))
    else:
        raise HTTPException(400, f"cannot run action '{action}'")
    return {"target": dev, "code": code, "action": action}


def _nothing_sent_why(b: dict) -> str:
    """Why a core rule's Run sent nothing: its target is gone, its channel missing, or a step on a non-dimmer."""
    tgt, code = b.get("target"), b.get("code")
    dev = DEVICES.get(tgt)
    if not dev:
        return f"{tgt} is gone"
    name = dev.get("name") or tgt
    ctl = (dev.get("_ctl") or {}).get(code)
    if not ctl:
        return f"{name} has no channel {code}"
    return f"{name} {code} is not a dimmer"


@use_route
@app.post("/api/rules/run")
async def rule_run(body: RuleIdBody, request: Request):
    """Manually fire a rule's action(s) to test the wiring -- ignores the rule's
    enabled flag, time window and conditions."""
    rid = body.id
    parts = rid.split("|")
    if parts[0] == "b" and len(parts) == 4:
        _, src, scode, g = parts
        b = (_bindings.get(src, {}).get(scode, {}) or {}).get(g)
        if not b:
            raise HTTPException(404, "no such rule")
        if b.get("action") not in BINDING_ACTIONS and b.get("action") not in _CORE_ACTIONS:
            raise HTTPException(409, f"{b.get('action')}: its plug-in is not loaded")
        _rule_guard(rid, request)
        if b.get("action") not in BINDING_ACTIONS:
            _need_broker()                               # a plug-in action may not need the broker at all
        try:
            res = await run_in_threadpool(_execute_binding, src, scode, g, True)
        except HTTPException:
            raise
        except Exception as e:
            if b.get("action") in BINDING_ACTIONS:
                raise HTTPException(502, f"{b['action']}: {e}")
            raise
        if res is None:
            raise HTTPException(409, f"{_nothing_sent_why(b)}: nothing was sent (relink or edit the rule)")
        if isinstance(res, dict) and res.get("error"):
            who = BY_ID_NAME.get(res.get("target"), res.get("target") or b.get("action"))
            if res["error"] == "offline":
                raise HTTPException(409, f"{who} is {res.get('why') or 'offline'}: nothing was sent")
            raise HTTPException(502, f"{who}: {res['error']}")
        return {"ok": True, **(res if isinstance(res, dict) else {})}
    if parts[0] == "a" and len(parts) >= 3:
        _need_broker()
        rule = next((r for r in _project_rules() if r["id"] == rid), None)
        if not rule:
            raise HTTPException(404, "no such rule")
        results = [await _run_then_action(t) for t in rule.get("then", [])]
        return {"ok": True, "results": results}
    raise HTTPException(400, "bad rule id")
