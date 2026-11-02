#!/bin/sh
# Yashome build server on a computer of the house: what the helper runs there (sudo sh -s, its arguments set at the top) when
# the computer's card installs it. Docker if missing, the server from the house (/plugins/build/server.tar.gz) in
# /opt/yashome-build, its port on this computer's loopback only (127.0.0.1:8795), no key: the house reaches it through
# its ssh tunnel (the helper's), the same key that installed it. A second argument: apk = the app builder beside it (the
# Android SDK image, about 7 GB), noapk = without it, none = as it is now. All of it in main(): a cut download runs nothing.
set -eu

fail() { echo "build server: $*" >&2; exit 1; }

get() {      # get <url> [timeout]: curl, or python3 where there is no curl
    if command -v curl >/dev/null 2>&1; then curl -fsS -m "${2:-60}" "$1"
    else python3 -c 'import sys, urllib.request; sys.stdout.buffer.write(urllib.request.urlopen(sys.argv[1], timeout=float(sys.argv[2])).read())' "$1" "${2:-60}"; fi
}

main() {
    YASH="${1:-}"
    APK="${2:-}"
    [ -n "$YASH" ] || fail "usage: install-build-server.sh <this house's address> [apk|noapk]"
    [ "$(id -u)" = 0 ] || fail "run it with sudo"
    if ! command -v docker >/dev/null 2>&1; then
        echo "installing docker (get.docker.com)"
        get https://get.docker.com > /tmp/get-docker.sh || fail "docker could not be downloaded"   # a file: a failed download never looks like success
        sh /tmp/get-docker.sh
    fi
    docker compose version >/dev/null 2>&1 || fail "docker compose is missing"
    DIR=/opt/yashome-build
    mkdir -p "$DIR"
    cd "$DIR"
    if [ -z "$APK" ]; then APK=noapk; grep -q '^  apk-builder:' compose.yml 2>/dev/null && APK=apk; fi
    case "$APK" in apk|noapk) ;; *) fail "the second argument: apk or noapk";; esac
    get "$YASH/plugins/build/server.tar.gz" | tar xzf - --no-same-owner
    rm -f .env                                 # the key of an earlier install (registered by a code): not used any more
    umask 077
    cat > compose.yml <<EOF
services:
  build-server:
    build: .
    restart: unless-stopped
    mem_limit: 4g
    environment:
      - APK_URL=http://apk-builder:8795
    ports:
      - "127.0.0.1:8795:8795"
    volumes:
      - cache:/cache
      - work:/work
EOF
    if [ "$APK" = apk ]; then
        echo "with the app builder (the Android SDK image: about 7 GB the first time)"
        cat >> compose.yml <<EOF
  apk-builder:
    build:
      context: .
      dockerfile: apk.Dockerfile
    restart: unless-stopped
    mem_limit: 4g
    volumes:
      - apk-cache:/cache
      - apk-work:/work
EOF
    fi
    printf 'volumes:\n  cache:\n  work:\n' >> compose.yml
    [ "$APK" = noapk ] || printf '  apk-cache:\n  apk-work:\n' >> compose.yml
    chmod 755 "$DIR"
    chmod 644 compose.yml                      # the probe (not root) sees which servers are here; there is no key in it
    docker compose up -d --build --remove-orphans || fail "not running: fix the cause above, then  docker compose up -d --build  in $DIR"
    for _ in $(seq 1 30); do
        get http://127.0.0.1:8795/v1/info 3 >/dev/null 2>&1 && { echo "build server is up on 127.0.0.1:8795 (Use: the house reaches it through ssh)"; exit 0; }
        sleep 2
    done
    fail "it started but does not answer on 127.0.0.1:8795: docker compose logs in $DIR"
}

main "$@"
