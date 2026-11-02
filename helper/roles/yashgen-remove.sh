#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this) to take yashGen off it: its container, image, caches and folder
# (refused while the house has not pulled its data back, no data/.pulled-back: the house asks again at every start).
set -eu
DIR=/opt/yashome-yashgen
rm -rf /run/yashome-yashgen
[ -d "$DIR" ] || { echo "no yashGen here"; exit 0; }
if [ -d "$DIR/data/projects" ] && [ ! -f "$DIR/data/.pulled-back" ]; then
    echo "the house has not got yashGen's data from here yet: restart yashGen on the house, then remove" >&2
    exit 1
fi
cd "$DIR"
[ -f .env ] || echo ESPHOME_VERSION=0 > .env
docker compose down -v --rmi all 2>&1 || true
cd /
rm -rf "$DIR"
echo "yashGen removed"
