"""What TV sits at an address: each smart-TV platform answers on a port or URL of its own, so a few short unicast
checks name it (Samsung Tizen, LG webOS, Android/Google TV, Roku, Hisense VIDAA, Philips). sweep() knocks on those
ports across a /24 on request. Unicast only: it works from the dashboard's bridge network. A TV that is off answers
nothing; the caller then asks to switch it on."""
import ipaddress
import json
import re
import socket
import urllib.request
from concurrent.futures import ThreadPoolExecutor

TIMEOUT = 1.5
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,98}")

# platform (the name of its plug-in, a transport) -> the maker most TVs of it come from, when the TV does not tell
PLATFORMS = {"tizen": "Samsung", "webos": "LG", "androidtv": "Android TV", "roku": "Roku", "vidaa": "Hisense",
             "philips": "Philips"}


def _port(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), TIMEOUT).close()
        return True
    except OSError:
        return False


def _get(url: str, limit: int = 20000) -> str:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:                    # nosec: a LAN address the owner typed
        return r.read(limit).decode("utf-8", "replace")


def _tag(xml: str, name: str) -> str:
    m = re.search(rf"<{name}>([^<]*)</{name}>", xml)
    return m.group(1).strip() if m else ""


def _tizen(host):
    d = json.loads(_get(f"http://{host}:8001/api/v2/")).get("device") or {}
    return {"platform": "tizen", "maker": "Samsung", "name": d.get("name") or "", "model": d.get("modelName") or "",
            "mac": d.get("wifiMac") or ""}


def _roku(host):
    x = _get(f"http://{host}:8060/query/device-info")
    return {"platform": "roku", "maker": _tag(x, "vendor-name") or "Roku", "name": _tag(x, "friendly-device-name"),
            "model": _tag(x, "model-name"), "mac": _tag(x, "wifi-mac") or _tag(x, "ethernet-mac")}


def _ports(platform, ports):
    def check(host):
        if not any(_port(host, p) for p in ports):
            raise OSError("closed")
        return {"platform": platform, "maker": PLATFORMS[platform]}
    return check


def _ssap(host):
    """LG webOS: its SSAP websocket answers a request before any pairing with an error (no prompt on the TV).
    A bare open port 3000 is not enough: Gitea, Grafana and dev servers use it too."""
    import ssl
    from websockets.sync.client import connect                  # the webos and tizen plug-ins need it anyway
    for url in (f"ws://{host}:3000", f"wss://{host}:3001"):
        tls = ssl._create_unverified_context() if url.startswith("wss") else None   # nosec: the TV's own certificate
        try:
            with connect(url, open_timeout=TIMEOUT, ssl=tls, close_timeout=0.5) as ws:
                ws.send(json.dumps({"type": "request", "id": "yashome", "uri": "ssap://system/getSystemInfo"}))
                m = json.loads(ws.recv(timeout=TIMEOUT))
        except (OSError, ValueError, TimeoutError, Exception):    # websockets raises its own kinds: any = not webOS here
            continue
        if isinstance(m, dict) and m.get("id") == "yashome" and m.get("type") in ("error", "response"):
            return {"platform": "webos", "maker": "LG"}
    raise OSError("no SSAP")


# most specific first: the first that answers names the TV (an Android TV also has cast ports, so those are not used)
CHECKS = [_tizen, _roku, _ssap, _ports("androidtv", (6466, 6467)), _ports("vidaa", (36669,)),
          _ports("philips", (1925, 1926))]


def probe(host: str) -> dict:
    """{host, platform, maker, name, model, mac}; platform "" when nothing answered (off, or not a TV)."""
    out = {"host": host, "platform": "", "maker": "", "name": "", "model": "", "mac": ""}
    if not HOST_RE.fullmatch(host or ""):
        return out

    def one(check):
        try:
            return check(host)
        except (OSError, ValueError, AttributeError):
            return None
    with ThreadPoolExecutor(len(CHECKS)) as pool:
        got = list(pool.map(one, CHECKS))
    hit = next((g for g in got if g), None)
    if hit:
        out.update({k: str(v)[:60] for k, v in hit.items() if v})
        out["mac"] = out["mac"].lower()
    return out


SWEEP_PORTS = (8001, 3000, 3001, 6466, 6467, 8060, 36669, 1925, 1926)
SWEEP_TIMEOUT = 0.4                # a LAN address answers in a few ms; an empty one is given up after this
MAX_NETS = 3


def nets(addresses) -> list:
    """The /24s of these addresses that are private IPv4 (the house's LAN), at most MAX_NETS, in order."""
    out = []
    for a in addresses:
        try:
            ip = ipaddress.ip_address(str(a or "").strip())
        except ValueError:
            continue
        if ip.version == 4 and ip.is_private and not ip.is_loopback:
            n = ipaddress.ip_network(f"{ip}/24", strict=False)
            if n not in out:
                out.append(n)
    return out[:MAX_NETS]


def _knock(pair) -> bool:
    try:
        socket.create_connection(pair, SWEEP_TIMEOUT).close()
        return True
    except OSError:
        return False


def sweep(networks) -> list:
    """Every address of these networks with a TV port open, each probed: [probe()] (about 4 s for one /24)."""
    pairs = [(str(h), p) for n in networks for h in n.hosts() for p in SWEEP_PORTS]
    with ThreadPoolExecutor(256) as pool:
        hosts = sorted({h for (h, _), ok in zip(pairs, pool.map(_knock, pairs)) if ok}, key=ipaddress.ip_address)
    with ThreadPoolExecutor(16) as pool:
        return [r for r in pool.map(probe, hosts) if r["platform"]]
