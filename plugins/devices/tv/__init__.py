"""TV on the dashboard: a card face of its own for category "tv" devices (power, volume + mute,
inputs), the "Appliances" tab (tv, media; the ir plug-in adds its appliances), a 12 s optimistic hold while a TV
wakes over WoL (webos, or a device with wakes), and one remote in its window, the same for every TV.

The remote's keys are the control kind "tv_key" (one stateless control per key, code "key_<name>", like an IR button):
a TV plug-in (webos, tizen) lists the keys its TV takes over the network with key_controls() and gets a press as
code "key_<name>" through its own transport. The window's remote sends a key over the network when the TV has it,
else through the TV's IR remote; the TV itself is the webos or the tizen plug-in."""
from fastapi import HTTPException

import app as core

PLUGIN = {"name": "TV", "version": "1.1.0", "icon": "tv"}

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
