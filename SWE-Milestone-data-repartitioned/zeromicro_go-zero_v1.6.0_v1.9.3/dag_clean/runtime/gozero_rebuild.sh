#!/bin/sh
set -eu
. /opt/swe-milestone-unified/gozero_environment.sh
cd /testbed
go version
go env GOMODCACHE GOPROXY GOTOOLCHAIN
go list -e -mod=readonly ./...
