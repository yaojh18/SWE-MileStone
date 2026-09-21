#!/usr/bin/env sh
# Rebuild the editable scikit-learn installation after switching endpoint state.
# Interpreter selection is intentionally dynamic: the pipeline first probes the
# image and never assumes that `python`, `python3`, or a versioned path exists.

set -eu
. /opt/swe-milestone-unified/sklearn_environment.sh

target_python=
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    target_python=$(command -v "$candidate")
    break
  fi
done
if [ -z "$target_python" ]; then
  echo "no target Python interpreter is available" >&2
  exit 2
fi

cd /testbed
exec "$target_python" -m pip install \
  --no-index \
  --no-build-isolation \
  --disable-pip-version-check \
  --no-deps \
  --editable .
