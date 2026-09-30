#!/bin/sh
# make check, zigbee: the coordinator stick and the frontend port. Exit 1 = a hard failure.
. tools/envlib.sh
n=$(ls /dev/serial/by-id/ 2>/dev/null | wc -l)
case "$n" in
    0) echo "  warn  no USB serial device in /dev/serial/by-id - the stack runs without Zigbee until you plug a coordinator in";;
    1) echo "  ok    one serial device: $(ls /dev/serial/by-id/)";;
    *) echo "  warn  $n serial devices - the installer will ask which one is the coordinator";;
esac
case "$(get Z2M_SERIAL | tr -d "\"'" | tr 'A-Z' 'a-z')" in ""|none|external) exit 0;; esac
p=$(get Z2M_PORT); p=${p:-8080}
if (command -v ss >/dev/null && ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$p\$"); then
    if docker compose ps --status running 2>/dev/null | grep -q zigbee2mqtt; then echo "  warn  port $p is in use (by this stack's zigbee2mqtt?)"
    else echo "  FAIL  port $p (Z2M_PORT) is already in use"; exit 1; fi
else echo "  ok    port $p is free"; fi
