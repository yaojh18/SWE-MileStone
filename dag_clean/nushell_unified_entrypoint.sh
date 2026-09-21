#!/bin/sh
set -eu
umask 000
ulimit -n 65536 2>/dev/null || true
. /opt/swe-milestone-unified/nushell_environment.sh
cd /testbed
if [ "$#" -eq 0 ]; then
    exec /bin/bash
fi
exec "$@"
