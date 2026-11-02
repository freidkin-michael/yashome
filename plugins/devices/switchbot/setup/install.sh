#!/bin/sh
# make install, switchbot: the broker account of the lock bridge (host/switchbot-lock.py).
set -eu
. tools/envlib.sh
put MQTT_SWITCHBOT_USER switchbot default
put MQTT_SWITCHBOT_PASSWORD "$(openssl rand -base64 18 | tr -d '/+=')" generated
