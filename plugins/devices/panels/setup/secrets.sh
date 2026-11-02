#!/bin/sh
# make secrets, panels: the discover companion's folder, made by this user before docker would make it as root.
set -eu
. tools/envlib.sh
mkdir -p "$(house_dir)/plugin-state/panels"
