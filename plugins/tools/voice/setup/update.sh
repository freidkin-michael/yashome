#!/bin/sh
# make update, voice: the container goes when its profile went off.
. tools/envlib.sh
case ",$(get COMPOSE_PROFILES | tr -d "\"' ")," in
    *,voice,*) ;;
    *) docker compose --profile voice rm -sf tts >/dev/null 2>&1 || true;;
esac
