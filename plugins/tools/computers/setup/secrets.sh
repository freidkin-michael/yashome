#!/bin/sh
# make secrets, computers: the discover companion's folder, made by this user before docker would make it as root.
set -eu
. tools/envlib.sh
mkdir -p "$(house_dir)/plugin-state/computers"
