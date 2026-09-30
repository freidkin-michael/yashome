"""Minimal LG webOS (SSAP) control for the dashboard - async, stdlib + websockets.

Power-on is Wake-on-LAN (SSAP can't wake from standby); everything else is an
SSAP request over ws://<ip>:3000. First contact needs a one-time PROMPT accept
on the TV, which yields a persistent client-key.

CLI (for bring-up, inside the dashboard container, which has `websockets`, from /app):
    python3 plugins/webos/_ssap.py pair   <ip>
    python3 plugins/webos/_ssap.py status <ip> <key>
    python3 plugins/webos/_ssap.py cmd    <ip> <key> <code> [value]   # power_off|volume|mute|input|app|media
    python3 plugins/webos/_ssap.py lists  <ip> <key>                  # dump real inputs + apps
    python3 plugins/webos/_ssap.py wol    <mac>
"""
import asyncio
import json
import os
import socket
import sys

try:
    import websockets
except ImportError as _e:              # fail at startup, not inside a 502 later
    raise RuntimeError("the webos plug-in needs the websockets package") from _e

PORT = 3000
HELLO = {
    "forcePairing": False,
    "pairingType": "PROMPT",
    "manifest": {
        "manifestVersion": 1,
        "appVersion": "1.1",
        "signed": {
            "created": "20140509",
            "appId": "com.lge.test",
            "vendorId": "com.lge",
            "localizedAppNames": {"": "LG Remote App", "ko-KR": "\ub9ac\ubaa8\ucee8"},
            "localizedVendorNames": {"": "LG Electronics"},
            "permissions": ["TEST_SECURE", "CONTROL_INPUT_TEXT", "CONTROL_MOUSE_AND_KEYBOARD",
                            "READ_INSTALLED_APPS", "READ_LGE_SDX", "READ_NOTIFICATIONS",
                            "SEARCH", "WRITE_SETTINGS", "WRITE_NOTIFICATION_ALERT",
                            "CONTROL_POWER", "READ_CURRENT_CHANNEL", "READ_RUNNING_APPS",
                            "READ_UPDATE_INFO", "UPDATE_FROM_REMOTE_APP",
                            "READ_LGE_TV_INPUT_EVENTS", "READ_TV_CURRENT_TIME"],
            "serial": "2f930e2d2cfe083771f68e4fe7bb07",
        },
        "permissions": ["LAUNCH", "LAUNCH_WEBAPP", "APP_TO_APP", "CLOSE", "TEST_OPEN",
                        "TEST_PROTECTED", "CONTROL_AUDIO", "CONTROL_DISPLAY",
                        "CONTROL_INPUT_JOYSTICK", "CONTROL_INPUT_MEDIA_RECORDING",
                        "CONTROL_INPUT_MEDIA_PLAYBACK", "CONTROL_INPUT_TV", "CONTROL_POWER",
                        "READ_APP_STATUS", "READ_CURRENT_CHANNEL", "READ_INPUT_DEVICE_LIST",
                        "READ_NETWORK_STATE", "READ_RUNNING_APPS", "READ_TV_CHANNEL_LIST",
                        "WRITE_NOTIFICATION_TOAST", "READ_POWER_STATE", "READ_COUNTRY_INFO",
                        "READ_SETTINGS", "CONTROL_TV_SCREEN", "CONTROL_TV_STANBY",
                        "CONTROL_FAVORITE_GROUP", "CONTROL_USER_INFO", "CONTROL_BLUETOOTH",
                        "CONTROL_TIMER_INFO", "STB_INTERNAL_CONNECTION", "CONTROL_RECORDING",
                        "READ_RECORDING_STATE", "WRITE_RECORDING_LIST", "READ_RECORDING_LIST",
                        "READ_RECORDING_SCHEDULE", "WRITE_RECORDING_SCHEDULE",
                        "READ_STORAGE_DEVICE_LIST", "READ_TV_PROGRAM_INFO", "CONTROL_BOX_CHANNEL",
                        "READ_TV_ACR_AUTH_TOKEN", "READ_TV_CONTENT_STATE", "READ_TV_CURRENT_TIME",
                        "ADD_LAUNCHER_CHANNEL", "SET_CHANNEL_SKIP", "RELEASE_CHANNEL_SKIP",
                        "CONTROL_CHANNEL_BLOCK", "DELETE_SELECT_CHANNEL", "CONTROL_CHANNEL_GROUP",
                        "SCAN_TV_CHANNELS", "CONTROL_TV_POWER", "CONTROL_WOL"],
    },
}

URIS = {
    "power_off": ("ssap://system/turnOff", None),
    "mute":      ("ssap://audio/setMute", lambda v: {"mute": bool(v)}),
    "volume":    ("ssap://audio/setVolume", lambda v: {"volume": int(v)}),
    "input":     ("ssap://tv/switchInput", lambda v: {"inputId": str(v)}),
    "app":       ("ssap://system.launcher/launch", lambda v: {"id": str(v)}),
}
MEDIA = {"play": "ssap://media.controls/play", "pause": "ssap://media.controls/pause",
         "stop": "ssap://media.controls/stop"}


def wol(mac: str, broadcast="255.255.255.255"):
    """Send a Wake-on-LAN magic packet to the TV's NIC."""
    m = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    pkt = b"\xff" * 6 + m * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        _wol_send(s, pkt, mac, broadcast)
    finally:
        s.close()


def _wol_send(s, pkt, mac, broadcast):
    for port in (9, 7):
        s.sendto(pkt, (broadcast, port))
    # A docker-bridge container cannot broadcast to the LAN (NAT eats it), so
    # also ask the host-side wol-relay (WOL_RELAY, udp 9099 by default)
    # to emit the magic packet from the real NIC.
    relay = os.environ.get("WOL_RELAY", "")       # host:port of wol-relay.py on the host, if any
    if relay:
        try:
            h, prt = relay.rsplit(":", 1)
            s.sendto(b"WOL " + mac.encode(), (h, int(prt)))
        except Exception:
            pass


async def _open(ip, key, timeout=8, register_timeout=None):
    """Connect + SSAP register handshake. Returns (ws, client_key). With a key the TV answers
    at once (`timeout`); a fresh pairing waits for the PROMPT on screen (`register_timeout`).
    A TV that accepts the socket and then says nothing is closed, never waited for."""
    ws = await asyncio.wait_for(
        websockets.connect(f"ws://{ip}:{PORT}", ping_interval=None, proxy=None,
                           max_size=2**22, open_timeout=timeout, close_timeout=1), timeout)
    payload = dict(HELLO)
    if key:
        payload["client-key"] = key

    async def register():
        await ws.send(json.dumps({"type": "register", "id": "reg", "payload": payload}))
        while True:
            msg = json.loads(await ws.recv())
            t = msg.get("type")
            if t == "registered":
                return msg["payload"].get("client-key", key)
            if t == "error":
                raise RuntimeError(msg.get("error") or "register error")
            # type == "response" with pairingType PROMPT -> keep waiting for accept
    try:
        return ws, await asyncio.wait_for(register(), register_timeout or timeout)
    except BaseException:
        await _close(ws)
        raise


async def _close(ws):
    """Close, and drop the socket even when the close is cancelled half way (a silent TV)."""
    try:
        await ws.close()
    finally:
        tr = getattr(ws, "transport", None)
        if tr is not None:
            tr.abort()


async def _request(ws, uri, payload=None):
    await ws.send(json.dumps({"type": "request", "id": "req", "uri": uri,
                              "payload": payload or {}}))
    return json.loads(await asyncio.wait_for(ws.recv(), 8)).get("payload", {})


async def pair(ip, prompt_timeout=40):
    ws, key = await _open(ip, None, register_timeout=prompt_timeout)
    await _close(ws)
    return key


def bounded(seconds):
    """The whole exchange of one call, connect to close, within `seconds`."""
    def wrap(fn):
        async def run(*a, **k):
            return await asyncio.wait_for(fn(*a, **k), seconds)
        run.__name__, run.__doc__ = fn.__name__, fn.__doc__
        return run
    return wrap


@bounded(20)
async def command(ip, key, code, value=None):
    ws, key = await _open(ip, key)
    try:
        if code == "media":
            uri = MEDIA.get(str(value))
            if not uri:
                raise ValueError(f"bad media value {value}")
            return await _request(ws, uri)
        uri, mk = URIS[code]
        return await _request(ws, uri, mk(value) if mk else None)
    finally:
        await _close(ws)


@bounded(20)
async def lists(ip, key):
    """Real external-input + launch-point lists, as {id: label} maps."""
    ws, key = await _open(ip, key)
    try:
        inp = await _request(ws, "ssap://tv/getExternalInputList")
        apps = await _request(ws, "ssap://com.webos.applicationManager/listLaunchPoints")
        inputs = {d["id"]: d.get("label") or d["id"] for d in inp.get("devices", [])}
        launch = {d["id"]: d.get("title") or d["id"] for d in apps.get("launchPoints", [])}
        return {"inputs": inputs, "apps": launch}
    finally:
        await _close(ws)


@bounded(20)
async def status(ip, key):
    """Best-effort live state: volume, mute, foreground app."""
    ws, key = await _open(ip, key)
    try:
        vol = await _request(ws, "ssap://audio/getVolume")
        fg = await _request(ws, "ssap://com.webos.applicationManager/getForegroundAppInfo")
        vs = vol.get("volumeStatus") or {}
        volume = vol.get("volume")
        if volume is None:
            volume = vs.get("volume")
        mute = vol.get("muted")
        if mute is None:
            mute = vol.get("mute")
        if mute is None:
            mute = vs.get("muteStatus")
        return {"online": True, "volume": volume, "mute": mute, "app": fg.get("appId")}
    finally:
        await _close(ws)


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        print(__doc__); sys.exit(0)
    op = a[0]
    if op == "wol":
        wol(a[1]); print("magic packet sent"); sys.exit(0)
    if op == "pair":
        print("ACCEPT THE PROMPT ON THE TV...")
        print("client-key:", asyncio.run(pair(a[1]))); sys.exit(0)
    if op == "status":
        print(json.dumps(asyncio.run(status(a[1], a[2])), ensure_ascii=False)); sys.exit(0)
    if op == "lists":
        print(json.dumps(asyncio.run(lists(a[1], a[2])), ensure_ascii=False, indent=2)); sys.exit(0)
    if op == "cmd":
        v = a[4] if len(a) > 4 else None
        print(json.dumps(asyncio.run(command(a[1], a[2], a[3], v)), ensure_ascii=False)); sys.exit(0)
    print("unknown op", op); sys.exit(1)
