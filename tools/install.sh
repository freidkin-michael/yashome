#!/bin/sh
# make install: write .env (only the keys that are still empty), derive the broker accounts,
# start the containers, print where the dashboard is. Safe to re-run.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { cp secrets.env.example .env; chmod 600 .env; : > .plugins-detect; echo "created .env from secrets.env.example"; }
. tools/envlib.sh
echo "== .env"
put DASHBOARD_TOKEN "$(openssl rand -hex 24)" generated
put MQTT_HOST mosquitto default
put MQTT_USER home default
put MQTT_PASSWORD "$(openssl rand -base64 18 | tr -d '/+=')" generated
put DASHBOARD_PORT 8765 default
put MQTT_PORT 1883 default
put PUID "$(id -u)" "$(id -u)"
put PGID "$(id -g)" "$(id -g)"
# time zone of the host; edit .env to change
put TZ "$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)" "from the host"
# the install that created .env (until it got this far): a plug-in whose setup/detect.sh finds its hardware is on
if [ -f .plugins-detect ]; then
    on=""
    for d in ./plugins/*/; do
        [ -f "$d/setup/detect.sh" ] && PLUGIN_DIR=$d sh "$d/setup/detect.sh" >/dev/null 2>&1 && on="$on,$(basename "$d")"
    done
    [ -n "$on" ] && [ -z "$(get PLUGINS)" ] && put PLUGINS "${on#,}" "found: ${on#,}"
    rm -f .plugins-detect .plugins-detected
fi
# each plug-in's keys (setup/env.example): the missing ones are appended, then its install step fills them
echo "== device and rule files"
mkdir -p plugins-extra  # default PLUGINS_EXTRA_DIR; created here so it belongs to you, not to docker (root)
[ -f mqtt_devices.json ] || { cp examples/mqtt_devices.json mqtt_devices.json; echo "  mqtt_devices.json <- examples/"; }
[ -f settings.json ] || { cp examples/settings.json settings.json; echo "  settings.json <- examples/"; }
mkdir -p config/mosquitto/data
echo "== broker accounts and the plug-ins' files"
sh tools/secrets.sh
echo "== docker compose up"
docker compose up -d --build --remove-orphans
docker compose restart mosquitto >/dev/null   # accounts are read only at start (a HUP does not reload them)
plugin_stage update || true
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
export HOST="$host"; plugin_stage summary || true
echo "next:       'make test' checks the stack, 'make update' applies .env or code changes"
