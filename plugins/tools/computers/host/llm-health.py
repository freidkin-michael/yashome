#!/usr/bin/env python3
"""Health of the house's big model (plug-in computers, role LLM): the Ollama of the computer in use, reached through
llm-tunnel.sh at 127.0.0.1:11435. GET 127.0.0.1:11436/health -> {"state": "available" | "busy" | "off", ...}; the voice
service sends to the big model only on "available", else to this host's own one. Also on each address of LLM_LISTEN.

off = no computer in use, or no tunnel (it is off, asleep, ssh down). busy = its owner uses the GPU (VRAM beyond ours
> 2 GB, or the GPU loaded while no model of ours is): our model is unloaded so the card is free again; also busy when the
model fell back to the CPU (a WSL that lost the GPU): it is unloaded and that WSL restarted, at most every 10 minutes. A
computer without an NVIDIA card: available while its Ollama answers. A training job of ours (a file job.<name> in
LLM_JOBS_DIR) owns the card: busy. The computer: <house>/plugin-data/computers/llm.json, read again at every check."""
import glob
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = "http://127.0.0.1:11435"
PORT = 11436
TARGET = os.path.join(os.environ.get("HOUSE_DIR", ""), "plugin-data", "computers", "llm.json")
KEY = os.environ.get("YASHOME_SSH_KEY") or os.path.expanduser("~/.config/yashome/ssh/id")
KNOWN = os.environ.get("YASHOME_HOSTS_KNOWN", "")
JOBS = os.environ.get("LLM_JOBS_DIR") or os.path.expanduser("~/llm-pc")
HER_VRAM_MB, UTIL_PCT, CACHE_S = 2048, 20, 15
HEAL_EVERY_S = 600
_cache = {"at": 0.0, "body": None}
_heal = {"at": 0.0}
_lock = threading.Lock()


def _target():
    try:
        t = json.load(open(TARGET))
    except (OSError, ValueError):
        return None
    host, user, wsl = str(t.get("host") or ""), str(t.get("user") or ""), str(t.get("wsl") or "")
    if not (re.match(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$", host) and re.match(r"^[a-z_][a-z0-9_-]{0,31}$", user)
            and re.match(r"^$|^[A-Za-z0-9._-]{1,40}/[a-z_][a-z0-9_-]{0,31}$", wsl)):
        return None
    return {"host": host, "user": user, "wsl": wsl}


def _ssh(t, cmd, timeout=10):
    """A command on the computer itself: on a Windows one it runs in Windows (nvidia-smi sees every process there)."""
    return subprocess.run(["ssh", "-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "IdentitiesOnly=yes", "-i", KEY,
                           "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + KNOWN, "-l", t["user"], t["host"], cmd],
                          capture_output=True, text=True, timeout=timeout)


def _get(path, body=None, timeout=3):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _unload(loaded):
    for m in loaded:
        try:
            _get("/api/generate", {"model": m["name"], "keep_alive": 0}, timeout=10)
        except Exception:
            pass


def _gpu(t):
    """(util %, used MiB, total MiB) of its NVIDIA card; None when it has none."""
    r = _ssh(t, "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits")
    lines = r.stdout.strip().splitlines() if r.returncode == 0 else []
    if not lines:
        return None
    def num(x):                                           # "[N/A]" on some cards: unknown, read as 0
        try:
            return int(x)
        except ValueError:
            return 0
    u, used, total = (num(x.strip()) for x in (lines[0].split(",") + ["", "", ""])[:3])
    return u, used, total


def check():
    t = _target()
    if not t:
        return {"state": "off", "reason": "no computer in use"}
    try:
        version = _get("/api/version")["version"]
    except Exception:
        return {"state": "off", "host": t["host"]}
    jobs = [os.path.basename(p)[4:] for p in glob.glob(os.path.join(JOBS, "job.*"))]
    if jobs:                                              # a training job of ours owns the card
        try:
            _unload(_get("/api/ps").get("models", []))
        except Exception:
            pass
        return {"state": "busy", "reason": "training: " + ",".join(jobs), "version": version, "host": t["host"]}
    try:
        loaded = _get("/api/ps").get("models", [])
        gpu = _gpu(t)
    except Exception as e:
        return {"state": "off", "error": str(e)[:200], "host": t["host"]}
    if gpu is None:                                       # no NVIDIA card there: its Ollama answers, that is all we know
        return {"state": "available", "version": version, "loaded": [m["name"] for m in loaded], "host": t["host"]}
    util, used, total = gpu
    ours = sum(m.get("size_vram", 0) for m in loaded) // (1024 * 1024)
    her = max(0, used - ours)
    if loaded and ours == 0 and t["wsl"]:                 # the WSL lost the GPU and the model runs on the CPU: unload, restart it
        _unload(loaded)
        # only the model's own distro (--shutdown stops every WSL of the PC), and never while the owner uses the card
        if time.time() - _heal["at"] > HEAL_EVERY_S and her <= HER_VRAM_MB and util <= UTIL_PCT:
            _heal["at"] = time.time()
            _ssh(t, "wsl --terminate " + t["wsl"].split("/")[0], timeout=60)
            return {"state": "busy", "reason": "cpu_fallback, WSL restarted", "version": version, "host": t["host"]}
        return {"state": "busy", "reason": "cpu_fallback", "version": version, "host": t["host"]}
    busy = her > HER_VRAM_MB or (util > UTIL_PCT and not loaded)
    if busy and loaded:                                   # the owner's turn: free the card at once
        _unload(loaded)
    return {"state": "busy" if busy else "available", "version": version, "loaded": [m["name"] for m in loaded], "host": t["host"],
            "gpu": {"util": util, "used_mb": used, "total_mb": total, "ours_mb": ours, "hers_mb": her}}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] != "/health":
            self.send_error(404)
            return
        # one check at a time; while it runs (ssh: seconds) the others get the last answer instead of waiting
        stale = _cache["body"] is None or time.time() - _cache["at"] > CACHE_S
        if stale and _lock.acquire(blocking=_cache["body"] is None):
            try:
                if _cache["body"] is None or time.time() - _cache["at"] > CACHE_S:
                    _cache["body"], _cache["at"] = dict(check(), checked=int(time.time())), time.time()
            finally:
                _lock.release()
        body = json.dumps(_cache["body"]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def listen(raw: str) -> list:
    """127.0.0.1 and the addresses of LLM_LISTEN (IPv4, comma or space separated, never 0.0.0.0); else ValueError."""
    extra = [a for a in re.split(r"[,\s]+", raw or "") if a]
    bad = [a for a in extra if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", a) or a == "0.0.0.0"]
    if bad:
        raise ValueError(f"LLM_LISTEN: {bad[0]} is not one IPv4 address of this host")
    return list(dict.fromkeys(["127.0.0.1"] + extra))


def serve(addr: str, retry_s: float = 10):
    """addr:PORT; an address not there yet (a Docker bridge made after this started) is tried again."""
    while True:
        try:
            srv = ThreadingHTTPServer((addr, PORT), H)
        except OSError as e:
            print(f"{addr}:{PORT}: {e}; again in {retry_s:g} s", flush=True)
            time.sleep(retry_s)
            continue
        srv.serve_forever()


if __name__ == "__main__":
    addrs = listen(os.environ.get("LLM_LISTEN", ""))
    for a in addrs[1:]:
        threading.Thread(target=serve, args=(a,), daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
