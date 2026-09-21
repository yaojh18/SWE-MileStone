#!/bin/sh
# Shared runtime only.  Milestone source/test semantics stay in endpoint patches.
export PATH="/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export RUSTUP_HOME="${RUSTUP_HOME:-/usr/local/rustup}"
export CARGO_HOME="${CARGO_HOME:-/usr/local/cargo}"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-/opt/swe-milestone-target}"
export RUST_BACKTRACE="${RUST_BACKTRACE:-1}"
export CARGO_NET_OFFLINE="${CARGO_NET_OFFLINE:-true}"
export CARGO_TERM_COLOR="${CARGO_TERM_COLOR:-never}"
export TERM="${TERM:-xterm}"
