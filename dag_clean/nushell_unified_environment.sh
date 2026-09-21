#!/bin/sh
# Common runtime only; milestone source/test semantics live in endpoint patches.
export PATH="/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export RUSTUP_HOME="${RUSTUP_HOME:-/usr/local/rustup}"
export CARGO_HOME="${CARGO_HOME:-/usr/local/cargo}"
export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.88.0-x86_64-unknown-linux-gnu}"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-/opt/swe-milestone-target}"
export CARGO_NET_OFFLINE="${CARGO_NET_OFFLINE:-true}"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-12}"
export NUSHELL_CARGO_PROFILE="${NUSHELL_CARGO_PROFILE:-ci}"
export NU_LOG_LEVEL="${NU_LOG_LEVEL:-DEBUG}"
export RUST_BACKTRACE="${RUST_BACKTRACE:-1}"
export CARGO_TERM_COLOR="${CARGO_TERM_COLOR:-never}"
export TERM="${TERM:-xterm}"
# The sandbox is materialized outside the target runtime and may have a
# different numeric owner. Keep every Git operation explicit and local.
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0=/testbed
