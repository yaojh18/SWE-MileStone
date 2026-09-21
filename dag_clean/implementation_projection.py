#!/usr/bin/env python3
"""Digest-bound projection of one reviewed canonical delta onto a Git tree.

The cleaner often needs to retain unrelated changes which share a file with a
canonical milestone change.  Replacing a complete file with the canonical
preimage/postimage would lose those changes.  This module instead applies the
canonical delta path by path, with zero-context hunks, to a temporary Git
index.  The apparently permissive zero-context operation is made fail-closed
by binding all of the following in a reviewed manifest:

* the canonical event and its first parent;
* the complete input and output trees for every endpoint subject;
* the input/output blob (and mode) for every projected path; and
* the exact binary patch digest for every path.

No worktree or public ref is changed.  The returned tree is written only to
the repository object store and can be consumed by the caller after review.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
MANIFEST_KIND = "reviewed_canonical_delta_projection"
SEQUENCE_MANIFEST_KIND = "reviewed_canonical_delta_projection_sequence"
TARGET_SIDES = frozenset({"preimage", "postimage"})


class ProjectionError(RuntimeError):
    """A reviewed projection cannot be reproduced without guessing."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(
    repo: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    process_env = os.environ.copy()
    process_env["LC_ALL"] = "C"
    if env:
        process_env.update(env)
    process = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=process_env,
        check=False,
    )
    if check and process.returncode:
        error = process.stderr.decode("utf-8", errors="replace").strip()
        raise ProjectionError(
            f"git {' '.join(args)} failed with exit {process.returncode}: {error}"
        )
    return process


def _git_text(repo: Path, *args: str, **kwargs: Any) -> str:
    return _git(repo, *args, **kwargs).stdout.decode("utf-8").strip()


def _repo_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or value.endswith("/"):
        raise ProjectionError(f"unsafe repository path: {value!r}")
    return path.as_posix()


def _resolve_commit(repo: Path, ref: str, *, label: str) -> str:
    process = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False)
    if process.returncode:
        error = process.stderr.decode("utf-8", errors="replace").strip()
        raise ProjectionError(f"cannot resolve {label} {ref!r}: {error}")
    return process.stdout.decode("ascii").strip()


def _entry_from_tree(repo: Path, treeish: str, path: str) -> dict[str, str] | None:
    raw = _git(repo, "ls-tree", "-z", treeish, "--", path).stdout
    records = [item for item in raw.split(b"\0") if item]
    if not records:
        return None
    if len(records) != 1:
        raise ProjectionError(f"multiple entries for {treeish}:{path}")
    try:
        metadata, observed_path = records[0].split(b"\t", 1)
        mode, object_type, oid = metadata.decode("ascii").split(" ", 2)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProjectionError(f"malformed tree entry for {treeish}:{path}") from exc
    if os.fsdecode(observed_path) != path:
        raise ProjectionError(f"tree lookup returned a different path for {path!r}")
    return {"mode": mode, "type": object_type, "oid": oid}


def _entry_from_index(
    repo: Path, index_env: Mapping[str, str], path: str
) -> dict[str, str] | None:
    raw = _git(repo, "ls-files", "--stage", "-z", "--", path, env=index_env).stdout
    records = [item for item in raw.split(b"\0") if item]
    if not records:
        return None
    if len(records) != 1:
        raise ProjectionError(f"unmerged or duplicate temporary-index entry: {path}")
    try:
        metadata, observed_path = records[0].split(b"\t", 1)
        mode, oid, stage = metadata.decode("ascii").split(" ", 2)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProjectionError(f"malformed temporary-index entry: {path}") from exc
    if stage != "0" or os.fsdecode(observed_path) != path:
        raise ProjectionError(f"unexpected temporary-index stage/path for {path}")
    return {"mode": mode, "type": "blob", "oid": oid}


def _canonical_path_patch(
    repo: Path, event_parent: str, event_commit: str, path: str
) -> bytes:
    patch = _git(
        repo,
        "diff",
        "--binary",
        "--full-index",
        "--no-renames",
        "--unified=0",
        event_parent,
        event_commit,
        "--",
        path,
    ).stdout
    if not patch:
        raise ProjectionError(
            f"canonical event does not change selected path {event_commit}:{path}"
        )
    return patch


def _apply_check(
    repo: Path,
    index_env: Mapping[str, str],
    patch: bytes,
    *,
    reverse: bool,
) -> subprocess.CompletedProcess[bytes]:
    args = ["apply", "--cached"]
    if reverse:
        args.append("--reverse")
    args.extend(["--check", "--unidiff-zero", "--whitespace=nowarn"])
    return _git(repo, *args, input_bytes=patch, env=index_env, check=False)


def _apply(
    repo: Path,
    index_env: Mapping[str, str],
    patch: bytes,
    *,
    reverse: bool,
) -> None:
    args = ["apply", "--cached"]
    if reverse:
        args.append("--reverse")
    args.extend(["--unidiff-zero", "--whitespace=nowarn"])
    _git(repo, *args, input_bytes=patch, env=index_env)


def _entry_identity(entry: Mapping[str, Any] | None) -> tuple[str, str, str] | None:
    if entry is None:
        return None
    return (
        str(entry.get("mode", "")),
        str(entry.get("type", "")),
        str(entry.get("oid", "")),
    )


def _binding_subject(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return exactly the executable fields covered by binding_sha256."""

    return {
        "schema_version": payload.get("schema_version"),
        "kind": payload.get("kind"),
        "event_commit": payload.get("event_commit"),
        "event_parent": payload.get("event_parent"),
        "paths": payload.get("paths"),
        "path_patches": payload.get("path_patches"),
        "decisions": payload.get("decisions"),
    }


def bind_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with a digest over all executable projection fields."""

    result = dict(payload)
    result["binding_sha256"] = canonical_sha256(_binding_subject(result))
    return result


def _validate_entry(value: Any, *, label: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"mode", "type", "oid"}:
        raise ProjectionError(f"{label} must be null or an exact Git entry")
    mode = str(value["mode"])
    object_type = str(value["type"])
    oid = str(value["oid"]).lower()
    if not re.fullmatch(r"[0-7]{6}", mode):
        raise ProjectionError(f"invalid mode in {label}: {mode!r}")
    if object_type != "blob":
        raise ProjectionError(f"only blob projection is supported in {label}")
    if not re.fullmatch(r"[0-9a-f]{40,64}", oid):
        raise ProjectionError(f"invalid object ID in {label}: {oid!r}")
    return {"mode": mode, "type": object_type, "oid": oid}


def validate_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate schema and digest without resolving repository objects."""

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ProjectionError("unsupported projection manifest schema")
    if payload.get("kind") != MANIFEST_KIND:
        raise ProjectionError("unexpected projection manifest kind")
    if payload.get("binding_sha256") != canonical_sha256(_binding_subject(payload)):
        raise ProjectionError("projection manifest binding_sha256 mismatch")
    event_commit = str(payload.get("event_commit", "")).lower()
    event_parent = str(payload.get("event_parent", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", event_commit) or not re.fullmatch(
        r"[0-9a-f]{40,64}", event_parent
    ):
        raise ProjectionError("projection manifest lacks full event commit IDs")
    raw_paths = payload.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ProjectionError("projection manifest paths must be a non-empty list")
    paths = [_repo_path(str(item)) for item in raw_paths]
    if paths != sorted(set(paths)):
        raise ProjectionError("projection manifest paths must be unique and sorted")
    patches = payload.get("path_patches")
    if not isinstance(patches, dict) or set(patches) != set(paths):
        raise ProjectionError("path_patches must cover the exact selected path set")
    for path in paths:
        record = patches[path]
        if not isinstance(record, dict):
            raise ProjectionError(f"invalid path_patches record for {path}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", ""))):
            raise ProjectionError(f"invalid patch digest for {path}")
        _validate_entry(record.get("preimage"), label=f"{path} preimage")
        _validate_entry(record.get("postimage"), label=f"{path} postimage")
        if _entry_identity(record.get("preimage")) == _entry_identity(
            record.get("postimage")
        ):
            raise ProjectionError(f"selected path has identical canonical sides: {path}")
    raw_decisions = payload.get("decisions")
    if not isinstance(raw_decisions, list) or not raw_decisions:
        raise ProjectionError("projection manifest decisions must be a non-empty list")
    subjects: set[str] = set()
    for decision in raw_decisions:
        if not isinstance(decision, dict):
            raise ProjectionError("projection decision must be an object")
        subject = str(decision.get("subject", ""))
        if not subject or subject in subjects:
            raise ProjectionError(f"invalid or duplicate projection subject: {subject!r}")
        subjects.add(subject)
        if decision.get("target_side") not in TARGET_SIDES:
            raise ProjectionError(f"invalid target_side for {subject}")
        for field in ("input_commit", "input_tree", "expected_output_tree"):
            if not re.fullmatch(r"[0-9a-f]{40,64}", str(decision.get(field, ""))):
                raise ProjectionError(f"invalid {field} for {subject}")
        for field in ("input_entries", "expected_output_entries"):
            entries = decision.get(field)
            if not isinstance(entries, dict) or set(entries) != set(paths):
                raise ProjectionError(f"{subject} {field} does not cover selected paths")
            for path in paths:
                _validate_entry(entries[path], label=f"{subject} {field} {path}")
    return dict(payload)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionError(f"invalid projection manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectionError("projection manifest root must be an object")
    result = validate_manifest(payload)
    result["manifest_path"] = str(path.resolve())
    result["manifest_sha256"] = file_sha256(path)
    return result


def _project_index(
    *,
    repo: Path,
    event_commit: str,
    event_parent: str,
    paths: Sequence[str],
    input_tree: str,
    target_side: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="implementation-projection-") as root:
        index = Path(root) / "index"
        index_env = {"GIT_INDEX_FILE": str(index)}
        _git(repo, "read-tree", input_tree, env=index_env)
        records: list[dict[str, Any]] = []
        for path in paths:
            patch = _canonical_path_patch(repo, event_parent, event_commit, path)
            preimage = _entry_from_tree(repo, event_parent, path)
            postimage = _entry_from_tree(repo, event_commit, path)
            current = _entry_from_index(repo, index_env, path)
            target = preimage if target_side == "preimage" else postimage
            opposite = postimage if target_side == "preimage" else preimage
            before = current
            forward = _apply_check(repo, index_env, patch, reverse=False)
            reverse = _apply_check(repo, index_env, patch, reverse=True)
            forward_ok = forward.returncode == 0
            reverse_ok = reverse.returncode == 0

            if _entry_identity(current) == _entry_identity(target):
                disposition = "already_exact_target"
            elif _entry_identity(current) == _entry_identity(opposite):
                use_reverse = target_side == "preimage"
                applicable = reverse_ok if use_reverse else forward_ok
                if not applicable:
                    raise ProjectionError(
                        f"exact opposite entry rejects canonical hunk: {path}"
                    )
                _apply(repo, index_env, patch, reverse=use_reverse)
                disposition = "exact_opposite_apply_canonical_hunk"
            elif forward_ok and reverse_ok:
                # Zero-context insertion/deletion hunks can occasionally pass
                # in both directions (for example, after a Docker cleanup has
                # already removed a neighbouring dependency block).  The
                # target side supplies the direction, while the reviewed
                # input/output blob and full-tree bindings below prevent this
                # from becoming an unchecked heuristic.
                use_reverse = target_side == "preimage"
                _apply(repo, index_env, patch, reverse=use_reverse)
                disposition = "reviewed_target_direction_for_ambiguous_hunk"
            elif target_side == "preimage" and reverse_ok:
                _apply(repo, index_env, patch, reverse=True)
                disposition = "reverse_hunk_preserving_unrelated_changes"
            elif target_side == "preimage" and forward_ok:
                disposition = "already_on_preimage_side_with_unrelated_changes"
            elif target_side == "postimage" and forward_ok:
                _apply(repo, index_env, patch, reverse=False)
                disposition = "forward_hunk_preserving_unrelated_changes"
            elif target_side == "postimage" and reverse_ok:
                disposition = "already_on_postimage_side_with_unrelated_changes"
            else:
                forward_error = forward.stderr.decode(errors="replace").strip()
                reverse_error = reverse.stderr.decode(errors="replace").strip()
                raise ProjectionError(
                    f"canonical hunk cannot be projected for {path}: "
                    f"forward={forward_error!r}; reverse={reverse_error!r}"
                )
            after = _entry_from_index(repo, index_env, path)
            records.append(
                {
                    "path": path,
                    "patch_sha256": hashlib.sha256(patch).hexdigest(),
                    "canonical_preimage": preimage,
                    "canonical_postimage": postimage,
                    "input": before,
                    "output": after,
                    "target_side": target_side,
                    "disposition": disposition,
                    "forward_check": forward_ok,
                    "reverse_check": reverse_ok,
                }
            )
        output_tree = _git_text(repo, "write-tree", env=index_env)
    return {"output_tree": output_tree, "paths": records}


def plan_projection(
    *,
    repo: Path,
    subject: str,
    input_ref: str,
    event_ref: str,
    target_side: str,
    paths: Sequence[str],
) -> dict[str, Any]:
    """Produce exact evidence for review; it is not an approval by itself."""

    repo = repo.resolve()
    if target_side not in TARGET_SIDES:
        raise ProjectionError(f"invalid target side: {target_side!r}")
    normalized_paths = sorted({_repo_path(path) for path in paths})
    if len(normalized_paths) != len(paths) or not normalized_paths:
        raise ProjectionError("projection paths must be non-empty and unique")
    input_commit = _resolve_commit(repo, input_ref, label="input")
    input_tree = _git_text(repo, "rev-parse", f"{input_commit}^{{tree}}")
    event_commit = _resolve_commit(repo, event_ref, label="event")
    lineage = _git_text(repo, "rev-list", "--parents", "-n", "1", event_commit).split()
    if len(lineage) != 2:
        raise ProjectionError("canonical event must have exactly one parent")
    event_parent = lineage[1]
    result = _project_index(
        repo=repo,
        event_commit=event_commit,
        event_parent=event_parent,
        paths=normalized_paths,
        input_tree=input_tree,
        target_side=target_side,
    )
    path_patches = {
        item["path"]: {
            "sha256": item["patch_sha256"],
            "preimage": item["canonical_preimage"],
            "postimage": item["canonical_postimage"],
        }
        for item in result["paths"]
    }
    decision = {
        "subject": subject,
        "input_ref": input_ref,
        "input_commit": input_commit,
        "input_tree": input_tree,
        "target_side": target_side,
        "input_entries": {item["path"]: item["input"] for item in result["paths"]},
        "expected_output_entries": {
            item["path"]: item["output"] for item in result["paths"]
        },
        "expected_output_tree": result["output_tree"],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": MANIFEST_KIND,
        "event_commit": event_commit,
        "event_parent": event_parent,
        "paths": normalized_paths,
        "path_patches": path_patches,
        "decisions": [decision],
        "projection_evidence": result["paths"],
    }


def apply_reviewed_projection(
    *,
    repo: Path,
    manifest: Mapping[str, Any],
    subject: str,
    input_ref_override: str | None = None,
) -> dict[str, Any]:
    """Reproduce one digest-bound decision in a temporary index."""

    payload = validate_manifest(manifest)
    repo = repo.resolve()
    event_commit = _resolve_commit(repo, str(payload["event_commit"]), label="event")
    if event_commit != payload["event_commit"]:
        raise ProjectionError("canonical event commit drifted")
    lineage = _git_text(repo, "rev-list", "--parents", "-n", "1", event_commit).split()
    if len(lineage) != 2 or lineage[1] != payload["event_parent"]:
        raise ProjectionError("canonical event parent drifted")
    decisions = [item for item in payload["decisions"] if item["subject"] == subject]
    if len(decisions) != 1:
        raise ProjectionError(f"manifest does not contain exactly one decision for {subject}")
    decision = decisions[0]
    effective_input_ref = input_ref_override or str(decision["input_ref"])
    input_commit = _resolve_commit(repo, effective_input_ref, label=subject)
    input_tree = _git_text(repo, "rev-parse", f"{input_commit}^{{tree}}")
    if input_commit != decision["input_commit"] or input_tree != decision["input_tree"]:
        raise ProjectionError(f"reviewed input ref drifted for {subject}")
    paths = list(payload["paths"])
    for path in paths:
        patch = _canonical_path_patch(
            repo, str(payload["event_parent"]), event_commit, path
        )
        patch_record = payload["path_patches"][path]
        if hashlib.sha256(patch).hexdigest() != patch_record["sha256"]:
            raise ProjectionError(f"canonical patch digest drifted for {path}")
        for side, ref in (("preimage", payload["event_parent"]), ("postimage", event_commit)):
            if _entry_from_tree(repo, str(ref), path) != patch_record[side]:
                raise ProjectionError(f"canonical {side} entry drifted for {path}")
        if _entry_from_tree(repo, input_tree, path) != decision["input_entries"][path]:
            raise ProjectionError(f"reviewed input entry drifted for {subject}:{path}")
    result = _project_index(
        repo=repo,
        event_commit=event_commit,
        event_parent=str(payload["event_parent"]),
        paths=paths,
        input_tree=input_tree,
        target_side=str(decision["target_side"]),
    )
    if result["output_tree"] != decision["expected_output_tree"]:
        raise ProjectionError(
            f"reviewed output tree drifted for {subject}: "
            f"{result['output_tree']} != {decision['expected_output_tree']}"
        )
    observed_outputs = {item["path"]: item["output"] for item in result["paths"]}
    if observed_outputs != decision["expected_output_entries"]:
        raise ProjectionError(f"reviewed output entries drifted for {subject}")
    return {
        "subject": subject,
        "input_ref": effective_input_ref,
        "manifest_input_ref": decision["input_ref"],
        "input_commit": input_commit,
        "input_tree": input_tree,
        "target_side": decision["target_side"],
        "output_tree": result["output_tree"],
        "binding_sha256": payload["binding_sha256"],
        "paths": result["paths"],
    }


def combine_plans(
    plans: Sequence[Mapping[str, Any]], *, rationale: str, reviewer: str
) -> dict[str, Any]:
    """Combine compatible plan results into one manifest ready for review."""

    if not plans:
        raise ProjectionError("at least one projection plan is required")
    first = plans[0]
    shared = {
        field: first[field]
        for field in (
            "schema_version",
            "kind",
            "event_commit",
            "event_parent",
            "paths",
            "path_patches",
        )
    }
    decisions: list[dict[str, Any]] = []
    subjects: set[str] = set()
    for plan in plans:
        for field, expected in shared.items():
            if plan.get(field) != expected:
                raise ProjectionError(f"incompatible projection plan field: {field}")
        raw = plan.get("decisions")
        if not isinstance(raw, list) or len(raw) != 1:
            raise ProjectionError("each plan must contain exactly one decision")
        decision = dict(raw[0])
        subject = str(decision.get("subject", ""))
        if not subject or subject in subjects:
            raise ProjectionError(f"duplicate projection subject: {subject!r}")
        subjects.add(subject)
        decisions.append(decision)
    payload = {
        **shared,
        "decisions": sorted(decisions, key=lambda item: str(item["subject"])),
        "rationale": rationale,
        "reviewer": reviewer,
    }
    return bind_manifest(payload)


def _sequence_binding_subject(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return all executable fields, including the exact step order."""

    return {
        "schema_version": payload.get("schema_version"),
        "kind": payload.get("kind"),
        "decisions": payload.get("decisions"),
    }


def bind_sequence_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a sequence manifest with a digest over its ordered pipeline."""

    result = dict(payload)
    result["binding_sha256"] = canonical_sha256(_sequence_binding_subject(result))
    return result


def _plan_sequence_step(
    *,
    repo: Path,
    ordinal: int,
    step_id: str,
    input_tree: str,
    event_ref: str,
    target_side: str,
    paths: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not step_id:
        raise ProjectionError("projection sequence step_id cannot be empty")
    if target_side not in TARGET_SIDES:
        raise ProjectionError(f"invalid target side for {step_id}: {target_side!r}")
    normalized_paths = sorted({_repo_path(path) for path in paths})
    if len(normalized_paths) != len(paths) or not normalized_paths:
        raise ProjectionError(f"{step_id} paths must be non-empty and unique")
    event_commit = _resolve_commit(repo, event_ref, label=f"{step_id} event")
    lineage = _git_text(repo, "rev-list", "--parents", "-n", "1", event_commit).split()
    if len(lineage) != 2:
        raise ProjectionError(f"{step_id} canonical event must have one parent")
    event_parent = lineage[1]
    result = _project_index(
        repo=repo,
        event_commit=event_commit,
        event_parent=event_parent,
        paths=normalized_paths,
        input_tree=input_tree,
        target_side=target_side,
    )
    step = {
        "ordinal": ordinal,
        "step_id": step_id,
        "event_commit": event_commit,
        "event_parent": event_parent,
        "target_side": target_side,
        "paths": normalized_paths,
        "path_patches": {
            item["path"]: {
                "sha256": item["patch_sha256"],
                "preimage": item["canonical_preimage"],
                "postimage": item["canonical_postimage"],
            }
            for item in result["paths"]
        },
        "input_tree": input_tree,
        "input_entries": {item["path"]: item["input"] for item in result["paths"]},
        "expected_output_tree": result["output_tree"],
        "expected_output_entries": {
            item["path"]: item["output"] for item in result["paths"]
        },
    }
    evidence = {
        "ordinal": ordinal,
        "step_id": step_id,
        "event_commit": event_commit,
        "event_parent": event_parent,
        "input_tree": input_tree,
        "output_tree": result["output_tree"],
        "paths": result["paths"],
    }
    return step, evidence


def plan_projection_sequence(
    *,
    repo: Path,
    subject: str,
    input_ref: str,
    steps: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Plan an ordered projection pipeline without intermediate commits/refs.

    Each step consumes the exact tree written by its predecessor.  ``steps``
    entries require ``step_id``, ``event_ref``, ``target_side``, and ``paths``.
    The returned object is still only a proposal until it is combined into a
    reviewed, digest-bound sequence manifest.
    """

    repo = repo.resolve()
    if not subject:
        raise ProjectionError("projection sequence subject cannot be empty")
    if not steps:
        raise ProjectionError("projection sequence requires at least one step")
    input_commit = _resolve_commit(repo, input_ref, label=f"{subject} input")
    input_tree = _git_text(repo, "rev-parse", f"{input_commit}^{{tree}}")
    current_tree = input_tree
    planned_steps: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    step_ids: set[str] = set()
    for ordinal, raw in enumerate(steps, start=1):
        if not isinstance(raw, Mapping):
            raise ProjectionError(f"{subject} sequence step #{ordinal} is not an object")
        step_id = str(raw.get("step_id", ""))
        if not step_id or step_id in step_ids:
            raise ProjectionError(f"invalid or duplicate sequence step ID: {step_id!r}")
        step_ids.add(step_id)
        raw_paths = raw.get("paths")
        if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
            raise ProjectionError(f"{step_id} paths must be a sequence")
        step, step_evidence = _plan_sequence_step(
            repo=repo,
            ordinal=ordinal,
            step_id=step_id,
            input_tree=current_tree,
            event_ref=str(raw.get("event_ref", "")),
            target_side=str(raw.get("target_side", "")),
            paths=[str(path) for path in raw_paths],
        )
        planned_steps.append(step)
        evidence.append(step_evidence)
        current_tree = step["expected_output_tree"]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": SEQUENCE_MANIFEST_KIND,
        "decisions": [
            {
                "subject": subject,
                "input_ref": input_ref,
                "input_commit": input_commit,
                "input_tree": input_tree,
                "steps": planned_steps,
                "expected_output_tree": current_tree,
            }
        ],
        "projection_evidence": evidence,
    }


def combine_sequence_plans(
    plans: Sequence[Mapping[str, Any]], *, rationale: str, reviewer: str
) -> dict[str, Any]:
    """Combine sequence proposals; each subject retains its own ordered steps."""

    if not plans:
        raise ProjectionError("at least one projection sequence plan is required")
    decisions: list[dict[str, Any]] = []
    subjects: set[str] = set()
    for plan in plans:
        if plan.get("schema_version") != SCHEMA_VERSION or plan.get("kind") != SEQUENCE_MANIFEST_KIND:
            raise ProjectionError("incompatible projection sequence plan")
        raw = plan.get("decisions")
        if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
            raise ProjectionError("each sequence plan must contain one decision")
        decision = dict(raw[0])
        subject = str(decision.get("subject", ""))
        if not subject or subject in subjects:
            raise ProjectionError(f"duplicate projection sequence subject: {subject!r}")
        subjects.add(subject)
        decisions.append(decision)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": SEQUENCE_MANIFEST_KIND,
        "decisions": sorted(decisions, key=lambda item: str(item["subject"])),
        "rationale": rationale,
        "reviewer": reviewer,
    }
    return bind_sequence_manifest(payload)


def validate_sequence_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a complete ordered projection contract without running Git."""

    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ProjectionError("unsupported projection sequence schema")
    if payload.get("kind") != SEQUENCE_MANIFEST_KIND:
        raise ProjectionError("unexpected projection sequence manifest kind")
    if payload.get("binding_sha256") != canonical_sha256(
        _sequence_binding_subject(payload)
    ):
        raise ProjectionError("projection sequence binding_sha256 mismatch")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ProjectionError("projection sequence decisions must be a non-empty list")
    subjects: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ProjectionError("projection sequence decision must be an object")
        subject = str(decision.get("subject", ""))
        if not subject or subject in subjects:
            raise ProjectionError(f"invalid or duplicate sequence subject: {subject!r}")
        subjects.add(subject)
        for field in ("input_commit", "input_tree", "expected_output_tree"):
            if not re.fullmatch(r"[0-9a-f]{40,64}", str(decision.get(field, ""))):
                raise ProjectionError(f"invalid {field} for {subject}")
        steps = decision.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ProjectionError(f"{subject} has no projection sequence steps")
        current_tree = str(decision["input_tree"])
        step_ids: set[str] = set()
        for ordinal, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or step.get("ordinal") != ordinal:
                raise ProjectionError(f"{subject} projection step order is invalid")
            step_id = str(step.get("step_id", ""))
            if not step_id or step_id in step_ids:
                raise ProjectionError(f"invalid or duplicate step ID for {subject}")
            step_ids.add(step_id)
            if step.get("input_tree") != current_tree:
                raise ProjectionError(
                    f"{subject} step {step_id} does not consume the preceding output tree"
                )
            if step.get("target_side") not in TARGET_SIDES:
                raise ProjectionError(f"invalid target side for {subject}:{step_id}")
            for field in ("event_commit", "event_parent", "input_tree", "expected_output_tree"):
                if not re.fullmatch(r"[0-9a-f]{40,64}", str(step.get(field, ""))):
                    raise ProjectionError(f"invalid {field} for {subject}:{step_id}")
            raw_paths = step.get("paths")
            if not isinstance(raw_paths, list) or not raw_paths:
                raise ProjectionError(f"missing paths for {subject}:{step_id}")
            paths = [_repo_path(str(path)) for path in raw_paths]
            if paths != sorted(set(paths)):
                raise ProjectionError(f"unsorted or duplicate paths for {subject}:{step_id}")
            patches = step.get("path_patches")
            if not isinstance(patches, dict) or set(patches) != set(paths):
                raise ProjectionError(f"patch coverage mismatch for {subject}:{step_id}")
            for path in paths:
                patch_record = patches[path]
                if not isinstance(patch_record, dict) or not re.fullmatch(
                    r"[0-9a-f]{64}", str(patch_record.get("sha256", ""))
                ):
                    raise ProjectionError(f"invalid patch record for {subject}:{step_id}:{path}")
                _validate_entry(
                    patch_record.get("preimage"),
                    label=f"{subject}:{step_id}:{path} preimage",
                )
                _validate_entry(
                    patch_record.get("postimage"),
                    label=f"{subject}:{step_id}:{path} postimage",
                )
            for field in ("input_entries", "expected_output_entries"):
                entries = step.get(field)
                if not isinstance(entries, dict) or set(entries) != set(paths):
                    raise ProjectionError(f"entry coverage mismatch for {subject}:{step_id}")
                for path in paths:
                    _validate_entry(entries[path], label=f"{subject}:{step_id}:{field}:{path}")
            current_tree = str(step["expected_output_tree"])
        if current_tree != decision["expected_output_tree"]:
            raise ProjectionError(f"{subject} final sequence tree is inconsistent")
    return dict(payload)


def load_sequence_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionError(f"invalid projection sequence manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProjectionError("projection sequence manifest root must be an object")
    result = validate_sequence_manifest(payload)
    result["manifest_path"] = str(path.resolve())
    result["manifest_sha256"] = file_sha256(path)
    return result


def apply_reviewed_projection_sequence(
    *,
    repo: Path,
    manifest: Mapping[str, Any],
    subject: str,
    input_ref_override: str | None = None,
) -> dict[str, Any]:
    """Reproduce an ordered, digest-bound pipeline using only intermediate trees."""

    payload = validate_sequence_manifest(manifest)
    repo = repo.resolve()
    matches = [item for item in payload["decisions"] if item["subject"] == subject]
    if len(matches) != 1:
        raise ProjectionError(f"sequence manifest lacks exactly one {subject} decision")
    decision = matches[0]
    effective_input_ref = input_ref_override or str(decision["input_ref"])
    input_commit = _resolve_commit(repo, effective_input_ref, label=subject)
    input_tree = _git_text(repo, "rev-parse", f"{input_commit}^{{tree}}")
    if input_commit != decision["input_commit"] or input_tree != decision["input_tree"]:
        raise ProjectionError(f"reviewed sequence input ref drifted for {subject}")
    current_tree = input_tree
    records: list[dict[str, Any]] = []
    for step in decision["steps"]:
        step_id = str(step["step_id"])
        if step["input_tree"] != current_tree:
            raise ProjectionError(f"runtime tree chain drifted before {subject}:{step_id}")
        event_commit = _resolve_commit(repo, str(step["event_commit"]), label=step_id)
        lineage = _git_text(repo, "rev-list", "--parents", "-n", "1", event_commit).split()
        if len(lineage) != 2 or lineage[1] != step["event_parent"]:
            raise ProjectionError(f"canonical lineage drifted for {subject}:{step_id}")
        for path in step["paths"]:
            patch = _canonical_path_patch(repo, lineage[1], event_commit, path)
            patch_record = step["path_patches"][path]
            if hashlib.sha256(patch).hexdigest() != patch_record["sha256"]:
                raise ProjectionError(f"patch digest drifted for {subject}:{step_id}:{path}")
            if _entry_from_tree(repo, lineage[1], path) != patch_record["preimage"]:
                raise ProjectionError(f"preimage drifted for {subject}:{step_id}:{path}")
            if _entry_from_tree(repo, event_commit, path) != patch_record["postimage"]:
                raise ProjectionError(f"postimage drifted for {subject}:{step_id}:{path}")
            if _entry_from_tree(repo, current_tree, path) != step["input_entries"][path]:
                raise ProjectionError(f"input entry drifted for {subject}:{step_id}:{path}")
        result = _project_index(
            repo=repo,
            event_commit=event_commit,
            event_parent=lineage[1],
            paths=step["paths"],
            input_tree=current_tree,
            target_side=str(step["target_side"]),
        )
        outputs = {item["path"]: item["output"] for item in result["paths"]}
        if result["output_tree"] != step["expected_output_tree"]:
            raise ProjectionError(f"output tree drifted for {subject}:{step_id}")
        if outputs != step["expected_output_entries"]:
            raise ProjectionError(f"output entries drifted for {subject}:{step_id}")
        records.append(
            {
                "ordinal": step["ordinal"],
                "step_id": step_id,
                "event_commit": event_commit,
                "target_side": step["target_side"],
                "input_tree": current_tree,
                "output_tree": result["output_tree"],
                "paths": result["paths"],
            }
        )
        current_tree = result["output_tree"]
    if current_tree != decision["expected_output_tree"]:
        raise ProjectionError(f"final output tree drifted for {subject}")
    return {
        "subject": subject,
        "input_ref": effective_input_ref,
        "manifest_input_ref": decision["input_ref"],
        "input_commit": input_commit,
        "input_tree": input_tree,
        "output_tree": current_tree,
        "binding_sha256": payload["binding_sha256"],
        "steps": records,
    }
