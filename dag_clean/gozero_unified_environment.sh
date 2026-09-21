#!/bin/sh
# Common, task-neutral go-zero runtime contract.
export PATH="/usr/local/go/bin:${PATH}"
export GOPATH=/go
export GOMODCACHE=/go/pkg/mod
export GOCACHE=/tmp/swe-milestone-go-build
# The download cache is laid out as a Go file proxy.  This keeps resolution
# fully offline while still allowing endpoint tasks to add a requirement that
# is already present in the reviewed dependency closure.
export GOPROXY=file:///go/pkg/mod/cache/download,off
export GOSUMDB=off
export GOTOOLCHAIN=local
export GO111MODULE=on
export GOFLAGS=-buildvcs=false
umask 000
