#!/bin/sh
# make update, zigbee: remove the container when its profile went off (up never stops one);
# restart it when secrets.py rewrote secret.yaml (a new password or frontend login).
. tools/envlib.sh
case ",$(get COMPOSE_PROFILES | tr -d "\"' ")," in
    *,zigbee,*)
        m="$(get Z2M_DATA_DIR | tr -d "\"'")/.secret-changed"
        if [ -f "$m" ]; then docker compose restart zigbee2mqtt >/dev/null 2>&1 && rm -f "$m" && echo "zigbee2mqtt: restarted for its new secret.yaml"; fi;;
    *) docker compose --profile zigbee rm -sf zigbee2mqtt >/dev/null 2>&1 || true;;
esac
