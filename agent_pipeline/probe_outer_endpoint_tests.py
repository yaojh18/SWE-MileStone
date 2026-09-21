#!/usr/bin/env python3
"""Probe unresolved merged-test definitions at all four source endpoints.

This program is deliberately read-only with respect to both datasets and the
SIFs.  For one deterministic merge operation it opens A in A's evaluator SIF
and B in B's evaluator SIF, resolves both runnable and environment-free state
commits, and searches every unresolved logical test identity in A START/END and
B START/END.  The resulting JSON is evidence for a later test runner; it does
not change any classification artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, Sequence

try:  # package import (unit tests)
    from agent_pipeline.build_task_manifests import load_sif_manifest
except ModuleNotFoundError:  # direct ``python agent_pipeline/...py`` execution
    from build_task_manifests import load_sif_manifest


EXPECTED_OPERATION_COUNT = 6
EXPECTED_CANDIDATE_COUNT = 33
MAX_FIRST_PARENT_DEPTH = 32
KNOWN_WORKSPACE_PREFIXES = {
    "cargo_rust": ("BurntSushi_ripgrep_", "nushell_nushell_"),
    "element_jest": ("element-hq_element-web_",),
    "navidrome_ginkgo": ("navidrome_navidrome_",),
    "dubbo_junit": ("apache_dubbo_",),
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


class GitCommandRunner(Protocol):
    def git(self, sif: Path, args: Sequence[str]) -> CommandResult: ...


class ApptainerGitRunner:
    """Run read-only Git queries against ``/testbed`` in an evaluator SIF."""

    def __init__(self, apptainer: str = "apptainer", testbed: str = "/testbed") -> None:
        self.apptainer = apptainer
        self.testbed = testbed

    def git(self, sif: Path, args: Sequence[str]) -> CommandResult:
        completed = subprocess.run(
            [
                self.apptainer,
                "exec",
                "--cleanenv",
                str(sif),
                "git",
                "-c",
                f"safe.directory={self.testbed}",
                "-C",
                self.testbed,
                *args,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class CandidateSpec:
    framework: str
    leaf: str
    path_hint: str | None = None
    title_chain: tuple[str, ...] = ()
    class_name: str | None = None
    module: str | None = None


@dataclass(frozen=True)
class MergeOperation:
    path: Path
    payload: dict[str, Any]
    candidates: tuple[dict[str, Any], ...]

    @property
    def workspace(self) -> str:
        return str(self.payload["workspace"])

    @property
    def retained_id(self) -> str:
        return str(self.payload["retained_id"])


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, chunk_size: int = 16 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def file_fingerprint(path: Path, *, content_hash: bool = True) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    stat = path.stat()
    result: dict[str, Any] = {
        "path": str(path),
        "resolved_path": str(path.resolve()),
        "bytes": stat.st_size,
    }
    if content_hash:
        result["sha256"] = sha256_file(path)
    return result


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def discover_merge_operations(
    dataset: Path,
    *,
    expected_operations: int | None = EXPECTED_OPERATION_COUNT,
    expected_candidates: int | None = EXPECTED_CANDIDATE_COUNT,
) -> list[MergeOperation]:
    paths = sorted(dataset.glob("*/merge_provenance/*.json"))
    operations: list[MergeOperation] = []
    total_candidates = 0
    for path in paths:
        payload = _read_json(path)
        workspace = str(payload.get("workspace", ""))
        retained_id = str(payload.get("retained_id", ""))
        if not workspace or not retained_id:
            raise ValueError(f"missing merge identity: {path}")
        if path.parent.parent.name != workspace or path.stem != retained_id:
            raise ValueError(f"merge provenance path/identity mismatch: {path}")
        ordered = payload.get("ordered_source_ids")
        segments = payload.get("patch_segments")
        if not isinstance(ordered, list) or len(ordered) != 2:
            raise ValueError(f"expected exactly A and B source IDs: {path}")
        if not isinstance(segments, list) or len(segments) != 2:
            raise ValueError(f"expected exactly two patch segments: {path}")
        segment_ids = [str(item.get("milestone_id", "")) for item in segments]
        if [str(value) for value in ordered] != segment_ids:
            raise ValueError(f"source/segment order mismatch: {path}")
        logical = payload.get("test_contract", {}).get("logical_composition", {})
        candidates = logical.get("unresolved_outer_evidence")
        if not isinstance(candidates, list):
            raise ValueError(f"missing unresolved_outer_evidence list: {path}")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates:
            if not isinstance(item, dict) or not isinstance(item.get("test_id"), str):
                raise ValueError(f"malformed unresolved candidate in {path}: {item!r}")
            test_id = item["test_id"]
            if not test_id or test_id in seen:
                raise ValueError(f"empty or duplicate unresolved test ID in {path}: {test_id!r}")
            seen.add(test_id)
            normalized.append(dict(item))
        total_candidates += len(normalized)
        operations.append(MergeOperation(path, payload, tuple(normalized)))
    operations.sort(key=lambda item: (item.workspace, item.retained_id))
    if expected_operations is not None and len(operations) != expected_operations:
        raise ValueError(
            f"expected {expected_operations} merge operations, found {len(operations)}"
        )
    if expected_candidates is not None and total_candidates != expected_candidates:
        raise ValueError(
            f"expected {expected_candidates} unresolved candidates, found {total_candidates}"
        )
    return operations


def _safe_repo_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not value:
        raise ValueError(f"unsafe repository path in test ID: {value!r}")
    return str(path)


def candidate_spec(workspace: str, test_id: str) -> CandidateSpec:
    if workspace.startswith(KNOWN_WORKSPACE_PREFIXES["element_jest"]):
        if "::" not in test_id:
            raise ValueError(f"Element test ID has no path/title separator: {test_id!r}")
        path, title = test_id.split("::", 1)
        chain = tuple(part.strip() for part in title.split(" > "))
        if not chain or any(not part for part in chain):
            raise ValueError(f"Element test ID has malformed title chain: {test_id!r}")
        return CandidateSpec(
            "element_jest", chain[-1], _safe_repo_path(path), chain
        )
    if workspace.startswith(KNOWN_WORKSPACE_PREFIXES["navidrome_ginkgo"]):
        if "::" not in test_id:
            raise ValueError(f"Navidrome test ID has no package/title separator: {test_id!r}")
        package, title = test_id.split("::", 1)
        prefix = "github.com/navidrome/navidrome/"
        if not package.startswith(prefix):
            raise ValueError(f"unexpected Navidrome package: {package!r}")
        chain = tuple(part.strip() for part in title.split(" > "))
        if not chain or any(not part for part in chain):
            raise ValueError(f"malformed Ginkgo title chain: {test_id!r}")
        return CandidateSpec(
            "navidrome_ginkgo",
            chain[-1],
            _safe_repo_path(package.removeprefix(prefix)),
            chain,
        )
    if workspace.startswith(KNOWN_WORKSPACE_PREFIXES["dubbo_junit"]):
        parts = test_id.split("::")
        if len(parts) != 3:
            raise ValueError(f"Dubbo test ID must be module::class::method: {test_id!r}")
        module, class_name, method = parts
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_.$]*", class_name):
            raise ValueError(f"malformed Dubbo test ID: {test_id!r}")
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", method):
            raise ValueError(f"malformed Dubbo test ID: {test_id!r}")
        return CandidateSpec(
            "dubbo_junit",
            method,
            _safe_repo_path(module),
            class_name=class_name,
            module=module,
        )
    if workspace.startswith(KNOWN_WORKSPACE_PREFIXES["cargo_rust"]):
        parts = test_id.split("::")
        leaf = parts[-1]
        if len(parts) < 2 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", leaf):
            raise ValueError(f"malformed Rust test ID: {test_id!r}")
        return CandidateSpec("cargo_rust", leaf, title_chain=tuple(parts[:-1]))
    raise ValueError(f"unsupported workspace/test framework: {workspace}/{test_id}")


def parse_grep_output(output: str, commit: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    prefix = f"{commit}:"
    for raw_line in output.splitlines():
        line = raw_line[len(prefix) :] if raw_line.startswith(prefix) else raw_line
        match = re.match(r"^(.+?):([0-9]+):(.*)$", line)
        if not match:
            raise ValueError(f"unparseable git grep output: {raw_line!r}")
        matches.append(
            {
                "path": match.group(1),
                "line": int(match.group(2)),
                "line_text": match.group(3),
            }
        )
    return matches


def line_is_definition(spec: CandidateSpec, line: str) -> bool:
    stripped = line.lstrip()
    if (
        stripped.startswith("//")
        or stripped.startswith("/*")
        or stripped.startswith("*")
        or stripped.startswith("<!--")
    ):
        return False
    leaf = re.escape(spec.leaf)
    if spec.framework == "cargo_rust":
        return bool(
            re.search(rf"\bfn\s+{leaf}\s*(?:<[^>]*>\s*)?\(", line)
            # ripgrep's regression suite declares leaf tests through a macro:
            # ``rgtest!(r3127_name, |dir, cmd| { ... })``. git-grep may return
            # either the comma-terminated name line or the rgtest! invocation.
            or re.search(rf"^\s*{leaf}\s*,(?:\s*\|.*)?\s*$", line)
            or re.search(rf"\brgtest!\s*\(\s*{leaf}\s*,", line)
        )
    if spec.framework == "element_jest":
        return bool(
            re.search(
                rf"\b(?:it|test)(?:\.(?:each|only|skip|todo))?\s*\(\s*"
                rf"(?:['\"`]){leaf}(?:['\"`])",
                line,
            )
        )
    if spec.framework == "navidrome_ginkgo":
        return bool(
            re.search(
                rf"\b(?:It|Specify|Entry)\s*\(\s*(?:['\"`]){leaf}(?:['\"`])",
                line,
            )
        )
    if spec.framework == "dubbo_junit":
        return bool(
            re.search(
                rf"^\s*(?:(?:public|protected|private)\s+)?(?:static\s+)?"
                rf"[A-Za-z_$][A-Za-z0-9_.$<>?,\[\]]*\s+{leaf}\s*\(",
                line,
            )
        )
    raise ValueError(f"unsupported candidate framework: {spec.framework}")


def _require_git(
    runner: GitCommandRunner, sif: Path, args: Sequence[str], *, context: str
) -> str:
    result = runner.git(sif, args)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed for {context} in {sif}: "
            f"rc={result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout


def _grep(
    runner: GitCommandRunner,
    sif: Path,
    args: Sequence[str],
    *,
    context: str,
) -> str:
    result = runner.git(sif, args)
    if result.returncode == 1:
        return ""  # documented git-grep result for no selected lines
    if result.returncode != 0:
        raise RuntimeError(
            f"git grep failed for {context} in {sif}: rc={result.returncode}: "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def resolve_canonical_state_in_sif(
    runner: GitCommandRunner,
    sif: Path,
    requested_ref: str,
    milestone_id: str,
    state: str,
    *,
    max_first_parent_depth: int = MAX_FIRST_PARENT_DEPTH,
) -> dict[str, Any]:
    normalized_state = state.strip().lower()
    if normalized_state not in {"start", "end"}:
        raise ValueError(f"state must be start or end, got {state!r}")
    runnable_commit = _require_git(
        runner,
        sif,
        ["rev-parse", "--verify", f"{requested_ref}^{{commit}}"],
        context=f"{milestone_id} {state} ref",
    ).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", runnable_commit):
        raise RuntimeError(f"invalid resolved commit for {requested_ref}: {runnable_commit!r}")
    history = _require_git(
        runner,
        sif,
        [
            "log",
            "--first-parent",
            f"--max-count={max_first_parent_depth}",
            "--format=%H%x00%s",
            runnable_commit,
        ],
        context=f"{milestone_id} {state} history",
    )
    expected_subject = f"{normalized_state.title()} state for {milestone_id}"
    observed: list[tuple[str, str]] = []
    canonical_commit: str | None = None
    canonical_subject: str | None = None
    for line in history.splitlines():
        if "\x00" not in line:
            raise RuntimeError(f"malformed git log record for {milestone_id}: {line!r}")
        sha, subject = line.split("\x00", 1)
        observed.append((sha, subject))
        if subject.casefold() == expected_subject.casefold():
            canonical_commit, canonical_subject = sha, subject
            break
    if canonical_commit is None:
        raise RuntimeError(
            f"could not resolve environment-free {state} state for {milestone_id} "
            f"from {requested_ref}; expected {expected_subject!r}, observed "
            f"{[subject for _, subject in observed[:6]]!r}"
        )
    runnable_tree = _require_git(
        runner,
        sif,
        ["rev-parse", f"{runnable_commit}^{{tree}}"],
        context=f"{milestone_id} runnable tree",
    ).strip()
    canonical_tree = _require_git(
        runner,
        sif,
        ["rev-parse", f"{canonical_commit}^{{tree}}"],
        context=f"{milestone_id} canonical tree",
    ).strip()
    return {
        "requested_ref": requested_ref,
        "runnable_commit": runnable_commit,
        "runnable_tree": runnable_tree,
        "canonical_commit": canonical_commit,
        "canonical_tree": canonical_tree,
        "canonical_subject": canonical_subject,
        "expected_subject": expected_subject,
        "stripped_environment_commits": [
            {"commit": sha, "subject": subject}
            for sha, subject in observed[:-1]
        ],
    }


def _grep_args(spec: CandidateSpec, commit: str, paths: Sequence[str]) -> list[str]:
    if spec.framework == "cargo_rust":
        # Search the exact leaf as text, then let ``line_is_definition`` reject
        # references.  This covers both ordinary ``fn leaf()`` tests and
        # ripgrep's ``rgtest!(leaf, ...)`` macro declarations.
        return ["grep", "-n", "-I", "-F", "-e", spec.leaf, commit, "--", "*.rs"]
    if spec.framework in {"element_jest", "navidrome_ginkgo"}:
        return ["grep", "-n", "-I", "-F", "-e", spec.leaf, commit, "--", *paths]
    if spec.framework == "dubbo_junit":
        pattern = rf"{spec.leaf}[[:space:]]*\("
        return ["grep", "-n", "-I", "-E", "-e", pattern, commit, "--", *paths]
    raise ValueError(spec.framework)


def _candidate_search_paths(
    runner: GitCommandRunner,
    sif: Path,
    commit: str,
    spec: CandidateSpec,
    *,
    context: str,
) -> list[str]:
    if spec.framework == "cargo_rust":
        return ["*.rs"]
    if spec.framework == "element_jest":
        assert spec.path_hint is not None
        listing = _require_git(
            runner,
            sif,
            ["ls-tree", "-r", "--name-only", commit, "--", spec.path_hint],
            context=context,
        )
        return [line for line in listing.splitlines() if line == spec.path_hint]
    if spec.framework == "navidrome_ginkgo":
        assert spec.path_hint is not None
        listing = _require_git(
            runner,
            sif,
            ["ls-tree", "-r", "--name-only", commit, "--", spec.path_hint],
            context=context,
        )
        return [line for line in listing.splitlines() if line.endswith("_test.go")]
    if spec.framework == "dubbo_junit":
        assert spec.module is not None and spec.class_name is not None
        listing = _require_git(
            runner,
            sif,
            ["ls-tree", "-r", "--name-only", commit, "--", spec.module],
            context=context,
        )
        expected_name = f"{spec.class_name.rsplit('.', 1)[-1]}.java"
        return [
            line
            for line in listing.splitlines()
            if line.endswith(f"/{expected_name}")
            and "/src/test/" in f"/{line}"
        ]
    raise ValueError(spec.framework)


def probe_definition(
    runner: GitCommandRunner,
    sif: Path,
    commit: str,
    workspace: str,
    test_id: str,
) -> dict[str, Any]:
    spec = candidate_spec(workspace, test_id)
    context = f"{workspace}/{test_id}@{commit}"
    paths = _candidate_search_paths(runner, sif, commit, spec, context=context)
    if not paths:
        return {
            "status": "absent",
            "status_reason": "no_matching_definition_path_in_tree",
            "framework": spec.framework,
            "selector": _spec_payload(spec),
            "search_paths": [],
            "search_path_blobs": {},
            "matches": [],
            "raw_matches": [],
            "raw_grep_matches": 0,
        }
    raw = _grep(runner, sif, _grep_args(spec, commit, paths), context=context)
    parsed = parse_grep_output(raw, commit) if raw else []
    allowed_paths = None if spec.framework == "cargo_rust" else set(paths)
    accepted = [
        item
        for item in parsed
        if (allowed_paths is None or item["path"] in allowed_paths)
        and (spec.framework != "navidrome_ginkgo" or item["path"].endswith("_test.go"))
        and line_is_definition(spec, item["line_text"])
    ]
    blobs: dict[str, str] = {}
    concrete_paths = (
        sorted(set(paths)) if spec.framework != "cargo_rust" else []
    )
    for path in concrete_paths:
        blobs[path] = _require_git(
            runner,
            sif,
            ["rev-parse", f"{commit}:{path}"],
            context=f"{context} search-path blob",
        ).strip()
    raw_matches: list[dict[str, Any]] = []
    for item in parsed:
        path = str(item["path"])
        if path not in blobs:
            blobs[path] = _require_git(
                runner,
                sif,
                ["rev-parse", f"{commit}:{path}"],
                context=f"{context} raw-match blob",
            ).strip()
        raw_matches.append({**item, "blob": blobs[path]})
    matches: list[dict[str, Any]] = []
    for item in accepted:
        path = str(item["path"])
        if path not in blobs:
            blobs[path] = _require_git(
                runner,
                sif,
                ["rev-parse", f"{commit}:{path}"],
                context=f"{context} blob",
            ).strip()
        matches.append({**item, "blob": blobs[path]})
    matches.sort(key=lambda item: (item["path"], item["line"], item["line_text"]))
    raw_matches.sort(key=lambda item: (item["path"], item["line"], item["line_text"]))
    # A concrete source path with no literal leaf can still be a generated or
    # parameterized test (notably Element's it.each tables). Static evidence is
    # insufficient in that case; only a runtime collector may resolve it.
    if matches:
        status = "present"
        status_reason = "exact_definition_line_matched"
    elif spec.framework in {"element_jest", "navidrome_ginkgo", "dubbo_junit"}:
        status = "ambiguous"
        status_reason = "definition_path_present_but_exact_leaf_not_statically_proven"
    else:
        status = "absent"
        status_reason = "no_exact_rust_definition_line"
    return {
        "status": status,
        "status_reason": status_reason,
        "framework": spec.framework,
        "selector": _spec_payload(spec),
        "search_paths": paths,
        "search_path_blobs": {path: blobs[path] for path in concrete_paths},
        "matches": matches,
        "raw_matches": raw_matches,
        "raw_grep_matches": len(parsed),
    }


def _spec_payload(spec: CandidateSpec) -> dict[str, Any]:
    return {
        "leaf": spec.leaf,
        "path_hint": spec.path_hint,
        "title_chain": list(spec.title_chain),
        "class_name": spec.class_name,
        "module": spec.module,
    }


def _test_config_fingerprint(source_dataset: Path, workspace: str, milestone_id: str) -> dict[str, Any]:
    path = source_dataset / workspace / "dockerfiles" / milestone_id / "test_config.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    if payload is None:
        raise FileNotFoundError(path)
    if not isinstance(payload, list):
        raise ValueError(f"test_config must be a JSON list: {path}")
    result = file_fingerprint(path)
    result["canonical_json_sha256"] = sha256_bytes(canonical_json_bytes(payload))
    return result


def _source_probe(
    *,
    runner: GitCommandRunner,
    position: str,
    workspace: str,
    segment: dict[str, Any],
    manifest_record: dict[str, Any],
    sif_root: Path,
    source_dataset: Path,
    candidates: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    milestone_id = str(segment["milestone_id"])
    sif = sif_root / str(manifest_record["destination_rel"])
    image = file_fingerprint(sif)
    image.update(
        {
            "source": str(manifest_record["source"]),
            "destination_rel": str(manifest_record["destination_rel"]),
            "manifest_record_sha256": sha256_bytes(canonical_json_bytes(manifest_record)),
        }
    )
    state_resolutions: dict[str, dict[str, Any]] = {}
    definitions: dict[str, dict[str, Any]] = {}
    for state in ("start", "end"):
        ref_key = f"{state}_ref"
        requested_ref = segment.get(ref_key)
        if not isinstance(requested_ref, str) or not requested_ref:
            raise ValueError(f"missing {ref_key} for {workspace}/{milestone_id}")
        resolution = resolve_canonical_state_in_sif(
            runner, sif, requested_ref, milestone_id, state
        )
        state_key = f"{position.lower()}_{state}"
        state_resolutions[state] = {"state_key": state_key, **resolution}
        for candidate in candidates:
            test_id = str(candidate["test_id"])
            canonical_definition = probe_definition(
                runner,
                sif,
                resolution["canonical_commit"],
                workspace,
                test_id,
            )
            if resolution["runnable_commit"] == resolution["canonical_commit"]:
                runnable_definition = canonical_definition
            else:
                runnable_definition = probe_definition(
                    runner,
                    sif,
                    resolution["runnable_commit"],
                    workspace,
                    test_id,
                )
            definitions.setdefault(test_id, {})[state_key] = {
                "canonical_commit": resolution["canonical_commit"],
                "runnable_commit": resolution["runnable_commit"],
                "canonical": canonical_definition,
                "runnable": runnable_definition,
                "definition_status_agrees": (
                    canonical_definition["status"] == runnable_definition["status"]
                ),
            }
    start_resolution = state_resolutions["start"]
    canonical_start_parent = _require_git(
        runner,
        sif,
        ["rev-parse", "--verify", f"{start_resolution['canonical_commit']}^1^{{commit}}"],
        context=f"{workspace}/{milestone_id} canonical START parent",
    ).strip()
    canonical_start_parent_tree = _require_git(
        runner,
        sif,
        ["rev-parse", f"{canonical_start_parent}^{{tree}}"],
        context=f"{workspace}/{milestone_id} canonical START parent tree",
    ).strip()
    parent_changed_paths_output = _require_git(
        runner,
        sif,
        [
            "diff",
            "--name-only",
            "--no-renames",
            canonical_start_parent,
            start_resolution["canonical_commit"],
            "--",
        ],
        context=f"{workspace}/{milestone_id} canonical START injection",
    )
    environment_changed_paths_output = _require_git(
        runner,
        sif,
        [
            "diff",
            "--name-only",
            "--no-renames",
            start_resolution["canonical_commit"],
            start_resolution["runnable_commit"],
            "--",
        ],
        context=f"{workspace}/{milestone_id} runnable environment injection",
    )
    parent_changed_paths = [
        line for line in parent_changed_paths_output.splitlines() if line
    ]
    environment_changed_paths = [
        line for line in environment_changed_paths_output.splitlines() if line
    ]
    source = {
        "position": position,
        "milestone_id": milestone_id,
        "image": image,
        "test_config": _test_config_fingerprint(source_dataset, workspace, milestone_id),
        "states": state_resolutions,
        "oracle_source_state": {
            "canonical_start_parent_commit": canonical_start_parent,
            "canonical_start_parent_tree": canonical_start_parent_tree,
            "canonical_start_commit": start_resolution["canonical_commit"],
            "canonical_start_tree": start_resolution["canonical_tree"],
        },
        "oracle_injection_parent_to_canonical_start": {
            "changed_paths": parent_changed_paths,
            "changed_path_count": len(parent_changed_paths),
            "changed_paths_sha256": sha256_bytes(
                canonical_json_bytes(parent_changed_paths)
            ),
        },
        "environment_injection_canonical_start_to_runnable_start": {
            "changed_paths": environment_changed_paths,
            "changed_path_count": len(environment_changed_paths),
            "changed_paths_sha256": sha256_bytes(
                canonical_json_bytes(environment_changed_paths)
            ),
        },
    }
    return source, definitions


def build_probe_payload(
    *,
    dataset: Path,
    source_dataset: Path,
    sif_manifest_path: Path,
    sif_root: Path,
    operation_index: int,
    runner: GitCommandRunner,
) -> dict[str, Any]:
    operations = discover_merge_operations(dataset)
    if operation_index < 0 or operation_index >= len(operations):
        raise IndexError(
            f"operation index {operation_index} outside [0, {len(operations) - 1}]"
        )
    operation = operations[operation_index]
    manifest = load_sif_manifest(sif_manifest_path)
    segments = operation.payload["patch_segments"]
    sources: list[dict[str, Any]] = []
    candidate_definitions: dict[str, dict[str, Any]] = {
        str(item["test_id"]): {} for item in operation.candidates
    }
    # ``discover_merge_operations`` already requires exactly two segments.
    # Avoid zip(strict=True) so the read-only probe also runs on the workspace's
    # Python 3.9 login environment.
    for position, segment in zip(("A", "B"), segments):
        milestone_id = str(segment["milestone_id"])
        record = manifest.get((operation.workspace, milestone_id.casefold()))
        if record is None:
            raise KeyError(f"no SIF manifest record for {operation.workspace}/{milestone_id}")
        if str(record.get("workspace")) != operation.workspace:
            raise ValueError(f"SIF manifest workspace mismatch for {milestone_id}")
        source, definitions = _source_probe(
            runner=runner,
            position=position,
            workspace=operation.workspace,
            segment=segment,
            manifest_record=record,
            sif_root=sif_root,
            source_dataset=source_dataset,
            candidates=operation.candidates,
        )
        sources.append(source)
        for test_id, state_map in definitions.items():
            overlap = set(candidate_definitions[test_id]) & set(state_map)
            if overlap:
                raise RuntimeError(f"duplicate state definition evidence: {overlap}")
            candidate_definitions[test_id].update(state_map)
    candidates: list[dict[str, Any]] = []
    for evidence in operation.candidates:
        test_id = str(evidence["test_id"])
        definitions = candidate_definitions[test_id]
        if set(definitions) != {"a_start", "a_end", "b_start", "b_end"}:
            raise RuntimeError(f"incomplete four-state probe for {test_id}: {definitions.keys()}")
        candidates.append(
            {
                "test_id": test_id,
                "candidate_sha256": sha256_bytes(canonical_json_bytes(evidence)),
                "unresolved_outer_evidence": evidence,
                "definitions": definitions,
            }
        )
    provenance_bytes = operation.path.read_bytes()
    return {
        "schema_version": 1,
        "kind": "read_only_four_state_test_definition_probe",
        "operation_index": operation_index,
        "operation_count": len(operations),
        "workspace": operation.workspace,
        "retained_id": operation.retained_id,
        "entry_id": str(operation.payload["entry_id"]),
        "exit_id": str(operation.payload["exit_id"]),
        "source_order": [str(item["milestone_id"]) for item in segments],
        "input_fingerprints": {
            "merge_provenance": {
                "path": str(operation.path),
                "sha256": sha256_bytes(provenance_bytes),
                "canonical_json_sha256": sha256_bytes(
                    canonical_json_bytes(operation.payload)
                ),
            },
            "candidate_set_sha256": sha256_bytes(
                canonical_json_bytes(list(operation.candidates))
            ),
            "sif_manifest": file_fingerprint(sif_manifest_path),
        },
        "candidate_count": len(candidates),
        "sources": sources,
        "candidates": candidates,
        "mutation_policy": "read_only_no_checkout_no_dataset_changes",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--source-dataset",
        type=Path,
        help="Original SWE-Milestone-data tree containing each source test_config",
    )
    parser.add_argument("--sif-manifest", type=Path, required=True)
    parser.add_argument("--sif-root", type=Path, required=True)
    parser.add_argument("--operation-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apptainer", default="apptainer")
    parser.add_argument("--testbed", default="/testbed")
    args = parser.parse_args()
    source_dataset = args.source_dataset or args.dataset.with_name("SWE-Milestone-data")
    runner = ApptainerGitRunner(args.apptainer, args.testbed)
    payload = build_probe_payload(
        dataset=args.dataset,
        source_dataset=source_dataset,
        sif_manifest_path=args.sif_manifest,
        sif_root=args.sif_root,
        operation_index=args.operation_index,
        runner=runner,
    )
    atomic_write_json(args.output, payload)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "operation_index": args.operation_index,
                "workspace": payload["workspace"],
                "retained_id": payload["retained_id"],
                "candidate_count": payload["candidate_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
