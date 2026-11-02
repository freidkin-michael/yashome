#!/bin/sh
# make update, family: remove the bridge container when its profile went off (up never stops one).
. tools/envlib.sh
case ",$(get COMPOSE_PROFILES | tr -d "\"' ")," in
    *,family,*) ;;
    *) docker compose --profile family rm -sf family-bridge >/dev/null 2>&1 || true;;
esac
