#!/bin/sh
# make secrets: derive what the containers need from .env (the broker accounts; each plug-in its own
# files via setup/secrets.sh and setup/secrets.py) and list the plug-ins' setup/compose.yml in COMPOSE_FILE.
# The password work runs inside the home image, so no password ever appears on a command line.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env - run 'make install' first" >&2; exit 1; }
. tools/envlib.sh
plugin_dirs >/dev/null || exit 1
house_check || exit 1
mkdir -p "$(house_dir)/plugin-state"    # before compose mounts them (docker would create them as root)
# the helper sees the checkout, the house, your plug-ins and zigbee2mqtt's data at their real paths (its
# jobs run docker compose and git from there; one inside the checkout is mounted with it) and a deploy key
mkdir -p .helper/none
here=$(pwd -P)
case "$here" in *:*) echo "the checkout's path holds ':' - docker cannot mount it for the helper" >&2; exit 1;; esac
outside() {   # $1 a directory, $2 a name: its real path, or a placeholder of its own when inside or missing
    mkdir -p ".helper/none-$2"
    _p=; [ -n "$1" ] && _p=$(cd "$1" 2>/dev/null && pwd -P) || :
    case "$_p" in *:*) echo "warning: $1 holds ':' - the helper will not see it" >&2; _p=;; esac
    case "$_p/" in /|"$here"/*) _p="$here/.helper/none-$2";; esac
    printf '%s\n' "$_p"
}
sync_key() { [ "$(get "$1")" = "$2" ] || env_set "$1" "$2"; }
sync_key YASHOME_DIR "$here"
sync_key YASHOME_HOUSE_ABS "$(outside "$(house_dir)" house)"
sync_key YASHOME_EXTRA_ABS "$(outside "$(get PLUGINS_EXTRA_DIR)" extra)"
# git over ssh: a deploy key and known_hosts of their own (README), never your ~/.ssh; a job has HOME=/tmp
[ -n "${YASHOME_JOB:-}" ] || sync_key YASHOME_SSH_DIR "$(outside "${XDG_CONFIG_HOME:-${HOME:-}/.config}/yashome/ssh" ssh)"
gid=$(stat -c %g /var/run/docker.sock 2>/dev/null || echo 65534)
[ "$(get DOCKER_GID)" = "$gid" ] || env_set DOCKER_GID "$gid"
put HELPER_KEY "$(python3 -c 'import secrets; print(secrets.token_hex(24))')" generated
# an install from before the plug-ins had their own switch: it ran Zigbee when zigbee2mqtt had a network
if ! grep -q '^PLUGINS=' .env; then
    case "$(get Z2M_SERIAL | tr 'A-Z' 'a-z')" in ""|none) [ -f config/zigbee2mqtt/configuration.yaml ] && put PLUGINS zigbee "kept: zigbee2mqtt has a network here";;
        *) put PLUGINS zigbee "kept: Z2M_SERIAL is set";; esac
fi
backend_only_keep
plugin_env_merge
mkdir -p config/mosquitto/data
plugin_stage install    # fills what a plug-in switched on later still lacks (a password); safe to repeat
plugin_stage secrets
sync_key YASHOME_Z2M_ABS "$(outside "$(get Z2M_DATA_DIR)" z2m)"    # the zigbee plug-in sets it just above
files=docker-compose.yml
for d in $(plugin_dirs); do
    [ -f "$d/setup/compose.yml" ] && files="$files:$d/setup/compose.yml"
done
# the house's own additions (networks of a reverse proxy, an alias): <HOUSE_DIR>/compose.override.yml
house=$(house_dir)
[ -f "${house:-.}/compose.override.yml" ] && files="$files:${house:-.}/compose.override.yml"
[ "$files" = docker-compose.yml ] && files=""
[ "$(get COMPOSE_FILE)" = "$files" ] || env_set COMPOSE_FILE "$files"
docker compose run --rm --no-deps -T home python /app/tools/mkpasswd.py
