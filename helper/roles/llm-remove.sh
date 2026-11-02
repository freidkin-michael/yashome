#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this) to take the house's big model off it: Ollama and its models.
set -eu
U="${SUDO_USER:-}"
H=$(getent passwd "${U:-root}" | cut -d: -f6)
if [ -d "$H/ollama" ] && grep -qi microsoft /proc/version 2>/dev/null; then
    pkill -u "$U" -f "$H/ollama/bin/ollama" 2>/dev/null || true
    rm -rf "$H/ollama"
    echo "Ollama removed from $H/ollama (WSL)"
    exit 0
fi
if command -v ollama >/dev/null 2>&1; then
    systemctl disable --now ollama 2>/dev/null || true
    rm -f /etc/systemd/system/ollama.service "$(command -v ollama)"
    rm -rf /usr/share/ollama /usr/local/lib/ollama
    userdel ollama 2>/dev/null || true
    groupdel ollama 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    echo "Ollama removed"
else
    echo "no Ollama here"
fi
