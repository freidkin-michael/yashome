"""Touchmouse: a phone or a wall panel as the touchpad of a TV box or a computer of the house.

The phone sends UDP datagrams straight to a receiver on the LAN ("m dx dy", "b l|r|m 1|0", "w n", "k back|home|kbd|up|down|left|right|enter", "t <text>",
port 7709 by default); this plug-in only lists the receivers: devices with a "touchmouse" field
{"port", "host"?, "os"?} in mqtt_devices.json, plain hosts in its settings section (Add ->
Touchmouse), and the receivers found on the LAN: the computers plug-in's one broadcast ("? yashome 1") is answered by
every computer with its servers; one with the receiver installed is listed by itself, with its computer's name and TV. The receiver of a Linux host is host/touchmouse-rx.py. No secret travels: keep the
port closed to everything but the house LAN and VPN (README).
"""
import io
import ipaddress
import os
import re
import sys
import tarfile

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import Field

import app as core

PLUGIN = {"name": "Touchmouse", "version": "2.1.0", "icon": "pointer"}

SECTION = "touchmouse"
OSES = ("linux", "android")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})$")
_receivers: list = [r for r in core.cfg_section(SECTION, []) if isinstance(r, dict)]
core.SETTINGS_SECTIONS[SECTION] = lambda: _receivers
core.DEVICE_FIELDS.append("touchmouse")
def _found() -> list:
    """The receivers the house's one broadcast found (the computers plug-in: discovered()), as (host, port, its name)."""
    m = sys.modules.get("yashome_plugins.computers")
    if not m:
        return [], {}
    try:
        return ([(h["host"], int((h.get("ports") or {}).get("touchmouse") or 7709), str(h.get("name") or h["host"])[:40])
                 for h in m.discovered() if "touchmouse" in h["servers"]], m.by_host())
    except Exception:              # its files unreadable: only the hand-made list
        return [], {}


def targets() -> list:
    """Every receiver: devices with "touchmouse" (host defaults to the device's own address) and the
    plain hosts of the settings section."""
    out = []
    for d in list(core.DEVICES.values()):
        tm = d.get("touchmouse")
        if not isinstance(tm, dict):
            continue
        host = tm.get("host") or d.get("host") or (d.get("probe") or {}).get("host")
        try:
            port = int(tm.get("port") or 7709)
        except (TypeError, ValueError):
            continue
        if host:
            out.append({"id": d["id"], "name": d.get("name") or d["id"], "host": host, "port": port,
                        "os": tm.get("os") if tm.get("os") in OSES else "android", "device": True})
    for r in _receivers:
        if not r.get("id") or not r.get("host") or r["id"] in core.DEVICES:      # a device took the id since
            continue
        try:
            port = int(r.get("port") or 7709)
        except (TypeError, ValueError):
            continue
        out.append({"id": r["id"], "name": r.get("name") or r["id"], "host": r["host"], "port": port,
                    "os": r.get("os") if r.get("os") in OSES else "linux", "device": False})
    found, pcs = _found()
    have, ids = {r["host"] for r in out}, {r["id"] for r in out}
    for host, port, own in found:      # found by the broadcast: not listed by hand or by a device already
        if host in have:
            continue
        pc = pcs.get(host)
        slug = "tm-" + re.sub(r"[^a-z0-9]+", "-", host.lower())
        rid = pc["id"] if pc and pc["id"] not in ids and ID_RE.match(pc["id"]) else slug
        name = (f"{pc['name']} ({pc['tv']})" if pc["tv"] else pc["name"]) if pc else own
        out.append({"id": rid, "name": name, "host": host, "port": port, "os": "linux", "device": False, "found": True})
        have.add(host)
        ids.add(rid)
    return out


@core.app.get("/api/touchmouse")
def touchmouse_list():
    """The receivers a touchpad may send to (the app and the panels pick one)."""
    return targets()


class Receiver(core.Body):
    id: str = Field(max_length=32)
    name: str = Field("", max_length=40)
    host: str = Field(max_length=253)
    port: int = Field(7709, ge=1, le=65535)
    os: str = "linux"


def _check(r: Receiver) -> dict:
    if not ID_RE.match(r.id):
        raise HTTPException(400, "id: lowercase latin letters, digits, - and _")
    try:
        ipaddress.ip_address(r.host)
    except ValueError:
        if not HOST_RE.match(r.host):
            raise HTTPException(400, "host: an address or a host name")
    if r.os not in OSES:
        raise HTTPException(400, "os: " + " or ".join(OSES))
    if r.id in core.DEVICES:
        raise HTTPException(409, "a device has this id")
    name = r.name.strip()
    if name:
        name = core.check_name(name, "receiver", [x.get("name") for x in _receivers if x.get("id") != r.id], most=40)
    return {"id": r.id, "name": name or r.id, "host": r.host, "port": r.port, "os": r.os}


@core.app.put("/api/touchmouse/receivers/{rid}")
def touchmouse_put(rid: str, body: Receiver, request: Request):
    core.house_token_only(request)
    if body.id != rid:
        raise HTTPException(400, "the id in the path and the body differ")
    entry = _check(body)
    _receivers[:] = [r for r in _receivers if r.get("id") != rid] + [entry]
    core.save_settings()
    return entry


@core.app.delete("/api/touchmouse/receivers/{rid}")
def touchmouse_delete(rid: str, request: Request):
    core.house_token_only(request)
    if not any(r.get("id") == rid for r in _receivers):
        raise HTTPException(404, "no such receiver")
    _receivers[:] = [r for r in _receivers if r.get("id") != rid]
    core.save_settings()
    return {"ok": True}


_HOST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "host")


def host_files():
    """The receiver and the typing helper of a Linux computer (helper/roles/touchmouse-install.sh fetches them); no key in it."""
    def plain(t):
        t.uid = t.gid = 0
        t.uname = t.gname = ""
        t.mode = 0o755 if t.name.endswith(".py") else 0o644
        return t
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("touchmouse-rx.py", "touchmouse-rx.service", "touchmouse-type.py", "touchmouse-type.service"):
            tar.add(os.path.join(_HOST, name), arcname=name, filter=plain)
    return Response(buf.getvalue(), media_type="application/gzip")


core.app.add_api_route("/plugins/touchmouse/host.tar.gz", host_files, methods=["GET"])
core.open_route(host_files)
