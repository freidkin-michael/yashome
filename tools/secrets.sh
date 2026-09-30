#!/bin/sh
# make secrets: derive what the containers need from .env (the broker accounts; each plug-in its own
# files via setup/secrets.sh and setup/secrets.py) and list the plug-ins' setup/compose.yml in COMPOSE_FILE.
# The password work runs inside the home image, so no password ever appears on a command line.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env - run 'make install' first" >&2; exit 1; }
. tools/envlib.sh
plugin_dirs >/dev/null || exit 1
# an install from before the plug-ins had their own switch: it ran Zigbee when zigbee2mqtt had a network
if ! grep -q '^PLUGINS=' .env; then
    case "$(get Z2M_SERIAL | tr 'A-Z' 'a-z')" in ""|none) [ -f config/zigbee2mqtt/configuration.yaml ] && put PLUGINS zigbee "kept: zigbee2mqtt has a network here";;
        *) put PLUGINS zigbee "kept: Z2M_SERIAL is set";; esac
fi
plugin_env_merge
mkdir -p config/mosquitto/data
plugin_stage install    # fills what a plug-in switched on later still lacks (a password); safe to repeat
plugin_stage secrets
files=docker-compose.yml
for d in $(plugin_dirs); do
    [ -f "$d/setup/compose.yml" ] && files="$files:$d/setup/compose.yml"
done
[ "$files" = docker-compose.yml ] && files=""
[ "$(get COMPOSE_FILE)" = "$files" ] || env_set COMPOSE_FILE "$files"
docker compose run --rm --no-deps -T home python /app/tools/mkpasswd.py
