#!/bin/sh
# make secrets, family: the compose profile "family" when the Firebase key is in place - other profiles stay; state.json from the example once. A helper job
# cannot see a key outside its mounts: the profile stays as it is then.
set -eu
. tools/envlib.sh
dir="$(house_dir)/plugin-data/family"
mkdir -p "$dir"
[ -f "$dir/state.json" ] || cp plugins/tools/family/setup/state.example.json "$dir/state.json"
key=$(get FAMILY_FIREBASE_KEY | tr -d "\"'")
case "$key" in "~"*) echo "FAMILY_FIREBASE_KEY: write the full path, no ~ ($key)" >&2; exit 1;; esac
want=""
seen=1
[ -z "$key" ] || { host_file "$key" && seen=0 || seen=$?; }
if [ "$seen" = 0 ]; then want=family
elif [ "$seen" = 2 ]; then
    case ",$(get COMPOSE_PROFILES | tr -d "\"' ")," in *,family,*) want=family;; esac
    echo "family-bridge: kept as it is - this job cannot see FAMILY_FIREBASE_KEY ($key)"
else echo "family-bridge: off - FAMILY_FIREBASE_KEY is not a file (${key:-empty}); keep it out of the house repo"; fi
other=$(get COMPOSE_PROFILES | tr -d "\"' " | tr ',' '\n' | grep -vx 'family' | grep . | paste -sd, - || true)
profiles=$(printf '%s\n%s\n' "$other" "$want" | grep . | paste -sd, - || true)
[ "$(get COMPOSE_PROFILES)" = "$profiles" ] || env_set COMPOSE_PROFILES "$profiles"
[ -z "$want" ] || echo "family-bridge: enabled"
