#!/bin/sh
# Build the Yash! APK in a containerised Android toolchain (any host with docker) and put it where the
# Family card offers it: <house>/plugin-state/family/yash.apk, or the directory given as $1. Signed with
# the house's key (plugin-state/family/app.keystore, the same one Build the app uses) when there is one.
# google-services.json (plugin-data/family/ or app/) turns Firebase on; FAMILY_APP_ID picks the package.
set -eu
APP=$(cd "$(dirname "$0")" && pwd)
CORE=$(cd "$APP/../../../.." && pwd)
OUT=${1:-}
if [ -z "$OUT" ]; then
    if [ -f "$CORE/tools/envlib.sh" ] && [ -f "$CORE/.env" ]; then OUT="$(cd "$CORE" && . tools/envlib.sh && house_dir)/plugin-state/family"
    else OUT="$APP/apk"; fi
    case "$OUT" in /*) ;; *) OUT="$CORE/$OUT";; esac
fi
STATE=$(dirname "$OUT")/../plugin-state/family
[ -f "$OUT/app.keystore" ] && STATE=$OUT
if [ -f "$STATE/app.keystore" ] && [ -f "$STATE/app.keystore.pass" ]; then
    # the house's key under a name of its own (an own release.keystore here is never touched)
    if [ -f "$APP/release.keystore" ] && ! cmp -s "$APP/release.keystore" "$STATE/app.keystore"; then
        echo "both $APP/release.keystore and the house's key: import yours as the house's key first (README), or move it away" >&2
        exit 1
    fi
    cp "$STATE/app.keystore" "$APP/house.keystore"
    trap 'rm -f "$APP/house.keystore"' EXIT
    trap 'exit 130' INT TERM
    ANDROID_KEYSTORE_FILE=house.keystore
    ANDROID_KEYSTORE_PASSWORD=$(cat "$STATE/app.keystore.pass")
    ANDROID_KEY_ALIAS=$(cat "$STATE/app.keystore.alias" 2>/dev/null || echo home)
    export ANDROID_KEYSTORE_FILE ANDROID_KEYSTORE_PASSWORD ANDROID_KEY_ALIAS
fi
GS=$(dirname "$OUT")/../plugin-data/family/google-services.json
if [ -f "$GS" ] && [ ! -f "$APP/app/google-services.json" ]; then cp "$GS" "$APP/app/google-services.json"; fi
[ -f "$APP/app/google-services.json" ] || echo "no google-services.json: building without Firebase (rings over the house link only)"
APP_ID=${FAMILY_APP_ID:-$(cd "$CORE" 2>/dev/null && [ -f .env ] && . tools/envlib.sh && get FAMILY_APP_ID || true)}
IMG=cimg/android:2024.07
if [ -z "${ANDROID_KEYSTORE_FILE:-}" ] && [ ! -f "$APP/release.keystore" ] && [ ! -f "$APP/debug.keystore" ]; then
    echo "no release.keystore: making debug.keystore once - keep it, a new key means reinstalling the app"
    docker run --rm --user "$(id -u):$(id -g)" -v "$APP:/proj" -w /proj "$IMG" keytool -genkeypair -noprompt \
        -keystore debug.keystore -storepass android -keypass android -alias androiddebugkey \
        -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Android Debug,O=Android,C=US"
fi
export ANDROID_KEYSTORE_PASSWORD="${ANDROID_KEYSTORE_PASSWORD:-}" ANDROID_KEY_ALIAS="${ANDROID_KEY_ALIAS:-home}"
# names only: docker takes the values from this environment, so the password is not on its command line
docker run --rm --user root -e ANDROID_KEYSTORE_PASSWORD -e ANDROID_KEY_ALIAS -e ANDROID_KEYSTORE_FILE \
    -v "$APP:/proj" -w /proj "$IMG" bash -lc "gradle --no-daemon -q assembleRelease ${APP_ID:+-PyashAppId=$APP_ID}"
docker run --rm --user root -v "$APP:/proj" "$IMG" chown -R "$(id -u):$(id -g)" /proj
mkdir -p "$OUT"
cp "$APP/app/build/outputs/apk/release/app-release.apk" "$OUT/yash.apk"
ls -la "$OUT/yash.apk"
