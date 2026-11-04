#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this, its arguments set at the top) to make it a Thread border router of
# the house (plug-in thread): docker when missing, the OpenThread border router (OTBR, image pinned by digest) in
# /opt/yashome-thread on the radio (RCP) $1 of /dev/serial/by-id at $2 baud, TREL and the backbone on the interface of the
# default route. Its REST (127.0.0.1:8081, no auth) and web page (127.0.0.1:8089) stay on the loopback: the house reaches the
# REST through the helper's ssh tunnel. The network (dataset) is the house's: the plug-in gives it after the start.
set -eu
RADIO="${1:?the radio: a name in /dev/serial/by-id}"
BAUD="${2:?the baud rate of the radio}"
IMAGE="openthread/otbr@sha256:4a64c957909b708041e3afb6f7663837476907d0fee80e0ec964f4eb5db8f186"
DIR=/opt/yashome-thread
NAME=yashome-otbr
fail() { echo "thread: $*" >&2; exit 1; }
get() {      # get <url> [timeout]: curl, or python3 where there is no curl
    if command -v curl >/dev/null 2>&1; then curl -fsS -m "${2:-60}" "$1"
    else python3 -c 'import sys, urllib.request; sys.stdout.buffer.write(urllib.request.urlopen(sys.argv[1], timeout=float(sys.argv[2])).read())' "$1" "${2:-60}"; fi
}
case "$RADIO" in */*|.*) fail "not a name in /dev/serial/by-id: $RADIO" ;; esac
[ -e "/dev/serial/by-id/$RADIO" ] || fail "no /dev/serial/by-id/$RADIO here: is the radio plugged in?"
IF=$(ip route 2>/dev/null | awk '/^default/{print $5; exit}')
[ -n "$IF" ] || fail "no default route: TREL and the backbone need the LAN interface"
if ! command -v docker >/dev/null 2>&1; then
    echo "installing docker (get.docker.com)"
    get https://get.docker.com > /tmp/get-docker.sh || fail "docker could not be downloaded"   # a file: a failed download never looks like success
    sh /tmp/get-docker.sh
fi
docker compose version >/dev/null 2>&1 || fail "docker compose is missing"
[ -S /run/systemd/journal/dev-log ] || fail "no systemd journal socket (/run/systemd/journal/dev-log) here"
# another OTBR on this computer holds the radio and the wpan0 interface: two would fight
other=$(docker ps --format '{{.Names}} {{.Image}}' | awk -v me="$NAME" '$1 != me && $1 != me "-routes" && $2 ~ /otbr/ {print $1}' | head -1)
[ -z "$other" ] || fail "another border router runs here (container $other): stop it first (README of plug-in thread: migration)"
# NAT44 of the OTBR uses iptables-legacy: these modules are not loaded on every host
for m in iptable_nat iptable_filter; do modprobe "$m" 2>/dev/null || echo "thread: could not load $m (NAT44 may not work)"; done
printf 'iptable_nat\niptable_filter\n' > /etc/modules-load.d/yashome-thread.conf
umask 077
mkdir -p "$DIR/data"
cd "$DIR"
touch syslog
echo "# yashome: no inputs, the router logs to the host journal (compose.yml)" > rsyslog.conf
cat > compose.yml <<EOF
# Thread border router of the house (plug-in thread, role "Thread border router"): written by helper/roles/thread-install.sh.
# Host network (it owns wpan0 and NAT64); its own ip6tables firewall is off (FIREWALL=0: it fails on nftables hosts).
services:
  otbr:
    image: $IMAGE
    container_name: $NAME
    restart: unless-stopped
    network_mode: host
    privileged: true
    tmpfs: [/run]
    labels:
      com.centurylinklabs.watchtower.enable: "false"
    volumes:
      - /dev:/dev
      # /dev is the host's: the image's rsyslogd would replace the host's /dev/log with its own socket. Without inputs
      # (rsyslog.conf) it leaves it alone, the host's link reaches the journal socket mounted below and the router logs
      # to the host journal (journalctl -t otbr-agent); the entrypoint waits for /var/log/syslog: an empty file
      - ./rsyslog.conf:/etc/rsyslog.conf:ro
      - /run/systemd/journal/dev-log:/run/systemd/journal/dev-log
      - ./syslog:/var/log/syslog
      - ./data:/var/lib/thread
      - ./routes.sh:/routes.sh:ro
    environment:
      DEBUG_LEVEL: "5"             # notice and worse: info/debug was ~2000 lines a minute in the host journal
      FIREWALL: "0"
      HTTP_HOST: 127.0.0.1
      HTTP_PORT: "8089"
      REST_HOST: 127.0.0.1
    # the routes guard (routes.sh loop) runs beside the router in the same container, then the image's own entrypoint
    entrypoint: ["/bin/sh", "-c", "sh /routes.sh loop & exec /app/etc/docker/test/docker_entrypoint.sh \"\$\$@\"", "otbr"]
    command:
      - --radio-url
      - spinel+hdlc+uart:///dev/serial/by-id/$RADIO?uart-baudrate=$BAUD
      - --trel-url
      - trel://$IF
      - --backbone-interface
      - $IF
EOF
printf '#!/bin/sh\nBB=%s\n' "$IF" > routes.sh
cat >> routes.sh <<'EOF'
# routes.sh [loop]: each border router announces the shared Thread prefixes on the LAN; the other takes that RA route
# (kernel: OTBR sets accept_ra_rt_info_max_plen=64; NetworkManager: always, metric 105) over its own wpan0 (256) and the
# two loop the packets (NAT64 answers never reach a node). Sets the sysctl back to 0 and pins every wpan0 prefix that also
# has an RA route at metric PIN (gone with wpan0); deleting the RA route is no cure, NetworkManager adds it again in
# seconds. Prints only what it changed. loop: every 10 s (beside the router, in its container).
set -u
CONF="${ROUTES_CONF:-/proc/sys/net/ipv6/conf}"
PIN=10
fix() {
    f="$CONF/$BB/accept_ra_rt_info_max_plen"
    if [ -w "$f" ] && [ "$(cat "$f")" != 0 ]; then echo 0 > "$f" && echo "routes: $BB accept_ra_rt_info_max_plen -> 0"; fi
    own=$(ip -6 route show dev wpan0 2>/dev/null) || return 0
    ip -6 route show dev "$BB" proto ra | awk '$2 == "via" && $1 != "default" {print $1}' | sort -u | while read -r p; do
        echo "$own" | awk -v p="$p" '$1 == p {n++} END {exit !n}' || continue
        echo "$own" | awk -v p="$p" -v m="$PIN" '$1 == p {for (i = 2; i < NF; i++) if ($i == "metric" && $(i + 1) == m) n++} END {exit !n}' && continue
        ip -6 route add "$p" dev wpan0 proto static metric "$PIN" && echo "routes: $p pinned to wpan0 (an RA route to it via $BB)"
    done
}
if [ "${1:-}" = loop ]; then
    while :; do fix; sleep 10; done
fi
fix
EOF
cat > ctl.sh <<'EOF'
#!/bin/sh
# ctl.sh status | restart | leaderweight <0-255> | joiner | pair <IPv6>: what the house may do here (the helper runs it with sudo, nothing
# else). status: runs routes.sh and forward, then key=value lines (routes_fixed, running, state, leaderweight, trel_peers,
# srp=<host> <address> per SRP host this router serves), no dataset, no key. joiner: "<eui64|*> <PSKd>" on stdin (never on the
# helper's command line): the commissioner lets that device in for 180 s.
set -u
cd /opt/yashome-thread || exit 1
ot() { timeout 10 docker exec yashome-otbr ot-ctl "$@" 2>/dev/null | tr -d '\r'; }
# Docker sets FORWARD to DROP: the nodes' NAT64 packets to the MQTT broker on the LAN die on this router. forward lets
# only that through (TCP 1883 from wpan0 into the LAN of the backbone) and the answers back; prints what it added.
# No chain DOCKER-USER: no docker rule, nothing to do. unforward takes the two rules away (the role's remove).
fwd_rules() {
    bb=$(sed -n 's/^BB=//p' routes.sh)
    lan=$(ip -4 route show dev "$bb" scope link 2>/dev/null | awk 'NR == 1 {print $1}')
    command -v iptables >/dev/null 2>&1 && [ -n "$lan" ] && iptables -S DOCKER-USER >/dev/null 2>&1
}
forward() {
    fwd_rules || return 0
    iptables -C DOCKER-USER -i wpan0 -o "$bb" -d "$lan" -p tcp --dport 1883 -j ACCEPT 2>/dev/null ||
        { iptables -I DOCKER-USER -i wpan0 -o "$bb" -d "$lan" -p tcp --dport 1883 -j ACCEPT && echo "forward: MQTT from wpan0 to $lan let through"; }
    iptables -C DOCKER-USER -i "$bb" -o wpan0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null ||
        { iptables -I DOCKER-USER -i "$bb" -o wpan0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT && echo "forward: answers to wpan0 let through"; }
}
case "${1:-}" in
status)
    fixed=$(sh ./routes.sh; forward)
    [ -z "$fixed" ] || echo "$fixed" | logger -t yashome-thread
    echo "routes_fixed=$(printf '%s' "$fixed" | grep -c '^routes:')"
    r=$(docker inspect -f '{{.State.Running}}' yashome-otbr 2>/dev/null || echo false)
    echo "running=$r"
    [ "$r" = true ] || exit 0
    echo "state=$(ot state | head -1)"
    echo "leaderweight=$(ot leaderweight | head -1)"
    echo "trel_peers=$(ot trel peers | grep -c '^| *[0-9]')"
    ot srp server host | awk '/^[^ ]/ {h = $1; sub(/[.].*/, "", h)} /deleted: true/ {h = ""}
        /addresses:/ && h != "" {gsub(/[][,]/, " "); for (i = 2; i <= NF; i++) print "srp=" h " " $i}' ;;
restart)
    timeout 120 docker compose restart && echo restarted ;;
pair)
    # pair <IPv6>: the house's pair request (a form on stdin, docs/pairing.md) to a Thread node's /yash/pair; prints
    # pair_status=<HTTP code, 0 = not reached> and pair_reply=<its answer>. The form is never on a command line.
    echo "${2:-}" | grep -Eq '^[0-9a-f:]{2,39}$' || { echo "an IPv6 address" >&2; exit 1; }
    python3 -c 'import sys, json, urllib.request, urllib.error
r = urllib.request.Request("http://[" + sys.argv[1] + "]/yash/pair", data=sys.stdin.buffer.read(4096),
                           headers={"Content-Type": "application/x-www-form-urlencoded"})
try:
    x = urllib.request.urlopen(r, timeout=25)
    c, b = x.status, x.read(600)
except urllib.error.HTTPError as e:
    c, b = e.code, e.read(600)
except Exception as e:
    c, b = 0, json.dumps({"ok": False, "error": str(e)[:120]}).encode()
print("pair_status=%d" % c)
print("pair_reply=" + b.decode("utf-8", "replace").replace("\n", " "))' "$2" ;;
unforward)
    fwd_rules || exit 0
    iptables -D DOCKER-USER -i wpan0 -o "$bb" -d "$lan" -p tcp --dport 1883 -j ACCEPT 2>/dev/null
    iptables -D DOCKER-USER -i "$bb" -o wpan0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null; exit 0 ;;
leaderweight)
    case "${2:-}" in ''|*[!0-9]*) echo "a number 0-255" >&2; exit 1 ;; esac
    [ "$2" -le 255 ] || { echo "a number 0-255" >&2; exit 1; }
    ot leaderweight "$2" | head -1 ;;
joiner)
    read -r eui pskd || exit 1
    echo "$eui" | grep -Eq '^([0-9a-f]{16}|[*])$' || { echo "eui: 16 hex digits or *" >&2; exit 1; }
    echo "$pskd" | grep -Eq '^[0-9A-HJ-NPR-Y]{6,32}$' || { echo "not a PSKd" >&2; exit 1; }
    ot commissioner start >/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do ot commissioner state | grep -q '^active' && break; sleep 1; done
    ot commissioner joiner add "$eui" "$pskd" 180 | grep -q '^Done' && echo "joiner added" ;;
*)
    echo "status | restart | leaderweight <n> | joiner | pair <IPv6> | unforward" >&2; exit 1 ;;
esac
EOF
chmod 700 ctl.sh routes.sh
chmod 755 "$DIR"                 # the probe (not root) sees compose.yml: the role is there; the network data stays root's
chmod 644 compose.yml syslog rsyslog.conf
docker compose up -d --remove-orphans || fail "not running: fix the cause above, then  docker compose up -d  in $DIR"
for _ in $(seq 1 30); do
    get http://127.0.0.1:8081/node/state 3 >/dev/null 2>&1 && { ./ctl.sh status >/dev/null || true; echo "the border router is up on $IF (radio $RADIO): the house gives it the network"; exit 0; }
    sleep 2
done
fail "it started but its REST does not answer on 127.0.0.1:8081 (the radio? journalctl -t otbr-agent)"
