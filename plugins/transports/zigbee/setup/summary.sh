#!/bin/sh
# end of make install, zigbee: where its frontend is (HOST comes from the core's install.sh).
. tools/envlib.sh
case "$(get Z2M_SERIAL | tr -d "\"'" | tr 'A-Z' 'a-z')" in ""|none|external) exit 0;; esac
echo "zigbee2mqtt: Settings -> Zigbee -> Gateway -> Open zigbee2mqtt (through the dashboard)"
