# Shared by the make targets and the plug-ins' setup hooks (sourced from the checkout root).
get() {   # the value as compose reads it: an inline " # comment" and surrounding quotes dropped
    [ -f .env ] || return 0
    _v=$(sed -n "s/^$1=\(.*\)$/\1/p" .env | head -1 | tr -d '\r')
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

env_set() {   # replaces the key (empty value: drops it); a checked copy renamed over .env
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

plugins_on() {
    printf ',%s,' "$(get PLUGINS | sed 's/[[:space:]]#.*$//' | tr -d "\"' ")"
}

plugin_dirs() {   # the enabled plug-ins, shipped first; a name is taken once
    _on=$(plugins_on)
    _ex=$(get PLUGINS_EXTRA_DIR | tr -d "\"'")
    case "$_ex" in *[[:space:]:]*) echo "PLUGINS_EXTRA_DIR must not contain spaces or ':'" >&2; return 1;; esac
    _seen=,
    for _d in ./plugins/*/ "${_ex:-./plugins-extra}"/*/; do
        [ -f "$_d/__init__.py" ] || continue
        _n=$(basename "$_d")
        case "$_n" in _*) continue;; esac
        case "$_seen" in *",$_n,"*) continue;; esac
        case "$_on" in *",*,"*|*",$_n,"*) _seen="$_seen$_n,"; printf '%s\n' "${_d%/}";; esac
    done
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
            printf '\n# %s\n' "$(basename "$_d")" >> .env; cat .env.plugin.tmp >> .env
            echo "  keys of $(basename "$_d") <- setup/env.example"
        fi
        rm -f .env.plugin.tmp
    done
}
