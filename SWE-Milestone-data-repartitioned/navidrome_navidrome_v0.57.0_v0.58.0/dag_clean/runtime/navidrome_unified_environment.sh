#!/bin/sh
# Common, task-neutral Navidrome runtime environment.
export CGO_ENABLED=1
export GOTOOLCHAIN=auto
export GOPROXY=off
export GOSUMDB=off
export GOCACHE="${NAVIDROME_GOCACHE:-/tmp/swe-milestone-navidrome-go-build}"
export PKG_CONFIG_PATH="/opt/swe-milestone-cache/taglib/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export NPM_CONFIG_OFFLINE=true
export NPM_CONFIG_AUDIT=false
export NPM_CONFIG_FUND=false
export NPM_CONFIG_UPDATE_NOTIFIER=false
export NPM_CONFIG_CACHE=/opt/swe-milestone-cache/npm
# The persistent UI dependency closure lives at a path whose final component is
# literally "node_modules".  Node can therefore resolve sibling ESM packages
# after following /testbed/ui/node_modules without global symlink flags.  In
# particular, do not set --preserve-symlinks-main: it breaks /usr/bin/npm.
umask 0000
if command -v git >/dev/null 2>&1; then
  git config --global --add safe.directory /testbed >/dev/null 2>&1 || true
fi
