#!/bin/sh
# make restore FILE=backups/yashome-backup-....tar.gz: stop the house, put what is there now aside as
# *.pre-restore-<time> (nothing is deleted or overwritten), unpack the backup into its places, make update.
set -eu
cd "$(dirname "$0")/.."
f=${1:?usage: make restore FILE=<a yashome-backup-*.tar.gz>}
[ -f "$f" ] || { echo "no such file: $f" >&2; exit 1; }
top=$(tar tzf "$f" 2>/dev/null | cut -d/ -f1 | sort -u) || { echo "not a readable backup: $f" >&2; exit 1; }
echo "$top" | grep -qx 'yashome-backup.json' || {
    echo "no yashome-backup.json in $f: not a Yashome backup, or one made before backup format 1 (not restorable)" >&2; exit 1; }
for t in $top; do
    case "$t" in yashome-backup.json|house|env|zigbee2mqtt|plugins-extra) ;; *) echo "unknown part '$t' in $f: refused" >&2; exit 1;; esac
done
. tools/envlib.sh
stamp=$(date +%Y%m%d-%H%M%S)
umask 077
tmp=$(mktemp -d)
kept=""
stopped=0
done_ok=0
finish() {
    rm -rf "$tmp"
    [ -n "$kept" ] && { echo "kept aside:"; printf '  %s\n' $kept; }
    if [ "$done_ok" != 1 ]; then
        [ -z "$kept" ] || echo "NOT finished: move the files above back (drop .pre-restore-$stamp) to undo" >&2
        [ "$stopped" != 1 ] || echo "the house is stopped: make update starts it again" >&2
    fi
}
trap finish EXIT
trap 'exit 1' INT TERM
tar xzpf "$f" -C "$tmp"
val() {    # a key of the backup's .env, else of this one
    for e in "$tmp/env/.env" .env; do
        [ -f "$e" ] || continue
        v=$(sed -n "s/^$1=//p" "$e" | tail -1 | tr -d '\r' | sed 's/ #.*//; s/^["'\'']//; s/["'\'']$//')
        [ -n "$v" ] && { echo "$v"; return 0; }
    done
    return 0
}
z=$(val Z2M_DATA_DIR)
x=$(val PLUGINS_EXTRA_DIR)
x=${x:-./plugins-extra}
if [ -d "$tmp/zigbee2mqtt" ] && [ -z "$z" ]; then
    echo "the backup holds zigbee2mqtt's data but neither its .env nor this one has Z2M_DATA_DIR: set it here, nothing restored" >&2
    exit 1
fi
h=$(house_dir)
here=$(get HOUSE_DIR | tr -d "\"'")
echo "restore $(basename "$f") ($(date -r "$f" '+%Y-%m-%d %H:%M')): $(echo "$top" | tr '\n' ' ')"
if [ -t 0 ]; then printf 'stop the house and restore it? [y/N] '; read -r ok; [ "$ok" = y ] || exit 1; fi
docker compose stop || { echo "the house did not stop: nothing restored" >&2; exit 1; }
stopped=1
aside() {
    [ -e "$1" ] || [ -L "$1" ] || return 0
    [ ! -e "$1.pre-restore-$stamp" ] || { echo "$1.pre-restore-$stamp exists already" >&2; exit 1; }
    mv "$1" "$1.pre-restore-$stamp"
    kept="$kept $1.pre-restore-$stamp"
}
if [ -f "$tmp/env/.env" ]; then
    aside .env
    install -m 600 /dev/null .env
    { grep -v '^HOUSE_DIR=' "$tmp/env/.env" || [ $? = 1 ]; } >> .env
    [ -z "$here" ] || printf 'HOUSE_DIR=%s\n' "$here" >> .env
    chmod 600 .env
    [ "$(sed -n 's/^HOUSE_DIR=//p' "$tmp/env/.env" | tail -1 | tr -d "\"'")" = "$here" ] ||
        echo "  HOUSE_DIR kept as on this computer (${here:-empty}), not the backup's"
fi
mkdir -p "$h"
if [ "$h" -ef . ]; then
    names="settings.json mqtt_devices.json plugin-data plugin-state $(cd "$tmp/house" 2>/dev/null && ls -d settings.json.pre-split-* 2>/dev/null || true)"
else
    names=$(ls -A "$tmp/house" 2>/dev/null || true)
fi
for n in $names; do
    [ -e "$tmp/house/$n" ] || continue
    aside "$h/$n"
    cp -a "$tmp/house/$n" "$h/$n"
done
if [ -d "$tmp/zigbee2mqtt" ]; then aside "$z"; mkdir -p "$(dirname "$z")"; cp -a "$tmp/zigbee2mqtt" "$z"; fi
zr=${z#./}
if [ ! -d "$tmp/zigbee2mqtt" ] && [ -n "$zr" ] && [ -d "$tmp/house/$zr" ] && ! [ "$h" -ef . ] && ! [ -e "$h/$zr" -a "$h/$zr" -ef "$z" ]; then
    aside "$z"; mkdir -p "$(dirname "$z")"; cp -a "$tmp/house/$zr" "$z"     # a flat backup: zigbee2mqtt was inside the house
    rm -rf "${h:?}/$zr"
fi
if [ -d "$tmp/plugins-extra" ]; then aside "$x"; cp -a "$tmp/plugins-extra" "$x"; fi
done_ok=1
echo "restored; starting the house"
make update
