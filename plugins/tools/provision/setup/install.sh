#!/bin/sh
# make install, provision: the house's shared setup-AP key PANELS_AP_PASSWORD (the panels make it too; whichever runs first, the other keeps it).
set -eu
. tools/envlib.sh
v=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
[ -n "$v" ] || { echo "provision: no setup-AP key made" >&2; exit 1; }
put PANELS_AP_PASSWORD "$v" generated
