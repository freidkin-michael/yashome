#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this, its arguments set at the top) to give it the house's big model:
# Ollama and one model. Linux: the official installer (a system service on 127.0.0.1:11434). A WSL one (no systemd service
# there): Ollama in the user's ~/ollama with serve.sh, the model server the house's tunnel starts and keeps for its lifetime.
# Either way Ollama answers on the computer's own loopback only: the house reaches it through its ssh tunnel.
set -eu
MODEL="${1:?the model}"
fail() { echo "llm: $*" >&2; exit 1; }
U="${SUDO_USER:-}"
[ -n "$U" ] || fail "run it through sudo by the user the house logs in as"
H=$(getent passwd "$U" | cut -d: -f6)
if grep -qi microsoft /proc/version 2>/dev/null; then
    D="$H/ollama"
    if [ ! -x "$D/bin/ollama" ]; then
        echo "installing Ollama in $D"
        mkdir -p "$D"
        curl -fsSL https://ollama.com/download/ollama-linux-amd64.tgz | tar xzf - -C "$D"
    fi
    if [ ! -f "$D/serve.sh" ]; then
        cat > "$D/serve.sh" <<'EOF'
#!/bin/sh
# The house's big model server (plug-in computers, role LLM): loopback only, one model at a time, unloaded after 5 min idle.
# Started by the house's tunnel; a server of an earlier session still running: wait on it instead of a second one.
export OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="$HOME/ollama/models" OLLAMA_KEEP_ALIVE=5m OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1
if curl -sf -m 3 http://127.0.0.1:11434/api/version >/dev/null; then
    while curl -sf -m 3 http://127.0.0.1:11434/api/version >/dev/null; do sleep 30; done
    exit 1
fi
exec "$HOME/ollama/bin/ollama" serve
EOF
        chmod 755 "$D/serve.sh"
    fi
    mkdir -p "$D/models"
    chown -R "$U" "$D"
    echo "pulling $MODEL"
    if curl -sf -m 3 http://127.0.0.1:11434/api/version >/dev/null; then
        su "$U" -c "OLLAMA_HOST=127.0.0.1:11434 '$D/bin/ollama' pull '$MODEL'"
    else
        su "$U" -c "OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS='$D/models' '$D/bin/ollama' serve >/dev/null 2>&1 & sleep 5; \
            OLLAMA_HOST=127.0.0.1:11434 '$D/bin/ollama' pull '$MODEL'; r=\$?; kill %1 2>/dev/null; exit \$r"
    fi
else
    if ! command -v ollama >/dev/null 2>&1; then
        echo "installing Ollama (ollama.com/install.sh)"
        curl -fsSL https://ollama.com/install.sh | sh
    fi
    systemctl enable --now ollama >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do curl -sf -m 3 http://127.0.0.1:11434/api/version >/dev/null && break; sleep 2; done
    echo "pulling $MODEL"
    ollama pull "$MODEL"
fi
echo "Ollama with $MODEL is ready (on the computer's loopback; the house reaches it through its tunnel)"
