# The core: running a rule's action.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

def _execute_binding(src_id: str, code: str, gesture: str, force: bool = False):
    tok = _CAUSE.set(f"rule:{src_id}/{code}={gesture}" + (" (test)" if force else ""))
    try:
        return _execute_binding_inner(src_id, code, gesture, force)
    finally:
        _CAUSE.reset(tok)


def _execute_binding_inner(src_id: str, code: str, gesture: str, force: bool = False):
    # force=True: a manual "test/run" from the UI -- fire the action regardless of
    # the rule's enabled flag, time window, conditions or for-window guard.
    with _bindings_lock:
        b = _bindings.get(src_id, {}).get(code, {}).get(gesture)
        if not b:
            return
        b = dict(b)
    rid = f"b|{src_id}|{code}|{gesture}"
    if not force and not _rule_enabled(rid):
        return
    # retrigger=ignore: while the `for` window is still open, swallow new triggers
    if not force and b.get("for") and (b.get("retrigger") or "restart").lower() == "ignore" \
            and _deferred_active(f"for|{rid}"):
        log.info(f"[binding] {src_id}/{code}={gesture} -> ignored (for-window active)")
        return
    if not force and not _in_time_window(b.get("time_window")):
        log.info(f"[binding] {src_id}/{code}={gesture} -> skipped (time window)")
        return
    if not force and not _conditions_pass(b.get("conditions")):
        gone = next((c.get("device") for c in b.get("conditions") or []
                     if not (_status_cache.get(c.get("device")) or {}).get("online")), None)
        if gone:                                         # unknown is not "off": it does not fire
            why = _not_live_word(gone)
            log.info(f"[binding] {src_id}/{code}={gesture} -> skipped: {gone} {why}")
            return {"target": gone, "code": "", "error": "offline", "why": why}
        log.info(f"[binding] {src_id}/{code}={gesture} -> skipped (conditions)")
        return
    if b.get("action") in BINDING_ACTIONS:              # a plug-in's action (its target is its own)
        res = BINDING_ACTIONS[b["action"]]["run"]({**b, "_forced": True} if force else b)
        res = dict(res) if isinstance(res, dict) else {}
        res.setdefault("action", b["action"])                 # the press log shows what fired
        log.info(f"[binding] {src_id}/{code}={gesture} -> {b['action']} {b.get('target')}")
        return res
    target_id = b["target"]
    if _is_offline(target_id):                            # not queued, not retried: it may be back changed
        log.warning(f"[binding] {src_id}/{code}={gesture} -> skipped: {target_id} offline")
        return {"target": target_id, "code": b.get("code"), "error": "offline"}
    # a transport's own way (a relative step the device does itself); None = the generic path
    _tb = TRANSPORT_BINDINGS.get(DEVICES.get(target_id, {}).get("transport"))
    _mark_for_echo(b, target_id, b.get("code"))
    try:
        res = _tb(src_id, code, gesture, b) if _tb else None
    except Exception:
        _for_echo.pop((target_id, b.get("code")), None)   # nothing went out: a manual ON is its own
        raise
    if res is not None:
        res = dict(res) if isinstance(res, dict) else {}
        if res.get("error"):                             # nothing went out: no for-timer, no auto-off
            _for_echo.pop((target_id, b.get("code")), None)
            return res
        zc = res.get("code") or b.get("code")
        if zc and not _apply_for_timer(rid, b, target_id, zc):
            if b.get("action") == "on":
                _arm_inching(target_id, zc)      # inching window (e.g. shredder)
            elif b.get("action") == "off":
                _cancel_inching(f"{target_id}/{zc}")
        return res
    target_code = b["code"]
    action = b["action"]
    if target_id not in DEVICES:
        return
    tcfg = DEVICES[target_id]
    tctl = tcfg["_ctl"].get(target_code)
    if not tctl:
        return
    tctl_kind = tctl.get("kind", "switch")
    if action in ("on", "press"):             # press: a plug-in's stateless channel (CONTROL_KINDS)
        val = True
    elif action == "off":
        val = False
    elif action == "toggle":
        val = not bool(_value_now(target_id, target_code, False))
    elif action in ("bright_up", "bright_down"):
        # dimmer step: +/-10% per event, clamped to 1..100 (off only via a switch binding)
        if tctl.get("kind") != "bright":
            return
        pct = _raw_to_pct(tctl, _value_now(target_id, target_code, way="up" if action == "bright_up" else "down")) or 0
        pct = max(1, min(100, pct + (10 if action == "bright_up" else -10)))
        val = _pct_to_raw(tctl, pct)
    else:
        return
    _mark_for_echo(b, target_id, target_code)
    try:
        _apply_value(target_id, target_code, val, tctl_kind)
    except Exception:
        _for_echo.pop((target_id, target_code), None)       # nothing went out: a manual ON is its own
        raise
    if tctl_kind in CONTROL_KINDS:            # an event: nothing to store or mirror
        _last_control_ts[target_id] = time.time()
        return {"target": target_id, "code": target_code, "action": action}
    _optimistic(target_id, target_code, val)
    _bind_origin[(target_id, target_code)] = {"src": src_id, "code": code, "g": gesture, "ts": time.time()}
    if not _apply_for_timer(rid, b, target_id, target_code):
        # by the value sent, so a toggle that turns on arms too: the echo of the device
        # matches the cache set above, so no state frame sees an edge to arm on
        if action in ("on", "toggle") and val is True:
            _arm_inching(target_id, target_code)  # inching window (e.g. shredder)
        elif action in ("off", "toggle") and val is False:
            _cancel_inching(f"{target_id}/{target_code}")
    log.info(f"[binding] {src_id}/{code}={gesture} -> {target_id}/{target_code}={val}")
    return {"target": target_id, "code": target_code, "value": val}
