#!/usr/bin/env python3
"""Build the reviewed 30-node go-zero clean DAG from captured SIF evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from build_ripgrep_clean import git, import_bundle, run
from build_state_transitions import Transition, build as build_transitions
from endpoint_state_builder import EndpointSpec, build_endpoint_states
from materialize_agent_anchor import materialize


WORKSPACE = "zeromicro_go-zero_v1.6.0_v1.9.3"
EXPECTED_MILESTONES = 30
EXPECTED_ENDPOINTS = 60
EXPECTED_GAPS = 30
EXPECTED_TRANSITIONS = 60
EXPECTED_EVALUATORS = 23
EXPECTED_CAPTURES = 24


class GoZeroCleanError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
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


def issue(code: str, subject: str, message: str, evidence: Any = None) -> dict[str, Any]:
    result = {
        "severity": "blocker",
        "code": code,
        "subject": subject,
        "message": message,
    }
    if evidence is not None:
        result["evidence"] = evidence
    return result


def read_catalog(dataset: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    with (dataset / "milestones.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    metadata = json.loads((dataset / "metadata.json").read_text(encoding="utf-8"))
    return rows, metadata


def read_edges(dataset: Path) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for name in ("dependencies.csv", "additional_dependencies.csv"):
        with (dataset / name).open(newline="", encoding="utf-8") as handle:
            edges.update(
                (str(row["source_id"]), str(row["target_id"]))
                for row in csv.DictReader(handle)
            )
    return edges


def topological(ids: Sequence[str], edges: set[tuple[str, str]]) -> list[str]:
    known = set(ids)
    if any(left not in known or right not in known for left, right in edges):
        raise GoZeroCleanError("DAG edge references an unknown catalog node")
    incoming = Counter(right for _, right in edges)
    children: dict[str, list[str]] = defaultdict(list)
    for left, right in edges:
        children[left].append(right)
    ready = deque(sorted(node for node in ids if incoming[node] == 0))
    order: list[str] = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for child in sorted(children[node]):
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if len(order) != len(ids):
        raise GoZeroCleanError("the 30-node dependency graph is cyclic")
    return order


def verify_capture_root(capture_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads((capture_root / "manifest.json").read_text())
    consensus = json.loads((capture_root / "endpoint_consensus.json").read_text())
    expected_denominators = {
        "milestones": EXPECTED_MILESTONES,
        "endpoints": EXPECTED_ENDPOINTS,
        "gaps": EXPECTED_GAPS,
        "captured_evaluator_sifs": EXPECTED_EVALUATORS,
        "captured_base_sifs": 1,
        "captured_images": EXPECTED_CAPTURES,
    }
    if (
        manifest.get("capture_count") != EXPECTED_CAPTURES
        or manifest.get("milestone_count") != EXPECTED_MILESTONES
        or manifest.get("endpoint_count") != EXPECTED_ENDPOINTS
        or manifest.get("gap_count") != EXPECTED_GAPS
        or consensus.get("denominators") != expected_denominators
    ):
        raise GoZeroCleanError("capture denominator drift")
    capture_dirs = sorted((capture_root / "captures").iterdir())
    if len(capture_dirs) != EXPECTED_CAPTURES:
        raise GoZeroCleanError("capture directory count is not 24")
    for directory in capture_dirs:
        row = json.loads((directory / "manifest.json").read_text())
        if row.get("status") != "validated":
            raise GoZeroCleanError(f"capture is not validated: {directory.name}")
        for artifact in row.get("artifacts", []):
            path = directory / str(artifact["path"])
            if (
                not path.is_file()
                or path.is_symlink()
                or path.stat().st_size != artifact.get("bytes")
                or sha256_file(path) != artifact.get("sha256")
            ):
                raise GoZeroCleanError(f"capture identity mismatch: {path}")
    if len(consensus.get("endpoint_refs", [])) != EXPECTED_ENDPOINTS:
        raise GoZeroCleanError("capture consensus does not contain 60 endpoints")
    return manifest, consensus


def strict_majority(row: Mapping[str, Any]) -> tuple[str, dict[str, str]] | None:
    observations = row.get("observations", {})
    if not observations:
        return None
    counts = Counter(str(value["tree"]) for value in observations.values())
    tree, count = counts.most_common(1)[0]
    if count * 2 <= len(observations):
        return None
    candidates = sorted(
        (str(image), value)
        for image, value in observations.items()
        if str(value["tree"]) == tree
    )
    return candidates[0]


def transition_rows(
    ids: Sequence[str], edges: set[tuple[str, str]]
) -> list[Transition]:
    result = [
        Transition(
            f"milestone:{milestone_id}",
            "milestone",
            f"{milestone_id}:start",
            f"{milestone_id}:end",
        )
        for milestone_id in ids
    ]
    result.extend(
        Transition(
            f"gap:{left}:end->{right}:start",
            "gap",
            f"{left}:end",
            f"{right}:start",
        )
        for left, right in sorted(edges)
    )
    return result


def owner_image_id(milestone_id: str) -> str:
    return "m" + milestone_id[1:].lower()


def docker_review(dataset: Path) -> dict[str, Any]:
    rows = []
    for path in sorted((dataset / "dockerfiles").glob("*/Dockerfile")):
        text = path.read_text(encoding="utf-8")
        rows.append(
            {
                "id": path.parent.name,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "go_build_ignore_mentions": text.count("go:build ignore"),
                "go_get_mentions": text.count("go get "),
                "go_mod_tidy_mentions": text.count("go mod tidy"),
                "test_comment_patch_mentions": text.count("[ENV-PATCH]"),
            }
        )
    if len(rows) != EXPECTED_EVALUATORS + 1:
        raise GoZeroCleanError("expected base plus 23 evaluator Dockerfiles")
    return {
        "schema_version": 1,
        "kind": "gozero_dockerfile_manual_review_evidence",
        "status": "reviewed",
        "created_at": now(),
        "dockerfile_count": len(rows),
        "evaluator_dockerfile_count": len(rows) - 1,
        "decision": (
            "drop endpoint-local Docker tree mutations; retain only the "
            "common runtime contract and offline dependency material"
        ),
        "files": rows,
    }


def canonical_poset_fallback(
    controller: Path,
    milestone: Mapping[str, str],
) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    """Resolve a CSV-only node without pretending its listed order is linear."""

    milestone_id = str(milestone["id"])
    declared = [item for item in str(milestone["commits"]).split(";") if item]
    commits: list[str] = []
    for short in declared:
        process = run(
            [
                "git",
                "-C",
                str(controller),
                "rev-parse",
                "--verify",
                f"{short}^{{commit}}",
            ],
            check=False,
        )
        if process.returncode:
            return None, issue(
                "canonical_commit_missing",
                milestone_id,
                "a CSV-declared commit is absent from captured evaluator history",
                short,
            )
        commits.append(process.stdout.decode().strip())
    if not commits:
        return None, issue(
            "canonical_commit_list_empty",
            milestone_id,
            "CSV canonical commit list is empty",
        )

    def ancestor(left: str, right: str) -> bool:
        process = run(
            [
                "git",
                "-C",
                str(controller),
                "merge-base",
                "--is-ancestor",
                left,
                right,
            ],
            check=False,
        )
        if process.returncode not in (0, 1):
            raise GoZeroCleanError(
                f"cannot compare canonical commits for {milestone_id}: "
                + process.stderr.decode(errors="replace")[-1000:]
            )
        return process.returncode == 0

    minima = [
        commit
        for commit in commits
        if not any(
            other != commit and ancestor(other, commit)
            for other in commits
        )
    ]
    maxima = [
        commit
        for commit in commits
        if not any(
            other != commit and ancestor(commit, other)
            for other in commits
        )
    ]
    if len(minima) != 1 or len(maxima) != 1:
        return None, issue(
            "canonical_commit_poset_ambiguous",
            milestone_id,
            "declared commits do not have unique minimal and maximal boundaries",
            {"minima": minima, "maxima": maxima},
        )
    minimum, maximum = minima[0], maxima[0]
    uncovered = [
        commit
        for commit in commits
        if not ancestor(minimum, commit) or not ancestor(commit, maximum)
    ]
    if uncovered:
        return None, issue(
            "canonical_commit_poset_disconnected",
            milestone_id,
            "not every declared commit lies between the unique boundaries",
            uncovered,
        )
    parents = git(controller, "show", "-s", "--format=%P", minimum).split()
    if len(parents) != 1:
        return None, issue(
            "canonical_minimum_parent_ambiguous",
            milestone_id,
            "the unique minimal declared commit does not have one parent",
            parents,
        )
    start, end = parents[0], maximum
    return {
        "start_commit": start,
        "start_tree": git(controller, "rev-parse", f"{start}^{{tree}}"),
        "end_commit": end,
        "end_tree": git(controller, "rev-parse", f"{end}^{{tree}}"),
        "minimum_declared_commit": minimum,
        "maximum_declared_commit": maximum,
    }, None


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    dataset = args.dataset.resolve()
    capture_root = args.capture_root.resolve()
    output = args.output.resolve()
    decisions = json.loads(args.manual_review.read_text(encoding="utf-8"))
    ownership = json.loads(args.ownership_contract.read_text(encoding="utf-8"))
    if output.exists():
        raise GoZeroCleanError(f"refusing to overwrite output: {output}")
    if decisions.get("workspace") != WORKSPACE:
        raise GoZeroCleanError("manual review workspace mismatch")
    rows, metadata = read_catalog(dataset)
    ids = [str(row["id"]) for row in rows]
    rows_by_id = {str(row["id"]): row for row in rows}
    edges = read_edges(dataset)
    order = topological(ids, edges)
    if (
        len(ids) != EXPECTED_MILESTONES
        or len(set(ids)) != EXPECTED_MILESTONES
        or len(edges) != EXPECTED_GAPS
        or len(metadata.get("milestones", [])) != EXPECTED_EVALUATORS
    ):
        raise GoZeroCleanError("catalog denominator drift")
    if ownership.get("test_patterns") != metadata.get("test_dirs"):
        raise GoZeroCleanError("ownership test patterns differ from metadata.test_dirs")
    _, consensus = verify_capture_root(capture_root)
    endpoint_rows = {
        str(row["endpoint_id"]): row for row in consensus["endpoint_refs"]
    }
    fallback_allowed = set(decisions.get("canonical_fallback_allowed", []))
    csv_only = set(ids) - {
        str(row["id"]) for row in metadata.get("milestones", [])
    }
    if fallback_allowed != csv_only:
        raise GoZeroCleanError("canonical fallback allowlist differs from seven CSV-only nodes")
    overrides = decisions.get("endpoint_overrides", {})
    if not isinstance(overrides, dict):
        raise GoZeroCleanError("endpoint_overrides must be an object")

    staging = output.parent / f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    active_staging: Path | None = staging
    blockers: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    try:
        controller = staging / "controller"
        run(["git", "init", "-q", "-b", "controller", str(controller)])
        git(controller, "config", "user.email", "clean.invalid")
        git(controller, "config", "user.name", "SWE Milestone Clean")
        for capture_dir in sorted((capture_root / "captures").iterdir()):
            if capture_dir.name == "base-offline":
                continue
            import_bundle(controller, capture_dir / "repo.bundle")
        fallback_cache: dict[
            str, tuple[dict[str, str] | None, dict[str, Any] | None]
        ] = {}
        for milestone_id in ids:
            for side in ("start", "end"):
                endpoint_id = f"{milestone_id}:{side}"
                row = endpoint_rows.get(endpoint_id)
                if row is None:
                    blockers.append(
                        issue(
                            "endpoint_record_absent",
                            endpoint_id,
                            "capture consensus has no endpoint record",
                        )
                    )
                    continue
                selected = strict_majority(row)
                policy: dict[str, Any]
                if selected is not None:
                    selected_image, observation = selected
                    commit = str(observation["commit"])
                    tree = str(observation["tree"])
                    owner_id = owner_image_id(milestone_id)
                    owner = row.get("observations", {}).get(owner_id)
                    policy = {
                        "policy": "strict_cross_sif_tree_majority",
                        "selected_image": selected_image,
                        "selected_commit": commit,
                        "selected_tree": tree,
                        "observation_count": len(row.get("observations", {})),
                        "tree_distribution": row.get("tree_distribution", {}),
                        "owner_image": owner_id if owner is not None else None,
                        "owner_commit": owner.get("commit") if owner else None,
                        "owner_tree": owner.get("tree") if owner else None,
                        "owner_tree_dropped": bool(owner and owner.get("tree") != tree),
                        "rationale": (
                            "strict cross-SIF post-hoist majority is runnable "
                            "authority; owner-only Docker mutation is excluded"
                        ),
                    }
                elif row.get("observations"):
                    override = overrides.get(endpoint_id)
                    if not isinstance(override, dict):
                        blockers.append(
                            issue(
                                "post_hoist_majority_absent",
                                endpoint_id,
                                "runnable observations lack a strict tree majority",
                                row.get("tree_distribution"),
                            )
                        )
                        continue
                    image = str(override.get("observation_image", ""))
                    observation = row["observations"].get(image)
                    if (
                        observation is None
                        or observation.get("tree") != override.get("expected_tree")
                    ):
                        blockers.append(
                            issue(
                                "manual_override_invalid",
                                endpoint_id,
                                "reviewed observation override is absent or changed",
                                override,
                            )
                        )
                        continue
                    commit = str(observation["commit"])
                    tree = str(observation["tree"])
                    policy = {
                        "policy": "human_reviewed_observation",
                        "selected_image": image,
                        "selected_commit": commit,
                        "selected_tree": tree,
                        "rationale": str(override.get("rationale", "")),
                    }
                else:
                    if milestone_id not in fallback_allowed:
                        blockers.append(
                            issue(
                                "canonical_fallback_not_authorized",
                                endpoint_id,
                                "runnable ref is absent and node is not CSV-only",
                            )
                        )
                        continue
                    if milestone_id not in fallback_cache:
                        fallback_cache[milestone_id] = canonical_poset_fallback(
                            controller,
                            rows_by_id[milestone_id],
                        )
                    fallback, fallback_issue = fallback_cache[milestone_id]
                    if fallback_issue is not None:
                        blockers.append({**fallback_issue, "subject": endpoint_id})
                        continue
                    assert fallback is not None
                    commit = fallback[f"{side}_commit"]
                    tree = fallback[f"{side}_tree"]
                    policy = {
                        "policy": "canonical_no_runnable_evidence",
                        "declared_commits": rows_by_id[milestone_id]["commits"].split(";"),
                        "boundary": (
                            "unique minimal declared commit parent"
                            if side == "start"
                            else "unique maximal declared commit"
                        ),
                        "commit_ordering": "validated_partial_order",
                        "minimum_declared_commit": fallback[
                            "minimum_declared_commit"
                        ],
                        "maximum_declared_commit": fallback[
                            "maximum_declared_commit"
                        ],
                        "common_runtime_tree_neutral": True,
                    }
                ref = f"refs/runnable/{milestone_id}/{side}"
                git(controller, "update-ref", ref, commit)
                actual = git(controller, "rev-parse", f"{ref}^{{tree}}")
                if actual != tree:
                    raise GoZeroCleanError(f"selected ref tree mismatch: {endpoint_id}")
                selections.append(
                    {
                        "endpoint_id": endpoint_id,
                        "ref": ref,
                        "commit": commit,
                        "tree": tree,
                        **policy,
                    }
                )

        review = {
            "schema_version": 1,
            "kind": "gozero_endpoint_manual_review",
            "status": "clear" if not blockers else "review_required",
            "created_at": now(),
            "denominators": {
                "milestones": EXPECTED_MILESTONES,
                "endpoints": EXPECTED_ENDPOINTS,
                "gaps": EXPECTED_GAPS,
                "transitions": EXPECTED_TRANSITIONS,
                "metadata_nodes": EXPECTED_EVALUATORS,
                "csv_only_nodes": len(csv_only),
            },
            "topological_order": order,
            "resolved_endpoints": len(selections),
            "strict_majority_endpoints": sum(
                row.get("policy") == "strict_cross_sif_tree_majority"
                for row in selections
            ),
            "canonical_fallback_endpoints": sum(
                row.get("policy") == "canonical_no_runnable_evidence"
                for row in selections
            ),
            "owner_tree_drops": sum(
                bool(row.get("owner_tree_dropped")) for row in selections
            ),
            "blockers": blockers,
            "docker_review": docker_review(dataset),
            "decision_source": {
                "path": str(args.manual_review.resolve()),
                "sha256": sha256_file(args.manual_review.resolve()),
            },
        }
        write_json(staging / "endpoint_selection.json", {
            "schema_version": 1,
            "status": "validated" if not blockers else "partial",
            "endpoints": selections,
        })
        write_json(staging / "review_queue.json", review)
        if blockers:
            result = {
                "schema_version": 1,
                "kind": "gozero_clean_bundle",
                "status": "review_required",
                "phase": "endpoint_resolution",
                "created_at": now(),
                "milestone_count": EXPECTED_MILESTONES,
                "endpoint_count": EXPECTED_ENDPOINTS,
                "resolved_endpoint_count": len(selections),
                "gap_count": EXPECTED_GAPS,
                "transition_count": 0,
                "blocker_count": len(blockers),
            }
            write_json(staging / "manifest.json", result)
            os.replace(staging, output)
            active_staging = None
            return result
        if len(selections) != EXPECTED_ENDPOINTS:
            raise GoZeroCleanError("resolved endpoint count is not 60")

        anchor_endpoint = str(decisions["anchor_endpoint"])
        anchor_ref = f"refs/runnable/{anchor_endpoint.replace(':', '/')}"
        anchor_commit = git(controller, "rev-parse", f"{anchor_ref}^{{commit}}")
        git(controller, "update-ref", "refs/dag-clean/anchor", anchor_commit)
        run(
            [
                "git",
                "-C",
                str(controller),
                "checkout",
                "-q",
                "-B",
                "anchor",
                anchor_commit,
            ]
        )
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
        endpoints = [
            EndpointSpec(
                f"{milestone_id}:{side}",
                f"refs/runnable/{milestone_id}/{side}",
            )
            for milestone_id in ids
            for side in ("start", "end")
        ]
        states_root = staging / "states"
        states = build_endpoint_states(
            repo=controller,
            anchor_ref="refs/dag-clean/anchor",
            endpoints=endpoints,
            ownership_contract=args.ownership_contract.resolve(),
            output=states_root,
        )
        transitions_root = staging / "transitions"
        transitions = build_transitions(
            state_root=states_root,
            source_repo=controller,
            transitions=transition_rows(ids, edges),
            output=transitions_root,
        )
        if (
            states.get("endpoint_count") != EXPECTED_ENDPOINTS
            or transitions.get("transition_count") != EXPECTED_TRANSITIONS
            or transitions.get("kind_counts")
            != {"milestone": EXPECTED_MILESTONES, "gap": EXPECTED_GAPS}
        ):
            raise GoZeroCleanError("state/transition denominator validation failed")

        anchor = staging / "agent-anchor"
        materialize(
            controller,
            states_root,
            anchor,
            staging / "agent_anchor_manifest.json",
        )
        delivery = staging / "delivery"
        delivery.mkdir()
        shutil.move(str(states_root), str(delivery / "states"))
        shutil.move(str(transitions_root), str(delivery / "transitions"))
        shutil.copy2(staging / "endpoint_selection.json", delivery)
        shutil.move(str(controller), str(delivery / "controller"))
        contracts = delivery / "contracts"
        contracts.mkdir()
        shutil.copy2(
            args.ownership_contract.resolve(),
            contracts / "gozero_ownership_contract.json",
        )

        state_path = delivery / "states" / "manifest.json"
        state_manifest = json.loads(state_path.read_text())
        state_manifest["repo"] = "../controller"
        state_manifest["ownership_contract"]["path"] = (
            "../contracts/gozero_ownership_contract.json"
        )
        state_manifest["synthetic_git_object_store"][
            "alternate_object_directory"
        ] = "../controller/.git/objects"
        write_json(state_path, state_manifest)
        transition_path = delivery / "transitions" / "manifest.json"
        transition_manifest = json.loads(transition_path.read_text())
        transition_manifest["state_manifest"] = "../states/manifest.json"
        transition_manifest["state_manifest_sha256"] = sha256_file(state_path)
        write_json(transition_path, transition_manifest)
        agent_path = staging / "agent_anchor_manifest.json"
        agent_manifest = json.loads(agent_path.read_text())
        agent_manifest["destination"] = "agent-anchor"
        agent_manifest["source_state_manifest"] = "delivery/states/manifest.json"
        agent_manifest["source_state_manifest_sha256"] = sha256_file(state_path)
        write_json(agent_path, agent_manifest)

        files = []
        for path in sorted(item for item in delivery.rglob("*") if item.is_file()):
            files.append(
                {
                    "path": path.relative_to(delivery).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        write_json(delivery / "bundle_manifest.json", {
            "schema_version": 1,
            "kind": "gozero_patch_delivery",
            "status": "validated",
            "milestone_count": EXPECTED_MILESTONES,
            "endpoint_count": EXPECTED_ENDPOINTS,
            "gap_count": EXPECTED_GAPS,
            "transition_count": EXPECTED_TRANSITIONS,
            "files": files,
        })
        result = {
            "schema_version": 1,
            "kind": "gozero_clean_bundle",
            "status": "validated",
            "phase": "exact_replay_complete",
            "created_at": now(),
            "milestone_count": EXPECTED_MILESTONES,
            "endpoint_count": EXPECTED_ENDPOINTS,
            "gap_count": EXPECTED_GAPS,
            "transition_count": EXPECTED_TRANSITIONS,
            "anchor_endpoint": anchor_endpoint,
            "review_blockers": 0,
        }
        write_json(staging / "manifest.json", result)
        os.replace(staging, output)
        active_staging = None
        return result
    finally:
        if active_staging is not None:
            shutil.rmtree(active_staging, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--dataset", type=Path, required=True)
    result.add_argument("--capture-root", type=Path, required=True)
    result.add_argument("--ownership-contract", type=Path, required=True)
    result.add_argument("--manual-review", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = prepare(parser().parse_args(argv))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "validated" else 42
    except (
        GoZeroCleanError,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        print(f"build-gozero-clean: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
