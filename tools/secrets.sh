#!/bin/sh
# make secrets: derive what the containers need from .env - the broker password file and
# zigbee2mqtt's secret.yaml (+ its configuration.yaml from the example on first run).
# Runs inside the home image so no password ever appears on a command line.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env - run 'make install' first" >&2; exit 1; }
mkdir -p config/mosquitto/data config/zigbee2mqtt
# zigbee2mqtt runs exactly when a coordinator is set: keep COMPOSE_PROFILES in step with
# Z2M_SERIAL, so setting the stick later and running `make update` starts it
serial=$(sed -n 's/^Z2M_SERIAL=\(.*\)$/\1/p' .env | head -1 | tr -d "\"'")
case "$(printf '%s' "$serial" | tr 'A-Z' 'a-z')" in ""|none|external) want="" ;; *) want=zigbee ;; esac
# other profiles the user added stay; only "zigbee" is switched on or off
other=$(sed -n 's/^COMPOSE_PROFILES=\(.*\)$/\1/p' .env | head -1 | tr -d "\"' " | tr ',' '\n' | grep -vx 'zigbee' | grep . | paste -sd, - || true)
profiles=$(printf '%s\n%s\n' "$other" "$want" | grep . | paste -sd, - || true)
# write next to .env and rename: a full disk leaves the old .env intact, never an empty one
tmp=$(mktemp ./.env.XXXXXX)
trap 'rm -f "$tmp"' EXIT INT TERM       # an abort must not leave a second copy of the secrets
chmod 600 "$tmp"
rc=0; grep -v '^COMPOSE_PROFILES=' .env > "$tmp" || rc=$?
[ "$rc" -le 1 ] || { echo "cannot write $tmp (disk full?): .env left as it was" >&2; exit 1; }   # 1 = no lines
if [ -n "$profiles" ]; then echo "COMPOSE_PROFILES=$profiles" >> "$tmp" || { echo "cannot write $tmp: .env left as it was" >&2; exit 1; }; fi
# a short write must never replace .env: every other line has to be in the copy
[ "$(grep -vc '^COMPOSE_PROFILES=' .env)" = "$(grep -vc '^COMPOSE_PROFILES=' "$tmp")" ] \
    || { echo "incomplete copy of .env (disk full?): .env left as it was" >&2; exit 1; }
mv "$tmp" .env
if [ -n "$want" ]; then echo "zigbee2mqtt: enabled (Z2M_SERIAL=$serial)"
else echo "zigbee2mqtt: off (Z2M_SERIAL empty or none)"; fi
docker compose run --rm --no-deps -T home python /app/tools/mkpasswd.py
