#!/bin/sh
# make install, family: the bridge's broker password and the key the plug-in calls it with.
set -eu
. tools/envlib.sh
put MQTT_FAMILY_USER family default
put MQTT_FAMILY_PASSWORD "$(openssl rand -base64 18 | tr -d '/+=')" generated
put FAMILY_BRIDGE_KEY "$(openssl rand -hex 24)" generated
