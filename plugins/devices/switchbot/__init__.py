"""SwitchBot locks (BLE only): a bridge on a host near the lock (host/switchbot-lock.py) speaks MQTT on
switchbot/<id>/...; this plug-in adds the lock as a generic MQTT device of that shape (Add -> SwitchBot
lock), lists the locks and puts the bridge's and the battery's state on the health page."""
import re

from fastapi import HTTPException, Request
from pydantic import Field

import app as core

PLUGIN = {"name": "SwitchBot", "version": "1.0.0", "icon": "lock"}

MODELS = {"lock": "SwitchBot Lock", "lock_pro": "SwitchBot Lock Pro", "lock_ultra": "SwitchBot Lock Ultra"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
LOW_BATTERY = 20
core.DEVICE_FIELDS.append("switchbot")


def entry(lid: str, name: str, model: str) -> dict:
    """The generic MQTT device of a lock behind the bridge; a switch with payload_on LOCKED is what the
    Yash! lock tile and widgets look for. "confirm": the house shows Locked only once the lock says so."""
    b = f"switchbot/{lid}"
    return {"id": lid, "name": name, "transport": "mqtt", "category": "switch", "model": MODELS[model],
            "switchbot": {"model": model},
            "availability_topic": b + "/availability", "payload_online": "online", "payload_offline": "offline",
            "controls": [
                {"kind": "switch", "code": "locked", "label": "Lock", "text_on": "Locked", "text_off": "Unlocked",
                 "confirm": True,
                 "state_topic": b + "/state", "command_topic": b + "/set",
                 "payload_on": "LOCKED", "payload_off": "UNLOCKED"},
                {"kind": "sensor_text", "code": "state", "label": "State", "ro": True, "state_topic": b + "/state"},
                {"kind": "sensor", "code": "battery", "label": "Battery", "unit": "%", "ro": True,
                 "state_topic": b + "/battery"},
                {"kind": "sensor_text", "code": "door", "label": "Door", "ro": True, "state_topic": b + "/door"}]}


def _ours(d) -> bool:
    return isinstance(d, dict) and isinstance(d.get("switchbot"), dict)


def locks() -> list:
    out = []
    for d in list(core.DEVICES.values()):
        if not _ours(d):
            continue
        st = core.get_status(d["id"])
        v = st.get("values") or {}
        out.append({"id": d["id"], "name": d.get("name") or d["id"], "model": d["switchbot"].get("model"),
                    "online": bool(st.get("online")), "state": v.get("state"), "battery": v.get("battery"),
                    "door": v.get("door")})
    return out


@core.app.get("/api/switchbot")
def switchbot_list():
    """The locks behind a bridge, with what the bridge last told."""
    return locks()


class Lock(core.Body):
    name: str = Field("", max_length=40)
    model: str = "lock_ultra"


@core.app.put("/api/switchbot/locks/{lid}")
def switchbot_put(lid: str, body: Lock, request: Request):
    """Add or rename a lock. An existing device of that id is replaced only when it is a SwitchBot lock
    already (ours, or a hand-made entry whose model says SwitchBot: the move from older setups)."""
    core.house_token_only(request)
    if not ID_RE.match(lid):
        raise HTTPException(400, "id: lowercase latin letters, digits, - and _ (it becomes the bridge's topic)")
    if body.model not in MODELS:
        raise HTTPException(400, "model: " + " | ".join(MODELS))
    old = core.DEVICES.get(lid)
    if old is not None and not (_ours(old) or str(old.get("model", "")).startswith("SwitchBot")):   # cutover-compat
        raise HTTPException(409, f"id '{lid}' is taken by another device")
    name = body.name.strip()
    if name:
        name = core.check_name(name, "device", [d.get("name") for k, d in list(core.DEVICES.items()) if k != lid], most=40)
    e = entry(lid, name or lid, body.model)
    core.mqtt_devices_upsert(e)
    core.register_mqtt_device(e)
    core.mqtt_resubscribe_generic()
    if old is not None and old.get("name") != e["name"]:
        core.set_device_name(lid, e["name"])         # one name: the device window shows it, an older rename there yields
    return {"id": lid, "name": e["name"], "model": body.model}


def _battery(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _health() -> list:
    rows = []
    for k in locks():
        rid, bat = "switchbot-" + k["id"], _battery(k["battery"])
        if not k["online"]:
            rows.append(core.health_row(rid, "bad", "SwitchBot lock", "{0}: the bridge or the lock is silent",
                                        "Is the bridge running on its host?", (k["id"],), cmd="systemctl status switchbot-lock@{0}"))
        elif bat is not None and bat < LOW_BATTERY:
            rows.append(core.health_row(rid, "warn", "SwitchBot lock", "{0}: battery {1}%",
                                        "Change the lock's batteries", (k["id"], bat)))
        else:
            rows.append(core.health_row(rid, "ok", "SwitchBot lock", "{0}", args=(k["id"],)))
    return rows


core.HEALTH_CHECKS.append(_health)
