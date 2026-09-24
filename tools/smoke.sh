#!/bin/sh
# make test (second half): is the running stack healthy? Broker answers with the backend's
# account, the backend answers with the token, zigbee2mqtt reports online when enabled.
set -u
cd "$(dirname "$0")/.." || exit 1
[ -f .env ] || { echo "no .env - nothing running yet"; exit 0; }
# read .env as data (KEY=value per line), never execute it: a value with spaces or $ stays a value
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue;; esac
    k=${line%%=*}; v=${line#*=}
    case "$k" in *[!A-Za-z0-9_]*|[0-9]*|'') continue;; esac   # a shell name: no leading digit
    v=${v#\"}; v=${v%\"}; v=${v#\'}; v=${v%\'}
    eval "$k=\$v"      # key checked above; a plain shell variable, not exported to children
done < .env
bad=0
# the password goes in on stdin and into a 0600 options file (-o): never into argv or the
# environment, where `ps` on the host or in the container would show it
if printf '%s' "$MQTT_PASSWORD" | docker compose exec -T mosquitto sh -c 'f=$(mktemp); printf -- "-P %s\n" "$(cat)" > "$f"; mosquitto_sub -o "$f" -u "'"$MQTT_USER"'" -t "\$SYS/broker/version" -C 1 -W 5; rc=$?; rm -f "$f"; exit $rc' >/dev/null 2>&1; then
    echo "  ok    broker answers to user $MQTT_USER"; else echo "  FAIL  broker does not accept user $MQTT_USER"; bad=1; fi
code=$(printf 'Authorization: Bearer %s\n' "$DASHBOARD_TOKEN" | curl -s -o /dev/null -w '%{http_code}' -H @- "http://127.0.0.1:${DASHBOARD_PORT:-8765}/api/health")
[ "$code" = 200 ] && echo "  ok    backend /api/health with the token" || { echo "  FAIL  backend /api/health -> $code"; bad=1; }
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${DASHBOARD_PORT:-8765}/api/devices")
[ "$code" = 401 ] && echo "  ok    /api refuses requests without the token" || { echo "  FAIL  /api without token -> $code (expected 401)"; bad=1; }
case ",${COMPOSE_PROFILES:-}," in *,zigbee,*) z2m=1;; *) z2m=0;; esac
if [ "$z2m" = 1 ]; then
    st=$(printf '%s' "$MQTT_PASSWORD" | docker compose exec -T mosquitto sh -c 'f=$(mktemp); printf -- "-P %s\n" "$(cat)" > "$f"; mosquitto_sub -o "$f" -u "'"$MQTT_USER"'" -t zigbee2mqtt/bridge/state -C 1 -W 10; rc=$?; rm -f "$f"; exit $rc' 2>/dev/null)
    case "$st" in *online*) echo "  ok    zigbee2mqtt online";; *) echo "  warn  zigbee2mqtt state: ${st:-no answer in 10 s} (first start takes a minute)";; esac
fi
[ "$bad" = 0 ] && echo "stack looks healthy" || exit 1
