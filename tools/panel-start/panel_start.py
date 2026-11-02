#!/usr/bin/env python3
"""Host side of the panels' launcher watchdog: one allowlisted action, "start-launcher <tablet id>".

systemd starts it once per connection (yashome-panel-start.socket on /run/yashome/panel-start.sock, Accept=yes, its group
the house's: who may write to it may ask) with the socket as stdin and stdout, as the house's user (its adb keys). It reads
one line (5 s, 256 bytes at most), finds the tablet's address in the house's paired.json itself, accepts only a private address of the house LAN, and runs only `adb [connect] ... am start` of
the launcher, 30 s at most in all. One JSON line answers: {"ok"}, {"ok": false, "why"}, {"noadb", "why"} or {"refused", "why"}."""
import ipaddress
import json
import os
import re
import select
import subprocess
import sys
import time

VERB = "start-launcher"
HOME = ["-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME"]   # the launcher the owner pinned, any package
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
LINE_MAX = 256
READ_S = 5
BUDGET_S = 30                       # every adb call together; the plug-in waits 35 s
NOT_LAN = re.compile(r"^(lo|docker|br-|veth|wg|tun|tap|virbr)")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read_env(path: str) -> dict:
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def lan_nets(ip_out: str | None = None) -> list:
    """The subnets of this host's own interfaces that are the house LAN: private IPv4, no docker bridge, VPN or loopback."""
    if ip_out is None:
        ip_out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5).stdout
    nets = []
    for line in ip_out.splitlines():
        f = line.split()
        if len(f) >= 4 and f[2] == "inet" and not NOT_LAN.match(f[1]):
            n = ipaddress.ip_network(f[3], strict=False)
            if n.is_private:
                nets.append(n)
    return nets


def in_lan(ip: str, nets: list) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and a.is_private and any(a in n for n in nets)


def run_adb(args: list, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=max(1.0, timeout))


def serial_for(ip: str, port, deadline: float):
    """The adb serial of the tablet at ip: one the host's adb server holds, else after `adb connect` on 5555 and its own port."""
    def listed():
        r = run_adb(["devices"], deadline - time.monotonic())
        return next((ln.split()[0] for ln in r.stdout.splitlines()[1:]
                     if ln.endswith("\tdevice") and ln.split(":")[0] == ip), None)
    s = listed()
    for p in dict.fromkeys(x for x in (5555, port) if isinstance(x, int) and 0 < x < 65536):
        if s or time.monotonic() >= deadline:
            break
        run_adb(["connect", f"{ip}:{p}"], min(10, deadline - time.monotonic()))
        s = listed()
    return s


def handle(line: str, paired: dict, nets: list) -> dict:
    parts = line.split()
    if len(parts) != 2 or parts[0] != VERB:
        return {"refused": True, "why": "usage: start-launcher <tablet id>"}
    _, dev_id = parts
    rec = paired.get(dev_id) if ID_RE.fullmatch(dev_id) else None
    if not isinstance(rec, dict) or rec.get("status") != "bound":
        return {"refused": True, "why": "unknown tablet"}
    ip = str(rec.get("ip") or "")
    if not in_lan(ip, nets):
        return {"refused": True, "why": "the tablet's address is not in the house LAN"}
    deadline = time.monotonic() + BUDGET_S
    try:
        serial = serial_for(ip, ((rec.get("caps") or {}).get("adb") or {}).get("port"), deadline)
        if not serial:
            return {"noadb": True, "why": f"adb has no connection to {ip}"}
        r = run_adb(["-s", serial, "shell", "am", "start", *HOME], deadline - time.monotonic())
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"noadb": True, "why": f"{type(e).__name__}"}
    out = (r.stdout + r.stderr).strip()
    ok = r.returncode == 0 and "Error" not in out
    return {"ok": ok, **({} if ok else {"why": out[-200:] or f"exit {r.returncode}"})}


def read_line(fd: int) -> str | None:
    """One line of at most LINE_MAX bytes within READ_S seconds; None when it is longer, late or not text."""
    buf, end = b"", time.monotonic() + READ_S
    while b"\n" not in buf and len(buf) <= LINE_MAX:
        left = end - time.monotonic()
        if left <= 0 or not select.select([fd], [], [], left)[0]:
            return None
        chunk = os.read(fd, LINE_MAX + 1 - len(buf))
        if not chunk:
            break
        buf += chunk
    line = buf.split(b"\n", 1)[0]
    if len(line) > LINE_MAX:
        return None
    try:
        return line.decode("ascii")
    except UnicodeDecodeError:
        return None


def main() -> int:
    env = read_env(os.path.join(REPO, ".env"))
    line = read_line(sys.stdin.fileno())
    if line is None:
        ans = {"refused": True, "why": "one line of 256 bytes within 5 s"}
    else:
        house = env.get("HOUSE_DIR") or REPO
        try:
            paired = json.load(open(os.path.join(house, "plugin-state", "panels", "paired.json"), encoding="utf-8"))
        except (OSError, ValueError):
            paired = {}
        ans = handle(line, paired if isinstance(paired, dict) else {}, lan_nets())
    sys.stdout.write(json.dumps(ans) + "\n")
    sys.stdout.flush()
    print(f"panel-start: {ans}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
