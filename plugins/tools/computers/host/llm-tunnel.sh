#!/bin/sh
# The house's big model (plug-in computers, role LLM): an ssh tunnel from this host's 127.0.0.1:11435 to the Ollama of the
# computer in use (its 127.0.0.1:11434: Ollama is never opened to the network). The computer is <house>/plugin-data/computers/
# llm.json {host, user, wsl, serve}, written by Settings -> Devices -> Computers -> Use; the .path unit restarts this one when it
# changes. serve: the command that runs the model server there for the tunnel's lifetime (a WSL one), empty = it runs by itself.
# No computer in use: it waits. Key and known hosts: the house's (YASHOME_SSH_DIR/id, the helper's hosts_known_hosts).
set -eu
HOUSE_DIR="${HOUSE_DIR:?the house folder}"
KEY="${YASHOME_SSH_KEY:-$HOME/.config/yashome/ssh/id}"
KNOWN="${YASHOME_HOSTS_KNOWN:?the hosts_known_hosts of the helper}"
TARGET="$HOUSE_DIR/plugin-data/computers/llm.json"
[ -s "$TARGET" ] || { echo "no computer in use for the big model"; exec sleep infinity; }
eval "$(python3 -I "$(dirname "$0")/llm-target.py" "$TARGET")"
[ -n "$HOST" ] || { echo "no computer in use for the big model"; exec sleep infinity; }
CMD=""
if [ -n "$SERVE" ]; then
    if [ -n "$WSL" ]; then CMD="wsl -d ${WSL%/*} -u ${WSL#*/} -- $SERVE"; else CMD="$SERVE"; fi
fi
echo "big model: $RUSER@$HOST${WSL:+ (WSL $WSL)} -> 127.0.0.1:11435"
set -- -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ConnectTimeout=10 \
    -o IdentitiesOnly=yes -i "$KEY" -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$KNOWN" \
    -L 127.0.0.1:11435:127.0.0.1:11434 -l "$RUSER"
if [ -n "$CMD" ]; then exec ssh "$@" "$HOST" "$CMD"; else exec ssh -N "$@" "$HOST"; fi
