#!/bin/sh
# After yashgen-tunnel.sh (ExecStopPost): when yashGen ran on a computer, its data comes back to YASHGEN_SRC (the newer copy
# wins; projects made there stay here) and it is stopped there, its secrets wiped. A computer whose data did not come
# back (asleep, off) stays in yashgen.pending and is asked again at every start and stop; the argument "pending" asks only those.
set -u
DIR="$HOME/.config/yashome"
STATE="$DIR/yashgen.current"
PENDING="$DIR/yashgen.pending"
KEY="${YASHOME_SSH_KEY:-$HOME/.config/yashome/ssh/id}"
R=/opt/yashome-yashgen
ssh_for() { echo "ssh -o BatchMode=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes -i $KEY -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$YASHOME_HOSTS_KNOWN -l $1"; }

# pull <user> <host>: every data file back; 0 only when all came (a file that is not there is no failure)
pull() {
    SSH=$(ssh_for "$1")
    for f in projects secrets.yaml pair-secrets.json visits.jsonl .build-key; do
        rsync -a --update --ignore-missing-args -e "$SSH" --rsync-path="sudo rsync" "$2:$R/data/$f" "$YASHGEN_SRC/" || return 1
    done
    $SSH "$2" "sudo -n touch $R/data/.pulled-back"         # yashgen-remove.sh deletes the data there only after this
}

left=""
todo=$(cat "$PENDING" 2>/dev/null)
[ "${1:-}" = pending ] || { [ -s "$STATE" ] && todo="$todo
$(cat "$STATE")"; }
for who in $(printf '%s\n' "$todo" | sort -u | tr ' ' '@'); do
    u=${who%%@*} h=${who#*@}
    if pull "$u" "$h"; then
        echo "yashGen data back from $h"
    else
        echo "yashGen: the data on $h did not come back; asked again at the next start" >&2
        left="$left$u $h
"
    fi
done
if [ "${1:-}" != pending ] && [ -s "$STATE" ]; then
    read -r RUSER HOST < "$STATE"
    $(ssh_for "$RUSER") "$HOST" "sudo -n sh $R/stop.sh"
    rm -f "$STATE"
fi
if [ -n "$left" ]; then printf '%s' "$left" > "$PENDING"; exit 1; fi
rm -f "$PENDING"
