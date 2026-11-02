#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this) to take the touchmouse receiver and typing helper off it.
set -eu
systemctl disable --now touchmouse-rx 2>/dev/null || true
U="${SUDO_USER:-}"
if [ -n "$U" ] && [ "$U" != root ] && [ -d "/run/user/$(id -u "$U")" ]; then
    UID_="$(id -u "$U")"
    runuser -u "$U" -- env XDG_RUNTIME_DIR="/run/user/$UID_" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UID_/bus" \
        systemctl --user disable --now touchmouse-type.service 2>/dev/null || true
fi
rm -f /etc/systemd/system/touchmouse-rx.service /etc/systemd/user/touchmouse-type.service \
      /usr/local/lib/yashome/touchmouse-rx.py /usr/local/lib/yashome/touchmouse-type.py
if command -v ufw >/dev/null 2>&1; then          # the rule its install opened, by its comment
    ufw status numbered 2>/dev/null | awk -F'[][]' '/yashome-touchmouse/{print $2}' | sort -rn | while read -r n; do
        ufw --force delete "$n" >/dev/null
    done
fi
systemctl daemon-reload
echo "touchmouse receiver removed"
