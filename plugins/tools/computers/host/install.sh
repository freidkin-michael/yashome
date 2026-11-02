#!/bin/sh
# The house's big model on this host (plug-in computers, role LLM): the tunnel to the computer in use and its health, as
# user units of the house's owner. Run it as that user (not root) from the checkout: sh plugins/tools/computers/host/install.sh
# It reads HOUSE_DIR and YASHOME_DIR from .env. The voice service reaches 127.0.0.1:11435 / :11436 through its relays (the
# house's voice/compose.yml). Again after an update of these files: it rewrites them.
# With YASHGEN_SRC in .env (the yashGen builder's folder on this host) also yashGen where the house uses it (yashgen-tunnel.sh).
set -eu
cd "$(dirname "$0")/../../../.."
get() { sed -n "s/^$1=//p" .env | tail -1 | tr -d "\"'"; }
HOUSE_DIR=$(get HOUSE_DIR)
YDIR=$(get YASHOME_DIR)
[ -n "$HOUSE_DIR" ] && [ -d "$HOUSE_DIR" ] || { echo "HOUSE_DIR is not set in .env" >&2; exit 1; }
[ -n "$YDIR" ] || YDIR=$(pwd)
CONF="$HOME/.config/yashome"
UNITS="$HOME/.config/systemd/user"
mkdir -p "$CONF" "$UNITS" "$HOUSE_DIR/plugin-data/computers"
for f in llm-tunnel.sh llm-target.py llm-health.py yashgen-tunnel.sh yashgen-stop.sh; do cp "plugins/tools/computers/host/$f" "$CONF/"; done
TARGET="$HOUSE_DIR/plugin-data/computers/llm.json"
cat > "$CONF/llm.env" <<EOF
HOUSE_DIR=$HOUSE_DIR
YASHOME_SSH_KEY=$CONF/ssh/id
YASHOME_HOSTS_KNOWN=$YDIR/.helper/hosts_known_hosts
EOF
cat > "$UNITS/yashome-llm-tunnel.service" <<EOF
[Unit]
Description=Yashome big model: ssh tunnel to the computer in use (127.0.0.1:11435)
After=network-online.target

[Service]
EnvironmentFile=$CONF/llm.env
ExecStart=/bin/sh $CONF/llm-tunnel.sh
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
EOF
cat > "$UNITS/yashome-llm-health.service" <<EOF
[Unit]
Description=Yashome big model: health 127.0.0.1:11436/health (available | busy | off)

[Service]
EnvironmentFile=$CONF/llm.env
ExecStart=/usr/bin/python3 -I $CONF/llm-health.py
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
cat > "$UNITS/yashome-llm-follow.service" <<EOF
[Unit]
Description=Yashome big model: the tunnel goes to the computer now in use

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl --user restart yashome-llm-tunnel.service
EOF
cat > "$UNITS/yashome-llm-follow.path" <<EOF
[Unit]
Description=Yashome big model: follow Use in Settings -> Devices -> Computers

[Path]
PathChanged=$TARGET
Unit=yashome-llm-follow.service

[Install]
WantedBy=default.target
EOF
GEN_SRC=$(get YASHGEN_SRC)
if [ -n "$GEN_SRC" ]; then
    cat > "$CONF/yashgen.env" <<EOF
YASHGEN_SRC=$GEN_SRC
YASHGEN_LISTEN=$(get YASHGEN_LISTEN)
YASHGEN_LOCAL_UNIT=$(get YASHGEN_LOCAL_UNIT)
YASHGEN_HOUSE_SECRETS=$(get ESPHOME_SECRETS)
YASHGEN_RESPEAKER=$(get YASHGEN_RESPEAKER)
EOF
    cat > "$UNITS/yashome-yashgen-tunnel.service" <<EOF
[Unit]
Description=Yashome yashGen: this host's builder, or the computer in use through an ssh tunnel

[Service]
EnvironmentFile=$CONF/llm.env
EnvironmentFile=$CONF/yashgen.env
ExecStart=/bin/sh $CONF/yashgen-tunnel.sh
ExecStopPost=/bin/sh $CONF/yashgen-stop.sh
TimeoutStartSec=infinity
TimeoutStopSec=120
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
EOF
    cat > "$UNITS/yashome-yashgen-follow.service" <<EOF
[Unit]
Description=Yashome yashGen: it moves to the computer now in use

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl --user restart yashome-yashgen-tunnel.service
EOF
    cat > "$UNITS/yashome-yashgen-follow.path" <<EOF
[Unit]
Description=Yashome yashGen: follow Use in Settings -> yashGen

[Path]
PathChanged=$HOUSE_DIR/plugin-data/computers/yashgen.json
Unit=yashome-yashgen-follow.service

[Install]
WantedBy=default.target
EOF
fi
systemctl --user daemon-reload
systemctl --user enable --now yashome-llm-follow.path yashome-llm-health.service yashome-llm-tunnel.service
systemctl --user restart yashome-llm-health.service yashome-llm-tunnel.service
echo "big model tunnel and health installed; the computer in use: $TARGET"
if [ -n "$GEN_SRC" ]; then
    systemctl --user enable --now yashome-yashgen-follow.path yashome-yashgen-tunnel.service
    systemctl --user restart yashome-yashgen-tunnel.service
    echo "yashGen installed; the computer in use: $HOUSE_DIR/plugin-data/computers/yashgen.json (empty: this one)"
fi
