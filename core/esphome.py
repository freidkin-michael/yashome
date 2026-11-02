# The core: ESPHome discovery and the nodes' broker block.
# A part of app.py, run by it in its own namespace in the order of app.CORE_PARTS: the names of the
# other parts and the imports of app.py are at hand here as they were in one file.

def _ha_key(cfg, *names):
    """HA discovery abbreviates keys (stat_t) but the long form (state_topic) is
    equally legal -- accept either."""
    for n in names:
        v = cfg.get(n)
        if v not in (None, ""):
            return v
    return None


def _esphome_control_from_ha(component, obj, cfg):
    """Map one HA-discovery entry onto our control schema. Returns [] for
    components the dashboard has no widget for (button/select/climate/text)."""
    stat = _ha_key(cfg, "stat_t", "state_topic")
    cmd = _ha_key(cfg, "cmd_t", "command_topic")
    label = _ha_key(cfg, "name") or obj
    if component in ("switch", "light") and cmd:
        out = [{"kind": "switch", "code": obj, "label": label,
                "state_topic": stat, "command_topic": cmd,
                "payload_on": _ha_key(cfg, "pl_on", "payload_on") or "ON",
                "payload_off": _ha_key(cfg, "pl_off", "payload_off") or "OFF"}]
        bcmd = _ha_key(cfg, "bri_cmd_t", "brightness_command_topic")
        if bcmd:
            out.append({"kind": "bright", "code": f"{obj}_bright",
                        "label": f"{label} -- brightness", "min": 0,
                        "max": int(float(_ha_key(cfg, "bri_scl",
                                                 "brightness_scale") or 255)),
                        "state_topic": _ha_key(cfg, "bri_stat_t",
                                               "brightness_state_topic"),
                        "command_topic": bcmd})
        return out
    if component == "number" and cmd:
        try:
            lo, hi = int(float(cfg.get("min", 0))), int(float(cfg.get("max", 100)))
        except (TypeError, ValueError):
            lo, hi = 0, 100
        return [{"kind": "bright", "code": obj, "label": label,
                 "min": lo, "max": hi,
                 "state_topic": stat, "command_topic": cmd}]
    if component == "binary_sensor" and stat:
        return [{"kind": "sensor", "code": obj, "label": label, "ro": True,
                 "vtype": "bool", "scale": 1, "state_topic": stat,
                 "payload_on": _ha_key(cfg, "pl_on", "payload_on") or "ON"}]
    if component == "sensor" and stat:
        return [{"kind": "sensor", "code": obj, "label": label, "ro": True,
                 "unit": _ha_key(cfg, "unit_of_meas", "unit_of_measurement") or "",
                 "scale": 1, "vtype": "num", "state_topic": stat}]
    return []


def _discovery_ignored(names: set) -> bool:
    """A plug-in knows the node (it has a description of its own, or it is paired): the names are the node's, and
    the first topic levels of its channels. A hook that fails never hides a node."""
    for fn in list(DISCOVERY_IGNORE):
        try:
            if fn(names):
                return True
        except Exception as e:                      # noqa: BLE001
            log.warning(f"[esphome] discovery-ignore {getattr(fn, '__name__', fn)}: {e}")
    return False


def _discovered() -> dict:
    """The nodes the add dialog may offer: the discovered ones except those a plug-in owns (a description or a pairing)."""
    out = {}
    for node, found in _esphome_discover().items():
        topics = [c.get(k) for c in found.get("controls", []) for k in ("state_topic", "command_topic")] + [found.get("availability_topic")]
        names = {node} | {t.split("/", 1)[0] for t in topics if isinstance(t, str) and "/" in t}
        if not _discovery_ignored(names):
            out[node] = found
    return out


def _esphome_discover(timeout: float = 6.0, ignore: bool = True) -> dict:
    """Collect the retained HA-discovery configs, grouped per ESPHome node. Uses a
    separate short-lived client, same pattern as the mirror prune."""
    if _paho is None:
        return {}
    import queue as _queue
    box = _queue.Queue()
    cli = _paho.Client(_paho.CallbackAPIVersion.VERSION2,
                       client_id=f"home-esphome-disco-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: box.put((m.topic, m.payload))
    nodes: dict = {}
    skip: set = set()
    try:
        _mqtt_auth(cli).connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        cli.subscribe(_HA_DISCOVERY_WILDCARD)
        cli.loop_start()
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                break
            try:
                topic, payload = box.get(timeout=min(1.5, left))
            except _queue.Empty:
                if box.empty():
                    break
                continue
            parts = topic.split("/")
            if len(parts) != 5:
                continue
            component, node, obj = parts[1], parts[2], parts[3]
            if ignore and node not in skip:
                if _discovery_ignored({node}):
                    skip.add(node)
            if ignore and node in skip:
                continue          # a node with a description: its configs are not even parsed
            try:
                cfg = json.loads(payload.decode("utf-8", "replace"))
            except ValueError:                    # not a discovery config: nothing to offer
                continue
            if not isinstance(cfg, dict) or not cfg:
                continue          # empty payload = discovery entry being deleted
            n = nodes.setdefault(node, {"controls": [], "skipped": [],
                                        "availability_topic": None,
                                        # every config topic of this node, so a leftover
                                        # can be cleared without guessing the shape
                                        "config_topics": [],
                                        "model": None, "title": None})
            if topic not in n["config_topics"]:
                n["config_topics"].append(topic)
            n["availability_topic"] = (n["availability_topic"]
                                       or _ha_key(cfg, "avty_t", "availability_topic"))
            dev = cfg.get("dev") or cfg.get("device") or {}
            n["model"] = n["model"] or dev.get("mdl") or dev.get("model")
            n["title"] = n["title"] or dev.get("name")
            try:
                ctrls = _esphome_control_from_ha(component, obj, cfg)
            except Exception as e:                # noqa: BLE001 - someone else's retained JSON
                log.warning(f"[esphome] {topic}: unreadable config skipped ({e})")
                ctrls = []
            if ctrls:
                n["controls"].extend(ctrls)
            else:
                n["skipped"].append(f"{component}/{obj}")
    except Exception as e:
        log.warning(f"[esphome] discovery failed: {e}")
    finally:
        try:
            cli.loop_stop(); cli.disconnect()
        except Exception:                         # noqa: BLE001  closing a one-off client: what it says no longer matters
            pass
    return nodes


def _mqtt_node_registered(node: str, found: dict | None = None):
    """Is this ESPHome node already a dashboard device? Matches the explicit esphome_node marker,
    the id, a topic under <node>/ and, from the node's discovery (`found`), any topic it publishes:
    its topic prefix may differ from its name (a node named room-ac may publish under room_ac/)."""
    pref = node + "/"
    topics = set()
    if found:
        for c in found.get("controls", []):
            topics.update(str(c.get(k) or "") for k in ("state_topic", "command_topic"))
        topics.add(str(found.get("availability_topic") or ""))
        topics.discard("")
    for d in list(DEVICES.values()):
        if d.get("transport", "mqtt") != "mqtt":
            continue
        if d.get("esphome_node") == node or d.get("id") == node:
            return d
        mine = [str(d.get("availability_topic") or "")]
        for c in d.get("controls", []):
            mine += [str(c.get(k) or "") for k in ("state_topic", "command_topic")]
        if any(t.startswith(pref) or t in topics for t in mine if t):
            return d
    return None


def _mqtt_devices_read() -> list:
    """mqtt_devices.json as a list; entries that are not objects are kept as they are (the
    start skips them with a warning), never a reason for a 500 or to be dropped."""
    if not MQTT_DEVICES_PATH.exists():
        return []
    try:
        cur = json.load(open(MQTT_DEVICES_PATH)) or []
    except Exception as e:
        raise HTTPException(500, f"mqtt_devices.json unreadable: {e}")
    if not isinstance(cur, list):
        raise HTTPException(500, "mqtt_devices.json must be a list of devices")
    return cur


def _mqtt_devices_upsert(entry: dict):
    """Persist a generic-MQTT device into mqtt_devices.json (replacing same id).
    Written via a temp file so a crash mid-write cannot truncate the registry."""
    with _mqtt_devices_lock:
        cur = _mqtt_devices_read()
        cur = [e for e in cur if not (isinstance(e, dict) and e.get("id") == entry["id"])]
        cur.append({k: v for k, v in entry.items() if not k.startswith("_")})
        _atomic_json_dump(MQTT_DEVICES_PATH, cur, ensure_ascii=False, indent=2)


def _register_mqtt_device(entry: dict) -> dict:
    if any(_own_topic(entry.get(t)) for t in ("state_topic", "command_topic", "availability_topic")):
        raise HTTPException(400, "a device topic under home/state/ or home/cmd/ is the backend's own bus")
    if entry["id"] not in DEVICES and any(p["id"] == entry["id"] for p in PLUGINS):
        raise HTTPException(409, f"id {entry['id']!r} is a plug-in's name (it owns home/{entry['id']}/): pick another")
    d = dict(entry)
    d["transport"] = "mqtt"
    d["controls"] = _safe_controls(d["id"], d.get("controls"))
    _mark_dimmers(d)
    _bad_request_warned.discard(d["id"])
    _state_request(d)                    # a bad state_request is named now, not at its first wait
    for _c in d["controls"]:
        _c.setdefault("_label0", _c.get("label", ""))
    _apply_channel_names(d)
    d["_ctl"] = {c["code"]: c for c in d["controls"]}
    _apply_parts(d)
    before = set((DEVICES.get(d["id"]) or {}).get("_ctl") or {})
    DEVICES[d["id"]] = d
    BY_ID_NAME[d["id"]] = d["name"]
    _forget_codes(d["id"], before)
    prev = _status_copy(d["id"], {"values": {}, "raw": {}})
    prev.setdefault("online", False)
    _store_status(d["id"], prev)
    return d


def _mqtt_resubscribe_generic() -> int:
    """Rebuild the exact-topic routing map and subscribe to whatever is new. The
    map is normally built once in _mqtt_on_connect; adding a device from the UI
    must not require a container restart."""
    global _MQTT_TOPIC_IDX
    old = set(_MQTT_TOPIC_IDX)
    _MQTT_TOPIC_IDX = _mqtt_topic_index()
    if _mqtt_client is None:
        return 0
    fresh = [t for t in _MQTT_TOPIC_IDX if t not in old]
    for t in fresh:
        _mqtt_client.subscribe(t)
    return len(fresh)


def _retained_topics(filters, listen: float = 2.5) -> list:
    """The topics under the filters (wildcards allowed) that hold a retained value: a short subscription collects
    what the broker replays. Empty when there is no broker."""
    if _paho is None:
        return []
    found: set = set()
    cli = _paho.Client(_paho.CallbackAPIVersion.VERSION2, client_id=f"home-retained-scan-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: found.add(m.topic) if m.retain and m.payload else None
    try:
        _mqtt_auth(cli).connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        for flt in filters:
            cli.subscribe(flt, qos=1)
        cli.loop_start()
        time.sleep(listen)
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[mqtt] retained scan {list(filters)}: {e}")
    finally:
        try:
            cli.loop_stop()
            cli.disconnect()
        except Exception:                         # noqa: BLE001
            pass
    return sorted(found)


def _retained_values(topics: list, timeout: float = 4.0) -> dict:
    """Read the retained value of each topic, or None where nothing is retained.
    Used to tell a real node from a leftover: an ESPHome node publishes its LWT
    ("online"/"offline") retained, so NO retained value at all means the node is gone
    and only its discovery configs are still sitting on the broker."""
    if _paho is None or not topics:
        return {}
    import queue as _queue
    box = _queue.Queue()
    cli = _paho.Client(_paho.CallbackAPIVersion.VERSION2, client_id=f"home-lwt-probe-{secrets.token_hex(3)}")
    cli.on_message = lambda c, u, m: box.put((m.topic, m.payload.decode("utf-8", "replace")))
    out: dict = {}
    try:
        _mqtt_auth(cli).connect(MQTT_HOST, MQTT_PORT, keepalive=10)
        for t in set(topics):
            cli.subscribe(t)
        cli.loop_start()
        deadline = time.time() + timeout
        while time.time() < deadline and len(out) < len(set(topics)):
            try:
                t, v = box.get(timeout=max(0.1, deadline - time.time()))
            except _queue.Empty:
                break
            out[t] = v.strip()
    except Exception as e:                        # noqa: BLE001
        log.warning(f"[esphome] lwt probe failed: {e}")
    finally:
        try:
            cli.loop_stop()
            cli.disconnect()
        except Exception:                         # noqa: BLE001
            pass
    return out


def node_broker_port() -> int:
    """The broker's port as nodes on the network reach it (the host port, MQTT_PORT of .env)."""
    raw = str(os.environ.get("MQTT_HOST_PORT") or "1883").rsplit(":", 1)[-1]   # compose allows host:port
    try:
        return int(raw)
    except ValueError:
        log.warning(f"[mqtt] MQTT_PORT {raw!r} is no port; nodes are told 1883")
        return 1883


def node_broker_host(request: Request):
    """(host, warning) nodes reach the broker at: BIND_ADDRESS (the house's LAN address), else the address the dashboard
    was opened at - useless when that is this machine's loopback, so a placeholder and a warning then."""
    set_ = (os.environ.get("BIND_ADDRESS") or "").strip()
    set_ = "" if set_ in ("0.0.0.0", "::") else set_
    host = (set_ or request.url.hostname or "").rstrip(".")
    try:
        a = ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host))) if host.replace(".", "").isdigit() \
            else ipaddress.ip_address(host)
        literal, loop = True, a.is_loopback or a.is_unspecified
    except (OSError, ValueError):
        literal, loop = False, host == "localhost" or host.endswith(".localhost")
    if loop or not host:
        return "<the house's LAN address>", "set BIND_ADDRESS in .env to the house's LAN address"
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    if not set_ and (https or not literal):
        return host, "a name or an HTTPS address may not reach the broker (a proxy does not pass MQTT): " \
                     "set BIND_ADDRESS in .env to the house's LAN address"
    return host, ""


def _device_accounts() -> dict:
    """MQTT_DEVICE_USERS -> {name: password}: the broker accounts meant for the house's own nodes."""
    out = {}
    for name in [x.strip() for x in os.environ.get("MQTT_DEVICE_USERS", "").split(",") if x.strip()]:
        pw = os.environ.get(f"MQTT_{re.sub('[^A-Z0-9]', '_', name.upper())}_PASSWORD", "")
        if pw:
            out[name] = pw
    return out


@app.get("/api/esphome/mqtt_block")
def esphome_mqtt_block(request: Request, account: str = ""):
    """The mqtt: block for an ESPHome node's yaml, with one of the device accounts, and its secrets.yaml
    line; the password is shown, so the house token only."""
    _house_token_only(request)
    accounts = _device_accounts()
    if not accounts:
        return {"accounts": [], "yaml": "", "secret": ""}
    name = account if account in accounts else sorted(accounts)[0]
    host, warning = node_broker_host(request)
    yaml_text = (f"mqtt:\n  broker: {host}\n  port: {node_broker_port()}\n  username: {name}\n"
                 f"  password: !secret mqtt_password\n  discovery: true\n  discovery_retain: true\n")
    secret = f"mqtt_password: {json.dumps(accounts[name])}\n"
    if os.environ.get("PANELS_AP_PASSWORD"):   # the house's shared setup-AP key (wifi: ap: password)
        secret += f"ap_password: {json.dumps(os.environ['PANELS_AP_PASSWORD'])}\n"
    return JSONResponse({"accounts": sorted(accounts), "account": name, "yaml": yaml_text, "warning": warning,
                         "secret": secret}, headers={"Cache-Control": "no-store"})


@app.get("/api/esphome/discover")
def esphome_discover():
    _need_broker()
    nodes = _discovered()
    lwt = _retained_values([nodes[n].get("availability_topic") for n in nodes
                            if nodes[n].get("availability_topic")])
    out = []
    for node in sorted(nodes):
        n = nodes[node]
        reg = _mqtt_node_registered(node, n)
        avail = n.get("availability_topic")
        out.append({
            "node": node, "title": n.get("title") or node, "model": n.get("model"),
            "availability_topic": avail,
            # None = neither online nor offline was ever published -> the node itself is
            # gone and these configs are a leftover on the broker.
            "alive": lwt.get(avail) if avail else None,
            "controls": n["controls"], "n_controls": len(n["controls"]),
            "skipped": n["skipped"],
            "registered": bool(reg), "device_id": (reg or {}).get("id"),
        })
    return {"nodes": out}


class EsphomePurge(Body):
    node: str
    # A live node that has since been built with discovery: false keeps its OLD configs
    # on the broker forever -- nothing will ever refresh or delete them. That case needs
    # an explicit decision, hence a flag rather than a silent purge.
    force: bool = False


@app.post("/api/esphome/purge_discovery")
def esphome_purge_discovery(body: EsphomePurge):
    """Clear the retained discovery configs of a node that no longer exists.
    Retained topics live forever, so a renamed or dismantled node keeps offering itself
    in the add dialog until someone publishes an empty payload over each config."""
    node = body.node.strip()
    if not node:
        raise HTTPException(400, "node not given")
    if _mqtt_node_registered(node) and not body.force:
        raise HTTPException(400, f"'{node}' is on the dashboard - remove its card first "
                                 f"(or force, if the configs are stale but the node is alive)")
    nodes = _esphome_discover(ignore=False)
    if node not in nodes:
        raise HTTPException(404, f"'{node}' has no retained configs")
    avail = nodes[node].get("availability_topic")
    if avail and not body.force and (_retained_values([avail]).get(avail) or "").strip():
        raise HTTPException(400, f"'{node}' still publishes its status - it is a live node "
                                 f"(force, if the configs are stale)")
    topics = nodes[node].get("config_topics") or []
    if _mqtt_client is None:
        raise HTTPException(503, "MQTT unavailable")
    for t in topics:
        _mqtt_pub(_mqtt_client, t, None, retain=True)   # empty retained = delete
    log.info(f"[esphome] purged {len(topics)} retained discovery topics of {node}")
    return {"ok": True, "node": node, "purged": len(topics)}


_BACKEND_ONLY = [f for f in re.split(r"[\s,]+", os.environ.get("MQTT_BACKEND_ONLY", "")) if f]


def _filter_hit(flt: str, t: str) -> bool:
    """MQTT filter match (+ one level, # the rest)."""
    fl, tl = flt.split("/"), t.split("/")
    for i, x in enumerate(fl):
        if x == "#":
            return True
        if i >= len(tl) or (x != "+" and x != tl[i]):
            return False
    return len(fl) == len(tl)


def _node_topic_ok(t) -> bool:
    """A topic taken from discovery: none, or a plain topic outside the core's bus, outside a
    plug-in's namespace (MQTT_NAMESPACES) and outside the filters only the backend may publish
    to (MQTT_BACKEND_ONLY)."""
    if t is None:
        return True
    return (isinstance(t, str) and 0 < len(t) <= 256 and t.isprintable() and "+" not in t
            and "#" not in t and not t.startswith("$") and t.split("/", 1)[0] not in MQTT_NAMESPACES
            and not _own_topic(t)
            and not any(_filter_hit(f, t) for f in _BACKEND_ONLY))


class EsphomeAdd(Body):
    node: str
    id: str = ""
    name: str = ""
    category: str = "switch"
    codes: list = []          # subset of the discovered codes; empty = take all
    # A node that is not answering right now is usually a retired one whose retained
    # configs still sit on the broker; onboarding it yields a card that can never work.
    force: bool = False


@app.post("/api/esphome/add")
def esphome_add(body: EsphomeAdd):
    _need_broker()
    nodes = _discovered()
    n = nodes.get(body.node)
    if not n:
        raise HTTPException(404, f"ESPHome node '{body.node}' not in MQTT discovery "
                                 f"-- check that its yaml has an mqtt: block")
    avail = n.get("availability_topic")
    alive = (_retained_values([avail]).get(avail) or "").strip() if avail else ""
    if alive != "online" and not body.force:
        raise HTTPException(409, f"'{body.node}' is offline - nothing to add "
                                 f"({alive or 'the node never published a status'}). "
                                 f"Power the node on and retry, clean its leftovers off the broker, "
                                 f"or add it with force.")
    if not _node_topic_ok(avail):
        raise HTTPException(400, f"availability topic {str(avail)[:80]!r} is not one a node may own")
    # the discovery configs are anyone's retained JSON: a channel whose topic points at the core's
    # bus (home/state|cmd/, $..., a plug-in's namespace) or is a wildcard is dropped, never published to
    controls = [c for c in n["controls"]
                if _node_topic_ok(c.get("state_topic")) and _node_topic_ok(c.get("command_topic"))]
    if body.codes:
        keep = set(body.codes)
        controls = [c for c in controls if c["code"] in keep]
    if not controls:
        raise HTTPException(400, "no usable controls selected")
    did = (body.id or body.node).strip()
    if not _CODE_RE.match(did):
        raise HTTPException(400, "id: 1-64 letters, digits, _ . - (it becomes part of MQTT topics)")
    if not _CAT_RE.match(body.category or "switch"):
        raise HTTPException(400, "category: 1-32 lowercase letters or _")
    prev = _mqtt_node_registered(body.node, n)
    if prev is not None and prev.get("id") != did:
        raise HTTPException(409, f"'{body.node}' is already in the house as {prev.get('name') or prev.get('id')}")
    if did in DEVICES and prev is None:
        raise HTTPException(409, f"id '{did}' is already taken by another device")
    entry = {"id": did, "name": (body.name or n.get("title") or did).strip(),
             "transport": "mqtt", "category": body.category or "switch",
             "esphome_node": body.node, "model": n.get("model"),
             "controls": controls}
    if n.get("availability_topic"):
        entry["availability_topic"] = n["availability_topic"]
        entry["payload_online"] = "online"
        entry["payload_offline"] = "offline"
    if any(_own_topic(entry.get(t)) for t in ("state_topic", "command_topic", "availability_topic")):
        raise HTTPException(400, "a device topic under home/state/ or home/cmd/ is the backend's own bus")
    _mqtt_devices_upsert(entry)
    _register_mqtt_device(entry)
    subscribed = _mqtt_resubscribe_generic()
    return {"id": did, "name": entry["name"], "category": entry["category"],
            "controls": [c["code"] for c in controls], "subscribed": subscribed}


class MqttDeviceRemove(Body):
    id: str


@app.post("/api/esphome/remove")
def esphome_remove(body: MqttDeviceRemove):
    """Drop a generic-MQTT device from the registry. Add-without-remove would be a
    trap: a node onboarded with the wrong category or name could not be undone
    from the UI, only by hand-editing mqtt_devices.json."""
    dev = DEVICES.get(body.id)
    if not dev or dev.get("transport") != "mqtt":
        raise HTTPException(404, f"no generic-MQTT device '{body.id}'")
    with _mqtt_devices_lock:
        cur = _mqtt_devices_read()
        kept = [e for e in cur if not (isinstance(e, dict) and e.get("id") == body.id)]
        if len(kept) == len(cur):
            raise HTTPException(409, f"'{body.id}' is not in mqtt_devices.json")
        _atomic_json_dump(MQTT_DEVICES_PATH, kept, ensure_ascii=False, indent=2)
    DEVICES.pop(body.id, None)
    BY_ID_NAME.pop(body.id, None)
    _device_removed(dev)
    _cmd_forget(body.id)
    with _cache_lock:
        _status_cache.pop(body.id, None)
    # stale topics stay subscribed until the next reconnect; they simply stop
    # resolving in _MQTT_TOPIC_IDX, so nothing is routed to the dead device
    _mqtt_resubscribe_generic()
    return {"ok": True, "id": body.id}
