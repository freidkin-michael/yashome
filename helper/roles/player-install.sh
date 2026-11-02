#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this, its arguments set at the top) to make it a player of the house:
# mpv when missing (apt), the player plug-in's ctl.py and user unit from the house (/plugins/player/host.tar.gz) in
# /opt/yashome-player, the unit enabled for the user the house logs in as (SUDO_USER): mpv waits in that user's graphical
# session and draws on its screen. No port, no key: the house drives it over ssh (ctl.py on stdin).
set -eu
HOUSE="${1:?the house's address}"
get() {      # curl, or python3 where there is no curl
    if command -v curl >/dev/null 2>&1; then curl -fsS "$1"
    else python3 -c 'import sys, urllib.request; sys.stdout.buffer.write(urllib.request.urlopen(sys.argv[1], timeout=60).read())' "$1"; fi
}
fail() { echo "player: $*" >&2; exit 1; }
U="${SUDO_USER:-}"
[ -n "$U" ] && [ "$U" != root ] || fail "run through sudo as the user whose screen it plays on"
UID_="$(id -u "$U")"
if ! command -v mpv >/dev/null 2>&1; then
    command -v apt-get >/dev/null 2>&1 || fail "no mpv here and no apt-get to install it: install mpv, then again"
    echo "installing mpv (apt)"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mpv >/dev/null
fi
command -v python3 >/dev/null 2>&1 || fail "python3 is missing"
DIR=/opt/yashome-player
mkdir -p "$DIR"
get "$HOUSE/plugins/player/host.tar.gz" | tar xzf - -C "$DIR" --no-same-owner
chmod 755 "$DIR" "$DIR/ctl.py"
install -m 644 "$DIR/yashome-player.service" /etc/systemd/user/yashome-player.service
as_user() { runuser -u "$U" -- env XDG_RUNTIME_DIR="/run/user/$UID_" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UID_/bus" "$@"; }
[ -d "/run/user/$UID_" ] || fail "$U is not logged in here: the player runs in its graphical session (log in on the screen, then again)"
as_user systemctl --user daemon-reload
as_user systemctl --user enable yashome-player.service
as_user systemctl --user restart yashome-player.service || fail "it does not start: journalctl --user -u yashome-player (as $U)"
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if [ -S "/run/user/$UID_/yashome-player/mpv.sock" ]; then
        as_user systemctl --user show-environment | grep -qE '^(WAYLAND_DISPLAY|DISPLAY)=' \
            || echo "note: $U's session has no screen now (no WAYLAND_DISPLAY/DISPLAY): it plays once $U is logged in on the screen"
        echo "player is ready on $(hostname) ($U's screen)"
        exit 0
    fi
    sleep 1
done
fail "mpv started but its socket did not come: journalctl --user -u yashome-player (as $U)"
