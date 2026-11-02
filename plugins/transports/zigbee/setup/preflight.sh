#!/bin/sh
# make check, zigbee: the coordinator (a stick, or a network one answering on its tcp:// port) and the
# frontend port. Exit 1 = a hard failure.
. tools/envlib.sh
s=$(get Z2M_SERIAL | tr -d "\"'")
case "$(printf '%s' "$s" | tr 'A-Z' 'a-z')" in
    tcp://*)
        hp=${s#*://}; h=${hp%:*}; p=${hp##*:}
        if ! command -v nc >/dev/null 2>&1; then echo "  warn  no nc here: cannot test the coordinator at $s"
        elif nc -z -w 3 "$h" "$p" 2>/dev/null; then echo "  ok    network coordinator answers: $s"
        else echo "  warn  network coordinator $s does not answer (power, cable, address?) - zigbee2mqtt will retry"; fi;;
    *)
        n=$(ls /dev/serial/by-id/ 2>/dev/null | wc -l)
        case "$n" in
            0) echo "  warn  no USB serial device in /dev/serial/by-id - the stack runs without Zigbee until you plug a coordinator in (or set Z2M_SERIAL=tcp://host:port)";;
            1) echo "  ok    one serial device: $(ls /dev/serial/by-id/)";;
            *) echo "  warn  $n serial devices - the installer will ask which one is the coordinator";;
        esac;;
esac
case "$(printf '%s' "$s" | tr 'A-Z' 'a-z')" in ""|none|external) exit 0;; esac
