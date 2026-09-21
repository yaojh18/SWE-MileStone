#!/bin/sh
set -eu
. /opt/swe-milestone-unified/gozero_environment.sh
mkdir -p "$GOCACHE"
cd /testbed
if [ "$#" -eq 0 ]; then
    set -- /bin/bash
fi
exec "$@"
