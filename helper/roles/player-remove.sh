#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this) to take the house's player off it: the unit and its folder (mpv stays).
set -eu
DIR=/opt/yashome-player
U="${SUDO_USER:-}"
if [ -n "$U" ] && [ "$U" != root ] && [ -d "/run/user/$(id -u "$U")" ]; then
    UID_="$(id -u "$U")"
    runuser -u "$U" -- env XDG_RUNTIME_DIR="/run/user/$UID_" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UID_/bus" \
        systemctl --user disable --now yashome-player.service 2>&1 || true
fi
rm -f /etc/systemd/user/yashome-player.service
rm -rf "$DIR"
echo "player removed (mpv itself stays installed)"
