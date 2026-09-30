#!/bin/sh
# end of make install, zigbee: where its frontend is (HOST comes from the core's install.sh).
. tools/envlib.sh
case "$(get Z2M_SERIAL | tr -d "\"'" | tr 'A-Z' 'a-z')" in ""|none|external) exit 0;; esac
p=$(get Z2M_PORT)
echo "zigbee2mqtt: http://${HOST:-localhost}:${p:-8080}   login: Z2M_AUTH_TOKEN from .env"
