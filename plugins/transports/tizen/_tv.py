"""Samsung Tizen TV (2016+) over the LAN: the remote-control channel (wss :8002, a token the TV hands out once its owner
presses Allow), the REST info and app launch (:8001), UPnP RenderingControl for an absolute volume and mute (:9197),
SSDP discovery. Standard library + websockets. The token is never logged: no URL with it reaches a log or an error."""
import asyncio
import base64
import json
import logging
import re
import socket
import ssl
import urllib.request
from xml.sax.saxutils import escape

try:
    import websockets
except ImportError as _e:              # fail at startup, not inside a 502 later
    raise RuntimeError("the tizen plug-in needs the websockets package") from _e

REST_PORT, WS_PORT, UPNP_PORT = 8001, 8002, 9197
WS_SCHEME = "wss"                      # the tests run a plain ws fake TV
CLIENT_NAME = "Yashome"
RC = "/upnp/control/RenderingControl1"
_WSLOG = logging.getLogger("yashome.tizen.ws")
_WSLOG.setLevel(logging.WARNING)        # websockets logs the request line (with the token) at DEBUG: never
KEYS = {"power": "KEY_POWER", "volume_up": "KEY_VOLUP", "volume_down": "KEY_VOLDOWN", "mute": "KEY_MUTE",
        "channel_up": "KEY_CHUP", "channel_down": "KEY_CHDOWN"}
MEDIA = {"play": "KEY_PLAY", "pause": "KEY_PAUSE", "stop": "KEY_STOP", "fast_forward": "KEY_FF", "rewind": "KEY_REWIND",
         "volume_up": "KEY_VOLUP", "volume_down": "KEY_VOLDOWN", "channel_up": "KEY_CHUP", "channel_down": "KEY_CHDOWN"}
# the remote's keys (the dashboard's TV remote, the tv_key controls): our name -> the TV's key
REMOTE = {"up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT", "ok": "KEY_ENTER", "back": "KEY_RETURN",
          "home": "KEY_HOME", "menu": "KEY_MENU", "exit": "KEY_EXIT", "info": "KEY_INFO",
          "vol_up": "KEY_VOLUP", "vol_down": "KEY_VOLDOWN", "ch_up": "KEY_CHUP", "ch_down": "KEY_CHDOWN"}
_KEY = re.compile(r"KEY_[A-Z0-9_]{1,30}")


class Unauthorized(Exception):
    """The TV refused the token (a new pairing is needed)."""


def _tls():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False                 # the TV's own self-signed certificate, on the house's LAN
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _url(host: str, token: str = "") -> str:
    name = base64.b64encode(CLIENT_NAME.encode()).decode()
    return (f"{WS_SCHEME}://{host}:{WS_PORT}/api/v2/channels/samsung.remote.control?name={name}"
            + (f"&token={token}" if token else ""))


def info(host: str, timeout: float = 1.5) -> dict:
    """GET :8001/api/v2/ -> the TV's description; raises OSError when it does not answer (off, or gone)."""
    with urllib.request.urlopen(f"http://{host}:{REST_PORT}/api/v2/", timeout=timeout) as r:
        return json.loads(r.read())


async def _open(host: str, token: str, wait: float):
    """The channel, after ms.channel.connect -> (ws, token it gave). wait: for the owner's Allow on the TV."""
    try:
        ws = await asyncio.wait_for(websockets.connect(_url(host, token), ssl=_tls() if WS_SCHEME == "wss" else None,
                                                       ping_interval=None, proxy=None, open_timeout=8, close_timeout=1,
                                                       logger=_WSLOG), 10)
    except Exception as e:                    # the message of a refused connect may carry the URL: never the token
        raise OSError(f"{host}:{WS_PORT}: {type(e).__name__}") from None
    try:
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), wait))
            ev = msg.get("event")
            if ev == "ms.channel.connect":
                return ws, (msg.get("data") or {}).get("token") or token
            if ev in ("ms.channel.unauthorized", "ms.channel.timeOut"):
                raise Unauthorized(ev)
    except BaseException:
        await _close(ws)
        raise


async def _close(ws):
    try:
        await ws.close()
    finally:
        tr = getattr(ws, "transport", None)
        if tr is not None:
            tr.abort()


async def pair(host: str, wait: float = 30) -> str:
    """Connect without a token: the TV asks its owner (Allow); its answer carries the token."""
    ws, token = await _open(host, "", wait)
    await _close(ws)
    if not token:
        raise Unauthorized("the TV gave no token")
    return token


async def keys(host: str, token: str, *names: str):
    """Press these keys (KEY_...), in order, over one connection; the whole exchange within 15 s."""
    await asyncio.wait_for(_keys(host, token, *names), 15)


async def _keys(host: str, token: str, *names: str):
    for k in names:
        if not _KEY.fullmatch(k):
            raise ValueError(f"not a key: {k!r}")
    ws, _ = await _open(host, token, 8)
    try:
        for k in names:
            await ws.send(json.dumps({"method": "ms.remote.control", "params": {
                "Cmd": "Click", "DataOfCmd": k, "Option": "false", "TypeOfRemote": "SendRemoteKey"}}))
            await asyncio.sleep(0.15)
    finally:
        await _close(ws)


def mac(host: str) -> str:
    """The TV's own MAC (for Wake-on-LAN) from its REST info; "" when it does not say."""
    m = str(((info(host).get("device") or {}).get("wifiMac") or "")).lower()
    return m if re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", m) else ""


async def installed_apps(host: str, token: str, wait: float = 8) -> list:
    """The apps installed on the TV: [{"id", "name"}] (ed.installedApp.get over the remote-control channel)."""
    ws, _ = await _open(host, token, wait)
    try:
        await ws.send(json.dumps({"method": "ms.channel.emit", "params": {"event": "ed.installedApp.get", "to": "host"}}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), wait))
            if msg.get("event") == "ed.installedApp.get":
                rows = ((msg.get("data") or {}).get("data")) or []
                return [{"id": str(a.get("appId")), "name": str(a.get("name") or a.get("appId"))[:40]} for a in rows
                        if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", str(a.get("appId") or ""))]
    finally:
        await _close(ws)


def launch(host: str, app_id: str, timeout: float = 5):
    """Start an app by its id (POST :8001/api/v2/applications/<id>)."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", app_id):
        raise ValueError(f"not an app id: {app_id!r}")
    req = urllib.request.Request(f"http://{host}:{REST_PORT}/api/v2/applications/{app_id}", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def _soap(host: str, action: str, args: str, timeout: float = 2) -> str:
    body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1"><InstanceID>0</InstanceID>'
            f'<Channel>Master</Channel>{args}</u:{action}></s:Body></s:Envelope>')
    req = urllib.request.Request(f"http://{host}:{UPNP_PORT}{RC}", data=body.encode(), method="POST", headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"urn:schemas-upnp-org:service:RenderingControl:1#{action}"'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _tag(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", xml)
    if not m:
        raise ValueError(f"no {tag} in the answer")
    return m.group(1)


def volume(host: str) -> int:
    return int(_tag(_soap(host, "GetVolume", ""), "CurrentVolume"))


def set_volume(host: str, v: int):
    _soap(host, "SetVolume", f"<DesiredVolume>{int(v)}</DesiredVolume>")


def mute(host: str) -> bool:
    return _tag(_soap(host, "GetMute", ""), "CurrentMute").strip() in ("1", "true")


def set_mute(host: str, on: bool):
    _soap(host, "SetMute", f"<DesiredMute>{1 if on else 0}</DesiredMute>")


def discover(timeout: float = 3.0) -> list:
    """SSDP: the Samsung TVs that answer on the LAN -> [{host, name}] (the name from their REST info when it answers)."""
    st = "urn:samsung.com:device:RemoteControlReceiver:1"
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
           f"ST: {st}\r\n\r\n").encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    hosts = []
    try:
        s.sendto(msg, ("239.255.255.250", 1900))
        while True:
            _, (ip, _) = s.recvfrom(4096)
            if ip not in hosts:
                hosts.append(ip)
    except OSError:
        pass
    finally:
        s.close()
    out = []
    for ip in hosts:
        try:
            out.append({"host": ip, "name": escape(str((info(ip).get("device") or {}).get("name") or ip))})
        except (OSError, ValueError):
            out.append({"host": ip, "name": ip})
    return out
