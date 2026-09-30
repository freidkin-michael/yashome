#!/bin/sh
# first make install: a Zigbee coordinator among /dev/serial/by-id -> switch the plug-in on
for d in /dev/serial/by-id/*; do
    case "$d" in *[Zz]igbee*|*SLZB*|*ZBDongle*|*CC2652*|*CC2531*|*EFR32*|*ConBee*|*Conbee*|*SkyConnect*|*zzh*|*SONOFF*|*Sonoff*) exit 0;; esac
done
exit 1
