#!/bin/sh
# What the helper runs on a computer (sudo sh -s < this) to take the Matter controller off it: its container and folder. The
# fabric (data/) is kept in /var/backups: without it every device must be reset and paired again; back in data/ it is the same.
set -eu
DIR=/opt/yashome-matter
[ -d "$DIR" ] || { echo "no Matter controller here"; exit 0; }
cd "$DIR"
docker compose down 2>&1 || true
if [ -d data ]; then
    mkdir -p /var/backups
    keep="/var/backups/yashome-matter-$(date +%Y%m%d-%H%M%S)"
    mv data "$keep"
    echo "its fabric is kept in $keep"
fi
cd /
rm -rf "$DIR"
echo "Matter controller removed"
