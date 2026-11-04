#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this) to give it the yashGen firmware builder: docker and rsync when
# missing, /opt/yashome-yashgen with its compose.yml (the official ESPHome image of the builder's version + the builder) and
# start.sh / stop.sh. Nothing runs yet: the house's host (plug-in computers, host/yashgen-tunnel.sh) copies the builder, its
# data and the house's node YAMLs there when it is used, then runs start.sh with the builder's token and the house's ESP
# secrets on its stdin: those stay in /run (memory), gone at stop.sh or a reboot. The builder answers on the computer's own
# loopback only (127.0.0.1:8090): the house reaches it through its ssh tunnel.
set -eu
fail() { echo "yashgen: $*" >&2; exit 1; }
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
if ! command -v rsync >/dev/null 2>&1; then
    echo "installing rsync"
    apt-get install -y rsync >/dev/null 2>&1 || dnf install -y rsync >/dev/null 2>&1 || fail "install rsync, then again"
fi
DIR=/opt/yashome-yashgen
umask 077
mkdir -p "$DIR/fwgen" "$DIR/data" "$DIR/house/esphome" "$DIR/house/respeaker"
cd "$DIR"
cat > Dockerfile <<'EOF'
ARG ESPHOME_VERSION
FROM esphome/esphome:${ESPHOME_VERSION}
RUN pip install --no-cache-dir jinja2 pyyaml
ENTRYPOINT []
EOF
cat > compose.yml <<'EOF'
# yashGen (plug-in computers, role yashgen): started and stopped by the house (start.sh / stop.sh), never by itself.
services:
  yashgen:
    build:
      context: .
      args:
        ESPHOME_VERSION: ${ESPHOME_VERSION:?the builder's ESPHome version}
    image: yashome-yashgen:${ESPHOME_VERSION}
    restart: "no"
    init: true
    working_dir: /fwgen
    command: ["python3", "server.py", "--host", "0.0.0.0", "--port", "8090", "--token-file", "/run/yashgen/token", "--quiet"]
    ports:
      - "127.0.0.1:8090:8090"
    # its own check: the image's (the ESPHome dashboard, 6052) never answers here. Any HTTP answer on 8090 = up;
    # a static file, so no page visit is logged.
    healthcheck:
      test: ["CMD", "python3", "-c", "import http.client as h; c = h.HTTPConnection('127.0.0.1', 8090, timeout=5); c.request('GET', '/web/favicon.svg'); c.getresponse()"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    environment:
      - FWGEN_DATA=/data
      - FWGEN_HOUSE_SECRETS=/run/yashgen/house-secrets.yaml
      - FWGEN_HA_DEFAULT=1
    volumes:
      - ./fwgen:/fwgen
      - ./data:/data
      - ./house/esphome:/root/yashome-house/esphome:ro
      - ./house/respeaker:/root/home-stack/respeaker:ro
      - /run/yashome-yashgen:/run/yashgen:ro
      - platformio:/root/.platformio
      - cache:/root/.cache
volumes:
  platformio:
  cache:
EOF
cat > start.sh <<'EOF'
#!/bin/sh
# start.sh <ESPHome version>, stdin: the builder's token (first line), then the house's ESP secrets. Run by the house (sudo).
set -eu
V="${1:?the ESPHome version}"
get() {      # get <url> [timeout]: curl, or python3 where there is no curl
    if command -v curl >/dev/null 2>&1; then curl -fsS -m "${2:-60}" "$1"
    else python3 -c 'import sys, urllib.request; sys.stdout.buffer.write(urllib.request.urlopen(sys.argv[1], timeout=float(sys.argv[2])).read())' "$1" "${2:-60}"; fi
}
echo "$V" | grep -Eq '^[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,3}$' || { echo "not a version: $V" >&2; exit 1; }
umask 077
R=/run/yashome-yashgen
mkdir -p "$R"
IFS= read -r TOKEN
[ -n "$TOKEN" ] || { echo "no token on stdin" >&2; exit 1; }
printf '%s\n' "$TOKEN" > "$R/token"
cat > "$R/house-secrets.yaml"
cd /opt/yashome-yashgen
rm -f data/.pulled-back                 # new work here: the house has not got it yet
echo "ESPHOME_VERSION=$V" > .env
docker compose up -d --build --remove-orphans
for _ in $(seq 1 60); do
    get http://127.0.0.1:8090/ 3 >/dev/null 2>&1 && { echo "yashGen is up (ESPHome $V)"; exit 0; }
    sleep 2
done
echo "it started but does not answer on 127.0.0.1:8090: docker compose logs in /opt/yashome-yashgen" >&2
exit 1
EOF
cat > stop.sh <<'EOF'
#!/bin/sh
# stop.sh: the builder stopped and the secrets it was given wiped. Run by the house (sudo).
cd /opt/yashome-yashgen && [ -f .env ] && docker compose stop -t 20 >/dev/null 2>&1
rm -rf /run/yashome-yashgen
echo "yashGen stopped"
EOF
chmod 700 start.sh stop.sh
chmod 755 "$DIR"                 # the probe (not root) sees compose.yml: the role is there; data and scripts stay root's
chmod 644 compose.yml Dockerfile
echo "yashGen is ready in $DIR: Settings -> yashGen -> Use starts it (the first start pulls the ESPHome image: minutes)"
