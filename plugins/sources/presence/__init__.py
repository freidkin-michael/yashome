"""Presence: is anyone home, from the phones on the house Wi-Fi.

The host piece (host/phone-presence.sh, on any host of the LAN) watches the phones' MACs and publishes
on the plug-in's own bus: presence/state (ON/OFF, retained), presence/unseen_days (retained) and
presence/event (arrival / departure, not retained). This plug-in turns that into the device
`presence`: "home" and "stale" for conditions and the header badge, "event" for rules.
"""
import asyncio
import time

import app as core

PLUGIN = {"name": "Presence", "version": "2.1.0", "icon": "person"}

DEVICE_ID = "presence"
STALE_DAYS = 4
SILENT_S = 900                   # the host piece refreshes every ~5 min: 15 min of nothing = no link
_last = {"ts": 0.0, "warned": False}


def _status(**values):
    st = core.status_copy(DEVICE_ID, {"online": False, "values": {}, "raw": {}})
    st["values"] = {**st.get("values", {}), **values}
    st["online"] = True
    return st


def _sensors(**values):
    """Store the readings and hand their changes to the sensor rules (true/false, thresholds)."""
    core.store_status(DEVICE_ID, _status(**values))
    core.fire_sensor_triggers(DEVICE_ID, core.DEVICES[DEVICE_ID], values)


def _on_message(parts, payload, retain):
    text = payload.decode("utf-8", "replace").strip() if isinstance(payload, (bytes, bytearray)) else str(payload).strip()
    leaf = parts[1] if len(parts) == 2 else None
    _last["ts"], _last["warned"] = time.time(), False
    if leaf == "state" and text in ("ON", "OFF"):
        _sensors(home=text == "ON")
    elif leaf == "unseen_days" and text.isdigit():
        days = int(text)
        _sensors(unseen_days=days, stale=days >= STALE_DAYS)
    elif leaf == "event" and text in ("arrival", "departure") and not retain:
        now = time.time()
        with core.events_lock:
            core.events[DEVICE_ID].append({"ts": now, "code": "event", "value": text, "t": int(now)})
        st = _status()
        st.update(last_event_ts=now, last_event={"code": "event", "value": text})
        core.store_status(DEVICE_ID, st)
        core.bg.submit(core.safe_execute_binding, DEVICE_ID, "event", text)


async def _watch_link():
    while True:
        await asyncio.sleep(60)
        if time.time() - _last["ts"] > SILENT_S and core.get_status(DEVICE_ID).get("online"):
            st = core.status_copy(DEVICE_ID, {"online": False, "values": {}, "raw": {}})
            st["online"] = False
            core.store_status(DEVICE_ID, st)
            if not _last["warned"]:
                _last["warned"] = True
                core.log.warning("[presence] nothing from the host piece for 15 min: the link is down")


core.STARTUP_TASKS.append(_watch_link)
core.MQTT_SUBSCRIPTIONS.append("presence/#")
core.MQTT_HANDLERS.append((("presence",), _on_message))
core.register_device({"id": DEVICE_ID, "name": "Who is home", "transport": "presence", "category": "indicator", "virtual": "presence",
                      "controls": [
                          {"kind": "sensor", "code": "home", "label": "At home", "ro": True, "vtype": "bool"},
                          {"kind": "sensor", "code": "stale", "label": "Phones unseen for days", "ro": True,
                           "vtype": "bool", "advanced": True},
                          {"kind": "sensor", "code": "unseen_days", "label": "Days unseen", "ro": True, "advanced": True},
                          {"kind": "trigger_text", "code": "event", "label": "Event", "ro": True,
                           "values": ["arrival", "departure"]}]})


def _health():
    age = time.time() - _last["ts"] if _last["ts"] else None
    if age is None:
        return [core.health_row("presence", "warn", "Who is home", "no report from the host piece yet",
                                "Install and start the host piece (the plug-in's README)", cmd="host/phone-presence.sh")]
    if age > SILENT_S:
        return [core.health_row("presence", "bad", "Who is home", "the host piece is silent for {0} min",
                                "Is the host piece running on its host?", (int(age // 60),), cmd="systemctl status phone-presence")]
    return [core.health_row("presence", "ok", "Who is home")]


core.HEALTH_CHECKS.append(_health)
