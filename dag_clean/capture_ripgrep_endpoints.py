#!/usr/bin/env python3
"""Capture every ripgrep evaluator SIF once and audit endpoint tag consensus.

This is deliberately a ripgrep-only capture gate.  Each image is published as
an independent, content-addressed record so a later failure never requires
recapturing images whose SIF bytes and capture artifacts still validate.
Target-image Python is not used or assumed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


WORKSPACE = "BurntSushi_ripgrep_14.1.1_15.0.0"
EXPECTED_MILESTONES = 24
EXPECTED_GAPS = 16
EXPECTED_EVALUATOR_SIFS = {
    "maintenance_fixes_1_sub-01",
    "maintenance_fixes_1_sub-02",
    "maintenance_style_1",
    "milestone_seed_119407d_1_sub-01",
    "milestone_seed_119407d_1_sub-02",
    "milestone_seed_2924d0c_1",
    "milestone_seed_292bc54_1",
    "milestone_seed_5f5da48_1_sub-01",
    "milestone_seed_5f5da48_1_sub-02",
    "milestone_seed_8c6595c_1",
    "milestone_seed_a6e0be3_1_sub-01",
    "milestone_seed_a6e0be3_1_sub-02",
    "milestone_seed_b610d1c_1",
}
ARTIFACTS = (
    "inspect.json",
    "refs.tsv",
    "milestone_tags.tsv",
    "probe.tsv",
    "worktree_status.z",
    "worktree.patch",
    "untracked_paths.z",
    "untracked.tar",
    "repo.bundle",
)


class CaptureError(RuntimeError):
    pass


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
for tool in git cargo rustc rustup bash sh cc gcc python3 python; do
    if command -v "$tool" >/dev/null 2>&1; then
        path=$(command -v "$tool")
        version=$("$tool" --version 2>&1 | head -n 1 || true)
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


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def run(
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.run(
        list(command),
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and process.returncode:
        stderr = process.stderr.decode("utf-8", errors="replace")[-4000:]
        raise CaptureError(
            f"command failed ({' '.join(command)}): {stderr.strip()}"
        )
    return process


def clean_apptainer_environment(scratch: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("APPTAINER", "SINGULARITY"))
    }
    paths = {
        "APPTAINER_CACHEDIR": scratch / "cache",
        "APPTAINER_TMPDIR": scratch / "tmp",
        "APPTAINER_CONFIGDIR": scratch / "config",
    }
    for key, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env["SINGULARITY_CACHEDIR"] = env["APPTAINER_CACHEDIR"]
    env["SINGULARITY_TMPDIR"] = env["APPTAINER_TMPDIR"]
    env["SINGULARITY_CONFIGDIR"] = env["APPTAINER_CONFIGDIR"]
    return env


def artifact_record(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def reusable(output: Path, sif: Path, sif_sha256: str) -> dict[str, Any] | None:
    manifest_path = output / "manifest.json"
    if not manifest_path.is_file() or output.is_symlink():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        manifest.get("status") != "validated"
        or manifest.get("sif_bytes") != sif.stat().st_size
        or manifest.get("sif_sha256") != sif_sha256
    ):
        return None
    rows = manifest.get("artifacts")
    if not isinstance(rows, list) or {row.get("path") for row in rows} != set(
        ARTIFACTS
    ):
        return None
    for row in rows:
        path = output / str(row["path"])
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != row.get("bytes")
            or sha256_file(path) != row.get("sha256")
        ):
            return None
    return manifest


def capture_one(
    *,
    apptainer: str,
    image_id: str,
    sif: Path,
    output: Path,
    scratch: Path,
    env: Mapping[str, str],
) -> dict[str, Any]:
    sif_sha256 = sha256_file(sif)
    prior = reusable(output, sif, sif_sha256)
    if prior is not None:
        return {**prior, "capture_source": "validated_cache"}
    if output.exists():
        raise CaptureError(
            f"stale capture must be reviewed or removed explicitly: {output}"
        )
    staging = output.parent / f".{output.name}.tmp.{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        inspect = run(
            [apptainer, "inspect", "--json", str(sif)], env=env
        ).stdout
        (staging / "inspect.json").write_bytes(inspect)
        run(
            [
                apptainer,
                "exec",
                "--cleanenv",
                "--no-home",
                "--contain",
                "--no-mount",
                "cwd",
                "--writable-tmpfs",
                "--bind",
                f"{staging}:/capture",
                str(sif),
                "/bin/sh",
                "-c",
                CAPTURE_SCRIPT,
                "capture-ripgrep",
            ],
            env=env,
        )
        (staging / "head.txt").unlink()
        nonempty = {
            "inspect.json",
            "refs.tsv",
            "probe.tsv",
            "untracked.tar",
            "repo.bundle",
        }
        if image_id != "base-offline":
            nonempty.add("milestone_tags.tsv")
        for name in ARTIFACTS:
            path = staging / name
            if not path.is_file():
                raise CaptureError(f"{image_id} did not produce {name}")
            if name in nonempty and path.stat().st_size <= 0:
                raise CaptureError(f"{image_id} produced empty {name}")
        manifest = {
            "schema_version": 1,
            "kind": "ripgrep_evaluator_sif_capture",
            "status": "validated",
            "created_at": now(),
            "workspace": WORKSPACE,
            "image_id": image_id,
            "capture_source": "live",
            "sif": str(sif.resolve()),
            "sif_bytes": sif.stat().st_size,
            "sif_sha256": sif_sha256,
            "artifacts": [artifact_record(staging / name) for name in ARTIFACTS],
        }
        write_json_atomic(staging / "manifest.json", manifest)
        os.replace(staging, output)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def catalog(dataset: Path) -> tuple[list[str], dict[str, dict[str, str]]]:
    with (dataset / "milestones.csv").open(newline="", encoding="utf-8") as h:
        ids = [str(row["id"]) for row in csv.DictReader(h)]
    metadata_path = dataset / "metadata.json"
    metadata: dict[str, dict[str, str]] = {}
    if metadata_path.is_file():
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata = {
            str(row["id"]): row for row in payload.get("milestones", [])
        }
    return ids, metadata


def expected_endpoint_refs(
    ids: Sequence[str], metadata: Mapping[str, Mapping[str, str]]
) -> dict[str, str]:
    result: dict[str, str] = {}
    for milestone_id in ids:
        row = metadata.get(milestone_id, {})
        result[f"{milestone_id}:start"] = "refs/tags/" + str(
            row.get("tag_name_start", f"milestone-{milestone_id}-start")
        )
        result[f"{milestone_id}:end"] = "refs/tags/" + str(
            row.get("tag_name_end", f"milestone-{milestone_id}-end")
        )
    return result


def load_tags(path: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            ref, commit, tree = line.rstrip("\n").split("\t")
            result[ref] = {"commit": commit, "tree": tree}
    return result


def build_consensus(
    *,
    ids: Sequence[str],
    metadata: Mapping[str, Mapping[str, str]],
    captures: Sequence[Mapping[str, Any]],
    output: Path,
) -> dict[str, Any]:
    refs = expected_endpoint_refs(ids, metadata)
    tags_by_image = {
        str(row["image_id"]): load_tags(
            output / "captures" / str(row["image_id"]) / "milestone_tags.tsv"
        )
        for row in captures
        if row["image_id"] != "base-offline"
    }
    endpoints: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for endpoint_id, ref in refs.items():
        observations = {
            image: tags.get(ref)
            for image, tags in tags_by_image.items()
        }
        present = {image: row for image, row in observations.items() if row}
        commit_counts = Counter(row["commit"] for row in present.values())
        tree_counts = Counter(row["tree"] for row in present.values())
        milestone_id = endpoint_id.rsplit(":", 1)[0]
        own = present.get(milestone_id)
        row = {
            "endpoint_id": endpoint_id,
            "ref": ref,
            "observed_images": len(present),
            "missing_images": sorted(set(tags_by_image) - set(present)),
            "commit_distribution": dict(sorted(commit_counts.items())),
            "tree_distribution": dict(sorted(tree_counts.items())),
            "own_sif_observation": own,
            "observations": dict(sorted(present.items())),
            "recoverability": (
                "own_sif_post_hoist"
                if own is not None
                else "cross_sif_only"
                if present
                else "missing_from_all_evaluator_sifs"
            ),
        }
        endpoints.append(row)
        if not present:
            blockers.append(
                {
                    "severity": "blocker",
                    "code": "post_hoist_endpoint_absent",
                    "subject": endpoint_id,
                    "message": "expected post-hoist ref is absent from every evaluator SIF",
                    "ref": ref,
                }
            )
        elif own is None:
            if len(commit_counts) == 1 and len(tree_counts) == 1:
                warnings.append(
                    {
                        "severity": "warning",
                        "code": "cross_sif_endpoint_consensus",
                        "subject": endpoint_id,
                        "message": (
                            "own evaluator SIF is absent, but all available "
                            "cross-SIF observations agree on commit and tree"
                        ),
                        "ref": ref,
                        "observed_images": len(present),
                    }
                )
            else:
                blockers.append(
                    {
                        "severity": "blocker",
                        "code": "cross_sif_endpoint_conflict",
                        "subject": endpoint_id,
                        "message": (
                            "own evaluator SIF is absent and cross-SIF "
                            "observations disagree"
                        ),
                        "ref": ref,
                        "commit_distribution": dict(commit_counts),
                        "tree_distribution": dict(tree_counts),
                    }
                )
    payload = {
        "schema_version": 1,
        "kind": "ripgrep_endpoint_ref_consensus",
        "status": "validated" if not blockers else "review_required",
        "created_at": now(),
        "workspace": WORKSPACE,
        "denominators": {
            "milestones": len(ids),
            "endpoints": len(refs),
            "gaps": EXPECTED_GAPS,
            "captured_evaluator_sifs": len(tags_by_image),
            "captured_base_sifs": 1,
            "captured_images": len(captures),
        },
        "endpoint_refs": endpoints,
        "blockers": blockers,
        "warnings": warnings,
    }
    write_json_atomic(output / "endpoint_consensus.json", payload)
    return payload


def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset = args.dataset.resolve()
    sif_root = args.sif_root.resolve()
    output = args.output.resolve()
    scratch = args.scratch.resolve()
    ids, metadata = catalog(dataset)
    if len(ids) != EXPECTED_MILESTONES or len(set(ids)) != EXPECTED_MILESTONES:
        raise CaptureError(
            f"milestones.csv must contain exactly {EXPECTED_MILESTONES} unique IDs"
        )
    evaluator = {
        path.stem: path
        for path in sif_root.glob("*.sif")
        if path.name != "base-offline.sif"
    }
    if set(evaluator) != EXPECTED_EVALUATOR_SIFS:
        raise CaptureError(
            "evaluator SIF set drift: "
            f"missing={sorted(EXPECTED_EVALUATOR_SIFS - set(evaluator))} "
            f"extra={sorted(set(evaluator) - EXPECTED_EVALUATOR_SIFS)}"
        )
    base = sif_root / "base-offline.sif"
    if not base.is_file() or base.stat().st_size <= 0:
        raise CaptureError(f"base SIF is absent or empty: {base}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "captures").mkdir(exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    apptainer = shutil.which("apptainer")
    if apptainer is None:
        raise CaptureError("apptainer is unavailable in the outer runtime")
    env = clean_apptainer_environment(scratch)
    images = {"base-offline": base, **dict(sorted(evaluator.items()))}
    captures = []
    for image_id, sif in images.items():
        captures.append(
            capture_one(
                apptainer=apptainer,
                image_id=image_id,
                sif=sif,
                output=output / "captures" / image_id,
                scratch=scratch,
                env=env,
            )
        )
        write_json_atomic(
            output / "capture_progress.json",
            {
                "schema_version": 1,
                "kind": "ripgrep_capture_progress",
                "status": "running",
                "completed": len(captures),
                "expected": len(images),
                "completed_image_ids": [
                    str(row["image_id"]) for row in captures
                ],
                "updated_at": now(),
            },
        )
    consensus = build_consensus(
        ids=ids,
        metadata=metadata,
        captures=captures,
        output=output,
    )
    manifest = {
        "schema_version": 1,
        "kind": "ripgrep_capture_preflight",
        "status": consensus["status"],
        "created_at": now(),
        "workspace": WORKSPACE,
        "capture_count": len(captures),
        "expected_capture_count": len(images),
        "live_captures": sum(
            row.get("capture_source") == "live" for row in captures
        ),
        "reused_captures": sum(
            row.get("capture_source") == "validated_cache" for row in captures
        ),
        "milestone_count": len(ids),
        "endpoint_count": len(ids) * 2,
        "gap_count": EXPECTED_GAPS,
        "consensus": "endpoint_consensus.json",
        "blocker_count": len(consensus["blockers"]),
        "warning_count": len(consensus["warnings"]),
    }
    write_json_atomic(output / "manifest.json", manifest)
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--dataset", type=Path, required=True)
    result.add_argument("--sif-root", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--scratch", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = execute(parser().parse_args(argv))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "validated" else 42
    except (CaptureError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"capture-ripgrep-endpoints: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
