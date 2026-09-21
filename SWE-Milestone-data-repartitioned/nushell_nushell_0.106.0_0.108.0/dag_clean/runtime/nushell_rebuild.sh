#!/bin/sh
# Offline compilation contract after selecting an endpoint with nushell_state.sh.
set -eu
. /opt/swe-milestone-unified/nushell_environment.sh
cd /testbed
command -v git >/dev/null
command -v cargo >/dev/null
command -v rustc >/dev/null
cargo metadata --locked --offline --format-version 1 --no-deps >/dev/null
cargo test --locked --no-run --offline --profile ci --workspace --exclude 'nu_plugin_*' -j 12
