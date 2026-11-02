#!/bin/sh
# make install, presence: the broker account of the host piece (host/phone-presence.sh).
set -eu
. tools/envlib.sh
put MQTT_PRESENCE_USER presence default
put MQTT_PRESENCE_PASSWORD "$(openssl rand -base64 18 | tr -d '/+=')" generated
