#!/usr/bin/env python3
"""Review and build the 10-node Navidrome clean DAG.

Runnable endpoint trees are selected from cross-evaluator post-hoist
observations.  A strict majority is the clean baseline.  Any owner-image tree
which differs from that baseline is emitted as a binary review patch and must
receive an explicit human decision before endpoint states or transitions are
built.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


WORKSPACE = "navidrome_navidrome_v0.57.0_v0.58.0"
EXPECTED_MILESTONES = 10
EXPECTED_ENDPOINTS = 20
EXPECTED_GAPS = 8
EXPECTED_CAPTURES = 10
ANCHOR_ENDPOINT = "milestone_002:start"


class NavidromeCleanError(RuntimeError):
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
        message = process.stderr.decode("utf-8", errors="replace")[-4000:]
        raise NavidromeCleanError(
            f"command failed ({' '.join(command)}): {message.strip()}"
        )
    return process


def git(
    repo: Path,
    *arguments: str,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> str:
    return run(
        ["git", "-C", str(repo), *arguments],
        env=env,
        input_bytes=input_bytes,
    ).stdout.decode("utf-8", errors="replace").strip()


def read_catalog(dataset: Path) -> tuple[list[dict[str, str]], set[tuple[str, str]]]:
    with (dataset / "milestones.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    edges: set[tuple[str, str]] = set()
    with (dataset / "dependencies.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            edges.add((str(row["source_id"]), str(row["target_id"])))
    return rows, edges


def topological(ids: Sequence[str], edges: set[tuple[str, str]]) -> list[str]:
    known = set(ids)
    if any(left not in known or right not in known for left, right in edges):
        raise NavidromeCleanError("DAG edge references a non-catalog milestone")
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
        raise NavidromeCleanError("the 10-node dependency graph is cyclic")
    return order


def verify_capture_root(capture_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads((capture_root / "manifest.json").read_text())
    consensus = json.loads((capture_root / "endpoint_consensus.json").read_text())
    expected = {
        "capture_count": EXPECTED_CAPTURES,
        "milestone_count": EXPECTED_MILESTONES,
        "endpoint_count": EXPECTED_ENDPOINTS,
        "gap_count": EXPECTED_GAPS,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise NavidromeCleanError(f"capture denominator mismatch: {key}")
    captures = sorted((capture_root / "captures").iterdir())
    if len(captures) != EXPECTED_CAPTURES:
        raise NavidromeCleanError("capture directory count is not 10")
    for directory in captures:
        row = json.loads((directory / "manifest.json").read_text())
        if row.get("status") != "validated":
            raise NavidromeCleanError(f"capture is not validated: {directory.name}")
        for artifact in row.get("artifacts", []):
            path = directory / str(artifact["path"])
            if (
                not path.is_file()
                or path.is_symlink()
                or path.stat().st_size != artifact.get("bytes")
                or sha256_file(path) != artifact.get("sha256")
            ):
                raise NavidromeCleanError(f"capture identity mismatch: {path}")
    if len(consensus.get("endpoint_refs", [])) != EXPECTED_ENDPOINTS:
        raise NavidromeCleanError("consensus endpoint count is not 20")
    return manifest, consensus


def import_bundles(controller: Path, capture_root: Path) -> None:
    pack_dir = controller / ".git" / "objects" / "pack"
    for partial in pack_dir.glob("tmp_pack_*"):
        partial.unlink()
    checkpoints: list[dict[str, Any]] = []
    for capture in sorted((capture_root / "captures").iterdir()):
        bundle = capture / "repo.bundle"
        heads = [
            line.split()[0]
            for line in git(controller, "bundle", "list-heads", str(bundle)).splitlines()
            if line
        ]
        batch = run(
            [
                "git",
                "-C",
                str(controller),
                "cat-file",
                "--batch-check=%(objectname) %(objecttype)",
            ],
            input_bytes="".join(f"{oid}\n" for oid in heads).encode(),
        ).stdout.decode()
        reusable = bool(heads) and all(
            not line.endswith(" missing") for line in batch.splitlines()
        )
        if not reusable:
            git(controller, "bundle", "verify", str(bundle))
            git(controller, "bundle", "unbundle", str(bundle))
            check = run(
                [
                    "git",
                    "-C",
                    str(controller),
                    "cat-file",
                    "--batch-check=%(objectname) %(objecttype)",
                ],
                input_bytes="".join(f"{oid}\n" for oid in heads).encode(),
            ).stdout.decode()
            if any(line.endswith(" missing") for line in check.splitlines()):
                raise NavidromeCleanError(
                    f"bundle heads remain missing after import: {capture.name}"
                )
        checkpoints.append(
            {
                "image_id": capture.name,
                "bundle_bytes": bundle.stat().st_size,
                "bundle_sha256": sha256_file(bundle),
                "head_count": len(heads),
                "source": "existing_object_closure" if reusable else "live_import",
            }
        )
        write_json(
            controller.parent / "bundle_import_progress.json",
            {
                "schema_version": 1,
                "kind": "navidrome_bundle_import_progress",
                "status": "running",
                "completed": len(checkpoints),
                "expected": EXPECTED_CAPTURES,
                "captures": checkpoints,
            },
        )


def majority(row: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    observations = row.get("observations", {})
    if not observations:
        raise NavidromeCleanError(
            f"no post-hoist observation for {row.get('endpoint_id')}"
        )
    counts = Counter(str(value["tree"]) for value in observations.values())
    ordered = counts.most_common()
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        raise NavidromeCleanError(
            f"post-hoist tree majority is tied for {row.get('endpoint_id')}"
        )
    tree = ordered[0][0]
    candidates = sorted(
        (image, value)
        for image, value in observations.items()
        if str(value["tree"]) == tree
    )
    return candidates[0]


def tree_diff(repo: Path, left: str, right: str) -> tuple[bytes, list[str]]:
    patch = run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--binary",
            "--full-index",
            "--no-color",
            left,
            right,
        ]
    ).stdout
    names = run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--name-status",
            "--no-renames",
            left,
            right,
        ]
    ).stdout.decode("utf-8", errors="replace").splitlines()
    return patch, names


def review(args: argparse.Namespace) -> dict[str, Any]:
    dataset = args.dataset.resolve()
    capture_root = args.capture_root.resolve()
    output = args.output.resolve()
    if output.exists():
        raise NavidromeCleanError(f"refusing to overwrite output: {output}")
    rows, edges = read_catalog(dataset)
    ids = [str(row["id"]) for row in rows]
    if len(ids) != EXPECTED_MILESTONES or len(set(ids)) != EXPECTED_MILESTONES:
        raise NavidromeCleanError("milestones.csv is not exactly 10 unique nodes")
    if len(edges) != EXPECTED_GAPS:
        raise NavidromeCleanError("dependency graph is not exactly 8 edges")
    order = topological(ids, edges)
    capture_manifest, consensus = verify_capture_root(capture_root)
    endpoint_rows = {
        str(row["endpoint_id"]): row for row in consensus["endpoint_refs"]
    }
    resume = args.resume_staging.resolve() if args.resume_staging else None
    if resume is not None:
        staging = resume
        controller = staging / "controller"
        if (
            not staging.is_dir()
            or staging.parent.resolve() != output.parent.resolve()
            or not (controller / ".git").is_dir()
        ):
            raise NavidromeCleanError("unsafe or incomplete --resume-staging")
    else:
        staging = output.parent / f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        staging.mkdir(parents=True)
    try:
        controller = staging / "controller"
        if not (controller / ".git").is_dir():
            run(["git", "init", "-q", "-b", "controller", str(controller)])
            git(controller, "config", "user.email", "navidrome-clean.invalid")
            git(controller, "config", "user.name", "Navidrome Clean Review")
        import_bundles(controller, capture_root)
        blockers: list[dict[str, Any]] = []
        selections: list[dict[str, Any]] = []
        review_items: list[dict[str, Any]] = []
        for milestone_id in ids:
            for side in ("start", "end"):
                endpoint_id = f"{milestone_id}:{side}"
                row = endpoint_rows.get(endpoint_id)
                if row is None or not row.get("observations"):
                    blockers.append(
                        {
                            "severity": "blocker",
                            "code": "endpoint_without_runnable_evidence",
                            "subject": endpoint_id,
                            "message": "no evaluator SIF contains this endpoint ref",
                        }
                    )
                    continue
                baseline_image, baseline = majority(row)
                owner = row["observations"].get(milestone_id)
                selection = {
                    "endpoint_id": endpoint_id,
                    "ref": row["ref"],
                    "baseline_image": baseline_image,
                    "baseline_commit": baseline["commit"],
                    "baseline_tree": baseline["tree"],
                    "owner_image": milestone_id if owner else None,
                    "owner_commit": owner["commit"] if owner else None,
                    "owner_tree": owner["tree"] if owner else None,
                    "observed_images": row["observed_images"],
                    "tree_distribution": row["tree_distribution"],
                }
                selections.append(selection)
                if owner is not None and owner["tree"] != baseline["tree"]:
                    patch, paths = tree_diff(
                        controller, str(baseline["tree"]), str(owner["tree"])
                    )
                    artifact = (
                        Path("review_patches")
                        / endpoint_id.replace(":", "__")
                        / "owner_vs_consensus.patch"
                    )
                    destination = staging / artifact
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(patch)
                    review_items.append(
                        {
                            "endpoint_id": endpoint_id,
                            "question": (
                                "retain owner Docker mutation or use the "
                                "cross-SIF post-hoist consensus tree"
                            ),
                            "allowed_decisions": ["baseline", "owner"],
                            "changed_paths": paths,
                            "patch": {
                                "path": artifact.as_posix(),
                                "bytes": len(patch),
                                "sha256": hashlib.sha256(patch).hexdigest(),
                            },
                        }
                    )
        write_json(
            staging / "endpoint_candidates.json",
            {
                "schema_version": 1,
                "kind": "navidrome_endpoint_candidates",
                "status": "complete" if not blockers else "partial",
                "endpoints": selections,
            },
        )
        manifest = {
            "schema_version": 1,
            "kind": "navidrome_manual_review",
            "status": (
                "blocked"
                if blockers
                else "requires_human_review"
                if review_items
                else "clear"
            ),
            "created_at": now(),
            "workspace": WORKSPACE,
            "source_capture_manifest_sha256": sha256_file(
                capture_root / "manifest.json"
            ),
            "milestone_count": len(ids),
            "endpoint_count": len(selections),
            "gap_count": len(edges),
            "review_item_count": len(review_items),
            "blocker_count": len(blockers),
            "topological_order": order,
            "review_items": review_items,
            "blockers": blockers,
        }
        write_json(staging / "manifest.json", manifest)
        os.replace(staging, output)
        staging = None
        return manifest
    finally:
        # Preserve an incomplete controller so bundle imports can resume after
        # scheduler or outer-process interruption.  Atomic publication still
        # occurs only through the os.replace above.
        pass


def transition_rows(ids: Sequence[str], edges: set[tuple[str, str]]) -> list[Any]:
    from build_state_transitions import Transition

    rows = [
        Transition(
            f"milestone:{milestone_id}",
            "milestone",
            f"{milestone_id}:start",
            f"{milestone_id}:end",
        )
        for milestone_id in ids
    ]
    rows.extend(
        Transition(
            f"gap:{left}:end->{right}:start",
            "gap",
            f"{left}:end",
            f"{right}:start",
        )
        for left, right in sorted(edges)
    )
    return rows


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    from build_state_transitions import build as build_transitions
    from endpoint_state_builder import EndpointSpec, build_endpoint_states
    from materialize_agent_anchor import materialize

    dataset = args.dataset.resolve()
    capture_root = args.capture_root.resolve()
    review_root = args.review_root.resolve()
    decisions_path = args.decisions.resolve()
    output = args.output.resolve()
    if output.exists():
        raise NavidromeCleanError(f"refusing to overwrite output: {output}")
    rows, edges = read_catalog(dataset)
    ids = [str(row["id"]) for row in rows]
    topological(ids, edges)
    verify_capture_root(capture_root)
    review_manifest = json.loads((review_root / "manifest.json").read_text())
    candidates = json.loads(
        (review_root / "endpoint_candidates.json").read_text()
    )["endpoints"]
    if (
        review_manifest.get("blocker_count") != 0
        or review_manifest.get("endpoint_count") != EXPECTED_ENDPOINTS
    ):
        raise NavidromeCleanError("manual review input is blocked or incomplete")
    decisions = json.loads(decisions_path.read_text())
    if decisions.get("schema_version") != 1:
        raise NavidromeCleanError("decision file must use schema_version 1")
    decision_rows = decisions.get("endpoint_decisions", {})
    required = {
        str(row["endpoint_id"]) for row in review_manifest["review_items"]
    }
    if set(decision_rows) != required:
        raise NavidromeCleanError(
            "decision endpoint set does not exactly match manual review items"
        )
    staging = output.parent / f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        controller = staging / "controller"
        run(["git", "init", "-q", "-b", "controller", str(controller)])
        git(controller, "config", "user.email", "navidrome-clean.invalid")
        git(controller, "config", "user.name", "Navidrome Clean Build")
        reviewed_objects = review_root / "controller" / ".git" / "objects"
        if not reviewed_objects.is_dir():
            raise NavidromeCleanError(
                "manual review controller object store is missing"
            )
        alternates = controller / ".git" / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_text(str(reviewed_objects.resolve()) + "\n")
        selected: list[dict[str, Any]] = []
        for row in candidates:
            endpoint_id = str(row["endpoint_id"])
            decision = decision_rows.get(endpoint_id)
            choice = str(decision["choice"]) if decision else "baseline"
            if choice not in {"baseline", "owner"}:
                raise NavidromeCleanError(
                    f"invalid decision for {endpoint_id}: {choice}"
                )
            if choice == "owner" and not row.get("owner_tree"):
                raise NavidromeCleanError(
                    f"owner choice has no owner tree: {endpoint_id}"
                )
            commit = str(row[f"{choice}_commit"])
            tree = str(row[f"{choice}_tree"])
            ref = f"refs/runnable/{endpoint_id.replace(':', '/')}"
            git(controller, "update-ref", ref, commit)
            if git(controller, "rev-parse", f"{ref}^{{tree}}") != tree:
                raise NavidromeCleanError(
                    f"selected endpoint tree mismatch: {endpoint_id}"
                )
            selected.append(
                {
                    "endpoint_id": endpoint_id,
                    "ref": ref,
                    "commit": commit,
                    "tree": tree,
                    "choice": choice,
                    "rationale": (
                        str(decision["rationale"])
                        if decision
                        else "owner tree equals consensus or owner SIF is absent"
                    ),
                    "baseline_image": row["baseline_image"],
                    "owner_image": row.get("owner_image"),
                }
            )
        if len(selected) != EXPECTED_ENDPOINTS:
            raise NavidromeCleanError("selected endpoint count is not 20")
        anchor_ref = f"refs/runnable/{ANCHOR_ENDPOINT.replace(':', '/')}"
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
        write_json(
            staging / "endpoint_selection.json",
            {
                "schema_version": 1,
                "kind": "navidrome_reviewed_endpoint_selection",
                "status": "validated",
                "endpoints": selected,
            },
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
            or transitions.get("transition_count")
            != EXPECTED_MILESTONES + EXPECTED_GAPS
            or transitions.get("kind_counts")
            != {"milestone": EXPECTED_MILESTONES, "gap": EXPECTED_GAPS}
        ):
            raise NavidromeCleanError("state/transition denominator drift")
        anchor = staging / "agent-anchor"
        materialize(
            controller,
            states_root,
            anchor,
            staging / "agent_anchor.json",
        )
        # Make the delivery controller self-contained.  The reviewed object
        # store is read-only input; all objects reachable from the 20 selected
        # runnable refs are packed locally before the alternate is removed.
        git(controller, "repack", "-a", "-d", "--no-write-bitmap-index")
        alternates.unlink()
        git(controller, "fsck", "--connectivity-only", "--no-dangling")
        for row in selected:
            git(controller, "cat-file", "-e", f"{row['commit']}^{{commit}}")
            git(controller, "cat-file", "-e", f"{row['tree']}^{{tree}}")
        delivery = staging / "delivery"
        delivery.mkdir()
        shutil.copytree(states_root, delivery / "states")
        shutil.copytree(transitions_root, delivery / "transitions")
        shutil.copy2(staging / "endpoint_selection.json", delivery)
        shutil.copy2(staging / "agent_anchor.json", delivery)
        shutil.copy2(decisions_path, delivery / "manual_decisions.json")
        shutil.copy2(
            args.ownership_contract.resolve(),
            delivery / "navidrome_ownership_contract.json",
        )
        runtime = delivery / "runtime"
        runtime.mkdir()
        code_root = Path(__file__).resolve().parent
        for name in (
            "navidrome_unified_environment.sh",
            "navidrome_unified_entrypoint.sh",
            "navidrome_state.sh",
            "navidrome_rebuild.sh",
            "Dockerfile.navidrome-common",
        ):
            source = code_root / name
            if not source.is_file() or source.is_symlink():
                raise NavidromeCleanError(
                    f"missing or unsafe runtime delivery input: {source}"
                )
            shutil.copy2(source, runtime / name)
        shutil.move(str(controller), str(delivery / "controller"))
        files = []
        for path in sorted(item for item in delivery.rglob("*") if item.is_file()):
            files.append(
                {
                    "path": path.relative_to(delivery).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        delivery_manifest = {
            "schema_version": 1,
            "kind": "navidrome_patch_delivery",
            "status": "validated",
            "milestone_count": EXPECTED_MILESTONES,
            "endpoint_count": EXPECTED_ENDPOINTS,
            "gap_count": EXPECTED_GAPS,
            "transition_count": EXPECTED_MILESTONES + EXPECTED_GAPS,
            "files": files,
        }
        write_json(delivery / "bundle_manifest.json", delivery_manifest)
        result = {
            "schema_version": 1,
            "kind": "navidrome_clean_bundle",
            "status": "validated",
            "phase": "exact_replay_complete",
            "created_at": now(),
            "milestone_count": EXPECTED_MILESTONES,
            "endpoint_count": EXPECTED_ENDPOINTS,
            "gap_count": EXPECTED_GAPS,
            "transition_count": EXPECTED_MILESTONES + EXPECTED_GAPS,
            "anchor_endpoint": ANCHOR_ENDPOINT,
            "review_blockers": 0,
        }
        write_json(staging / "manifest.json", result)
        os.replace(staging, output)
        staging = None
        return result
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("review")
    audit.add_argument("--dataset", type=Path, required=True)
    audit.add_argument("--capture-root", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument("--resume-staging", type=Path)
    build = commands.add_parser("prepare")
    build.add_argument("--dataset", type=Path, required=True)
    build.add_argument("--capture-root", type=Path, required=True)
    build.add_argument("--review-root", type=Path, required=True)
    build.add_argument("--decisions", type=Path, required=True)
    build.add_argument("--ownership-contract", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = review(args) if args.command == "review" else prepare(args)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] in {"validated", "clear"} else 42
    except (
        NavidromeCleanError,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        print(f"build-navidrome-clean: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
