#!/usr/bin/env python3
"""Build the scikit-learn SWE-Milestone endpoint/transition clean bundle.

This program is intentionally repository-specific.  It does not replay the
evaluator Dockerfiles.  Every endpoint comes from the START/END tag in that
milestone's own runnable evaluator SIF, after the Dockerfile has already moved
the tag to its post-hoist compatibility commit.

All orchestration and JSON generation use the Python interpreter in the outer
configured Pyxis image.  Target-image Python is only probed dynamically and is
never assumed by name or version.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
WORKSPACE = "scikit-learn_scikit-learn_1.5.2_1.6.0"
EXPECTED_IDS = (
    "M01",
    "M03",
    "M04",
    "M06",
    "M11",
    "M12.1",
    "M12.2",
    "M12.3",
    "M12.4",
    "M12.5",
    "M13",
    "M17",
)
EXPECTED_EDGE_COUNT = 14
EXPECTED_EDGES = frozenset(
    {
        ("M01", "M12.5"),
        ("M03", "M12.5"),
        ("M06", "M12.5"),
        ("M11", "M12.5"),
        ("M12.1", "M12.2"),
        ("M12.2", "M01"),
        ("M12.2", "M03"),
        ("M12.2", "M12.3"),
        ("M12.2", "M13"),
        ("M12.3", "M12.4"),
        ("M12.4", "M12.5"),
        ("M12.5", "M04"),
        ("M12.5", "M17"),
        ("M13", "M04"),
    }
)
ANCHOR_ID = "M06"
RUNTIME_BASE_ID = "M06"
REPRESENTATIVE_TRANSITION_ID = "M11"
REQUIRED_TOOLS = ("git", "pytest", "gfortran", "ninja")
ALLOWED_M06_RUNTIME_PACKAGES = {"array_api_compat", "array_api_strict"}
ROOT_BUILD_FILES = {
    "pyproject.toml",
    "meson.build",
    "meson_options.txt",
    "setup.py",
    "setup.cfg",
}


class SklearnCleanError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.tmp."
    )
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
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and process.returncode:
        stderr = process.stderr.decode("utf-8", errors="replace")[-4000:]
        raise SklearnCleanError(
            f"command failed ({' '.join(command)}): {stderr.strip()}"
        )
    return process


def issue(
    severity: str,
    code: str,
    message: str,
    *,
    subject: str | None = None,
    evidence: Any = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "severity": severity,
        "code": code,
        "message": message,
    }
    if subject is not None:
        record["subject"] = subject
    if evidence is not None:
        record["evidence"] = evidence
    return record


def read_catalog_ids(dataset: Path) -> list[str]:
    with (dataset / "milestones.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        return [str(row["id"]) for row in csv.DictReader(handle)]


def read_dependency_edges(dataset: Path) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for name in ("dependencies.csv", "additional_dependencies.csv"):
        path = dataset / name
        if not path.is_file():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            edges.update(
                (str(row["source_id"]), str(row["target_id"]))
                for row in csv.DictReader(handle)
            )
    return edges


def _logical_run_installs(text: str) -> list[list[str]]:
    """Return simple top-level RUN pip-install package lists.

    The scikit Dockerfiles contain large heredocs.  We deliberately recognize
    only a narrow, one-line runtime install form and fail closed on another
    top-level ``RUN pip install`` form.
    """

    installs: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not re.match(r"^RUN\s+pip\s+install(?:\s|$)", stripped):
            continue
        payload = re.sub(r"^RUN\s+pip\s+install\s*", "", stripped)
        if "--editable" in payload or "-e " in payload:
            continue
        tokens = [
            token
            for token in re.split(r"\s+", payload)
            if token
            and not token.startswith("-")
            and token not in {"&&", "\\"}
        ]
        installs.append(tokens)
    return installs


def static_audit(
    *,
    dataset: Path,
    dockerfile_root: Path,
    sif_root: Path,
    common_dockerfile: Path,
    ownership_contract: Path,
) -> dict[str, Any]:
    dataset = dataset.resolve()
    dockerfile_root = dockerfile_root.resolve()
    sif_root = sif_root.resolve()
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    required_inputs = (
        dataset / "metadata.json",
        dataset / "milestones.csv",
        dataset / "dependencies.csv",
        common_dockerfile,
        ownership_contract,
    )
    for path in required_inputs:
        if not path.is_file() or path.stat().st_size <= 0:
            blockers.append(
                issue(
                    "blocker",
                    "missing_input",
                    f"required input is missing or empty: {path}",
                )
            )
    if blockers:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "sklearn_static_input_audit",
            "status": "review_required",
            "created_at": now(),
            "workspace": WORKSPACE,
            "blockers": blockers,
            "warnings": warnings,
        }

    metadata = load_json(dataset / "metadata.json")
    rows = metadata.get("milestones")
    if not isinstance(rows, list):
        raise SklearnCleanError("metadata milestones must be a list")
    metadata_ids = [str(row["id"]) for row in rows]
    catalog_ids = read_catalog_ids(dataset)
    expected = set(EXPECTED_IDS)
    for source, ids in (
        ("metadata", metadata_ids),
        ("milestones.csv", catalog_ids),
    ):
        if set(ids) != expected or len(ids) != len(expected):
            blockers.append(
                issue(
                    "blocker",
                    "milestone_set_drift",
                    f"{source} does not contain the expected 12 milestones",
                    evidence={
                        "missing": sorted(expected - set(ids)),
                        "extra": sorted(set(ids) - expected),
                        "count": len(ids),
                    },
                )
            )

    csv_edges = read_dependency_edges(dataset)
    metadata_edges = {
        (str(parent), str(row["id"]))
        for row in rows
        for parent in row.get("parent_milestones", [])
    }
    topological = metadata.get("topological_order", {}).get("full_order")
    topological_valid = (
        isinstance(topological, list)
        and len(topological) == len(expected)
        and set(map(str, topological)) == expected
    )
    if topological_valid:
        positions = {
            str(milestone_id): index
            for index, milestone_id in enumerate(topological)
        }
        topological_valid = all(
            positions[source] < positions[target]
            for source, target in EXPECTED_EDGES
        )
    if (
        len(csv_edges) != EXPECTED_EDGE_COUNT
        or csv_edges != EXPECTED_EDGES
        or metadata_edges != EXPECTED_EDGES
        or not topological_valid
    ):
        blockers.append(
            issue(
                "blocker",
                "dag_edge_drift",
                (
                    "CSV/metadata edges or declared topological order differ "
                    "from the exact reviewed acyclic 14-edge graph"
                ),
                evidence={
                    "csv_edge_count": len(csv_edges),
                    "metadata_edge_count": len(metadata_edges),
                    "csv_missing": sorted(EXPECTED_EDGES - csv_edges),
                    "csv_extra": sorted(csv_edges - EXPECTED_EDGES),
                    "metadata_missing": sorted(EXPECTED_EDGES - metadata_edges),
                    "metadata_extra": sorted(metadata_edges - EXPECTED_EDGES),
                    "topological_order": topological,
                    "topological_valid": topological_valid,
                },
            )
        )

    sif_rows: list[dict[str, Any]] = []
    docker_rows: list[dict[str, Any]] = []
    for milestone_id in EXPECTED_IDS:
        sif = sif_root / f"{milestone_id.lower()}.sif"
        if not sif.is_file() or sif.stat().st_size <= 0:
            blockers.append(
                issue(
                    "blocker",
                    "missing_evaluator_sif",
                    f"non-empty evaluator SIF is required for {milestone_id}",
                    subject=milestone_id,
                    evidence=str(sif),
                )
            )
        else:
            sif_rows.append(
                {
                    "milestone_id": milestone_id,
                    "path": str(sif),
                    "bytes": sif.stat().st_size,
                }
            )

        dockerfile = dockerfile_root / milestone_id / "Dockerfile"
        if not dockerfile.is_file() or dockerfile.stat().st_size <= 0:
            blockers.append(
                issue(
                    "blocker",
                    "missing_evaluator_dockerfile",
                    f"Dockerfile is required for {milestone_id}",
                    subject=milestone_id,
                    evidence=str(dockerfile),
                )
            )
            continue
        text = dockerfile.read_text(encoding="utf-8", errors="replace")
        row = {
            "milestone_id": milestone_id,
            "path": str(dockerfile),
            "bytes": dockerfile.stat().st_size,
            "sha256": sha256_file(dockerfile),
            "from_common_base": (
                "FROM scikit-learn_scikit-learn_1.5.2_1.6.0/base:latest"
                in text
            ),
            "declares_start_tag": f"milestone-{milestone_id}-start" in text,
            "declares_end_tag": f"milestone-{milestone_id}-end" in text,
            "env_patch_marker_count": text.count("[ENV-PATCH]"),
            "tracked_tree_mutation": bool(
                re.search(
                    r"(?:RUN|COPY)[^\n]*(?:/testbed|sklearn/)",
                    text,
                    flags=re.IGNORECASE,
                )
            ),
            "runtime_pip_installs": _logical_run_installs(text),
        }
        docker_rows.append(row)
        if not row["from_common_base"]:
            blockers.append(
                issue(
                    "blocker",
                    "docker_base_drift",
                    "evaluator Dockerfile does not use the shared scikit base",
                    subject=milestone_id,
                )
            )
        if not row["declares_start_tag"] or not row["declares_end_tag"]:
            blockers.append(
                issue(
                    "blocker",
                    "docker_tag_contract_missing",
                    "evaluator Dockerfile does not name both post-hoist tags",
                    subject=milestone_id,
                )
            )
        installs = {
            token
            for group in row["runtime_pip_installs"]
            for token in group
        }
        if milestone_id == RUNTIME_BASE_ID:
            if installs != ALLOWED_M06_RUNTIME_PACKAGES:
                blockers.append(
                    issue(
                        "blocker",
                        "runtime_superset_drift",
                        "M06 no longer has exactly the reviewed runtime dependency superset",
                        subject=milestone_id,
                        evidence=sorted(installs),
                    )
                )
        elif installs:
            blockers.append(
                issue(
                    "blocker",
                    "unexpected_node_runtime_install",
                    "a non-M06 Dockerfile adds a node-specific runtime package",
                    subject=milestone_id,
                    evidence=sorted(installs),
                )
            )

    ownership = load_json(ownership_contract)
    expected_patterns = metadata.get("test_dirs")
    if ownership.get("test_patterns") != expected_patterns:
        blockers.append(
            issue(
                "blocker",
                "test_ownership_drift",
                "ownership test patterns differ from metadata.test_dirs",
                evidence={
                    "ownership": ownership.get("test_patterns"),
                    "metadata": expected_patterns,
                },
            )
        )
    if ownership.get("default_owner") != "implementation":
        blockers.append(
            issue(
                "blocker",
                "implementation_default_missing",
                "non-test paths must default to implementation ownership",
            )
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "sklearn_static_input_audit",
        "status": "validated" if not blockers else "review_required",
        "created_at": now(),
        "workspace": WORKSPACE,
        "inputs": {
            "dataset": str(dataset),
            "dockerfile_root": str(dockerfile_root),
            "sif_root": str(sif_root),
            "common_dockerfile": str(common_dockerfile.resolve()),
            "common_dockerfile_sha256": sha256_file(common_dockerfile),
            "ownership_contract": str(ownership_contract.resolve()),
            "ownership_contract_sha256": sha256_file(ownership_contract),
        },
        "counts": {
            "milestones": len(metadata_ids),
            "edges": len(csv_edges),
            "evaluator_sifs": len(sif_rows),
            "evaluator_dockerfiles": len(docker_rows),
            "blockers": len(blockers),
            "warnings": len(warnings),
        },
        "milestone_ids": metadata_ids,
        "topological_order": metadata.get("topological_order"),
        "edges": [
            {"source": source, "target": target}
            for source, target in sorted(csv_edges)
        ],
        "sifs": sif_rows,
        "dockerfiles": docker_rows,
        "runtime_authority": {
            "base_milestone": RUNTIME_BASE_ID,
            "reason": (
                "common evaluator base plus the only reviewed runtime-package "
                "superset; /testbed is discarded"
            ),
            "additional_packages": sorted(ALLOWED_M06_RUNTIME_PACKAGES),
        },
        "blockers": blockers,
        "warnings": warnings,
    }


def apptainer_environment(scratch: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in list(env):
        if name.startswith(("APPTAINER", "SINGULARITY")):
            env.pop(name, None)
    cache = scratch / "apptainer-cache"
    temporary = scratch / "apptainer-tmp"
    config = scratch / "apptainer-config"
    for path in (cache, temporary, config):
        path.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "APPTAINER_CACHEDIR": str(cache),
            "APPTAINER_TMPDIR": str(temporary),
            "APPTAINER_CONFIGDIR": str(config),
            "SINGULARITY_CACHEDIR": str(cache),
            "SINGULARITY_TMPDIR": str(temporary),
            "SINGULARITY_CONFIGDIR": str(config),
            "LC_ALL": "C",
        }
    )
    return env


CAPTURE_SCRIPT = r"""
set -eu
start_ref=$1
end_ref=$2
milestone_id=$3
cd /testbed
test "$(git rev-parse --show-toplevel)" = /testbed
start_commit=$(git rev-parse "${start_ref}^{commit}")
end_commit=$(git rev-parse "${end_ref}^{commit}")
start_tree=$(git rev-parse "${start_ref}^{tree}")
end_tree=$(git rev-parse "${end_ref}^{tree}")
head_commit=$(git rev-parse "HEAD^{commit}")
tracked_status=$(git status --porcelain=v1 --untracked-files=no)
printf 'identity\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$milestone_id" "$start_commit" "$start_tree" "$end_commit" "$end_tree" "$head_commit"
if [ -n "$tracked_status" ]; then
  printf 'tracked_status\tdirty\t%s\n' "$(printf '%s' "$tracked_status" | tr '\t\r\n' '   ')"
else
  printf 'tracked_status\tclean\t\n'
fi

emit_tool() {
  tool=$1
  if location=$(command -v "$tool" 2>/dev/null); then
    version=$("$tool" --version 2>&1 | head -n 1 || true)
    version=$(printf '%s' "$version" | tr '\t\r\n' '   ')
    printf 'tool\t%s\t%s\t%s\n' "$tool" "$location" "$version"
  else
    printf 'tool\t%s\tabsent\t\n' "$tool"
  fi
}
for tool in git python3 python pip3 pip pytest cc gcc gfortran ninja; do
  emit_tool "$tool"
done

pip_tool=
for candidate in pip3 pip; do
  if command -v "$candidate" >/dev/null 2>&1; then
    pip_tool=$candidate
    break
  fi
done
if [ -n "$pip_tool" ]; then
  for package in array-api-compat array-api-strict scikit-learn; do
    version=$(
      "$pip_tool" show "$package" 2>/dev/null |
        sed -n 's/^Version: //p' | head -n 1 || true
    )
    if [ -n "$version" ]; then
      printf 'package\t%s\tpresent\t%s\n' "$package" "$version"
    else
      printf 'package\t%s\tabsent\t\n' "$package"
    fi
  done
fi

probe=/tmp/swe-milestone-sklearn-write-smoke
printf 'SKLEARN_SIF_WRITE_OK' > "$probe"
test "$(cat "$probe")" = SKLEARN_SIF_WRITE_OK
printf 'writable_tmpfs\tpassed\t%s\n' "$probe"
git fsck --connectivity-only --strict --no-progress >/dev/null
git bundle create /capture/endpoint.bundle "$start_ref" "$end_ref"
"""


def parse_capture_tsv(raw: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tools": {},
        "packages": {},
    }
    for line in raw.splitlines():
        fields = line.split("\t")
        if not fields:
            continue
        kind = fields[0]
        if kind == "identity" and len(fields) == 7:
            result.update(
                {
                    "milestone_id": fields[1],
                    "start_commit": fields[2],
                    "start_tree": fields[3],
                    "end_commit": fields[4],
                    "end_tree": fields[5],
                    "head_commit": fields[6],
                }
            )
        elif kind == "tracked_status" and len(fields) >= 2:
            result["tracked_status"] = fields[1]
            result["tracked_status_detail"] = fields[2] if len(fields) > 2 else ""
        elif kind == "tool" and len(fields) >= 3:
            result["tools"][fields[1]] = {
                "path": fields[2],
                "version": fields[3] if len(fields) > 3 else "",
            }
        elif kind == "package" and len(fields) >= 3:
            result["packages"][fields[1]] = {
                "status": fields[2],
                "version": fields[3] if len(fields) > 3 else "",
            }
        elif kind == "writable_tmpfs" and len(fields) >= 2:
            result["writable_tmpfs"] = fields[1]
    required = {
        "milestone_id",
        "start_commit",
        "start_tree",
        "end_commit",
        "end_tree",
        "head_commit",
        "tracked_status",
        "writable_tmpfs",
    }
    missing = required - set(result)
    if missing:
        raise SklearnCleanError(
            f"capture output lacks required fields: {sorted(missing)}"
        )
    return result


def capture_sif(
    *,
    apptainer: str,
    sif: Path,
    milestone: Mapping[str, Any],
    output: Path,
    env: Mapping[str, str],
) -> dict[str, Any]:
    output.mkdir(parents=True)
    inspect_process = run(
        [apptainer, "inspect", "--json", str(sif)],
        env=env,
    )
    (output / "inspect.json").write_bytes(inspect_process.stdout)
    start_ref = f"refs/tags/{milestone['tag_name_start']}"
    end_ref = f"refs/tags/{milestone['tag_name_end']}"
    process = run(
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
            f"{output}:/capture",
            str(sif),
            "/bin/sh",
            "-c",
            CAPTURE_SCRIPT,
            "capture-sklearn",
            start_ref,
            end_ref,
            str(milestone["id"]),
        ],
        env=env,
    )
    raw = process.stdout.decode("utf-8", errors="replace")
    (output / "capture.tsv").write_text(raw, encoding="utf-8")
    bundle = output / "endpoint.bundle"
    if not bundle.is_file() or bundle.stat().st_size <= 0:
        raise SklearnCleanError(f"capture did not produce a bundle: {sif}")
    parsed = parse_capture_tsv(raw)
    parsed.update(
        {
            "capture_source": "live",
            "sif": str(sif.resolve()),
            "sif_bytes": sif.stat().st_size,
            "sif_sha256": sha256_file(sif),
            "inspect": "inspect.json",
            "capture_tsv": "capture.tsv",
            "bundle": "endpoint.bundle",
            "bundle_bytes": bundle.stat().st_size,
            "bundle_sha256": sha256_file(bundle),
            "start_ref": start_ref,
            "end_ref": end_ref,
        }
    )
    write_json_atomic(output / "manifest.json", parsed)
    return parsed


def reuse_cached_capture(
    *,
    cache_root: Path,
    sif: Path,
    milestone: Mapping[str, Any],
    output: Path,
) -> dict[str, Any]:
    """Copy one complete, content-validated prior SIF capture.

    A cancelled run may leave a staging directory containing already-complete
    milestone captures.  Reuse is explicit and read-only: incomplete or stale
    cache entries are rejected and the caller performs a fresh capture.
    """

    milestone_id = str(milestone["id"])
    source = cache_root / milestone_id
    required_names = {
        "inspect.json",
        "capture.tsv",
        "endpoint.bundle",
        "manifest.json",
    }
    if not source.is_dir() or source.is_symlink():
        raise SklearnCleanError(f"capture cache entry is absent: {source}")
    observed_names = {
        path.name
        for path in source.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    unsafe = [path.name for path in source.iterdir() if path.is_symlink()]
    if unsafe or observed_names != required_names:
        raise SklearnCleanError(
            f"capture cache entry has an incomplete/unsafe file set: "
            f"files={sorted(observed_names)} symlinks={sorted(unsafe)}"
        )
    manifest = load_json(source / "manifest.json")
    start_ref = f"refs/tags/{milestone['tag_name_start']}"
    end_ref = f"refs/tags/{milestone['tag_name_end']}"
    expected_fields = {
        "milestone_id": milestone_id,
        "start_ref": start_ref,
        "end_ref": end_ref,
        "bundle": "endpoint.bundle",
        "inspect": "inspect.json",
        "capture_tsv": "capture.tsv",
    }
    for name, expected_value in expected_fields.items():
        if manifest.get(name) != expected_value:
            raise SklearnCleanError(
                f"capture cache {milestone_id} field {name} drifted: "
                f"{manifest.get(name)!r} != {expected_value!r}"
            )
    for name in ("inspect.json", "capture.tsv", "endpoint.bundle"):
        path = source / name
        if path.stat().st_size <= 0:
            raise SklearnCleanError(
                f"capture cache {milestone_id} contains an empty {name}"
            )
    try:
        json.loads((source / "inspect.json").read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SklearnCleanError(
            f"capture cache {milestone_id} inspect JSON is invalid"
        ) from exc
    parsed = parse_capture_tsv(
        (source / "capture.tsv").read_text(encoding="utf-8")
    )
    comparison_fields = (
        "milestone_id",
        "start_commit",
        "start_tree",
        "end_commit",
        "end_tree",
        "head_commit",
        "tracked_status",
        "tracked_status_detail",
        "writable_tmpfs",
        "tools",
        "packages",
    )
    for name in comparison_fields:
        if parsed.get(name) != manifest.get(name):
            raise SklearnCleanError(
                f"capture cache {milestone_id} manifest/TSV mismatch: {name}"
            )
    bundle = source / "endpoint.bundle"
    if (
        manifest.get("bundle_bytes") != bundle.stat().st_size
        or manifest.get("bundle_sha256") != sha256_file(bundle)
    ):
        raise SklearnCleanError(
            f"capture cache {milestone_id} bundle identity mismatch"
        )
    if (
        manifest.get("sif_bytes") != sif.stat().st_size
        or manifest.get("sif_sha256") != sha256_file(sif)
    ):
        raise SklearnCleanError(
            f"capture cache {milestone_id} belongs to different SIF bytes"
        )
    shutil.copytree(source, output)
    reused = dict(manifest)
    reused.update(
        {
            "capture_source": "validated_cache",
            "cache_reused_from": str(source.resolve()),
            "sif": str(sif.resolve()),
        }
    )
    write_json_atomic(output / "manifest.json", reused)
    return reused


def target_python_record(capture: Mapping[str, Any]) -> dict[str, str] | None:
    tools = capture.get("tools", {})
    for name in ("python3", "python"):
        row = tools.get(name)
        if isinstance(row, dict) and row.get("path") not in {None, "absent"}:
            return {
                "name": name,
                "path": str(row["path"]),
                "version": str(row.get("version", "")),
            }
    return None


def capture_review(
    captures: Sequence[Mapping[str, Any]],
    metadata_rows: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    python_versions: set[str] = set()
    for row in captures:
        milestone_id = str(row["milestone_id"])
        declared = metadata_rows[milestone_id]
        if row["tracked_status"] != "clean":
            items.append(
                issue(
                    "blocker",
                    "runnable_worktree_dirty",
                    "evaluator SIF has tracked changes outside its post-hoist START tag",
                    subject=milestone_id,
                    evidence=row.get("tracked_status_detail"),
                )
            )
        if row["head_commit"] != row["start_commit"]:
            items.append(
                issue(
                    "blocker",
                    "default_head_not_start",
                    "evaluator SIF does not open at its runnable START tag",
                    subject=milestone_id,
                    evidence={
                        "head": row["head_commit"],
                        "start": row["start_commit"],
                    },
                )
            )
        if row.get("writable_tmpfs") != "passed":
            items.append(
                issue(
                    "blocker",
                    "writable_tmpfs_failed",
                    "target SIF writable-tmpfs smoke did not pass",
                    subject=milestone_id,
                )
            )
        target_python = target_python_record(row)
        if target_python is None:
            items.append(
                issue(
                    "blocker",
                    "target_python_absent",
                    "target SIF has neither python3 nor python after probing",
                    subject=milestone_id,
                )
            )
        else:
            python_versions.add(target_python["version"])
        tools = row.get("tools", {})
        for tool in REQUIRED_TOOLS:
            descriptor = tools.get(tool)
            if not isinstance(descriptor, dict) or descriptor.get("path") == "absent":
                items.append(
                    issue(
                        "blocker",
                        "required_tool_absent",
                        f"required target tool is absent: {tool}",
                        subject=milestone_id,
                    )
                )
        pip_present = any(
            isinstance(tools.get(name), dict)
            and tools[name].get("path") != "absent"
            for name in ("pip3", "pip")
        )
        if not pip_present:
            items.append(
                issue(
                    "blocker",
                    "target_pip_absent",
                    "target SIF has no pip command after probing",
                    subject=milestone_id,
                )
            )
        for role in ("start", "end"):
            runnable = str(row[f"{role}_commit"])
            canonical = str(declared[f"commit_sha_{role}"])
            if runnable != canonical:
                items.append(
                    issue(
                        "warning",
                        "runnable_commit_differs_declared_sha",
                        (
                            "post-hoist runnable tag commit differs from metadata "
                            "canonical provenance; runnable tree remains authoritative"
                        ),
                        subject=f"{milestone_id}:{role}",
                        evidence={
                            "runnable_commit": runnable,
                            "declared_commit": canonical,
                        },
                    )
                )
    if len(python_versions) != 1:
        items.append(
            issue(
                "blocker",
                "target_python_version_drift",
                "evaluator SIFs do not expose one probed Python version",
                evidence=sorted(python_versions),
            )
        )
    runtime = next(
        (row for row in captures if row["milestone_id"] == RUNTIME_BASE_ID),
        None,
    )
    if runtime is None:
        items.append(
            issue(
                "blocker",
                "runtime_base_capture_missing",
                "M06 runtime-base capture is missing",
            )
        )
    else:
        packages = runtime.get("packages", {})
        for package in ("array-api-compat", "array-api-strict"):
            if packages.get(package, {}).get("status") != "present":
                items.append(
                    issue(
                        "blocker",
                        "runtime_superset_package_absent",
                        f"M06 runtime does not contain {package}",
                        subject=RUNTIME_BASE_ID,
                    )
                )
    return items


def initialize_controller(
    *,
    output: Path,
    captures: Sequence[Mapping[str, Any]],
    capture_root: Path,
) -> tuple[Path, list[dict[str, Any]]]:
    controller = output / "controller_repo"
    run(["git", "init", "-q", str(controller)])
    run(["git", "-C", str(controller), "config", "gc.auto", "0"])
    run(["git", "-C", str(controller), "config", "maintenance.auto", "false"])
    refs: list[dict[str, Any]] = []
    for row in captures:
        milestone_id = str(row["milestone_id"])
        bundle = capture_root / milestone_id / str(row["bundle"])
        run(["git", "-C", str(controller), "bundle", "verify", str(bundle)])
        for role in ("start", "end"):
            source = str(row[f"{role}_ref"])
            destination = f"refs/runnable/{milestone_id}/{role}"
            run(
                [
                    "git",
                    "-C",
                    str(controller),
                    "fetch",
                    "--no-tags",
                    "--force",
                    str(bundle),
                    f"{source}:{destination}",
                ]
            )
            commit = (
                run(
                    [
                        "git",
                        "-C",
                        str(controller),
                        "rev-parse",
                        f"{destination}^{{commit}}",
                    ]
                )
                .stdout.decode()
                .strip()
            )
            tree = (
                run(
                    [
                        "git",
                        "-C",
                        str(controller),
                        "rev-parse",
                        f"{destination}^{{tree}}",
                    ]
                )
                .stdout.decode()
                .strip()
            )
            if commit != row[f"{role}_commit"] or tree != row[f"{role}_tree"]:
                raise SklearnCleanError(
                    f"bundle ref drift for {milestone_id}:{role}"
                )
            refs.append(
                {
                    "milestone_id": milestone_id,
                    "role": role,
                    "ref": destination,
                    "commit": commit,
                    "tree": tree,
                }
            )
    anchor_source = f"refs/runnable/{ANCHOR_ID}/start"
    anchor_ref = "refs/dag-clean/anchor"
    run(
        [
            "git",
            "-C",
            str(controller),
            "update-ref",
            anchor_ref,
            anchor_source,
        ]
    )
    run(["git", "-C", str(controller), "repack", "-a", "-d", "--no-write-bitmap-index"])
    # The runnable bundles intentionally preserve their upstream ancestry.
    # Some historical scikit-learn trees use Git's legacy zero-padded filemode
    # encoding.  A full strict scan rejects that unrelated ancestry even though
    # every delivered endpoint tree is readable and is subsequently checked by
    # exact checkout/diff/reconstruction.  Validate the imported ref closure
    # here; endpoint_state_builder and build_state_transitions validate the
    # actual delivered tree and blob contents below.
    run(
        [
            "git",
            "-C",
            str(controller),
            "fsck",
            "--connectivity-only",
            "--strict",
            "--no-dangling",
        ]
    )
    return controller, refs


def build_states_and_transitions(
    *,
    controller: Path,
    dataset: Path,
    ownership_contract: Path,
    output: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from endpoint_state_builder import EndpointSpec, build_endpoint_states
    from build_state_transitions import build, transitions_from_metadata

    endpoints = [
        EndpointSpec(
            f"{milestone_id}:{role}",
            f"refs/runnable/{milestone_id}/{role}",
        )
        for milestone_id in EXPECTED_IDS
        for role in ("start", "end")
    ]
    states_root = output / "states"
    state = build_endpoint_states(
        repo=controller,
        anchor_ref="refs/dag-clean/anchor",
        endpoints=endpoints,
        ownership_contract=ownership_contract,
        output=states_root,
    )
    transitions_root = output / "transitions"
    transitions = build(
        state_root=states_root,
        source_repo=controller,
        transitions=transitions_from_metadata(dataset / "metadata.json"),
        output=transitions_root,
    )
    return state, transitions


def transition_review(
    transitions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    expected_transition_count = len(EXPECTED_IDS) + EXPECTED_EDGE_COUNT
    if transitions.get("transition_count") != expected_transition_count:
        items.append(
            issue(
                "blocker",
                "transition_count_drift",
                "transition bundle does not contain 12 milestones plus 14 gaps",
                evidence=transitions.get("transition_count"),
            )
        )
    rows = {
        str(row["transition_id"]): row
        for row in transitions.get("transitions", [])
    }
    expected_milestones = {f"milestone:{value}" for value in EXPECTED_IDS}
    observed_milestones = {
        key for key, row in rows.items() if row.get("kind") == "milestone"
    }
    if observed_milestones != expected_milestones:
        items.append(
            issue(
                "blocker",
                "milestone_transition_set_drift",
                "milestone transition IDs differ from the expected set",
                evidence={
                    "missing": sorted(expected_milestones - observed_milestones),
                    "extra": sorted(observed_milestones - expected_milestones),
                },
            )
        )
    for milestone_id in EXPECTED_IDS:
        row = rows.get(f"milestone:{milestone_id}")
        if row is None:
            continue
        if not row.get("implementation_paths"):
            items.append(
                issue(
                    "warning",
                    "empty_milestone_implementation_delta",
                    "runnable START/END has no implementation-owned delta",
                    subject=milestone_id,
                )
            )
        if not row.get("test_paths"):
            items.append(
                issue(
                    "warning",
                    "empty_milestone_test_delta",
                    "runnable START/END has no filename-owned test delta",
                    subject=milestone_id,
                )
            )
        environment = sorted(
            {
                *row.get("environment_change_paths", []),
                *(
                    path
                    for path in row.get("implementation_paths", [])
                    if path in ROOT_BUILD_FILES
                ),
            }
        )
        if environment:
            items.append(
                issue(
                    "warning",
                    "milestone_changes_build_contract",
                    (
                        "milestone changes a root build/dependency file; retain it "
                        "in the implementation patch and later expose it in the "
                        "problem statement"
                    ),
                    subject=milestone_id,
                    evidence=environment,
                )
            )
        external = sorted(
            path
            for path in row.get("implementation_paths", [])
            if not path.startswith("sklearn/") and path not in ROOT_BUILD_FILES
        )
        if external:
            items.append(
                issue(
                    "warning",
                    "implementation_outside_primary_source_root",
                    "implementation delta touches paths outside sklearn/ and root build files",
                    subject=milestone_id,
                    evidence=external,
                )
            )
    return items


def _copy_state_delivery(states_root: Path, delivery: Path) -> None:
    shutil.copy2(states_root / "manifest.json", delivery / "state_manifest.json")
    shutil.copytree(states_root / "endpoints", delivery / "states" / "endpoints")


def _copy_transition_delivery(transitions_root: Path, delivery: Path) -> None:
    shutil.copy2(
        transitions_root / "manifest.json",
        delivery / "transition_manifest.json",
    )
    shutil.copytree(
        transitions_root / "transitions",
        delivery / "transitions",
    )


def build_delivery(
    *,
    output: Path,
    state: Mapping[str, Any],
    transitions: Mapping[str, Any],
    ownership_contract: Path,
    common_dockerfile: Path,
    environment_script: Path,
    entrypoint_script: Path,
    rebuild_script: Path,
) -> dict[str, Any]:
    from materialize_agent_anchor import materialize

    delivery = output / "delivery"
    delivery.mkdir()
    _copy_state_delivery(output / "states", delivery)
    _copy_transition_delivery(output / "transitions", delivery)
    for source, destination in (
        (output / "static_audit.json", "static_audit.json"),
        (output / "capture_manifest.json", "capture_manifest.json"),
        (output / "review_queue.json", "review_queue.json"),
        (ownership_contract, "sklearn_ownership_contract.json"),
        (common_dockerfile, "Dockerfile.sklearn-common"),
        (environment_script, "sklearn_unified_environment.sh"),
        (entrypoint_script, "sklearn_unified_entrypoint.sh"),
        (rebuild_script, "sklearn_rebuild.sh"),
    ):
        shutil.copy2(source, delivery / destination)

    endpoints = {
        str(row["endpoint_id"]): row for row in state["endpoints"]
    }
    transition_rows = {
        str(row["transition_id"]): row
        for row in transitions["transitions"]
    }
    artifacts: list[dict[str, Any]] = []
    for milestone_id in EXPECTED_IDS:
        start = endpoints[f"{milestone_id}:start"]
        end = endpoints[f"{milestone_id}:end"]
        transition = transition_rows[f"milestone:{milestone_id}"]
        artifacts.append(
            {
                "milestone_id": milestone_id,
                "start_endpoint": start["endpoint_id"],
                "end_endpoint": end["endpoint_id"],
                "start_tree": transition["start_tree"],
                "end_tree": transition["end_tree"],
                "patches": {
                    "start_implementation": (
                        "states/" + start["implementation_state"]["patch"]["path"]
                    ),
                    "start_test": (
                        "states/" + start["test_state"]["patch"]["path"]
                    ),
                    "milestone_implementation": transition["patches"][
                        "implementation"
                    ]["path"],
                    "milestone_test": transition["patches"]["test"]["path"],
                    "milestone_full": transition["patches"]["full"]["path"],
                },
                "patch_sha256": {
                    "start_implementation": start["implementation_state"][
                        "patch"
                    ]["sha256"],
                    "start_test": start["test_state"]["patch"]["sha256"],
                    "milestone_implementation": transition["patches"][
                        "implementation"
                    ]["sha256"],
                    "milestone_test": transition["patches"]["test"]["sha256"],
                    "milestone_full": transition["patches"]["full"]["sha256"],
                },
                "implementation_paths": transition["implementation_paths"],
                "test_paths": transition["test_paths"],
            }
        )
    artifact_manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "sklearn_milestone_patch_artifacts",
        "status": "validated",
        "milestone_count": len(artifacts),
        "milestones": artifacts,
    }
    write_json_atomic(delivery / "milestone_artifacts.json", artifact_manifest)

    agent_anchor = output / "agent-anchor"
    anchor_manifest_path = output / "agent_anchor.json"
    anchor_manifest = materialize(
        output / "controller_repo",
        output / "states",
        agent_anchor,
        anchor_manifest_path,
    )
    bundle_manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "sklearn_post_hoist_clean_delivery",
        "status": "validated",
        "workspace": WORKSPACE,
        "authority": "per-evaluator-post-hoist-start-end-tags",
        "anchor_tree": anchor_manifest["anchor_tree"],
        "milestone_count": len(EXPECTED_IDS),
        "endpoint_count": state["endpoint_count"],
        "transition_count": transitions["transition_count"],
        "kind_counts": transitions["kind_counts"],
        "runtime_base_milestone": RUNTIME_BASE_ID,
        "representative_transition": REPRESENTATIVE_TRANSITION_ID,
    }
    write_json_atomic(delivery / "bundle_manifest.json", bundle_manifest)
    write_checksums(delivery)
    verify_delivery(delivery, agent_anchor)
    return bundle_manifest


def write_checksums(root: Path) -> None:
    checksum_path = root / "artifact_checksums.sha256"
    rows: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path == checksum_path:
            continue
        if path.is_symlink():
            raise SklearnCleanError(f"delivery contains a symlink: {path}")
        relative = path.relative_to(root).as_posix()
        rows.append(f"{sha256_file(path)}  {relative}")
    checksum_path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def verify_checksums(root: Path) -> int:
    path = root / "artifact_checksums.sha256"
    declared: set[str] = set()
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SklearnCleanError("malformed delivery checksum row")
        if relative in declared:
            raise SklearnCleanError(f"duplicate delivery checksum path: {relative}")
        declared.add(relative)
        target = root / relative
        if (
            not target.is_file()
            or target.is_symlink()
            or sha256_file(target) != digest
        ):
            raise SklearnCleanError(f"delivery checksum mismatch: {relative}")
        count += 1
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != root / "artifact_checksums.sha256"
    }
    if actual != declared:
        raise SklearnCleanError(
            f"delivery file set drift: extra={sorted(actual-declared)} "
            f"missing={sorted(declared-actual)}"
        )
    return count


def reconstruct_with_patches(
    repo: Path,
    patches: Sequence[Path],
) -> str:
    with tempfile.TemporaryDirectory(prefix="sklearn-delivery-index-") as raw:
        index = Path(raw) / "index"
        env = os.environ.copy()
        env.update({"LC_ALL": "C", "GIT_INDEX_FILE": str(index)})
        run(["git", "-C", str(repo), "read-tree", "HEAD"], env=env)
        for patch in patches:
            content = patch.read_bytes()
            if not content:
                continue
            run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "apply",
                    "--cached",
                    "--binary",
                    "--whitespace=nowarn",
                    "-",
                ],
                env=env,
                input_bytes=content,
            )
        return (
            run(["git", "-C", str(repo), "write-tree"], env=env)
            .stdout.decode()
            .strip()
        )


def verify_delivery(root: Path, repo: Path) -> dict[str, Any]:
    root = root.resolve()
    repo = repo.resolve()
    checksum_count = verify_checksums(root)
    bundle = load_json(root / "bundle_manifest.json")
    artifacts = load_json(root / "milestone_artifacts.json")
    if (
        bundle.get("status") != "validated"
        or artifacts.get("status") != "validated"
        or artifacts.get("milestone_count") != len(EXPECTED_IDS)
    ):
        raise SklearnCleanError("delivery manifest is not validated")
    head_tree = (
        run(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"])
        .stdout.decode()
        .strip()
    )
    if head_tree != bundle["anchor_tree"]:
        raise SklearnCleanError("delivery anchor tree mismatch")

    verified: list[dict[str, Any]] = []
    for row in artifacts["milestones"]:
        paths = {
            name: root / relative for name, relative in row["patches"].items()
        }
        for name, path in paths.items():
            if not path.is_file():
                raise SklearnCleanError(
                    f"missing {row['milestone_id']} patch {name}: {path}"
                )
        start_orders = (
            (paths["start_implementation"], paths["start_test"]),
            (paths["start_test"], paths["start_implementation"]),
        )
        for order in start_orders:
            actual = reconstruct_with_patches(repo, order)
            if actual != row["start_tree"]:
                raise SklearnCleanError(
                    f"{row['milestone_id']} START reconstruction mismatch"
                )
        milestone_orders = (
            (
                paths["start_implementation"],
                paths["start_test"],
                paths["milestone_implementation"],
                paths["milestone_test"],
            ),
            (
                paths["start_test"],
                paths["start_implementation"],
                paths["milestone_test"],
                paths["milestone_implementation"],
            ),
        )
        for order in milestone_orders:
            actual = reconstruct_with_patches(repo, order)
            if actual != row["end_tree"]:
                raise SklearnCleanError(
                    f"{row['milestone_id']} END reconstruction mismatch"
                )
        verified.append(
            {
                "milestone_id": row["milestone_id"],
                "start_tree": row["start_tree"],
                "end_tree": row["end_tree"],
                "orders_verified": 4,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "sklearn_embedded_delivery_verification",
        "status": "validated",
        "checksum_file_count": checksum_count,
        "milestone_count": len(verified),
        "milestones": verified,
    }


def review_payload(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row["severity"]) for row in items)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "sklearn_clean_review_queue",
        "status": "clear" if counts["blocker"] == 0 else "review_required",
        "created_at": now(),
        "counts": {
            "total": len(items),
            "blockers": counts["blocker"],
            "warnings": counts["warning"],
        },
        "items": list(items),
    }


def publish_staging(staging: Path, output: Path) -> None:
    if output.exists():
        raise SklearnCleanError(f"refusing to overwrite output: {output}")
    os.replace(staging, output)


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise SklearnCleanError(f"refusing to overwrite output: {output}")
    staging = output.parent / f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    staging.mkdir()
    review_items: list[dict[str, Any]] = []
    static: dict[str, Any] | None = None
    try:
        static = static_audit(
            dataset=args.dataset,
            dockerfile_root=args.dockerfile_root,
            sif_root=args.sif_root,
            common_dockerfile=args.common_dockerfile,
            ownership_contract=args.ownership_contract,
        )
        write_json_atomic(staging / "static_audit.json", static)
        review_items.extend(static["blockers"])
        review_items.extend(static["warnings"])
        if any(row["severity"] == "blocker" for row in review_items):
            queue = review_payload(review_items)
            write_json_atomic(staging / "review_queue.json", queue)
            write_json_atomic(
                staging / "manifest.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "sklearn_post_hoist_clean",
                    "status": "review_required",
                    "phase": "static_audit",
                    "review_queue": "review_queue.json",
                },
            )
            publish_staging(staging, output)
            staging = None
            return load_json(output / "manifest.json")

        apptainer = shutil.which("apptainer")
        if apptainer is None:
            review_items.append(
                issue(
                    "blocker",
                    "outer_apptainer_absent",
                    "configured outer image does not expose apptainer",
                )
            )
            raise SklearnCleanError("apptainer is absent")
        apptainer_version = (
            run([apptainer, "--version"]).stdout.decode(errors="replace").strip()
        )
        (staging / "apptainer.version.txt").write_text(
            apptainer_version + "\n", encoding="utf-8"
        )
        scratch = args.scratch.resolve()
        scratch.mkdir(parents=True, exist_ok=True)
        cache_root = (
            args.capture_cache.resolve()
            if args.capture_cache is not None
            else None
        )
        if cache_root is not None and (
            not cache_root.is_dir() or cache_root.is_symlink()
        ):
            review_items.append(
                issue(
                    "blocker",
                    "capture_cache_root_invalid",
                    "supplied capture cache root is absent, not a directory, or a symlink",
                    evidence=str(cache_root),
                )
            )
        env = apptainer_environment(scratch)
        metadata = load_json(args.dataset / "metadata.json")
        rows = {str(row["id"]): row for row in metadata["milestones"]}
        capture_root = staging / "captures"
        capture_root.mkdir()
        captures: list[dict[str, Any]] = []
        for milestone_id in EXPECTED_IDS:
            try:
                sif = args.sif_root / f"{milestone_id.lower()}.sif"
                if cache_root is not None:
                    try:
                        row = reuse_cached_capture(
                            cache_root=cache_root,
                            sif=sif,
                            milestone=rows[milestone_id],
                            output=capture_root / milestone_id,
                        )
                    except Exception as cache_exc:
                        review_items.append(
                            issue(
                                "warning",
                                "capture_cache_entry_rejected",
                                str(cache_exc),
                                subject=milestone_id,
                            )
                        )
                        row = capture_sif(
                            apptainer=apptainer,
                            sif=sif,
                            milestone=rows[milestone_id],
                            output=capture_root / milestone_id,
                            env=env,
                        )
                else:
                    row = capture_sif(
                        apptainer=apptainer,
                        sif=sif,
                        milestone=rows[milestone_id],
                        output=capture_root / milestone_id,
                        env=env,
                    )
                captures.append(row)
            except Exception as exc:
                review_items.append(
                    issue(
                        "blocker",
                        "evaluator_sif_capture_failed",
                        str(exc),
                        subject=milestone_id,
                    )
                )
        review_items.extend(capture_review(captures, rows))
        capture_manifest = {
            "schema_version": SCHEMA_VERSION,
            "kind": "sklearn_post_hoist_sif_capture",
            "status": (
                "validated"
                if not any(row["severity"] == "blocker" for row in review_items)
                else "review_required"
            ),
            "apptainer_version": apptainer_version,
            "capture_count": len(captures),
            "capture_source_counts": dict(
                Counter(
                    str(row.get("capture_source", "unknown"))
                    for row in captures
                )
            ),
            "capture_cache_root": (
                str(cache_root) if cache_root is not None else None
            ),
            "captures": captures,
        }
        write_json_atomic(staging / "capture_manifest.json", capture_manifest)
        if any(row["severity"] == "blocker" for row in review_items):
            queue = review_payload(review_items)
            write_json_atomic(staging / "review_queue.json", queue)
            write_json_atomic(
                staging / "manifest.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "sklearn_post_hoist_clean",
                    "status": "review_required",
                    "phase": "sif_capture",
                    "review_queue": "review_queue.json",
                },
            )
            publish_staging(staging, output)
            staging = None
            return load_json(output / "manifest.json")

        controller, refs = initialize_controller(
            output=staging,
            captures=captures,
            capture_root=capture_root,
        )
        write_json_atomic(
            staging / "runnable_refs.json",
            {
                "schema_version": SCHEMA_VERSION,
                "status": "validated",
                "anchor_ref": "refs/dag-clean/anchor",
                "refs": refs,
            },
        )
        state, transitions = build_states_and_transitions(
            controller=controller,
            dataset=args.dataset,
            ownership_contract=args.ownership_contract,
            output=staging,
        )
        review_items.extend(transition_review(transitions))
        queue = review_payload(review_items)
        write_json_atomic(staging / "review_queue.json", queue)
        if queue["counts"]["blockers"] == 0:
            build_delivery(
                output=staging,
                state=state,
                transitions=transitions,
                ownership_contract=args.ownership_contract,
                common_dockerfile=args.common_dockerfile,
                environment_script=args.environment_script,
                entrypoint_script=args.entrypoint_script,
                rebuild_script=args.rebuild_script,
            )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "kind": "sklearn_post_hoist_clean",
            "status": (
                "validated" if queue["counts"]["blockers"] == 0
                else "review_required"
            ),
            "phase": "complete" if queue["counts"]["blockers"] == 0 else "review",
            "workspace": WORKSPACE,
            "authority": "per-evaluator-post-hoist-start-end-tags",
            "counts": {
                "milestones": len(EXPECTED_IDS),
                "endpoints": state["endpoint_count"],
                "transitions": transitions["transition_count"],
                "milestone_transitions": transitions["kind_counts"]["milestone"],
                "gap_transitions": transitions["kind_counts"]["gap"],
                "review_blockers": queue["counts"]["blockers"],
                "review_warnings": queue["counts"]["warnings"],
            },
            "outputs": {
                "static_audit": "static_audit.json",
                "capture_manifest": "capture_manifest.json",
                "review_queue": "review_queue.json",
                "states": "states",
                "transitions": "transitions",
                "delivery": "delivery" if queue["counts"]["blockers"] == 0 else None,
                "agent_anchor": "agent-anchor" if queue["counts"]["blockers"] == 0 else None,
            },
        }
        write_json_atomic(staging / "manifest.json", manifest)
        publish_staging(staging, output)
        staging = None
        return load_json(output / "manifest.json")
    except Exception as exc:
        if staging is None:
            raise
        if not any(
            row.get("code") == "internal_pipeline_error" for row in review_items
        ):
            review_items.append(
                issue(
                    "blocker",
                    "internal_pipeline_error",
                    f"{type(exc).__name__}: {exc}",
                )
            )
        queue = review_payload(review_items)
        write_json_atomic(staging / "review_queue.json", queue)
        write_json_atomic(
            staging / "manifest.json",
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "sklearn_post_hoist_clean",
                "status": "review_required",
                "phase": "internal_error",
                "message": str(exc),
                "review_queue": "review_queue.json",
            },
        )
        publish_staging(staging, output)
        staging = None
        return load_json(output / "manifest.json")
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def resume_delivery(args: argparse.Namespace) -> dict[str, Any]:
    """Resume after a delivery-only materialization failure.

    The source run has already captured all SIFs and validated all endpoint and
    transition replays.  This path deliberately reuses those immutable
    artifacts and reruns only the fixed single-anchor/delivery materialization.
    """

    source = args.source_bundle.resolve()
    output = args.output.resolve()
    if output.exists():
        raise SklearnCleanError(f"refusing to overwrite output: {output}")
    required_files = (
        "manifest.json",
        "review_queue.json",
        "static_audit.json",
        "capture_manifest.json",
        "runnable_refs.json",
        "states/manifest.json",
        "transitions/manifest.json",
    )
    for relative in required_files:
        path = source / relative
        if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
            raise SklearnCleanError(
                f"resume source is missing a safe non-empty artifact: {path}"
            )
    for relative in ("controller_repo", "states", "transitions"):
        path = source / relative
        if not path.is_dir() or path.is_symlink():
            raise SklearnCleanError(
                f"resume source is missing a safe directory: {path}"
            )

    source_manifest = load_json(source / "manifest.json")
    source_queue = load_json(source / "review_queue.json")
    static = load_json(source / "static_audit.json")
    capture = load_json(source / "capture_manifest.json")
    runnable = load_json(source / "runnable_refs.json")
    state = load_json(source / "states" / "manifest.json")
    transitions = load_json(source / "transitions" / "manifest.json")
    blockers = [
        row
        for row in source_queue.get("items", [])
        if row.get("severity") == "blocker"
    ]
    expected_failure = (
        source_manifest.get("status") == "review_required"
        and source_manifest.get("phase") == "internal_error"
        and len(blockers) == 1
        and blockers[0].get("code") == "internal_pipeline_error"
        and "anchor tree changed during materialization"
        in str(blockers[0].get("message", ""))
    )
    if not expected_failure:
        raise SklearnCleanError(
            "resume source is not the reviewed anchor-only failure"
        )
    if (
        static.get("status") != "validated"
        or capture.get("status") != "validated"
        or capture.get("capture_count") != len(EXPECTED_IDS)
        or runnable.get("status") != "validated"
        or len(runnable.get("refs", [])) != 2 * len(EXPECTED_IDS)
        or state.get("status") != "validated"
        or state.get("endpoint_count") != 2 * len(EXPECTED_IDS)
        or transitions.get("status") != "validated"
        or transitions.get("transition_count")
        != len(EXPECTED_IDS) + EXPECTED_EDGE_COUNT
        or transitions.get("kind_counts")
        != {"gap": EXPECTED_EDGE_COUNT, "milestone": len(EXPECTED_IDS)}
    ):
        raise SklearnCleanError(
            "resume source denominators or validated statuses changed"
        )
    replay_items = transition_review(transitions)
    if any(row["severity"] == "blocker" for row in replay_items):
        raise SklearnCleanError(
            "resume source no longer passes exact transition review"
        )

    # Validate the exact 24 ref identities before copying any large directory.
    ref_rows = {
        str(row["ref"]): row for row in runnable.get("refs", [])
    }
    controller = source / "controller_repo"
    for milestone_id in EXPECTED_IDS:
        for role in ("start", "end"):
            ref = f"refs/runnable/{milestone_id}/{role}"
            row = ref_rows.get(ref)
            if row is None:
                raise SklearnCleanError(f"resume source lacks runnable ref {ref}")
            observed_commit = (
                run(["git", "-C", str(controller), "rev-parse", f"{ref}^{{commit}}"])
                .stdout.decode()
                .strip()
            )
            observed_tree = (
                run(["git", "-C", str(controller), "rev-parse", f"{ref}^{{tree}}"])
                .stdout.decode()
                .strip()
            )
            if (
                observed_commit != row.get("commit")
                or observed_tree != row.get("tree")
            ):
                raise SklearnCleanError(f"resume runnable ref drift: {ref}")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / (
        f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    )
    staging.mkdir()
    try:
        for name in ("controller_repo", "states", "transitions"):
            shutil.copytree(source / name, staging / name)
        for name in (
            "static_audit.json",
            "capture_manifest.json",
            "runnable_refs.json",
            "apptainer.version.txt",
        ):
            path = source / name
            if path.is_file() and not path.is_symlink():
                shutil.copy2(path, staging / name)

        review_items = [
            dict(row)
            for row in source_queue.get("items", [])
            if row.get("severity") != "blocker"
        ]
        review_items.append(
            issue(
                "warning",
                "anchor_tree_reserialization_resolved",
                (
                    "manual review confirmed the prior archive/index round-trip "
                    "changed the anchor tree; the fixed materializer commits the "
                    "validated tree object directly and copies its reachable "
                    "object closure into the one-commit repository"
                ),
                evidence={
                    "source_bundle": str(source),
                    "previous_error": blockers[0].get("message"),
                },
            )
        )
        queue = review_payload(review_items)
        if queue["counts"]["blockers"] != 0:
            raise SklearnCleanError("resume review queue unexpectedly has blockers")
        write_json_atomic(staging / "review_queue.json", queue)
        write_json_atomic(
            staging / "resume_provenance.json",
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "sklearn_delivery_only_resume",
                "status": "validated",
                "source_bundle": str(source),
                "source_manifest_sha256": sha256_file(source / "manifest.json"),
                "source_state_manifest_sha256": sha256_file(
                    source / "states" / "manifest.json"
                ),
                "source_transition_manifest_sha256": sha256_file(
                    source / "transitions" / "manifest.json"
                ),
                "reused": {
                    "captures": len(EXPECTED_IDS),
                    "endpoints": state["endpoint_count"],
                    "transitions": transitions["transition_count"],
                },
                "rerun_scope": ["agent_anchor", "delivery"],
            },
        )
        build_delivery(
            output=staging,
            state=state,
            transitions=transitions,
            ownership_contract=args.ownership_contract,
            common_dockerfile=args.common_dockerfile,
            environment_script=args.environment_script,
            entrypoint_script=args.entrypoint_script,
            rebuild_script=args.rebuild_script,
        )
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "kind": "sklearn_post_hoist_clean",
            "status": "validated",
            "phase": "resumed_delivery_complete",
            "workspace": WORKSPACE,
            "authority": "per-evaluator-post-hoist-start-end-tags",
            "counts": {
                "milestones": len(EXPECTED_IDS),
                "endpoints": state["endpoint_count"],
                "transitions": transitions["transition_count"],
                "milestone_transitions": transitions["kind_counts"]["milestone"],
                "gap_transitions": transitions["kind_counts"]["gap"],
                "review_blockers": 0,
                "review_warnings": queue["counts"]["warnings"],
            },
            "resume_provenance": "resume_provenance.json",
            "outputs": {
                "static_audit": "static_audit.json",
                "capture_manifest": "capture_manifest.json",
                "review_queue": "review_queue.json",
                "states": "states",
                "transitions": "transitions",
                "delivery": "delivery",
                "agent_anchor": "agent-anchor",
            },
        }
        write_json_atomic(staging / "manifest.json", manifest)
        publish_staging(staging, output)
        staging = None
        return load_json(output / "manifest.json")
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    static_parser = subparsers.add_parser(
        "static-audit", help="validate inputs without opening a SIF"
    )
    for target in (static_parser,):
        target.add_argument("--dataset", type=Path, required=True)
        target.add_argument("--dockerfile-root", type=Path, required=True)
        target.add_argument("--sif-root", type=Path, required=True)
        target.add_argument("--common-dockerfile", type=Path, required=True)
        target.add_argument("--ownership-contract", type=Path, required=True)

    prepare_parser = subparsers.add_parser(
        "prepare", help="capture runnable tags and build the clean bundle"
    )
    prepare_parser.add_argument("--dataset", type=Path, required=True)
    prepare_parser.add_argument("--dockerfile-root", type=Path, required=True)
    prepare_parser.add_argument("--sif-root", type=Path, required=True)
    prepare_parser.add_argument("--common-dockerfile", type=Path, required=True)
    prepare_parser.add_argument("--ownership-contract", type=Path, required=True)
    prepare_parser.add_argument("--environment-script", type=Path, required=True)
    prepare_parser.add_argument("--entrypoint-script", type=Path, required=True)
    prepare_parser.add_argument("--rebuild-script", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--scratch", type=Path, required=True)
    prepare_parser.add_argument(
        "--capture-cache",
        type=Path,
        help=(
            "read-only captures/MID root from an earlier interrupted run; "
            "entries are reused only after full SIF/bundle/manifest validation"
        ),
    )

    resume_parser = subparsers.add_parser(
        "resume-delivery",
        help="reuse validated captures/states/transitions after anchor-only failure",
    )
    resume_parser.add_argument("--source-bundle", type=Path, required=True)
    resume_parser.add_argument("--common-dockerfile", type=Path, required=True)
    resume_parser.add_argument("--ownership-contract", type=Path, required=True)
    resume_parser.add_argument("--environment-script", type=Path, required=True)
    resume_parser.add_argument("--entrypoint-script", type=Path, required=True)
    resume_parser.add_argument("--rebuild-script", type=Path, required=True)
    resume_parser.add_argument("--output", type=Path, required=True)

    verify_parser = subparsers.add_parser(
        "verify-delivery", help="reconstruct every milestone from embedded patches"
    )
    verify_parser.add_argument("--bundle-root", type=Path, required=True)
    verify_parser.add_argument("--repo", type=Path, required=True)
    verify_parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "static-audit":
            result = static_audit(
                dataset=args.dataset,
                dockerfile_root=args.dockerfile_root,
                sif_root=args.sif_root,
                common_dockerfile=args.common_dockerfile,
                ownership_contract=args.ownership_contract,
            )
        elif args.command == "prepare":
            result = prepare(args)
        elif args.command == "resume-delivery":
            result = resume_delivery(args)
        else:
            result = verify_delivery(args.bundle_root, args.repo)
            if args.output is not None:
                write_json_atomic(args.output, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        status = result.get("status")
        return 0 if status in {"validated", "clear"} else 42
    except (
        SklearnCleanError,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        print(f"build-sklearn-clean: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
