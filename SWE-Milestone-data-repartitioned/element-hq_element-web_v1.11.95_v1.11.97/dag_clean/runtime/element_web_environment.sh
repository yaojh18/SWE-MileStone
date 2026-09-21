#!/usr/bin/env sh
# Task-independent Element Web runtime environment.

export HOME=/tmp/swe-milestone-element-home
export CI=true
export FORCE_COLOR=true
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=4096}"
export PLAYWRIGHT_BROWSERS_PATH=/opt/swe-milestone-element/playwright-browsers
export YARN_CACHE_FOLDER=/testbed/.element-yarn-cache
export YARN_ENABLE_NETWORK=0

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy NO_PROXY no_proxy
umask 000
