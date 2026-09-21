#!/usr/bin/env bash
# DAG-wide, task-independent source-tree normalization for the Dubbo experiment.
#
# This script intentionally does not contain a milestone id, endpoint ref, test
# list, or problem-statement input.  It is run on every raw endpoint checkout.
# Runtime dependencies are supplied by the pinned base-offline SIF; this script
# only removes derived state and normalizes repository-local Git settings.

set -Eeuo pipefail

repo=${1:-.}
cd "$repo"

git rev-parse --is-inside-work-tree >/dev/null

# Derived Maven state must never become part of a node tree or influence a
# later endpoint.  Worktrees are fresh, but keeping this cleanup here makes the
# preprocessor independently idempotent and safe for resume/review workflows.
find . -type d -name target -prune -exec rm -rf -- {} +
rm -rf -- .tmp/dag-clean .mvn/timing.properties

# Any source-tree repair belongs to the endpoint-local Docker overlay.  In
# particular, dependency/POM fixes, module deletion, test deletion, and
# START/END-specific edits are deliberately forbidden here.  Keeping the
# repository index unchanged is an executable invariant checked by the DAG
# builder, not merely a convention documented in this file.

# These settings live in Git metadata rather than the committed tree.  They
# make the same normalized node runnable after it is materialized in a sandbox.
git config user.name "SWE Milestone DAG Cleaner"
git config user.email "swe-milestone-dag-clean@example.invalid"
git config core.autocrlf false
git config core.filemode true

# Fail if preprocessing accidentally leaves a merge/cherry-pick operation.
if git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1; then
  echo "preprocessor found an unfinished merge" >&2
  exit 20
fi
if git rev-parse -q --verify CHERRY_PICK_HEAD >/dev/null 2>&1; then
  echo "preprocessor found an unfinished cherry-pick" >&2
  exit 21
fi
