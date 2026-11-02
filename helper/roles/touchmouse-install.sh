#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this, its arguments set at the top) to give it the touchmouse receiver:
# the touchmouse plug-in's host files from the house (/plugins/touchmouse/host.tar.gz) - the receiver (a system unit,
# UDP 7709 -> a uinput mouse, private addresses only) and the typing helper (a user unit of SUDO_USER, the screen keyboard).
set -eu
HOUSE="${1:?the house's address}"
get() {      # curl, or python3 where there is no curl
    if command -v curl >/dev/null 2>&1; then curl -fsS "$1"
    else python3 -c 'import sys, urllib.request; sys.stdout.buffer.write(urllib.request.urlopen(sys.argv[1], timeout=60).read())' "$1"; fi
}
fail() { echo "touchmouse: $*" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || fail "python3 is missing"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
get "$HOUSE/plugins/touchmouse/host.tar.gz" | tar xzf - -C "$T" --no-same-owner
install -D -m 755 "$T/touchmouse-rx.py" /usr/local/lib/yashome/touchmouse-rx.py
install -m 755 "$T/touchmouse-type.py" /usr/local/lib/yashome/touchmouse-type.py
install -m 644 "$T/touchmouse-rx.service" /etc/systemd/system/touchmouse-rx.service
install -m 644 "$T/touchmouse-type.service" /etc/systemd/user/touchmouse-type.service
systemctl daemon-reload
systemctl enable touchmouse-rx >/dev/null 2>&1
systemctl restart touchmouse-rx || fail "the receiver does not start: journalctl -u touchmouse-rx"
U="${SUDO_USER:-}"
if [ -n "$U" ] && [ "$U" != root ] && [ -d "/run/user/$(id -u "$U")" ]; then
    UID_="$(id -u "$U")"
    as_user() { runuser -u "$U" -- env XDG_RUNTIME_DIR="/run/user/$UID_" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UID_/bus" "$@"; }
    as_user systemctl --user daemon-reload
    as_user systemctl --user enable touchmouse-type.service >/dev/null 2>&1 || true
    as_user systemctl --user restart touchmouse-type.service || echo "note: the typing helper does not start (journalctl --user -u touchmouse-type as $U)"
else
    echo "note: no session of the user now: the typing helper starts at its next login"
fi
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then   # the phones' taps from its LAN
    i=$(ip route 2>/dev/null | awk '/^default/{print $5; exit}')
    net=$(ip -o -f inet addr show "$i" 2>/dev/null | awk '{print $4; exit}')
    if [ -n "$net" ]; then
        net=$(python3 -I -c 'import ipaddress, sys; print(ipaddress.ip_interface(sys.argv[1]).network)' "$net")
        ufw allow from "$net" to any port 7709 proto udp comment yashome-touchmouse >/dev/null && echo "firewall: 7709/udp open to $net"
    fi
fi
echo "touchmouse receiver is up on $(hostname) (UDP 7709)"
