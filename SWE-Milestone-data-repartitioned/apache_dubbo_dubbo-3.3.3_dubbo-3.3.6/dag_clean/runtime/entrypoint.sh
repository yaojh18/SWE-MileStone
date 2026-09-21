#!/usr/bin/env sh
# Docker-compatible counterpart of Apptainer's image environment fragment.
set -eu
. /opt/swe-milestone-unified/unified_dubbo_environment.sh
exec "$@"
