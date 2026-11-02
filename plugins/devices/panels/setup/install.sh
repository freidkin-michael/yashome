#!/bin/sh
# make install, panels: the ESP panels' broker account, their OTA password and their fallback AP password.
set -eu
. tools/envlib.sh
put MQTT_PANELS_USER panels default
for k in MQTT_PANELS_PASSWORD PANELS_OTA_PASSWORD PANELS_AP_PASSWORD; do
    v=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')   # a failure stops here (set -e)
    [ -n "$v" ] || { echo "panels: no password made for $k" >&2; exit 1; }
    put "$k" "$v" generated
done
