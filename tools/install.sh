#!/bin/sh
# make install: write .env (only the keys that are still empty), derive the broker accounts,
# start the containers, print where the dashboard is. Safe to re-run.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { cp secrets.env.example .env; chmod 600 .env; echo "created .env from secrets.env.example"; }
get() { sed -n "s/^$1=\(.*\)$/\1/p" .env | head -1; }
put() {   # put KEY VALUE - sets the key if it is empty or missing; never touches a filled one
    cur=$(get "$1")
    [ -n "$cur" ] && return 0
    if grep -q "^$1=" .env; then
        tmp=$(mktemp); awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k && $2==""{$2=v} {print}' .env > "$tmp" && cat "$tmp" > .env && rm -f "$tmp"
    else printf '%s=%s\n' "$1" "$2" >> .env; fi
    echo "  $1 <- ${3:-set}"
}
echo "== .env"
put DASHBOARD_TOKEN "$(openssl rand -hex 24)" generated
put MQTT_HOST mosquitto default
put MQTT_USER home default
put MQTT_PASSWORD "$(openssl rand -base64 18 | tr -d '/+=')" generated
put MQTT_Z2M_USER z2m default
put MQTT_Z2M_PASSWORD "$(openssl rand -base64 18 | tr -d '/+=')" generated
put Z2M_AUTH_TOKEN "$(openssl rand -hex 8)" generated
put DASHBOARD_PORT 8765 default
put MQTT_PORT 1883 default
put Z2M_PORT 8080 default
put PUID "$(id -u)" "$(id -u)"
put PGID "$(id -g)" "$(id -g)"
# time zone of the host; edit .env to change
put TZ "$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)" "from the host"
# zigbee coordinator (Z2M_SERIAL=none in .env = deliberately without Zigbee)
if [ -z "$(get Z2M_SERIAL)" ]; then
    set -- /dev/serial/by-id/*; n=$#; [ -e "$1" ] || n=0
    if [ "$n" = 1 ]; then put Z2M_SERIAL "$1" "$1"
    elif [ "$n" -gt 1 ] && [ -t 0 ]; then
        echo "  several serial devices - which one is the Zigbee coordinator? (Enter = none, run without Zigbee)"
        i=1; for d in "$@"; do echo "    $i) $d"; i=$((i+1)); done
        printf '  > '; read -r ans
        case "$ans" in
            ""|*[!0-9]*) ;;                                   # empty or not a number: no Zigbee
            *) [ "$ans" -ge 1 ] && [ "$ans" -le "$n" ] && { shift $((ans-1)); put Z2M_SERIAL "$1" chosen; };;
        esac
    elif [ "$n" -gt 1 ]; then echo "  Z2M_SERIAL: $n serial devices and no terminal to ask - starting without Zigbee (set it in .env, then 'make update')"
    else echo "  Z2M_SERIAL: no coordinator found - starting without Zigbee (set it in .env later, then 'make update')"; fi
fi
z2m_serial=$(get Z2M_SERIAL | tr -d "\"'" | tr 'A-Z' 'a-z')   # read like tools/secrets.sh does
case "$z2m_serial" in
    ""|none) echo "  zigbee2mqtt stays off (Z2M_SERIAL empty or 'none')";;
    external) echo "  zigbee2mqtt runs elsewhere on this broker (Z2M_SERIAL=external)";;
    *) echo "  zigbee2mqtt on $(get Z2M_SERIAL)";;   # COMPOSE_PROFILES follows in tools/secrets.sh
esac
echo "== device and rule files"
[ -f mqtt_devices.json ] || { cp examples/mqtt_devices.json mqtt_devices.json; echo "  mqtt_devices.json <- examples/"; }
[ -f settings.json ] || { cp examples/settings.json settings.json; echo "  settings.json <- examples/"; }
mkdir -p config/mosquitto/data config/zigbee2mqtt
echo "== broker accounts and zigbee2mqtt config"
sh tools/secrets.sh
echo "== docker compose up"
docker compose up -d --build --remove-orphans
docker compose exec -T mosquitto kill -HUP 1 >/dev/null 2>&1 || true
# the LAN address, not a docker bridge
port=$(get DASHBOARD_PORT); host=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')
# no default route (offline install): the first global address on an interface that is not a
# docker / VM bridge (judged by the interface, not the range: a LAN may well be 172.16-31.x)
[ -n "$host" ] || host=$(ip -4 -o addr show scope global 2>/dev/null \
    | awk '$2 !~ /^(lo$|docker|br-|veth|virbr|cni|flannel|podman)/ {split($4, a, "/"); print a[1]; exit}' || true)
[ -n "$host" ] || host=localhost
for i in 1 2 3 4 5 6 7 8 9 10; do curl -fsS -m 2 "http://127.0.0.1:${port:-8765}/" >/dev/null 2>&1 && break; sleep 2; done
echo
echo "dashboard:  http://$host:${port:-8765}"
echo "token:      $(get DASHBOARD_TOKEN)      (paste it once when the page asks; it is DASHBOARD_TOKEN in .env)"
case "$z2m_serial" in
    ""|none|external) ;;                     # no zigbee2mqtt container here: no frontend to point at
    *) echo "zigbee2mqtt: http://$host:$(get Z2M_PORT 2>/dev/null || echo 8080)   login: Z2M_AUTH_TOKEN from .env";;
esac
echo "next:       'make test' checks the stack, 'make update' applies .env or code changes"
