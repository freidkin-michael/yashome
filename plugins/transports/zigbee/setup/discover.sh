#!/bin/sh
# Network Zigbee coordinators announced over mDNS (SMLIGHT SLZB, ZigStar, TubesZB), one per line:
# "tcp://IP:PORT ADAPTER" (ADAPTER from the radio_type it announces: ezsp -> ember, znp -> zstack).
# Prints nothing without avahi-browse; never fails.
command -v avahi-browse >/dev/null 2>&1 || exit 0
for t in _slzb-06._tcp _zigstar_gw._tcp _tubeszb._tcp; do
    timeout 5 avahi-browse -tpr "$t" 2>/dev/null
done | awk -F';' '$1 == "=" && $3 == "IPv4" {
    a = "ember"
    if ($10 ~ /radio_type=znp/) a = "zstack"
    if (!seen[$8 ":" $9]++) print "tcp://" $8 ":" $9, a
}'
exit 0
