#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this, its arguments set at the top) to give it the house's TTS service:
# docker when missing, the service from the house (/plugins/voice/tts.tar.gz) in /opt/yashome-tts, port 8092 on this
# computer's loopback only and no key: the house reaches it through its ssh tunnel (the helper's) and passes the spoken files
# on to the satellites itself. The Silero models (CC BY-NC-SA 4.0) are fetched once into its volume.
set -eu
HOUSE="${1:?the house's address}"
fail() { echo "tts: $*" >&2; exit 1; }
get() {      # get <url> [timeout]: curl, or python3 where there is no curl
    if command -v curl >/dev/null 2>&1; then curl -fsS -m "${2:-60}" "$1"
    else python3 -c 'import sys, urllib.request; sys.stdout.buffer.write(urllib.request.urlopen(sys.argv[1], timeout=float(sys.argv[2])).read())' "$1" "${2:-60}"; fi
}
if ! command -v docker >/dev/null 2>&1; then
    echo "installing docker (get.docker.com)"
    get https://get.docker.com > /tmp/get-docker.sh || fail "docker could not be downloaded"   # a file: a failed download never looks like success
    sh /tmp/get-docker.sh
fi
docker compose version >/dev/null 2>&1 || fail "docker compose is missing"
DIR=/opt/yashome-tts
mkdir -p "$DIR"
cd "$DIR"
get "$HOUSE/plugins/voice/tts.tar.gz" | tar xzf - --no-same-owner
umask 077
cat > compose.yml <<EOF
services:
  tts:
    build: .
    restart: unless-stopped
    init: true
    mem_limit: 2g
    ports:
      - "127.0.0.1:8092:8092"
    environment:
      - VOICE_TTS_OPEN=1
      - HOME=/tmp
    tmpfs:
      - /data
    volumes:
      - models:/models
volumes:
  models:
EOF
chmod 755 "$DIR"
chmod 644 compose.yml
docker compose up -d --build --remove-orphans || fail "not running: fix the cause above, then  docker compose up -d --build  in $DIR"
for _ in $(seq 1 60); do
    get "http://127.0.0.1:8092/health" 3 >/dev/null 2>&1 && { echo "tts is up on 127.0.0.1:8092 (Use: the house reaches it through ssh)"; exit 0; }
    sleep 5
done
fail "it started but does not answer on http://127.0.0.1:8092/health (the models load at the first start: docker compose logs in $DIR)"
