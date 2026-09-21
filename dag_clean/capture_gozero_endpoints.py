#!/usr/bin/env python3
"""Capture go-zero evaluator SIFs without assuming target-image Python.

This instance-specific adapter deliberately reuses the already exercised,
resumable capture implementation.  Only the immutable go-zero catalog
constants and the target-side shell probe differ.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tarfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import capture_ripgrep_endpoints as capture


WORKSPACE = "zeromicro_go-zero_v1.6.0_v1.9.3"
EXPECTED_MILESTONES = 30
EXPECTED_GAPS = 30
EXPECTED_EVALUATOR_SIFS = {
    "m001",
    "m003",
    "m004",
    "m005",
    "m007.1",
    "m007.2",
    "m008",
    "m009",
    "m010",
    "m013",
    "m014",
    "m017",
    "m018",
    "m019",
    "m020",
    "m021",
    "m022",
    "m023",
    "m024",
    "m025",
    "m026",
    "m027",
    "m028",
}

CAPTURE_SCRIPT = r"""
set -eu
cd /testbed
git rev-parse HEAD > /capture/head.txt
git show-ref | LC_ALL=C sort > /capture/refs.tsv
: > /capture/milestone_tags.tsv
git for-each-ref --format='%(refname)' refs/tags/milestone-* |
while IFS= read -r ref; do
    case "$ref" in
        *-start|*-end)
            commit=$(git rev-parse "$ref^{commit}")
            tree=$(git rev-parse "$ref^{tree}")
            printf '%s\t%s\t%s\n' "$ref" "$commit" "$tree" \
                >> /capture/milestone_tags.tsv
            ;;
    esac
done
LC_ALL=C sort -o /capture/milestone_tags.tsv /capture/milestone_tags.tsv
git status --porcelain=v1 -z --untracked-files=all > /capture/worktree_status.z
git diff --binary --full-index --no-color HEAD > /capture/worktree.patch
git ls-files --others --exclude-standard -z > /capture/untracked_paths.z
tar --null -T /capture/untracked_paths.z -cf /capture/untracked.tar
git bundle create /capture/repo.bundle --all
: > /capture/probe.tsv
for tool in git go bash sh cc gcc python3 python; do
    if command -v "$tool" >/dev/null 2>&1; then
        path=$(command -v "$tool")
        version=$("$tool" version 2>&1 | head -n 1 || \
                  "$tool" --version 2>&1 | head -n 1 || true)
        printf 'tool\t%s\t%s\t%s\n' "$tool" "$path" "$version" \
            >> /capture/probe.tsv
    else
        printf 'missing\t%s\t\t\n' "$tool" >> /capture/probe.tsv
    fi
done
printf 'head\t%s\n' "$(cat /capture/head.txt)" >> /capture/probe.tsv
printf 'git_dir\t%s\n' "$(git rev-parse --git-dir)" >> /capture/probe.tsv
printf 'shallow\t%s\n' "$(git rev-parse --is-shallow-repository)" \
    >> /capture/probe.tsv
printf 'worktree\t%s\n' "$(git rev-parse --show-toplevel)" \
    >> /capture/probe.tsv
"""

BASE_CAPTURE_SCRIPT = r"""
set -eu
roundtrip=/tmp/gozero-base-roundtrip.$$
printf 'gozero-base\n' > "$roundtrip"
test "$(cat "$roundtrip")" = gozero-base
rm -f "$roundtrip"
for tool in git go bash sh cc gcc python3 python; do
    if command -v "$tool" >/dev/null 2>&1; then
        path=$(command -v "$tool")
        printf 'tool\t%s\t%s\n' "$tool" "$path"
    else
        printf 'missing\t%s\t\n' "$tool"
    fi
done
if test -d /testbed; then
    printf 'testbed\tpresent\n'
else
    printf 'testbed\tabsent\n'
fi
if test -d /testbed/.git; then
    printf 'git_dir\tpresent\n'
else
    printf 'git_dir\tabsent\n'
fi
printf 'bind_roundtrip\twritable-tmpfs\n'
"""


_original_capture_one = capture.capture_one


def capture_one(
    *,
    apptainer: str,
    image_id: str,
    sif: Path,
    output: Path,
    scratch: Path,
    env: Mapping[str, str],
) -> dict[str, Any]:
    if image_id != "base-offline":
        return _original_capture_one(
            apptainer=apptainer,
            image_id=image_id,
            sif=sif,
            output=output,
            scratch=scratch,
            env=env,
        )
    sif_sha256 = capture.sha256_file(sif)
    prior = capture.reusable(output, sif, sif_sha256)
    if prior is not None:
        return {**prior, "capture_source": "validated_cache"}
    if output.exists():
        raise capture.CaptureError(
            f"stale capture must be reviewed or removed explicitly: {output}"
        )
    staging = output.parent / f".{output.name}.tmp.{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        inspect = capture.run(
            [apptainer, "inspect", "--json", str(sif)], env=env
        ).stdout
        (staging / "inspect.json").write_bytes(inspect)
        probe = capture.run(
            [
                apptainer,
                "exec",
                "--cleanenv",
                "--no-home",
                "--contain",
                "--no-mount",
                "cwd",
                "--writable-tmpfs",
                str(sif),
                "/bin/sh",
                "-c",
                BASE_CAPTURE_SCRIPT,
                "capture-gozero-base",
            ],
            env=env,
        ).stdout
        (staging / "probe.tsv").write_bytes(probe)
        for name in (
            "refs.tsv",
            "milestone_tags.tsv",
            "worktree_status.z",
            "worktree.patch",
            "untracked_paths.z",
            "repo.bundle",
        ):
            (staging / name).write_bytes(b"")
        with tarfile.open(staging / "untracked.tar", "w"):
            pass
        for name in capture.ARTIFACTS:
            if not (staging / name).is_file():
                raise capture.CaptureError(
                    f"base-offline did not produce {name}"
                )
        manifest = {
            "schema_version": 1,
            "kind": "gozero_base_sif_capture",
            "status": "validated",
            "created_at": capture.now(),
            "workspace": WORKSPACE,
            "image_id": image_id,
            "capture_source": "live",
            "sif": str(sif.resolve()),
            "sif_bytes": sif.stat().st_size,
            "sif_sha256": sif_sha256,
            "repository_history": "absent",
            "artifacts": [
                capture.artifact_record(staging / name)
                for name in capture.ARTIFACTS
            ],
        }
        capture.write_json_atomic(staging / "manifest.json", manifest)
        os.replace(staging, output)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def configure() -> None:
    capture.WORKSPACE = WORKSPACE
    capture.EXPECTED_MILESTONES = EXPECTED_MILESTONES
    capture.EXPECTED_GAPS = EXPECTED_GAPS
    capture.EXPECTED_EVALUATOR_SIFS = EXPECTED_EVALUATOR_SIFS
    capture.CAPTURE_SCRIPT = CAPTURE_SCRIPT
    capture.capture_one = capture_one


def relabel(path: Path) -> None:
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    kind = payload.get("kind")
    if isinstance(kind, str):
        payload["kind"] = kind.replace("ripgrep", "gozero")
    payload["workspace"] = WORKSPACE
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    configure()
    args = capture.parser().parse_args(argv)
    try:
        result = capture.execute(args)
        output = args.output.resolve()
        relabel(output / "manifest.json")
        relabel(output / "endpoint_consensus.json")
        relabel(output / "capture_progress.json")
        for directory in (output / "captures").iterdir():
            if directory.is_dir():
                relabel(directory / "manifest.json")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "validated" else 42
    except (
        capture.CaptureError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"capture-gozero-endpoints: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
