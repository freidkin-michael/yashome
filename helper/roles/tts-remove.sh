#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this) to take the TTS service off it: its container, models and folder.
set -eu
DIR=/opt/yashome-tts
[ -d "$DIR" ] || { echo "no TTS service here"; exit 0; }
cd "$DIR"
docker compose down -v --rmi local 2>&1 || true
cd /
rm -rf "$DIR"
echo "TTS service removed"
