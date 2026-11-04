#!/bin/sh
# make install, voice: the key the plug-in calls the voice container with.
set -eu
. tools/envlib.sh
put VOICE_TTS_KEY "$(openssl rand -hex 24)" generated
