"""TV on the dashboard: a card face of its own for category "tv" devices (power, volume + mute,
inputs), the "Appliances" tab (tv, media; the ir plug-in adds its appliances), a 12 s optimistic hold while a TV
wakes over WoL (webos, or a device with wakes), and one remote in its window, the same for every TV.

The remote's keys are the control kind "tv_key" (one stateless control per key, code "key_<name>", like an IR button):
a TV plug-in (webos, tizen) lists the keys its TV takes over the network with key_controls() and gets a press as
code "key_<name>" through its own transport. The window's remote sends a key over the network when the TV has it,
else through the TV's IR remote; the TV itself is the webos or the tizen plug-in."""
import asyncio
import os
import re

from fastapi import HTTPException, Request
from pydantic import Field

import app as core

from . import _probe

PLUGIN = {"name": "TV", "version": "1.2.0", "icon": "tv"}

# the remote's keys, in the order of a remote; the names are the remote layout's (panels widgets.js irKey). No input key:
# the inputs are the TV's input list, the same on every TV
KEYS = {"up": "Up", "down": "Down", "left": "Left", "right": "Right", "ok": "OK", "back": "Back", "home": "Home",
        "menu": "Menu", "exit": "Exit", "info": "Info", "vol_up": "Volume up", "vol_down": "Volume down",
        "ch_up": "Channel up", "ch_down": "Channel down"}


def key_controls(names) -> list:
    """The tv_key controls for these key names (unknown names skipped), in the order of KEYS."""
    have = set(names)
    return [{"kind": "tv_key", "code": "key_" + k, "label": label, "key": k} for k, label in KEYS.items() if k in have]


def _press(cfg: dict, ctl: dict, val):
    """CONTROL_KINDS["tv_key"]: the press goes to the TV's own transport as code key_<name>."""
    send = core.TRANSPORTS.get(cfg.get("transport"))
    if send is None:
        raise HTTPException(409, f"{cfg.get('name') or cfg.get('id')}: its plug-in is off")
    send(cfg, ctl["code"], True, ctl["kind"])
    return {"key": ctl.get("key")}


core.CONTROL_KINDS["tv_key"] = _press


_MEDIA_SLOT = {"play": "play", "pause": "pause", "stop": "stop", "rewind": "rew", "fast_forward": "ff"}
_IR_ALIAS = {"power_on": "on", "poweron": "on", "power_off": "off", "poweroff": "off"}


def remote(d: dict, devices: dict) -> list:
    """A TV's one remote, the same for every make (the dashboard's tv/ui.js and the app's screen): its network keys, its
    player keys, then the keys of its IR remote the network does not have. [{slot, dev, code, value?, label}]: slot is the
    place on the remote (KEYS, the player's, an IR key's own), "" = a chip; a press sends value (default true) to dev/code."""
    ctl = d.get("controls") or []
    has = {c.get("code") for c in ctl}
    out, taken = [], set()
    for c in ctl:
        if c.get("kind") == "tv_key" and c.get("key") and c["key"] not in taken:
            taken.add(c["key"])
            out.append({"slot": c["key"], "dev": d["id"], "code": c["code"], "label": c.get("label") or c["key"]})
    media = next((c for c in ctl if c.get("code") == "media" and isinstance(c.get("range"), list)), None)
    for v in (media or {}).get("range") or []:
        s = _MEDIA_SLOT.get(v)
        if s and s not in taken:
            taken.add(s)
            out.append({"slot": s, "dev": d["id"], "code": "media", "value": v, "label": v})
    if "mute" in has:
        taken.add("mute")                                  # its switch is on the card
    net = bool(taken)
    own = [(d["id"], c) for c in ctl if c.get("kind") == "ir_press"]
    hosted = [(a["id"], c) for a in devices.values() if a.get("ir_appliance") and a.get("ir_card") == d["id"]
              for c in a.get("controls") or [] if c.get("kind") == "ir_press"]
    for dev, c in own + hosted:
        k = c.get("icon") if c.get("icon") in _MEDIA_SLOT.values() or c.get("icon") in KEYS else None
        k = k or _IR_ALIAS.get(str(c.get("code", "")).removeprefix("ir_"), str(c.get("code", "")).removeprefix("ir_"))
        if k in taken or ("power" in has and (k in ("power", "on", "off") or c.get("icon") in ("power", "on", "off"))):
            continue                                       # the network has it; power is the card's switch
        if "input" in has and re.search(r"hdmi|source|input", f"{c.get('code')} {c.get('button') or ''}", re.I):
            continue                                       # the inputs are the card's list
        if net and k not in KEYS and k not in _MEDIA_SLOT.values():
            continue                                       # a network TV: no stray chips of its IR remote
        taken.add(k)
        out.append({"slot": k if k in KEYS or k in _MEDIA_SLOT.values() else "", "dev": dev, "code": c["code"],
                    "label": c.get("label") or c["code"]})
    return out


# ---- Add a TV: the TVs a sweep of the house's /24 finds, on request, and what TV sits at one address ----
KNOWN_HOSTS: list = []             # a TV plug-in appends a callable -> {address: name} of the TVs it has ("already added")


def _known() -> dict:
    out = {}
    for f in KNOWN_HOSTS:
        try:
            out.update({h: str(n or h) for h, n in f().items() if h})
        except Exception as e:                                      # a broken plug-in must not hide the list
            core.log.warning(f"[tv] known hosts: {e}")
    return out


def _mark(r: dict, known: dict) -> dict:
    r["known"] = r["host"] in known
    r["house_name"] = known.get(r["host"], "")                      # the name on its card (an LG tells none before pairing)
    r["supported"] = bool(r["platform"]) and r["platform"] in core.TRANSPORTS
    return r


@core.app.post("/api/tv/search")
async def tv_search(request: Request):
    """The TVs on the house's LAN: a sweep of the /24 of BIND_ADDRESS, of the address the page came from and of the
    devices' addresses (a few seconds; nothing runs between two presses)."""
    core.house_token_only(request)
    seeds = [os.environ.get("BIND_ADDRESS"), request.url.hostname] + [d.get("host") for d in list(core.DEVICES.values())]
    networks = _probe.nets(seeds)
    if not networks:
        raise HTTPException(409, "the house's LAN is not known: set BIND_ADDRESS in .env, or type the TV's address")
    known = _known()
    rows = [_mark(r, known) for r in await asyncio.to_thread(_probe.sweep, networks)]
    return {"tvs": sorted(rows, key=lambda r: r["known"]), "networks": [str(n) for n in networks]}


class ProbeBody(core.Body):
    host: str = Field(min_length=1, max_length=100)


@core.app.post("/api/tv/probe")
async def tv_probe(body: ProbeBody, request: Request):
    """What TV sits at this address: the wizard's "not in the list" (platform "" = no answer: off, or not a TV)."""
    core.house_token_only(request)
    host = body.host.strip()
    if not _probe.HOST_RE.fullmatch(host):
        raise HTTPException(400, "address: an IP address or a host name")
    return _mark(await asyncio.to_thread(_probe.probe, host), _known())
