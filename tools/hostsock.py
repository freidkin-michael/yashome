"""The house's helpers on its own host (tools/host-sockets.sh installs them): each listens on a socket file in DIR, which
the home container has mounted. No port, no token: the file's group (the house's PGID) is who may ask. Standard library only.
  wol           datagram "WOL <mac>"                    plugins/transports/webos/host/wol-relay.py
  provision     HTTP: GET /health, /scan; POST /provision  plugins/tools/provision/host/esp-provision.py
  panel-start   one line "start-launcher <tablet id>"   tools/panel-start/panel_start.py"""
import http.client
import json
import os
import socket

DIR = os.environ.get("HOST_SOCKETS_DIR") or "/run/yashome"      # another folder: the stand and the tests only


def path(name: str) -> str:
    return os.path.join(DIR, f"{name}.sock")


def there(name: str) -> bool:
    return os.path.exists(path(name))


def datagram(name: str, data: bytes) -> None:
    """One datagram; OSError when the socket is not there or does not take it."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.sendto(data, path(name))


def line(name: str, text: str, timeout: float) -> str:
    """One line out, one line back (at most 4 KB)."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path(name))
        s.sendall(text.encode() + b"\n")
        buf = b""
        while b"\n" not in buf and len(buf) < 4096:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    return buf.split(b"\n", 1)[0].decode(errors="replace")


class _Conn(http.client.HTTPConnection):
    def __init__(self, sock_path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = sock_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def http(name: str, method: str, url: str, body=None, timeout: float = 15.0):
    """(status, the JSON answer); OSError when nobody listens, ValueError when the answer is not JSON."""
    c = _Conn(path(name), timeout)
    try:
        raw = json.dumps(body).encode() if body is not None else None
        c.request(method, url, body=raw, headers={"Content-Type": "application/json"} if raw is not None else {})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    finally:
        c.close()
