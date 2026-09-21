#!/bin/sh
set -eu
. /opt/swe-milestone-unified/navidrome_environment.sh
if [ "$#" -eq 0 ]; then
  exec /bin/sh
fi
exec "$@"
