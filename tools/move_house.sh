#!/bin/sh
# make move-house: move this checkout's house files into HOUSE_DIR, the backend stopped meanwhile.
# Refuses to overwrite anything; zigbee2mqtt's data stays where Z2M_DATA_DIR says; settings.json goes last.
set -eu
cd "$(dirname "$0")/.."
. tools/envlib.sh
h=$(house_dir)
[ "$h" != . ] || { echo "set HOUSE_DIR in .env to the new directory first" >&2; exit 1; }
z=$(get Z2M_DATA_DIR | tr -d "\"'")
z=${z:-./plugin-data/zigbee/zigbee2mqtt}
items=""
for f in mqtt_devices.json status_cache.json pending_timers.json pending_timers.corrupt-* settings.json.pre-split-* plugin-state; do
    [ -e "$f" ] && items="$items $f"
done
for d in plugin-data/*/; do
    [ -d "$d" ] || continue
    d=${d%/}
    case "$z" in "./$d"|"./$d"/*|"$d"|"$d"/*) echo "  $d stays here (zigbee2mqtt's data, Z2M_DATA_DIR)"; continue;; esac
    items="$items $d"
done
[ -e settings.json ] && items="$items settings.json"
[ -n "$items" ] || { echo "nothing to move: no house files in this checkout"; exit 0; }
if command -v docker >/dev/null 2>&1; then
    docker compose ps -q >/dev/null 2>&1 || { echo "docker compose cannot tell whether the backend runs: nothing moved" >&2; exit 1; }
    docker compose stop home >/dev/null 2>&1 || { echo "could not stop the backend: nothing moved" >&2; exit 1; }
    [ -z "$(docker compose ps -q --status running home 2>/dev/null)" ] || { echo "the backend still runs: nothing moved" >&2; exit 1; }
fi
for i in $items; do               # an empty directory there (a backend started on the empty house) is not in the way
    [ ! -d "$h/$i" ] || find "$h/$i" -depth -type d -empty -delete || true
    [ ! -e "$h/$i" ] || { echo "$h/$i exists already: nothing moved (the backend is stopped)" >&2; exit 1; }
done
mkdir -p "$h/plugin-data"
for i in $items; do mv "$i" "$h/$i"; echo "  $i -> $h/"; done
[ -f "$h/.gitignore" ] || cp examples/house.gitignore "$h/.gitignore"
echo "moved; now: make update"
