#!/bin/sh
# make test, zigbee: zigbee2mqtt reports online when it runs here (a warning only: the first start takes a minute).
. tools/envlib.sh
case ",$(get COMPOSE_PROFILES | tr -d "\"' ")," in *,zigbee,*) ;; *) exit 0;; esac
u=$(get MQTT_USER | tr -d "\"'")
st=$(get MQTT_PASSWORD | tr -d "\"'" | tr -d '\n' | docker compose exec -T mosquitto sh -c 'f=$(mktemp); printf -- "-P %s\n" "$(cat)" > "$f"; mosquitto_sub -o "$f" -u "'"${u:-home}"'" -t zigbee2mqtt/bridge/state -C 1 -W 10; rc=$?; rm -f "$f"; exit $rc' 2>/dev/null)
case "$st" in *online*) echo "  ok    zigbee2mqtt online";; *) echo "  warn  zigbee2mqtt state: ${st:-no answer in 10 s} (first start takes a minute)";; esac
