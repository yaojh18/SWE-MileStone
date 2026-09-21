#!/bin/sh
set -eu
. /opt/swe-milestone-unified/navidrome_environment.sh
cd /testbed
command -v git >/dev/null
command -v go >/dev/null
command -v node >/dev/null
command -v npm >/dev/null
git --version
go version
node --version
npm --version
pkg-config --define-prefix --cflags --libs taglib
go test -tags netgo ./...
if [ "${NAVIDROME_VALIDATE_UI:-0}" = 1 ]; then
  test -d ui/node_modules
  (
    cd ui
    npm exec --offline -- vitest --run --passWithNoTests
  )
fi
