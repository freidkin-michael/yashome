#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this) to take the build server off it: its container, volumes and folder.
set -eu
DIR=/opt/yashome-build
[ -d "$DIR" ] || { echo "no build server here"; exit 0; }
cd "$DIR"
docker compose down -v --rmi local 2>&1 || true
cd /
rm -rf "$DIR"
echo "build server removed"
