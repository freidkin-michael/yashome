#!/bin/bash
# Phones on the house Wi-Fi -> MQTT presence/state, presence/unseen_days, presence/event; settings from
# the environment (host/presence.env.example). The broker password goes through a private options file.
set -u
[ -n "${PRESENCE_MACS:-}" ] || { echo "presence: PRESENCE_MACS is empty (the phones Wi-Fi MACs, comma separated)" >&2; exit 2; }
[ -n "${MQTT_PRESENCE_PASSWORD:-}" ] || { echo "presence: MQTT_PRESENCE_PASSWORD is empty" >&2; exit 2; }
MACS=$(echo "$PRESENCE_MACS" | tr 'A-Z,' 'a-z ')
for m in $MACS; do
    echo "$m" | grep -Eq '^([0-9a-f]{2}:){5}[0-9a-f]{2}$' || { echo "presence: not a MAC: $m" >&2; exit 2; }
done
IFACE=${PRESENCE_IFACE:-$(ip route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}')}
[ -n "$IFACE" ] || { echo "presence: no interface (no default route yet?) - set PRESENCE_IFACE" >&2; exit 1; }
GRACE=${PRESENCE_GRACE:-600}
INTERVAL=${PRESENCE_INTERVAL:-30}
SWEEP_MIN_GAP=${PRESENCE_SWEEP_GAP:-300}
STATE_DIR=${STATE_DIRECTORY:-${PRESENCE_STATE_DIR:-/var/lib/phone-presence}}
RUN_DIR=${RUNTIME_DIRECTORY:-${XDG_RUNTIME_DIR:-$STATE_DIR}}
mkdir -p "$STATE_DIR" "$RUN_DIR"
umask 077
printf -- '-h %s\n-p %s\n-u %s\n-P %s\n' "${MQTT_HOST:-localhost}" "${MQTT_PORT:-1883}" \
    "${MQTT_PRESENCE_USER:-presence}" "$MQTT_PRESENCE_PASSWORD" \
    > "$RUN_DIR/mosquitto_pub"
export XDG_CONFIG_HOME="$RUN_DIR"

pub() { mosquitto_pub "$@" 2>/dev/null || echo "presence: publish failed ($*)" >&2; }

subnet_scan() {
    local net i
    net=$(ip -4 addr show "$IFACE" 2>/dev/null | awk '/inet /{split($2, a, "."); print a[1]"."a[2]"."a[3]; exit}')
    [ -n "$net" ] || return
    for i in $(seq 1 254); do
        ping -c1 -W1 -n "$net.$i" >/dev/null 2>&1 &
        [ $((i % 64)) -eq 0 ] && wait
    done
    wait
}

ip_of() {
    ip -4 neigh show dev "$IFACE" 2>/dev/null | awk -v m="$1" 'tolower($0) ~ m && $0 !~ /INCOMPLETE|FAILED/ {print $1; exit}'
}

seen_mac() {    # arping first: L2 gets through Android doze; ICMP only with the MAC confirmed
    local ip
    ip=$(ip_of "$1")
    [ -n "$ip" ] || return 1
    arping -I "$IFACE" -c 2 -w 3 "$ip" 2>/dev/null | grep -qi "$1" && return 0
    ping -c1 -W2 -n "$ip" >/dev/null 2>&1 && [ "$(ip_of "$1")" = "$ip" ]
}

since=$(cat "$STATE_DIR/since" 2>/dev/null)
case "$since" in ''|*[!0-9]*) since=$(date +%s); echo "$since" > "$STATE_DIR/since";; esac
last_seen=$(cat "$STATE_DIR/last_seen" 2>/dev/null)
case "$last_seen" in ''|*[!0-9]*) last_seen=0 any_seen=$since;; *) any_seen=$last_seen;; esac
state="" last_sweep=0 refresh=0
while true; do
    now=$(date +%s) seen=0
    for m in $MACS; do seen_mac "$m" && { seen=1; break; }; done
    if [ "$seen" = 0 ] && [ $((now - last_sweep)) -ge "$SWEEP_MIN_GAP" ]; then
        last_sweep=$now
        subnet_scan
        for m in $MACS; do seen_mac "$m" && { seen=1; break; }; done
    fi
    [ "$seen" = 1 ] && { last_seen=$now any_seen=$now; echo "$now" > "$STATE_DIR/last_seen"; }
    if [ $((now - last_seen)) -lt "$GRACE" ]; then ns=ON; else ns=OFF; fi
    if [ "$ns" != "$state" ]; then
        [ "$state" = OFF ] && [ "$ns" = ON ] && pub -t presence/event -m arrival
        [ "$state" = ON ] && [ "$ns" = OFF ] && pub -t presence/event -m departure
        state=$ns refresh=10
    fi
    refresh=$((refresh + 1))
    if [ "$refresh" -ge 10 ]; then
        pub -r -t presence/state -m "$state"
        pub -r -t presence/unseen_days -m $(( (now - any_seen) / 86400 ))
        refresh=0
    fi
    sleep "$INTERVAL"
done
