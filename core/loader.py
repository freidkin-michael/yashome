# The core: loading the plug-ins, their assets, the public API (PLUGIN_API, __all__).
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

def _load_plugins(pdir: "Path | None" = None, top: str = _PLUGIN_PKG) -> list:
    """Import the enabled <dir>/<name>/ packages (alphabetical; names starting with _ are helpers
    and examples). Without `pdir`: plugins/ and plugins-extra/, only the names in PLUGINS ("*" =
    all). With `pdir` (tests): every package there. A plug-in registers itself through the hooks
    when imported; a failing one is logged and skipped - one broken plug-in must not take the
    house down. Returns the ids loaded."""
    import importlib
    import sys as _sys
    import types
    dirs = [Path(pdir)] if pdir else [d for d in (PLUGINS_DIR, PLUGINS_EXTRA) if d.is_dir()]
    want = None if pdir else _enabled_plugins()
    roots = [r for d in dirs for r in pluginlib.roots(d)]
    found, dup = pluginlib.discover(dirs)
    for q, first in dup:
        _PLUGIN_ERRORS[q.name + "@extra"] = f"{q} has the name of another plug-in ({first}): skipped"
        log.error(f"[plugins] {_PLUGIN_ERRORS[q.name + '@extra']}")
    plan = pluginlib.plan(found, want)             # what loads and why the rest does not, before any import
    for name, why_not in sorted(plan["errors"].items()):
        _PLUGIN_ERRORS[name] = why_not
        log.error(f"[plugins] {name}: {why_not}")
    if not found:
        return []
    by_name, why, reqs = {q.name: q for q in found}, plan["why"], plan["reqs"]
    _PLUGIN_OFF.extend(sorted(n for n in by_name if n not in why))
    _sys.modules.setdefault("app", _sys.modules[__name__])   # plug-ins do `import app as core`
    pkg = types.ModuleType(top)
    pkg.__path__ = [str(r) for r in roots]
    _sys.modules[top] = pkg
    loaded = []
    for name in plan["on"]:
        q = by_name[name]
        failed = [dep for dep in reqs[name] if dep not in loaded]
        if failed:
            _PLUGIN_ERRORS[name] = "requires " + ", ".join(f"{dep} (failed to load)" for dep in failed)
            log.error(f"[plugins] {name}: {_PLUGIN_ERRORS[name]}")
            continue
        snap = _hooks_snapshot()
        try:
            mod = importlib.import_module(f"{top}.{name}")
            meta = getattr(mod, "PLUGIN", {}) or {}
            if not isinstance(meta, dict):
                raise TypeError("PLUGIN must be a dict like {'name': ..., 'version': ...}")
            _hooks_check(name, snap)
            i18n = {}
            if (q / "i18n.json").is_file():
                i18n = json.loads((q / "i18n.json").read_text(encoding="utf-8"))
                if not isinstance(i18n, dict) or not all(isinstance(v, dict) for v in i18n.values()):
                    raise ValueError("i18n.json must be {lang: {text: translation}}")
        except BaseException as e:              # noqa: BLE001 - SystemExit in a plug-in must not stop us
            if isinstance(e, KeyboardInterrupt):
                raise
            _hooks_restore(snap)                 # what it registered before failing is taken back
            _sys.modules.pop(f"{top}.{name}", None)
            _PLUGIN_ERRORS[name] = f"{type(e).__name__}: {e}"
            log.error(f"[plugins] {name} failed to load: {_PLUGIN_ERRORS[name]}")
            continue
        _PLUGIN_ERRORS.pop(name, None)
        for k in set(SETTINGS_SECTIONS) - set(snap["dicts"]["SETTINGS_SECTIONS"]):
            prev = _SECTION_FILE.get(k)
            if prev and prev != name:
                _SECTION_MOVED.setdefault(prev, set()).add(k)
                log.warning(f"[settings] section {k!r} moves from plug-in {prev} (not loaded) to {name}")
            _SECTION_FILE[k] = name
        PLUGINS[:] = [m for m in PLUGINS if m["id"] != name]
        PLUGINS.append({"id": name, "name": str(meta.get("name") or name), "version": meta.get("version"),
                        "ui": f"/plugins/{name}/ui.js" if (q / "ui.js").is_file() else None,
                        "i18n": i18n,
                        "group": None if q.parent in dirs else q.parent.name, "requires": reqs[name],
                        "icon": str(meta.get("icon") or ""), "menu_group": str(meta.get("menu_group") or ""), "menu": meta.get("menu") is not False,
                        "required_by": why.get(name)})
        PLUGIN_PATHS[name] = q
        loaded.append(name)
        log.info(f"[plugins] {name} loaded")
    return loaded


_I18N_SEEN: dict = {}        # plug-in -> (mtime of its i18n.json, the dictionary read then)


def _plugin_i18n(m: dict) -> dict:
    """A plug-in's dictionary as its i18n.json is now (read again when the file changed); a file that does not read keeps the last good one."""
    f = PLUGIN_PATHS.get(m["id"], PLUGINS_DIR / m["id"]) / "i18n.json"
    try:
        mt = f.stat().st_mtime_ns
    except OSError:
        return m.get("i18n") or {}
    seen = _I18N_SEEN.get(m["id"])
    if seen and seen[0] == mt:
        return seen[1]
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or not all(isinstance(v, dict) for v in d.values()):
            raise ValueError("i18n.json must be {lang: {text: translation}}")
    except (OSError, ValueError) as e:
        log.warning(f"[plugins] {m['id']}: i18n.json not read again ({e}): the last good one stays")
        return seen[1] if seen else (m.get("i18n") or {})
    _I18N_SEEN[m["id"]] = (mt, d)
    return d


@app.get("/api/plugins")
def api_plugins():
    """What the dashboard loads before its first render (scripts, dictionaries) plus the
    plug-ins that failed, for the Settings screen."""
    def ui_v(m):                                     # script and dictionary as they are now: a file changed after the start must not keep its old one
        f = PLUGIN_PATHS.get(m["id"], PLUGINS_DIR / m["id"]) / "ui.js"
        m = {**m, "i18n": _plugin_i18n(m)}
        return {**m, "ui_v": int(f.stat().st_mtime)} if m.get("ui") and f.is_file() else m
    if RESTRICTED:                                    # the public demo tells no paths and no error texts
        return {"loaded": [ui_v(m) for m in PLUGINS], "failed": [{"id": k} for k in sorted(_PLUGIN_ERRORS)], "off": [], "dirs": []}
    return {"loaded": [ui_v(m) for m in PLUGINS], "failed": [{"id": k, "error": v} for k, v in sorted(_PLUGIN_ERRORS.items())],
            "off": sorted(_PLUGIN_OFF), "dirs": [str(PLUGINS_DIR), str(PLUGINS_EXTRA)]}


def plugin_asset(name: str, path: str):
    """Public files of a loaded plug-in: ui.js, i18n.json and anything in its static/ folder.
    Nothing else of the package is served (no source, no config). One answer for every miss,
    so the route does not tell which plug-ins exist."""
    miss = HTTPException(404, "not found")
    if not _OPEN_ASSET.match(f"/plugins/{name}/{path}") or not any(m["id"] == name for m in PLUGINS):
        raise miss
    base = PLUGIN_PATHS.get(name, PLUGINS_DIR / name).resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file() or not target.name.lower().endswith(_PLUGIN_ASSETS):
        raise miss                                   # symlinks out of the package, directories
    return FileResponse(target, headers={"Cache-Control": "no-cache"})


# The plug-in API (docs/plugins.md): what a plug-in may use besides the documented hooks and helpers.
# Each name resolves at access time to the core's own object, so a value the core rebinds later (the
# broker client, the rules) and a test's monkeypatch of the private name both hold.
PLUGIN_API = {
    "status_copy": "_status_copy",
    "mqtt_client": "_mqtt_client",
    "house_token_only": "_house_token_only",
    "wait_registry": "_wait_registry",
    "store_status": "_store_status",
    "paho": "_paho",
    "mqtt_pub": "_mqtt_pub",
    "atomic_json_dump": "_atomic_json_dump",
    "cfg_section": "_cfg_section",
    "store_status_exact": "_store_status_exact",
    "save_settings": "_save_settings",
    "status_cache": "_status_cache",
    "mqtt_auth": "_mqtt_auth",
    "main_loop": "_MAIN_LOOP",
    "events_lock": "_events_lock",
    "events": "_events",
    "safe_execute_binding": "_safe_execute_binding",
    "check_topic_name": "_check_topic_name",
    "bindings_lock": "_bindings_lock",
    "bindings": "_bindings",
    "same_origin": "_same_origin",
    "inching_hook": "_inching_hook",
    "fire_sensor_triggers": "_fire_sensor_triggers",
    "chan_of": "_chan_of",
    "cache_lock": "_cache_lock",
    "bg": "_bg",
    "settings_lock": "_settings_lock",
    "settings_bad": "_settings_bad",
    "safe_controls": "_safe_controls",
    "retained_values": "_retained_values",
    "retained_topics": "_retained_topics",
    "register_mqtt_device": "_register_mqtt_device",
    "mqtt_resubscribe_generic": "_mqtt_resubscribe_generic",
    "mqtt_publish_state": "_mqtt_publish_state",
    "mqtt_devices_upsert": "_mqtt_devices_upsert",
    "load_failed": "_load_failed",
    "json_state": "_json_state",
    "find_room": "_find_room",
    "favorites": "_favorites",
    "device_owner": "_DEVICE_OWNER",
    "deferred_active": "_deferred_active",
    "sensor_last": "_sensor_last",
    "sensor_since": "_sensor_since",
    "bind_origin": "_bind_origin",
    "is_offline": "_is_offline",
    "rule_enabled": "_rule_enabled",
    "rules_disabled": "_rules_disabled",
    "orphan_refs": "_orphan_refs",
    "registry_known": "_registry_known",
    "plugins": "PLUGINS",
    "quiet_device": "_quiet_device",
    "emit_event": "_emit",
    "sent_commands": "_sent_log",
    "data_ref_kinds": "DATA_REF_KINDS",
    "prune_data_refs": "_prune_data_refs",
    "prune_state_mirror": "_prune_orphan_state_mirror",
    "cancel_deferred": "_cancel_deferred",
    "autos": "_autos",
    "csp": "_CSP",
    "helper": "_helper",
}


__all__ = sorted(PLUGIN_API) + ["app", "log", "DEVICES", "get_status", "get_rooms", "served_room", "served_devices", "room_reading", "room_sensor_order", "rooms_edit", "set_room_locked", "ROOM_LOCKED", "ROOM_ZONED", "ROOM_HIDDEN", "register_device", "update_device",
                                "remove_device", "set_device_name", "check_name", "take_name_override", "own_names", "house_lang", "health_row", "plugin_data", "plugin_state", "plugin_state_file",
                                "open_route", "use_route", "is_use_route", "request_token", "Body", "OPEN_PATHS", "is_house_token", "is_admin_request", "migrate_device_key", "mqtt_command",
                                "RESTRICTED", "RESTRICTED_REFUSED", "RESTRICTED_BODY_MAX", "RESTRICTED_WS_MAX", "restricted_role",
                                "node_broker_host", "node_broker_port", "registry_pending", "registry_ready", "VIRTUAL_PREFIXES", "forgotten",
                                "extend_device", "drop_part", "aliases", "merged", "merge_plan", "PARTS",
                                "control", "ControlBody", "MQTT_HOST", "MQTT_PORT", "MQTT_NAMESPACES", "CHECKOUT",
                                "wiring", "wiring_put", "wiring_edit", "wiring_postponed", "wiring_owns", "WiringBroken", "wake_on_lan", "hostsock",
                                "PLUGIN_API"] + list(_HOOK_LISTS) + list(_HOOK_DICTS)
