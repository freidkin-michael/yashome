#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this) to take the Thread border router off it: its container and folder
# (the radio's copy of the network goes with it; the house keeps the network itself), its module list and
# its two forward rules (MQTT from the Thread nodes through docker's FORWARD DROP).
set -eu
DIR=/opt/yashome-thread
rm -f /etc/modules-load.d/yashome-thread.conf
[ -d "$DIR" ] || { echo "no border router here"; exit 0; }
cd "$DIR"
[ ! -x ./ctl.sh ] || ./ctl.sh unforward
docker compose down 2>&1 || true
cd /
rm -rf "$DIR"
echo "Thread border router removed"
