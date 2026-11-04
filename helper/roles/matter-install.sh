#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this) to make it the house's Matter controller (plug-in matter): docker when
# missing, python-matter-server (image pinned by digest) in /opt/yashome-matter, host network (Matter is IPv6 and mDNS; Thread
# nodes through the house's border routers), the computer's Bluetooth (hci0, BlueZ over the host's D-Bus) for new devices. Its
# WebSocket (127.0.0.1:5580) stays on the loopback: the house reaches it through the helper's ssh tunnel. The fabric (data/)
# stays here; the house gives it the Thread network after the start.
set -eu
IMAGE="ghcr.io/home-assistant-libs/python-matter-server@sha256:170aa093ce91c76cde4cc390918307590f0f5558fcec93f913af3cb019e6562a"   # 8.1.0
DIR=/opt/yashome-matter
NAME=yashome-matter
PORT=5580
fail() { echo "matter: $*" >&2; exit 1; }
get() {      # get <url> [timeout]: curl, or python3 where there is no curl
    if command -v curl >/dev/null 2>&1; then curl -fsS -m "${2:-60}" "$1"
    else python3 -c 'import sys, urllib.request; sys.stdout.buffer.write(urllib.request.urlopen(sys.argv[1], timeout=float(sys.argv[2])).read())' "$1" "${2:-60}"; fi
}
IF=$(ip route 2>/dev/null | awk '/^default/{print $5; exit}')
[ -n "$IF" ] || fail "no default route: Matter needs the LAN interface"
if ! command -v docker >/dev/null 2>&1; then
    echo "installing docker (get.docker.com)"
    get https://get.docker.com > /tmp/get-docker.sh || fail "docker could not be downloaded"   # a file: a failed download never looks like success
    sh /tmp/get-docker.sh
fi
docker compose version >/dev/null 2>&1 || fail "docker compose is missing"
# another Matter server here would answer on the same port and keep a fabric of its own
other=$(docker ps --format '{{.Names}} {{.Image}}' | awk -v me="$NAME" '$1 != me && $2 ~ /matter-server/ {print $1}' | head -1)
[ -z "$other" ] || fail "another Matter server runs here (container $other): stop it first"
if ! docker ps --format '{{.Names}}' | grep -qx "$NAME" && command -v ss >/dev/null 2>&1 && ss -ltn | awk '{print $4}' | grep -Eq "[:.]$PORT\$"; then
    fail "port $PORT is taken here: the controller needs it on 127.0.0.1"
fi
BT=""
if [ -e /sys/class/bluetooth/hci0 ] && [ -S /run/dbus/system_bus_socket ]; then
    BT=', "--bluetooth-adapter", "0"'
    echo "Bluetooth hci0: new devices pair over it (near this computer)"
else
    echo "matter: no Bluetooth (hci0 and the D-Bus system bus): only devices already on the network (a code shared from a phone app)"
fi
umask 077
mkdir -p "$DIR/data"
cd "$DIR"
cat > compose.yml <<EOF
# Matter controller of the house (plug-in matter, role "Matter controller"): written by helper/roles/matter-install.sh.
# Host network (IPv6, mDNS, Thread through the border routers); BlueZ over the host's D-Bus for Bluetooth pairing.
services:
  matter:
    image: $IMAGE
    container_name: $NAME
    restart: unless-stopped
    network_mode: host
    security_opt: ["apparmor=unconfined"]
    labels:
      com.centurylinklabs.watchtower.enable: "false"
    volumes:
      - ./data:/data
      - /run/dbus:/run/dbus:ro
    command: ["--storage-path", "/data", "--paa-root-cert-dir", "/data/credentials", "--listen-address", "127.0.0.1", "--port", "$PORT",
              "--primary-interface", "$IF"$BT]
EOF
chmod 755 "$DIR"                 # the probe (not root) sees compose.yml: the role is there; the fabric stays root's
chmod 644 compose.yml
docker compose up -d --remove-orphans || fail "not running: fix the cause above, then  docker compose up -d  in $DIR"
for _ in $(seq 1 90); do         # the first start fetches the device certificates: a minute or two
    get "http://127.0.0.1:$PORT/info" 3 >/dev/null 2>&1 && { echo "the Matter controller is up on $IF: the house connects to it"; exit 0; }
    sleep 2
done
fail "it started but does not answer on 127.0.0.1:$PORT (docker logs $NAME)"
