#!/usr/bin/env python3
"""Wake-on-LAN relay of the house: a container on Docker's bridge network cannot broadcast to the LAN, so the house sends one
datagram "WOL <mac>" to /run/yashome/wol.sock (systemd hands the socket over: yashome-wol.socket, its group the house's),
and this sends the magic packet from the host's own network card. Who may write to the socket file may wake any MAC;
at most RATE requests in PER seconds."""
import os
import re
import socket
import sys
import time

MAC_RE = re.compile(r"^[0-9a-fA-F]{2}([:-]?)[0-9a-fA-F]{2}(\1[0-9a-fA-F]{2}){4}$")
RATE, PER = 10, 10.0                                  # requests in this many seconds
TARGETS = ("255.255.255.255",)
LISTEN_FD = 3                                         # the first socket systemd passes (sd_listen_fds)


def packet(datagram: bytes):
    """The magic packet for a "WOL <mac>" datagram, else None."""
    parts = datagram.decode(errors="replace").split()
    if len(parts) != 2 or parts[0] != "WOL" or not MAC_RE.match(parts[1]):
        return None
    return b"\xff" * 6 + bytes.fromhex(re.sub("[:-]", "", parts[1])) * 16


class Relay:
    """One datagram in, the packets out: at most RATE requests in PER seconds, each to every target on ports 9 and 7; a
    send that fails is logged and the next one still goes."""

    def __init__(self, targets, sendto, log=print):
        self.targets, self.sendto, self.log, self.recent = targets, sendto, log, []

    def handle(self, data: bytes, now: float) -> int:
        pkt = packet(data)
        if pkt is None:
            return 0
        self.recent = [t for t in self.recent if now - t < PER]
        if len(self.recent) >= RATE:
            return 0
        self.recent.append(now)
        sent, errors = 0, []
        for t in self.targets:
            for p in (9, 7):
                try:
                    self.sendto(pkt, (t, p))
                    sent += 1
                except OSError as e:
                    errors.append(f"{t}:{p}: {e}")
        self.log(f"WOL {data.decode().split()[1]}: {sent} sent" + (f", failed {'; '.join(errors)}" if errors else ""))
        return sent


def main():
    if os.environ.get("LISTEN_FDS") != "1":
        print("wol-relay: start it through yashome-wol.socket (tools/host-sockets.sh)", file=sys.stderr)
        return 2
    srv = socket.socket(fileno=LISTEN_FD)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    out.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    relay = Relay(TARGETS, out.sendto, lambda m: print(m, flush=True))
    while True:
        relay.handle(srv.recv(64), time.monotonic())


if __name__ == "__main__":
    sys.exit(main())
