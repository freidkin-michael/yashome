#!/usr/bin/env python3
"""The player of a computer of the house: one command to its mpv (the user service yashome-player, mpv --idle with an IPC
socket in $XDG_RUNTIME_DIR/yashome-player/mpv.sock), read as JSON from stdin, the player's state printed as JSON.

The house's helper runs it over ssh as the computer's user (`python3 /opt/yashome-player/ctl.py`, the command on stdin, never
on a command line): {"cmd": "status" | "play" | "pause" | "seek" | "volume" | "mute" | "audio" | "sub" | "stop", ...}
- play {url, title?, start?, headers?, subs?}: an http(s) address, from start seconds; mpv's window goes full screen;
  headers {name: value} go with every request of it (a site's Referer, User-Agent, Cookie), subs: http(s) addresses of
  subtitle files;
- pause {on?}, mute {on?}: on / off, without it: the other way;
- seek {by} seconds from here, or {to} seconds from the start;
- volume {level} 0-130, or {by};
- audio {id}, sub {id}: a track of the playing file (the status lists them), 0 = none.
Out: {"ok": true, "state": {...}} or {"ok": false, "error": "..."}; exit code 0 either way (1: not JSON in)."""
import json
import os
import re
import socket
import sys
import time

SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "yashome-player", "mpv.sock")
TIMEOUT = 5
URL_RE = re.compile(r"^https?://[^\s\x00-\x1f]{1,2000}$")
TRACKS_MAX = 40
HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,40}$")
HEADERS_MAX = 10
SUBS_MAX = 10


class Error(Exception):
    pass


class Mpv:
    """mpv's JSON IPC: one request at a time, the events between the answers skipped."""

    def __init__(self, path=SOCK):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(TIMEOUT)
        try:
            self.s.connect(path)
        except OSError:
            raise Error("the player is not running (systemctl --user status yashome-player)")
        self.buf = b""
        self.n = 0

    def __call__(self, *command):
        self.n += 1
        self.s.sendall(json.dumps({"command": list(command), "request_id": self.n}).encode() + b"\n")
        deadline = time.monotonic() + TIMEOUT
        while True:
            while b"\n" not in self.buf:
                if time.monotonic() > deadline:
                    raise Error("the player does not answer")
                try:
                    chunk = self.s.recv(65536)
                except socket.timeout:
                    raise Error("the player does not answer")
                if not chunk:
                    raise Error("the player closed the connection")
                self.buf += chunk
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("request_id") == self.n:
                if msg.get("error") not in (None, "success"):
                    raise Error(f"{command[0]}: {msg['error']}")
                return msg.get("data")

    def get(self, prop, default=None):
        try:
            return self(*("get_property", prop))
        except Error:
            return default        # nothing playing: most properties are "unavailable"


def state(mpv) -> dict:
    idle = bool(mpv.get("idle-active", True))
    out = {"idle": idle, "volume": mpv.get("volume"), "mute": mpv.get("mute")}
    if idle:
        return out
    tracks = []
    for t in (mpv.get("track-list") or [])[:TRACKS_MAX]:
        if t.get("type") in ("audio", "sub"):
            tracks.append({"id": t.get("id"), "type": t["type"], "lang": t.get("lang") or "", "title": t.get("title") or "",
                           "selected": bool(t.get("selected"))})
    out.update({"title": mpv.get("media-title") or "", "url": mpv.get("path") or "", "pause": mpv.get("pause"),
                "pos": mpv.get("time-pos"), "duration": mpv.get("duration"), "tracks": tracks,
                "buffering": mpv.get("paused-for-cache") or False})
    return out


def num(v, name, lo=None, hi=None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise Error(f"{name}: a number")
    if lo is not None and v < lo or hi is not None and v > hi:
        raise Error(f"{name}: {lo}-{hi}")
    return v


def run(req: dict, mpv=None) -> dict:
    cmd = req.get("cmd")
    if cmd not in ("status", "play", "pause", "seek", "volume", "mute", "audio", "sub", "stop"):
        raise Error("cmd: status, play, pause, seek, volume, mute, audio, sub or stop")
    mpv = mpv or Mpv()
    if cmd == "play":
        url = str(req.get("url") or "")
        if not URL_RE.match(url):
            raise Error("url: an http(s) address")
        headers, subs = req.get("headers") or {}, req.get("subs") or []
        if not isinstance(headers, dict) or len(headers) > HEADERS_MAX or not all(
                HEADER_RE.match(str(k)) and isinstance(v, str) and len(v) <= 4000 and not re.search(r"[\x00-\x1f]", v) for k, v in headers.items()):
            raise Error(f"headers: at most {HEADERS_MAX} {{name: value}}, one line each")
        if not isinstance(subs, list) or len(subs) > SUBS_MAX or not all(isinstance(u, str) and URL_RE.match(u) for u in subs):
            raise Error(f"subs: at most {SUBS_MAX} http(s) addresses")
        mpv("set_property", "http-header-fields", [f"{k}: {v}" for k, v in headers.items()])
        mpv("set_property", "sub-files", subs)
        start = req.get("start")
        mpv("set_property", "start", "none" if not start else str(num(start, "start", 0)))
        mpv("set_property", "force-media-title", str(req.get("title") or "")[:200])
        mpv("set_property", "fullscreen", True)
        mpv("loadfile", url, "replace")
        mpv("set_property", "pause", False)
    elif cmd in ("pause", "mute"):
        on = req.get("on")
        if on is None:
            mpv("cycle", cmd)
        else:
            mpv("set_property", cmd, bool(on))
    elif cmd == "seek":
        if "to" in req:
            mpv("seek", num(req["to"], "to", 0), "absolute")
        else:
            mpv("seek", num(req.get("by"), "by", -86400, 86400), "relative")
    elif cmd == "volume":
        if "level" in req:
            mpv("set_property", "volume", num(req["level"], "level", 0, 130))
        else:
            mpv("add", "volume", num(req.get("by"), "by", -130, 130))
    elif cmd in ("audio", "sub"):
        tid = int(num(req.get("id"), "id", 0, 999))
        mpv("set_property", "aid" if cmd == "audio" else "sid", tid or "no")
    elif cmd == "stop":
        mpv("stop")
    return state(mpv)


def main():
    try:
        req = json.loads(sys.stdin.read(65536) or "{}")
    except ValueError:
        print(json.dumps({"ok": False, "error": "not JSON"}))
        return 1
    try:
        out = {"ok": True, "state": run(req if isinstance(req, dict) else {})}
    except Error as e:
        out = {"ok": False, "error": str(e)}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
