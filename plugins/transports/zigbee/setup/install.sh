#!/bin/sh
# make install, zigbee: generated secrets and the coordinator: a stick or a network one (tcp://),
# Z2M_SERIAL=none = without Zigbee.
set -eu
. tools/envlib.sh
put MQTT_Z2M_USER z2m default
put MQTT_Z2M_PASSWORD "$(openssl rand -base64 18 | tr -d '/+=')" generated
put Z2M_AUTH_TOKEN "$(openssl rand -hex 8)" generated
if [ -z "$(get Z2M_SERIAL)" ]; then
    net=$(sh "$(dirname "$0")/discover.sh")
    set --
    for d in /dev/serial/by-id/*; do
        case "$d" in *[Zz]igbee*|*SLZB*|*ZBDongle*|*CC2652*|*CC2531*|*EFR32*|*ConBee*|*Conbee*|*SkyConnect*|*zzh*|*SONOFF*|*Sonoff*) set -- "$@" "$d";; esac
    done
    for d in $(printf '%s\n' "$net" | awk 'NF {print $1}'); do set -- "$@" "$d"; done
    n=$#
    if [ "$n" = 1 ]; then put Z2M_SERIAL "$1" "$1"
    elif [ "$n" -gt 1 ] && [ -t 0 ]; then
        echo "  several Zigbee coordinators - which one? (Enter = none, run without Zigbee)"
        i=1; for d in "$@"; do echo "    $i) $d"; i=$((i+1)); done
        printf '  > '; read -r ans
        case "$ans" in
            ""|*[!0-9]*) ;;
            *) [ "$ans" -ge 1 ] && [ "$ans" -le "$n" ] && { shift $((ans-1)); put Z2M_SERIAL "$1" chosen; };;
        esac
    elif [ "$n" -gt 1 ]; then echo "  Z2M_SERIAL: $n coordinators and no terminal to ask - starting without Zigbee (set it in .env, then 'make update')"
    else echo "  Z2M_SERIAL: no Zigbee coordinator in /dev/serial/by-id or on the network - set it in .env (stick path or tcp://host:port), then 'make update'"; fi
    # a network coordinator says its radio type: that is its adapter
    a=$(printf '%s\n' "$net" | awk -v s="$(get Z2M_SERIAL)" '$1 == s {print $2}')
    [ -z "$a" ] || put Z2M_ADAPTER "$a" "announced by the coordinator"
fi
case "$(get Z2M_SERIAL | tr 'A-Z' 'a-z')" in
    ""|none|external) ;;                                         # no coordinator here: nothing to pick
    tcp://*) put Z2M_ADAPTER ember "network coordinator (zstack for a TI one: set Z2M_ADAPTER)";;
    *conbee*|*raspbee*) put Z2M_ADAPTER deconz "deCONZ stick";;
    *slzb-06p*|*slzb-07p*) put Z2M_ADAPTER zstack "TI stick";;
    *dongle_plus_v2*|*zbdongle-e*|*slzb-06m*|*slzb-07*|*efr32*|*skyconnect*) put Z2M_ADAPTER ember "Silicon Labs stick";;
    *dongle_plus*|*cc26*|*cc2531*|*zbdongle-p*|*slzb-06*|*zzh*|*texas*) put Z2M_ADAPTER zstack "TI stick";;
    *) put Z2M_ADAPTER ember "Silicon Labs stick";;
esac
case "$(get Z2M_SERIAL | tr -d "\"'" | tr 'A-Z' 'a-z')" in
    ""|none) echo "  zigbee2mqtt stays off (Z2M_SERIAL empty or 'none')";;
    external) echo "  zigbee2mqtt runs elsewhere on this broker (Z2M_SERIAL=external)";;
    *) echo "  zigbee2mqtt on $(get Z2M_SERIAL)";;
esac
