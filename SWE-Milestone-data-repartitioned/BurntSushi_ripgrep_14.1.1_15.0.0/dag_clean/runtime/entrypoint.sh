#!/bin/sh
set -eu
umask 000
. /opt/swe-milestone-unified/ripgrep_environment.sh
cd /testbed
if [ "$#" -eq 0 ]; then
    exec /bin/bash
fi
exec "$@"
