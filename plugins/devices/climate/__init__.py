"""Air conditioners on the dashboard: the "in the room" reading of a climate card comes from
another device's temperature (room_temp_from), mirrored every 120 s and at once on a change;
the card face and the modal remote are in ui.js. The choice lives in the settings section
"climate"; entries that still carry room_temp_from in mqtt_devices.json are taken over once."""
import asyncio
from typing import Optional

from fastapi import HTTPException

import app as core

PLUGIN = {"name": "Climate", "version": "1.0.0", "icon": "climate"}

SECTION = "climate"
POLL_S = 120


def _initial(section: dict, devices: dict) -> dict:
    """The saved choice; mqtt_devices.json is taken over only while nothing was ever saved
    (a saved empty choice stays empty: cleared cards must not come back after a restart)."""
    if "room_temp_from" in section:
        v = section["room_temp_from"]
        return dict(v) if isinstance(v, dict) else {}
    return {did: d["room_temp_from"] for did, d in devices.items() if d.get("room_temp_from")}


_from: dict = _initial(core.cfg_section(SECTION, {}), core.DEVICES)


def _section():
    return {"room_temp_from": dict(_from)}


def _publish_choice():
    for did, d in list(core.DEVICES.items()):
        if d.get("category") == "climate":
            if _from.get(did):
                d["room_temp_from"] = _from[did]
            else:
                d.pop("room_temp_from", None)


ROOM = "room"                     # the source "room": the room's thermometer (core.room_reading), not one device


def _source_temp(dev_id: str, src: str):
    """The live temperature of a source, or None: a device's own, or the reading of the AC's room."""
    if src == ROOM:
        rid = next((r["id"] for r in core.get_rooms() if dev_id in (r.get("devices") or [])), None)
        got = core.room_reading(rid, "temperature") if rid else None
        return got["value"] if got and got["dev"] != dev_id else None       # never its own copy of the room
    st = core.status_copy(src, {})
    t = (st.get("values") or {}).get("temperature")
    return t if st.get("online") and not st.get("stale") else None


def _mirror(dev_id: str):
    """source.temperature -> dev.room_temp, only while the source is alive (a dead sensor must
    not freeze the card on a reading from days ago); no live source -> the reading goes."""
    src = _from.get(dev_id)
    cur = core.status_copy(dev_id, {"online": False, "values": {}, "raw": {}})
    t = _source_temp(dev_id, src) if src else None
    if src and t is not None:
        if (cur.get("values") or {}).get("room_temp") != t:
            cur.setdefault("values", {})["room_temp"] = t
            core.store_status(dev_id, cur)
    elif (cur.get("values") or {}).get("room_temp") is not None and src is not None:
        cur["values"].pop("room_temp", None)
        core.store_status(dev_id, cur)


async def _poller():
    while True:
        # first pass one period after start: restored states are all stale until each source reports
        await asyncio.sleep(POLL_S)
        _publish_choice()                         # a card re-added since shows its source again
        for did in list(_from):
            if did in core.DEVICES:
                _mirror(did)


class RoomTempFromBody(core.Body):
    source: Optional[str] = None   # id of a device reporting `temperature`; "" or null clears


@core.app.post("/api/devices/{dev_id}/room-temp-from")
async def set_room_temp_from(dev_id: str, body: RoomTempFromBody):
    d = core.DEVICES.get(dev_id)
    if d is None or d.get("category") != "climate":
        raise HTTPException(404, "not a climate device")
    src = (body.source or "").strip() or None
    if src and src != ROOM and (src == dev_id or src not in core.DEVICES):
        raise HTTPException(404, "unknown source device")
    rid = next((r["id"] for r in core.get_rooms() if dev_id in (r.get("devices") or [])), None)
    if src == ROOM and rid and core.room_sensor_order(rid, "temperature")["pick"].rpartition("/")[0] == dev_id:
        raise HTTPException(409, "the room's thermometer is this AC: pick another one for the room first")
    old = _from.get(dev_id)
    if src:
        _from[dev_id] = src
    else:
        _from.pop(dev_id, None)
    _publish_choice()
    try:
        await asyncio.to_thread(core.save_settings)
    except BaseException:                         # not saved (settings.json unreadable): not live either
        if old:
            _from[dev_id] = old
        else:
            _from.pop(dev_id, None)
        _publish_choice()
        raise
    if old and not src:
        cur = core.status_copy(dev_id, {"online": True, "values": {}, "raw": {}})
        if (cur.get("values") or {}).get("room_temp") is not None:
            cur["values"].pop("room_temp", None)
            core.store_status(dev_id, cur)
    elif src:
        _mirror(dev_id)
    return {"ok": True, "room_temp_from": src}


core.SETTINGS_SECTIONS[SECTION] = _section


def _on_rename(old: str, new: str):
    """A renamed AC or source keeps its room reading ("room" is no device: it stays)."""
    moved = {(new if k == old else k): (new if v == old else v) for k, v in _from.items()}
    if moved != _from:
        _from.clear()
        _from.update(moved)
        _publish_choice()
        core.save_settings()


core.ON_RENAME.append(_on_rename)
core.DEVICE_FIELDS.append("room_temp_from")
core.STARTUP_TASKS.append(_poller)
_publish_choice()
