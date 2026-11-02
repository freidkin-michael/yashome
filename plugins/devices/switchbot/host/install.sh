#!/bin/sh
# On the host next to the lock (its Bluetooth): the venv, the bridge and the unit; then fill the env file.
# Usage, from a checkout of this repository: sudo sh plugins/devices/switchbot/host/install.sh <lock id>
set -eu
id=${1:?usage: install.sh <lock id, as in the dashboard>}
here=$(dirname "$0")
lib=/usr/local/lib/yashome
python3 -m venv "$lib/switchbot-venv"
"$lib/switchbot-venv/bin/pip" install -q -r "$here/requirements.txt"
install -D -m 755 "$here/switchbot-lock.py" "$lib/switchbot-lock.py"
install -m 755 "$here/switchbot-get-key.py" "$lib/switchbot-get-key.py"
install -m 644 "$here/switchbot-lock@.service" /etc/systemd/system/
env=/etc/yashome/switchbot-$id.env
[ -e "$env" ] || { install -D -m 600 "$here/switchbot.env.example" "$env"; sed -i "s/^SWITCHBOT_LOCK_ID=.*/SWITCHBOT_LOCK_ID=$id/" "$env"; }
systemctl daemon-reload
echo "next: fill $env (the key: $lib/switchbot-venv/bin/python $lib/switchbot-get-key.py <MAC> <e-mail>),"
echo "then: systemctl enable --now switchbot-lock@$id"
