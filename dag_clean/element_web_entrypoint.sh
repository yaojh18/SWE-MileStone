#!/usr/bin/env sh
set -eu

. /opt/swe-milestone-element/environment.sh
mkdir -p "$HOME"
git config --global --add safe.directory /testbed

if test -n "${ELEMENT_MILESTONE_ID:-}" || test -n "${ELEMENT_MILESTONE_ROLE:-}"; then
  test -n "${ELEMENT_MILESTONE_ID:-}"
  test -n "${ELEMENT_MILESTONE_ROLE:-}"
  /opt/swe-milestone-element/element-state \
    "$ELEMENT_MILESTONE_ID" "$ELEMENT_MILESTONE_ROLE"
fi

cd /testbed
if test "$#" -eq 0; then
  set -- /bin/bash
fi
exec "$@"
