#!/bin/sh
# yashGen (plug-in computers, role yashgen) where the house uses it: <house>/plugin-data/computers/yashgen.json {host, user},
# written by Use (Settings -> yashGen, or the computer's card); the .path unit restarts this one when it changes. Empty: this
# host's own builder runs (YASHGEN_LOCAL_UNIT, a user unit). A computer: that unit stops, the builder's code (YASHGEN_SRC
# without its tests, outputs and data), its data (projects, own secrets, pairing codes) and the house's node YAMLs (no
# secrets) go to its /opt/yashome-yashgen, start.sh there gets the builder's token and the house's ESP secrets on stdin (they
# stay in its memory), then an ssh tunnel brings it to YASHGEN_LISTEN, the address the front door proxies yashGen to: the
# front door does not change. yashgen-stop.sh (ExecStopPost) brings the data back and stops it there.
set -eu
HOUSE_DIR="${HOUSE_DIR:?the house folder}"
KEY="${YASHOME_SSH_KEY:-$HOME/.config/yashome/ssh/id}"
KNOWN="${YASHOME_HOSTS_KNOWN:?the hosts_known_hosts of the helper}"
SRC="${YASHGEN_SRC:?the builder's folder}"
LISTEN="${YASHGEN_LISTEN:?the address the front door proxies yashGen to}"
LOCAL_UNIT="${YASHGEN_LOCAL_UNIT:-}"
SECRETS="${YASHGEN_HOUSE_SECRETS:?the house's ESP secrets}"
STATE="$HOME/.config/yashome/yashgen.current"
TARGET="$HOUSE_DIR/plugin-data/computers/yashgen.json"
sh "$(dirname "$0")/yashgen-stop.sh" pending || true      # data left on a computer earlier: asked again
HOST=""
[ -s "$TARGET" ] && eval "$(python3 -I "$(dirname "$0")/llm-target.py" "$TARGET")"
if [ -z "$HOST" ]; then
    echo "yashGen: this computer"
    rm -f "$STATE"
    [ -z "$LOCAL_UNIT" ] || systemctl --user start "$LOCAL_UNIT"
    exec sleep infinity
fi
[ -z "$LOCAL_UNIT" ] || systemctl --user stop "$LOCAL_UNIT"
echo "yashGen: $RUSER@$HOST -> $LISTEN"
printf '%s %s\n' "$RUSER" "$HOST" > "$STATE"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes -i $KEY -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KNOWN -l $RUSER"
R=/opt/yashome-yashgen
sync() { rsync -a -e "$SSH" --rsync-path="sudo rsync" "$@"; }
sync --delete --exclude=/.git/ --exclude=/.venv/ --exclude=/tests/ --exclude=/out/ --exclude=/jobs/ --exclude=/builds/ \
    --exclude=/projects/ --exclude=__pycache__/ --exclude=/secrets.yaml --exclude=/pair-secrets.json --exclude='/visits.*' \
    --exclude='/.*-token' --exclude=/.build-key --exclude='/pinout-suggestions*' --exclude=/fwgen-full.tar.gz \
    "$SRC/" "$HOST:$R/fwgen/"
for f in projects secrets.yaml pair-secrets.json visits.jsonl .build-key; do      # its data: the newer copy wins
    [ -e "$SRC/$f" ] && sync --update "$SRC/$f" "$HOST:$R/data/"
done
NODES="--delete --exclude=.esphome/ --exclude=secrets.yaml"
sync $NODES "$HOUSE_DIR/esphome/" "$HOST:$R/house/esphome/"
[ -z "${YASHGEN_RESPEAKER:-}" ] || sync $NODES "$YASHGEN_RESPEAKER/" "$HOST:$R/house/respeaker/"
PIN=$(sed -n 's/^ESPHOME_PIN = "\(.*\)"$/\1/p' "$SRC/fwgen.py")
{ head -n1 "$SRC/.builder-token"; echo; cat "$SECRETS"; } | $SSH "$HOST" "sudo -n sh $R/start.sh $PIN"
exec $SSH -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L "$LISTEN:127.0.0.1:8090" "$HOST"
