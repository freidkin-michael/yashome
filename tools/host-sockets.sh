#!/bin/sh
# The house's helpers on its own host, once (and after an update that changes them): sudo sh tools/host-sockets.sh
# Each listens on a socket file in /run/yashome that systemd makes (the home container has the folder mounted): its group is
# the house's (PGID), so the house and nothing else may ask; no port, no token, no address to set (tools/hostsock.py).
#   yashome-wol            Wake-on-LAN from the host's own card                    always
#   yashome-provision      ESP rescue through a spare Wi-Fi card (nmcli gate)      when the provision plug-in is on
#   yashome-panel-start    a tablet's launcher started over adb, as the house's user  when the panels plug-in is on and adb is here
# The helpers of before (wol-relay, esp-provision on a port, the user unit panel-start on the docker gateway) are stopped.
set -eu

fail() { echo "host-sockets: $*" >&2; exit 1; }

main() {
    [ "$(id -u)" = 0 ] || fail "run it with sudo"
    REPO=$(cd "$(dirname "$0")/.." && pwd)
    cd "$REPO"
    . tools/envlib.sh
    U=$(stat -c %U "$REPO")
    UID_=$(id -u "$U")
    G=$(get PGID)
    G=${G:-$(id -g "$U")}
    PLUGINS=$(get PLUGINS)
    on() { case ",$PLUGINS," in *",$1,"*|*",*,"*) return 0;; esac; return 1; }
    LIB=/usr/local/lib/yashome
    UNITS=/etc/systemd/system
    install -d -m 755 "$LIB"

    sock() {   # name, ListenStream|ListenDatagram, Accept
        cat > "$UNITS/yashome-$1.socket" <<EOF
# tools/host-sockets.sh: only the house's group (the home container) may write to it
[Unit]
Description=Yashome $1 (socket of the house)

[Socket]
$2=/run/yashome/$1.sock
SocketUser=root
SocketGroup=$G
SocketMode=0660
DirectoryMode=0755
Accept=$3
MaxConnections=8

[Install]
WantedBy=sockets.target
EOF
    }

    install -m 755 plugins/transports/webos/host/wol-relay.py "$LIB/wol-relay.py"
    sock wol ListenDatagram no
    cat > "$UNITS/yashome-wol.service" <<EOF
[Unit]
Description=Yashome Wake-on-LAN relay
Requires=yashome-wol.socket

[Service]
ExecStart=/usr/bin/python3 $LIB/wol-relay.py
DynamicUser=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_UNIX
EOF
    UP="yashome-wol.socket"

    if on provision; then
        id yashome-provision >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin yashome-provision
        install -D -o root -g root -m 755 plugins/tools/provision/host/esp-provision-nmcli /usr/local/libexec/yashome/esp-provision-nmcli
        echo 'yashome-provision ALL=(root) NOPASSWD: /usr/local/libexec/yashome/esp-provision-nmcli' > /etc/sudoers.d/yashome-provision
        chmod 440 /etc/sudoers.d/yashome-provision
        install -m 755 plugins/tools/provision/host/esp-provision.py "$LIB/esp-provision.py"
        sock provision ListenStream no
        cat > "$UNITS/yashome-provision.service" <<EOF
[Unit]
Description=Yashome ESP rescue through the host's Wi-Fi card
Requires=yashome-provision.socket
After=NetworkManager.service

[Service]
ExecStart=/usr/bin/python3 $LIB/esp-provision.py
EnvironmentFile=-/etc/yashome/provision.env
User=yashome-provision
# sudo -n esp-provision-nmcli needs setuid: no NoNewPrivileges, no capability bounding set
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
RestrictRealtime=yes
LockPersonality=yes
EOF
        UP="$UP yashome-provision.socket"
    fi

    if on panels && command -v adb >/dev/null 2>&1; then
        sock panel-start ListenStream yes
        cat > "$UNITS/yashome-panel-start@.service" <<EOF
[Unit]
Description=Yashome panel-start request

[Service]
ExecStart=/usr/bin/python3 $REPO/tools/panel-start/panel_start.py
User=$U
Environment=HOME=$(getent passwd "$U" | cut -d: -f6)
StandardInput=socket
StandardOutput=socket
StandardError=journal
RuntimeMaxSec=40
EOF
        UP="$UP yashome-panel-start.socket"
    fi

    # the helpers of before: their ports close
    for old in wol-relay esp-provision; do
        if [ -f "$UNITS/$old.service" ]; then
            systemctl disable --now "$old" 2>/dev/null || true
            rm -f "$UNITS/$old.service"
            echo "stopped $old (it listened on a port)"
        fi
    done
    if [ -e "/home/$U/.config/systemd/user/panel-start.socket" ] || [ -d "/home/$U/.config/systemd/user/panel-start.socket.d" ]; then
        runuser -u "$U" -- env XDG_RUNTIME_DIR="/run/user/$UID_" systemctl --user disable --now panel-start.socket 2>/dev/null || true
        rm -rf "/home/$U/.config/systemd/user/panel-start.socket.d"
        rm -f "/home/$U/.config/systemd/user/panel-start.socket" "/home/$U/.config/systemd/user/panel-start@.service"
        runuser -u "$U" -- env XDG_RUNTIME_DIR="/run/user/$UID_" systemctl --user daemon-reload 2>/dev/null || true
        echo "stopped the user unit panel-start (it listened on the docker gateway)"
    fi
    if command -v ufw >/dev/null 2>&1; then
        ufw status numbered 2>/dev/null | grep -E 'yashome-panel-start|9117|9098|9099' | sed 's/^/  firewall rule no longer needed: /' || true
    fi

    systemctl daemon-reload
    for s in $UP; do                         # a running helper keeps its old socket: stopped first, started on the next ask
        systemctl stop "${s%.socket}.service" 2>/dev/null || true
        systemctl enable "$s" >/dev/null 2>&1
        systemctl restart "$s"
    done
    echo "on /run/yashome: $(ls /run/yashome | tr '\n' ' ')(group $G)"
}

main "$@"
