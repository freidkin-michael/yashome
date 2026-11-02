#!/bin/sh
# make update, zigbee: remove the container when its profile went off (up never stops one);
# restart it when secrets.py rewrote secret.yaml (a new password or frontend login) or the
# serial block (another coordinator).
. tools/envlib.sh
case ",$(get COMPOSE_PROFILES | tr -d "\"' ")," in
    *,zigbee,*)
        d="$(get Z2M_DATA_DIR | tr -d "\"'")"
        if [ -f "$d/.secret-changed" ] || [ -f "$d/.serial-changed" ]; then
            docker compose restart zigbee2mqtt >/dev/null 2>&1 && rm -f "$d/.secret-changed" "$d/.serial-changed" \
                && echo "zigbee2mqtt: restarted for its new secret.yaml / coordinator"
        fi;;
    *) docker compose --profile zigbee rm -sf zigbee2mqtt >/dev/null 2>&1 || true;;
esac
