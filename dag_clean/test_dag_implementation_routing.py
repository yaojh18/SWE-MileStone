#!/usr/bin/env python3

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dag_implementation_routing import (
    DERIVATION_METHOD,
    RoutingError,
    plan_dag_routing,
    replay_dag_routing,
    validate_review_queue,
    validate_routing_manifest,
)


def command(*args: str, cwd: Path, input_bytes: bytes | None = None) -> bytes:
    process = subprocess.run(
        list(args),
        cwd=cwd,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise AssertionError(process.stderr.decode(errors="replace"))
    return process.stdout


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class DagImplementationRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.dataset = self.root / "dataset"
        self.repo.mkdir()
        self.dataset.mkdir()
        command("git", "init", "-q", cwd=self.repo)
        command("git", "config", "user.name", "test", cwd=self.repo)
        command("git", "config", "user.email", "test@example.invalid", cwd=self.repo)
        (self.repo / "spring.txt").write_text("spring-pre\n", encoding="utf-8")
        (self.repo / "mutiny.txt").write_text("mutiny-pre\n", encoding="utf-8")
        (self.repo / "semantic.txt").write_text("semantic-pre\n", encoding="utf-8")
        command("git", "add", ".", cwd=self.repo)
        command("git", "commit", "-qm", "base", cwd=self.repo)
        self.base = command("git", "rev-parse", "HEAD", cwd=self.repo).decode().strip()

        (self.repo / "spring.txt").write_text("spring-post\n", encoding="utf-8")
        command("git", "add", "spring.txt", cwd=self.repo)
        command("git", "commit", "-qm", "spring event", cwd=self.repo)
        self.spring = command("git", "rev-parse", "HEAD", cwd=self.repo).decode().strip()

        (self.repo / "mutiny.txt").write_text("mutiny-post\n", encoding="utf-8")
        command("git", "add", "mutiny.txt", cwd=self.repo)
        command("git", "commit", "-qm", "mutiny event", cwd=self.repo)
        self.mutiny = command("git", "rev-parse", "HEAD", cwd=self.repo).decode().strip()

        (self.repo / "semantic.txt").write_text("semantic-post\n", encoding="utf-8")
        command("git", "add", "semantic.txt", cwd=self.repo)
        command("git", "commit", "-qm", "semantic event", cwd=self.repo)
        self.semantic = command("git", "rev-parse", "HEAD", cwd=self.repo).decode().strip()
        self.semantic_patch = command(
            "git",
            "diff",
            f"{self.semantic}^",
            self.semantic,
            "--",
            "semantic.txt",
            cwd=self.repo,
        )

        # Raw endpoint commits do not need ancestry.  Most deliberately start
        # with both canonical events absent.
        for milestone in ("A", "B", "C"):
            for side in ("start", "end"):
                command("git", "tag", f"raw-{milestone}-{side}", self.base, cwd=self.repo)

        self.metadata_path = self.dataset / "metadata.json"
        self.dag_path = self.dataset / "dag.json"
        self.events_path = self.dataset / "events.json"
        write_json(
            self.metadata_path,
            {
                "topological_order": {"full_order": ["A", "B", "C"]},
                "milestones": [
                    {
                        "id": "A",
                        "tag_name_start": "raw-A-start",
                        "tag_name_end": "raw-A-end",
                        "parent_milestones": [],
                    },
                    {
                        "id": "B",
                        "tag_name_start": "raw-B-start",
                        "tag_name_end": "raw-B-end",
                        "parent_milestones": ["A"],
                    },
                    {
                        "id": "C",
                        "tag_name_start": "raw-C-start",
                        "tag_name_end": "raw-C-end",
                        "parent_milestones": [],
                    },
                ],
            },
        )
        write_json(
            self.dag_path,
            {
                "nodes": [{"id": "A"}, {"id": "B"}],
                "edges": [{"source_id": "A", "target_id": "B"}],
            },
        )
        self.events = {
            "schema_version": 1,
            "kind": "dag_canonical_implementation_events",
            "events": [
                {
                    "event_id": "spring",
                    "owner_milestone_id": "A",
                    "event_ref": self.spring,
                    "paths": ["spring.txt"],
                },
                {
                    "event_id": "mutiny",
                    "owner_milestone_id": "C",
                    "event_ref": self.mutiny,
                    "paths": ["mutiny.txt"],
                },
            ],
        }
        write_json(self.events_path, self.events)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_owner_descendant_and_unrelated_routes_replay_exactly(self) -> None:
        artifacts = plan_dag_routing(
            repo=self.repo,
            metadata_path=self.metadata_path,
            dag_path=self.dag_path,
            events_path=self.events_path,
        )
        routing = validate_routing_manifest(artifacts["routing_manifest"])
        self.assertEqual(routing["status"], "complete")
        self.assertEqual(routing["denominators"]["endpoints"], 6)
        by_subject = {row["subject"]: row for row in routing["endpoints"]}

        def targets(subject: str) -> list[str]:
            return [step["target_side"] for step in by_subject[subject]["route"]]

        self.assertEqual(targets("A:start"), ["preimage", "preimage"])
        self.assertEqual(targets("A:end"), ["postimage", "preimage"])
        self.assertEqual(targets("B:start"), ["postimage", "preimage"])
        self.assertEqual(targets("B:end"), ["postimage", "preimage"])
        self.assertEqual(targets("C:start"), ["preimage", "preimage"])
        self.assertEqual(targets("C:end"), ["preimage", "postimage"])
        replay = replay_dag_routing(repo=self.repo, artifacts=artifacts)
        self.assertEqual(replay["status"], "exact")
        self.assertEqual(replay["replayed_endpoint_count"], 6)

    def test_unprojectable_third_version_is_digest_bound_for_review(self) -> None:
        command("git", "checkout", "-q", "-b", "third", self.base, cwd=self.repo)
        (self.repo / "spring.txt").write_text("third-version\n", encoding="utf-8")
        command("git", "add", "spring.txt", cwd=self.repo)
        command("git", "commit", "-qm", "incompatible raw B end", cwd=self.repo)
        command("git", "tag", "-f", "raw-B-end", cwd=self.repo)

        artifacts = plan_dag_routing(
            repo=self.repo,
            metadata_path=self.metadata_path,
            dag_path=self.dag_path,
            events_path=self.events_path,
        )
        routing = artifacts["routing_manifest"]
        queue = validate_review_queue(artifacts["review_queue"])
        self.assertEqual(routing["status"], "requires_human_review")
        self.assertEqual(len(queue["items"]), 1)
        self.assertEqual(queue["items"][0]["subject"], "B:end")
        self.assertEqual(queue["items"][0]["failed_step_id"], "route-001-spring")
        tampered = copy.deepcopy(queue)
        tampered["items"][0]["error"] = "changed"
        with self.assertRaisesRegex(RoutingError, "binding_sha256 mismatch"):
            validate_review_queue(tampered)

    def test_semantic_end_is_derived_from_routed_start_not_raw_end(self) -> None:
        command("git", "checkout", "-q", "-b", "third", self.base, cwd=self.repo)
        (self.repo / "spring.txt").write_text("third-version\n", encoding="utf-8")
        command("git", "add", "spring.txt", cwd=self.repo)
        command("git", "commit", "-qm", "incompatible raw B end", cwd=self.repo)
        command("git", "tag", "-f", "raw-B-end", cwd=self.repo)

        patch_dir = self.dataset / "patches" / "B"
        patch_dir.mkdir(parents=True)
        gold = patch_dir / "gold.patch"
        gold.write_bytes(self.semantic_patch)
        manifest = {
            "schema_version": 1,
            "retained_id": "B",
            "materialization_status": "materialized",
            "merged_start_ref": "raw-B-start",
            "merged_end_ref": "raw-B-end",
            "gold_patch_file": "gold.patch",
            "gold_patch_sha256": hashlib.sha256(self.semantic_patch).hexdigest(),
            "semantic_materialization": {
                "net_patch": {
                    "semantic_scope": {"selected_paths": ["semantic.txt"]}
                }
            },
        }
        write_json(patch_dir / "patch_manifest.json", manifest)
        events = copy.deepcopy(self.events)
        events["semantic_derivations"] = [
            {
                "subject": "B:end",
                "seed_subject": "B:start",
                "method": DERIVATION_METHOD,
                "patch_manifest": "patches/B/patch_manifest.json",
            }
        ]
        write_json(self.events_path, events)
        artifacts = plan_dag_routing(
            repo=self.repo,
            metadata_path=self.metadata_path,
            dag_path=self.dag_path,
            events_path=self.events_path,
        )
        routing = artifacts["routing_manifest"]
        self.assertEqual(routing["status"], "complete")
        self.assertEqual(routing["denominators"]["raw_projected_endpoints"], 5)
        by_subject = {row["subject"]: row for row in routing["endpoints"]}
        derived = by_subject["B:end"]
        self.assertEqual(derived["materialization"], DERIVATION_METHOD)
        self.assertEqual(derived["seed_subject"], "B:start")
        semantic = command(
            "git",
            "show",
            f"{derived['expected_output_tree']}:semantic.txt",
            cwd=self.repo,
        ).decode().strip()
        spring = command(
            "git",
            "show",
            f"{derived['expected_output_tree']}:spring.txt",
            cwd=self.repo,
        ).decode().strip()
        self.assertEqual(semantic, "semantic-post")
        self.assertEqual(spring, "spring-post")
        replay = replay_dag_routing(repo=self.repo, artifacts=artifacts)
        self.assertEqual(replay["status"], "exact")
        self.assertEqual(replay["replayed_endpoint_count"], 6)

    def test_reactor_blocking_endpoint_and_cross_alias_are_covered(self) -> None:
        audit = self.root / "reactor.json"
        write_json(
            audit,
            {
                "blocking_aliases": [
                    {
                        "kind": "endpoint",
                        "id": "B:start",
                        "tree": "1" * 40,
                        "dangling_module_refs": [{"target_pom": "spring.txt"}],
                    },
                    {
                        "kind": "cross_composition",
                        "id": "B:start-implementation+end-tests",
                        "tree": "2" * 40,
                        "dangling_module_refs": [{"target_pom": "spring.txt"}],
                    },
                ]
            },
        )
        artifacts = plan_dag_routing(
            repo=self.repo,
            metadata_path=self.metadata_path,
            dag_path=self.dag_path,
            events_path=self.events_path,
            blocking_audit_path=audit,
        )
        coverage = artifacts["routing_manifest"]["reactor_blocking_coverage"]
        self.assertEqual(coverage["status"], "covered")
        self.assertEqual(coverage["blocking_alias_count"], 2)
        self.assertEqual(coverage["endpoint_alias_count"], 1)
        self.assertEqual(coverage["cross_composition_alias_count"], 1)
        self.assertEqual(coverage["routed_implementation_tree_count"], 1)
        self.assertEqual(coverage["routed_implementation_trees_cleared"], 1)


if __name__ == "__main__":
    unittest.main()
