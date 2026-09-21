#!/bin/sh
# Rebuild/test compilation contract used after switching endpoint patches.
set -eu
. /opt/swe-milestone-unified/ripgrep_environment.sh
cd /testbed
command -v git >/dev/null
command -v cargo >/dev/null
command -v rustc >/dev/null
test -d /opt/vendor
test -s /usr/local/cargo/config.toml
# The fixed offline vendor cache can legitimately re-resolve Cargo.lock for
# older endpoint trees. Runtime validation permits only Cargo.lock to change.
cargo test --workspace --features pcre2 --no-run --offline
