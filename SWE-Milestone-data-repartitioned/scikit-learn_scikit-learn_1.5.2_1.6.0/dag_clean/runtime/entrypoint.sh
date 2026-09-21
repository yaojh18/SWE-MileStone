#!/usr/bin/env sh
set -eu

. /opt/swe-milestone-unified/sklearn_environment.sh
mkdir -p "$HOME"
cd /testbed
if [ "$#" -eq 0 ]; then
  set -- /bin/bash
fi
exec "$@"
