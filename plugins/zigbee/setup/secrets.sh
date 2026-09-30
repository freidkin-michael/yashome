#!/bin/sh
# make secrets, zigbee: its data directory (made here so it is yours, not root's) and the compose
# profile "zigbee" in step with Z2M_SERIAL - other profiles stay; secret.yaml comes from secrets.py.
set -eu
. tools/envlib.sh
if [ -f config/zigbee2mqtt/configuration.yaml ]; then put Z2M_DATA_DIR ./config/zigbee2mqtt "kept: the network is there"
else put Z2M_DATA_DIR ./plugin-data/zigbee/zigbee2mqtt default; fi
dir=$(get Z2M_DATA_DIR | sed 's#//*#/#g; s#/\.$##; s#/$##')
case "/${dir#./}/" in */./*|*/../*) echo "Z2M_DATA_DIR: no . or .. parts ($dir)" >&2; exit 1;; esac
case "$dir" in *'$'*) echo "Z2M_DATA_DIR: no \$ in it ($dir)" >&2; exit 1;; esac
case "$dir" in
    ./config/mosquitto|./config/mosquitto/*) echo "Z2M_DATA_DIR: not the broker's directory ($dir)" >&2; exit 1;;
    ./config/?*|./plugin-data/?*) ;;
    *) echo "Z2M_DATA_DIR must be a directory under ./config/ or ./plugin-data/ ($dir)" >&2; exit 1;;
esac
[ "$dir" = "$(get Z2M_DATA_DIR)" ] || env_set Z2M_DATA_DIR "$dir"
mkdir -p "$dir"
case "$(get Z2M_SERIAL | tr -d "\"'" | tr 'A-Z' 'a-z')" in ""|none|external) want="" ;; *) want=zigbee ;; esac
other=$(get COMPOSE_PROFILES | tr -d "\"' " | tr ',' '\n' | grep -vx 'zigbee' | grep . | paste -sd, - || true)
profiles=$(printf '%s\n%s\n' "$other" "$want" | grep . | paste -sd, - || true)
[ "$(get COMPOSE_PROFILES)" = "$profiles" ] || env_set COMPOSE_PROFILES "$profiles"
if [ -n "$want" ]; then echo "zigbee2mqtt: enabled (Z2M_SERIAL=$(get Z2M_SERIAL))"
else echo "zigbee2mqtt: off (Z2M_SERIAL empty, none or external)"; fi
