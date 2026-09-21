#!/usr/bin/env sh
# Task-independent environment shared by every cleaned Dubbo node.
#
# This file is installed as an Apptainer image environment fragment.  It must
# not inspect an endpoint, milestone, patch, test list, or host home directory.

export HOME=/tmp/swe-milestone-home
export MAVEN_CONFIG="$HOME/.m2"
export MAVEN_OPTS="-Dmaven.repo.local=/opt/swe-milestone-unified/maven-repository -Dmaven.javadoc.skip=true -Dspotless.check.skip=true -Duser.home=$HOME"
export PATH="/opt/swe-milestone-unified/bin:${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
export LANG=C.UTF-8
export LC_ALL=C.UTF-8

# The runtime is intentionally offline.  Do not inherit scheduler/login-node
# proxy configuration into Maven or test services.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
unset ALL_PROXY all_proxy NO_PROXY no_proxy
