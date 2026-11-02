#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this) after any server was installed or removed there: the house's
# hello responder. One broadcast of the house ("? yashome 1", UDP 7708) is answered by every computer with the servers
# installed on it (the same checks as helper/host_probe.sh), so one request tells the house about all of them.
# Private addresses only; no secret in the answer. Re-running it updates the responder.
set -eu
command -v python3 >/dev/null 2>&1 || { echo "hello: python3 is missing" >&2; exit 1; }
mkdir -p /usr/local/lib/yashome
cat > /usr/local/lib/yashome/yashome-hello.py <<'PY'
#!/usr/bin/env python3
"""The house's hello responder: "? yashome 1" (UDP 7708, a broadcast of the house) -> a JSON answer to the asker:
{"yashome": 1, "name": <host name>, "servers": [...], "ports": {"touchmouse": 7709}}. Private askers only."""
import glob
import ipaddress
import json
import os
import shutil
import socket
import time

PORT = int(os.environ.get("YASHOME_HELLO_PORT", "7708"))
MAX_PER_S = 20


def servers():
    out = sorted(os.path.basename(d.rstrip("/"))[len("yashome-"):] for d in glob.glob("/opt/yashome-*/")
                 if os.path.isfile(os.path.join(d, "compose.yml")))
    try:
        with open("/opt/yashome-build/compose.yml", encoding="utf-8", errors="replace") as f:
            if any(line.startswith("  apk-builder:") for line in f):
                out.append("apk")
    except OSError:
        pass
    if os.path.isfile("/opt/yashome-player/ctl.py"):
        out.append("player")
    if os.path.isfile("/usr/local/lib/yashome/touchmouse-rx.py"):
        out.append("touchmouse")
    if shutil.which("ollama"):
        out.append("llm")
    return sorted(set(out))


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", PORT))
    second, count = 0, 0
    while True:
        data, addr = s.recvfrom(64)
        now = int(time.time())
        second, count = (second, count + 1) if now == second else (now, 1)
        if count > MAX_PER_S or data.strip() != b"? yashome 1":
            continue
        try:
            if not ipaddress.ip_address(addr[0]).is_private:
                continue
            msg = {"yashome": 1, "name": socket.gethostname()[:40], "servers": servers(), "ports": {"touchmouse": 7709}}
            s.sendto(json.dumps(msg).encode(), addr)
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    main()
PY
chmod 755 /usr/local/lib/yashome/yashome-hello.py
cat > /etc/systemd/system/yashome-hello.service <<'UNIT'
[Unit]
Description=The house's hello responder (UDP 7708: which servers of the house run here)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 -I /usr/local/lib/yashome/yashome-hello.py
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then   # its LAN may ask
    i=$(ip route 2>/dev/null | awk '/^default/{print $5; exit}')
    net=$(ip -o -f inet addr show "$i" 2>/dev/null | awk '{print $4; exit}')
    if [ -n "$net" ]; then
        net=$(python3 -I -c 'import ipaddress, sys; print(ipaddress.ip_interface(sys.argv[1]).network)' "$net")
        ufw allow from "$net" to any port 7708 proto udp comment yashome-hello >/dev/null && echo "firewall: 7708/udp open to $net"
    fi
fi
systemctl daemon-reload
systemctl enable yashome-hello >/dev/null 2>&1
systemctl restart yashome-hello || { echo "hello: does not start: journalctl -u yashome-hello" >&2; exit 1; }
echo "hello responder is up on $(hostname) (UDP 7708)"
