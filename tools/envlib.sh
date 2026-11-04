# Shared by the make targets and the plug-ins' setup hooks (sourced from the checkout root).
get() {   # the value as compose reads it: the last one, an inline " # comment" and surrounding quotes dropped
    [ -f .env ] || return 0
    _v=$(sed -n "s/^$1=\(.*\)$/\1/p" .env | tail -n 1 | tr -d '\r')
    case "$_v" in
        \"*) _v=${_v#\"}; _v=${_v%%\"*};;
        \'*) _v=${_v#\'}; _v=${_v%%\'*};;
        *) _v=$(printf '%s' "$_v" | sed 's/[[:space:]]#.*$//');;
    esac
    printf '%s\n' "$_v"
}

put() {   # sets the key only if it is empty or missing; the value never goes on a command line
    [ -n "$(get "$1")" ] && return 0
    env_set "$1" "$2" || return 1
    echo "  $1 <- ${3:-set}"
}

_env_locked() {   # runs "$@" under .env.lock - the helper takes it too - when flock exists
    if command -v flock >/dev/null 2>&1; then
        ( flock 9 && "$@" ) 9>>.env.lock
    else
        "$@"
    fi
}

env_set() {   # replaces the key (empty value: drops it), under .env.lock
    _env_locked _env_set "$@"
}

_env_set() {   # a checked copy renamed over .env
    _tmp=$(mktemp ./.env.XXXXXX)
    trap 'rm -f "$_tmp"' EXIT; trap 'rm -f "$_tmp"; exit 130' INT TERM
    chmod 600 "$_tmp"
    _rc=0; grep -v "^$1=" .env > "$_tmp" || _rc=$?
    [ "$_rc" -le 1 ] || { echo "cannot write $_tmp (disk full?): .env left as it was" >&2; return 1; }
    if [ -n "$2" ]; then
        printf '%s=%s\n' "$1" "$2" >> "$_tmp" || { echo "cannot write $_tmp: .env left as it was" >&2; return 1; }
    fi
    [ "$(grep -vc "^$1=" .env)" = "$(grep -vc "^$1=" "$_tmp")" ] \
        || { echo "incomplete copy of .env (disk full?): .env left as it was" >&2; return 1; }
    mv "$_tmp" .env || { rm -f "$_tmp"; trap - EXIT INT TERM; return 1; }
    trap - EXIT INT TERM
}

host_file() {     # 0: the file is there; 1: it is not; 2: unknown - a helper job (YASHOME_JOB=1) sees only its mounts
    [ -f "$1" ] && return 0
    [ "${YASHOME_JOB:-}" = 1 ] && return 2
    return 1
}

plugins_on() {
    printf ',%s,' "$(get PLUGINS | sed 's/[[:space:]]#.*$//' | tr -d "\"' ")"
}

house_dir() {     # HOUSE_DIR: the house's files (devices, settings, plugin-data/) in a directory of their own; empty = here
    _h=$(get HOUSE_DIR | tr -d "\"'")
    case "$_h" in *[[:space:]:\$]*|"~"*) echo "HOUSE_DIR must not contain spaces, ':' or '\$' nor start with ~ (write the path out)" >&2; return 1;; esac
    printf '%s\n' "${_h:-.}"
}

house_check() {   # an empty HOUSE_DIR while this checkout still holds a house would start the house empty
    _h=$(house_dir) || return 1
    [ "$_h" = . ] || [ "$_h" -ef . ] || [ -f "$_h/settings.json" ] || { [ ! -f settings.json ] && [ ! -f mqtt_devices.json ]; } || {
        echo "HOUSE_DIR=$_h has no settings.json but this checkout holds a house: 'make move-house' moves it there" >&2
        return 1; }
}

plugin_args() {   # the plug-in directories pluginlib.py looks in: ./plugins and PLUGINS_EXTRA_DIR
    _ex=$(get PLUGINS_EXTRA_DIR | tr -d "\"'")
    case "$_ex" in *[[:space:]:\$]*|"~"*) echo "PLUGINS_EXTRA_DIR must not contain spaces, ':' or '\$' nor start with ~ (write the path out)" >&2; return 1;; esac
    printf '%s\n' ./plugins "${_ex:-./plugins-extra}"
}

plugin_all() {    # every plug-in present, flat or in a group folder, shipped first; a name is taken once
    _a=$(plugin_args) || return 1
    _out=$(python3 tools/pluginlib.py all $_a) || return 1
    [ -z "$_out" ] || printf '%s\n' "$_out" | sed 's#^\([^./]\)#./\1#'
}

plugin_dirs() {   # the enabled plug-ins and what they require, as the backend resolves them (tools/pluginlib.py);
                  # why one stays off is printed, as Settings shows it
    _a=$(plugin_args) || return 1
    _out=$(python3 tools/pluginlib.py dirs "$(get PLUGINS)" $_a) || return 1
    [ -z "$_out" ] || printf '%s\n' "$_out" | sed 's#^\([^./]\)#./\1#'
}

plugin_stage() {
    _bad=0
    for _d in $(plugin_dirs); do
        [ -f "$_d/setup/$1.sh" ] || continue
        PLUGIN_DIR=$_d sh "$_d/setup/$1.sh" || { echo "  FAIL  $(basename "$_d"): setup/$1.sh" >&2; _bad=$((_bad+1)); }
    done
    return "$_bad"
}

plugin_env_merge() {
    for _d in $(plugin_dirs); do
        [ -f "$_d/setup/env.example" ] || continue
        awk -F= 'FILENAME == ARGV[1] { if ($0 ~ /^[A-Za-z_][A-Za-z0-9_]*=/) have[$1] = 1; next }
                 /^[A-Za-z_][A-Za-z0-9_]*=/ { if (!($1 in have)) { printf "%s%s\n", pending, $0; have[$1] = 1; added = 1 } pending = ""; next }
                 { pending = pending $0 "\n" }
                 END { if (added && pending != "") printf "%s", pending }' .env "$_d/setup/env.example" > .env.plugin.tmp
        if [ -s .env.plugin.tmp ]; then
            _env_locked sh -c 'printf "\n# %s\n" "$1" >> .env && cat .env.plugin.tmp >> .env' _ "$(basename "$_d")"
            echo "  keys of $(basename "$_d") <- setup/env.example"
        fi
        rm -f .env.plugin.tmp
    done
}

backend_only_keep() {   # a house from before MQTT_BACKEND_ONLY: added empty, its broker rights stay as they are
    grep -Eq '^[[:space:]]*(export[[:space:]]+)?MQTT_BACKEND_ONLY[[:space:]]*=' .env && return 0
    _env_locked _backend_only_add && \
        echo "  MQTT_BACKEND_ONLY added empty (as before): set it to home/cmd/# once no node of yours publishes there"
}

_backend_only_add() {   # a checked copy renamed over .env, as _env_set: a newline first if .env lacks its last one
    _tmp=$(mktemp ./.env.XXXXXX) || return 1
    trap 'rm -f "$_tmp"' EXIT; trap 'rm -f "$_tmp"; exit 130' INT TERM
    chmod 600 "$_tmp"
    { cat .env; [ -z "$(tail -c1 .env)" ] || echo
      echo "# home/cmd/# here keeps a node from commanding the house (new installs have it; see CHANGELOG)"
      echo "MQTT_BACKEND_ONLY="; } > "$_tmp" \
        && [ "$(grep -c '' "$_tmp")" -ge "$(grep -c '' .env)" ] && mv "$_tmp" .env \
        || { rm -f "$_tmp"; trap - EXIT INT TERM; echo "cannot write .env: MQTT_BACKEND_ONLY not added" >&2; return 1; }
    trap - EXIT INT TERM
}

yashctl_on_path() {   # ~/.local/bin/yashctl runs this checkout's tools/yashctl; idempotent
    [ "${YASHOME_JOB:-}" = 1 ] && return 0            # a helper job runs in a container: not the house user's shell
    [ "$(id -u)" = 0 ] && return 0                     # root's PATH is not the house user's
    [ -n "${HOME:-}" ] || return 0
    _yb=$HOME/.local/bin; _yw=$_yb/yashctl; _yd=$(pwd -P)
    case "$_yd" in *"'"*) echo "  yashctl: a checkout path with ' is not put on PATH: run $_yd/tools/yashctl"; return 0;; esac
    if grep -q '^# yashome-yashctl$' "$_yw" 2>/dev/null; then
        _yo=$(sed -n "s/^D='\(.*\)'\$/\1/p" "$_yw")
    elif [ -L "$_yw" ] && [ "$(basename "$(readlink "$_yw")")" = yashctl ]; then    # a link (ln -s) to a checkout's own
        _yo=$(dirname "$(dirname "$(readlink "$_yw")")")
    elif [ -e "$_yw" ] || [ -L "$_yw" ]; then
        echo "  $_yw is not Yashome's: left as it is"; return 0
    else
        _yo=$_yd
    fi
    if [ "$_yo" != "$_yd" ] && [ -f "$_yo/tools/yashctl" ]; then
        echo "  $_yw runs the checkout $_yo: left as it is (delete it, then make update here, to switch)"; return 0
    fi
    mkdir -p "$_yb"
    printf '%s\n' '#!/bin/sh' '# yashome-yashctl' "# the house's yashctl (make install / make update writes it): the checkout below" \
        "D='$_yd'" 'P="$D/.venv/bin/python"; [ -x "$P" ] || P=python3' 'exec "$P" "$D/tools/yashctl" "$@"' > "$_yw.tmp"
    chmod 755 "$_yw.tmp"
    if [ ! -L "$_yw" ] && cmp -s "$_yw.tmp" "$_yw"; then rm -f "$_yw.tmp"; else mv -f "$_yw.tmp" "$_yw"; echo "  $_yw -> $_yd/tools/yashctl"; fi
    case ":$PATH:" in *":$_yb:"*) ;; *) echo "  $_yb is not on PATH yet: log in again (~/.profile adds it) or add it";; esac
}
