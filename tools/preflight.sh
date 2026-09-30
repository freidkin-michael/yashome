#!/bin/sh
# make check: can this host run Yashome? Reports, changes nothing. Exit 1 on a hard failure.
set -u
cd "$(dirname "$0")/.." || exit 1
ok=0; bad=0
pass() { printf '  ok    %s\n' "$1"; ok=$((ok+1)); }
fail() { printf '  FAIL  %s\n' "$1"; bad=$((bad+1)); }
warn() { printf '  warn  %s\n' "$1"; }
echo "== tools"
for t in git make curl openssl docker; do command -v "$t" >/dev/null 2>&1 && pass "$t" || fail "$t is not installed"; done
if docker compose version >/dev/null 2>&1; then pass "docker compose v2"; else fail "docker compose v2 plugin missing (docker-compose-v2 / compose-plugin)"; fi
if docker info >/dev/null 2>&1; then pass "docker daemon reachable as $(id -un)"; else fail "cannot talk to the docker daemon: add yourself to the docker group (sudo usermod -aG docker \$USER) and log in again"; fi
echo "== ports"
[ -f .env ] && {
    # read .env as data (KEY=value per line), never execute it: a value with spaces or $ stays a value
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue;; esac
        k=${line%%=*}; v=${line#*=}
        case "$k" in *[!A-Za-z0-9_]*|[0-9]*|'') continue;; esac   # a shell name: no leading digit
        v=${v#\"}; v=${v%\"}; v=${v#\'}; v=${v%\'}
        eval "$k=\$v"      # key checked above; a plain shell variable, not exported to children
    done < .env
}
ports="${DASHBOARD_PORT:-8765} ${MQTT_PORT:-1883}"
for p in $ports; do
    if (command -v ss >/dev/null && ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$p\$") \
       || (command -v netstat >/dev/null && netstat -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$p\$"); then
        if docker compose ps --status running 2>/dev/null | grep -q .; then warn "port $p is in use (by this stack?)"; else fail "port $p is already in use"; fi
    else pass "port $p is free"; fi
done
. tools/envlib.sh
if [ "$(plugins_on)" = ",," ]; then     # before the first install: the plug-ins that would be switched on
    dirs=$(for d in ./plugins/*/; do [ -f "$d/setup/detect.sh" ] && PLUGIN_DIR=$d sh "$d/setup/detect.sh" >/dev/null 2>&1 && echo "${d%/}"; done)
else dirs=$(plugin_dirs); fi
for d in $dirs; do       # a plug-in's own checks (its ports, a coordinator stick)
    [ -f "$d/setup/preflight.sh" ] || continue
    echo "== plug-in $(basename "$d")"
    PLUGIN_DIR=$d sh "$d/setup/preflight.sh" || bad=$((bad+1))
done
echo "== disk"
free_kb=$(df -Pk . | awk 'NR==2{print $4}')
[ "${free_kb:-0}" -gt 2000000 ] && pass "disk: $((free_kb/1024)) MB free" || warn "less than 2 GB free here"
echo
if [ "$bad" = 0 ]; then echo "ready: run 'make install'"; exit 0; else echo "$bad problem(s) above must be fixed first"; exit 1; fi
