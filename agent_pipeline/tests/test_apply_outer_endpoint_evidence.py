from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


PIPELINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE_ROOT.parent))

import build_merged_dataset as merged  # noqa: E402
from agent_pipeline import adjudicate_outer_compile_failures as adjudicator  # noqa: E402
from agent_pipeline import apply_outer_endpoint_evidence as publisher  # noqa: E402


WORKSPACE = "example_org_example_repo_v1_v2"
RETAINED = "A"
ENTRY = "A"
EXIT = "B"
CANDIDATES = (
    "a_fail_to_pass",
    "b_pass_to_pass",
    "c_none_to_pass",
    "d_fail_to_fail",
    "e_flaky",
    "f_uncollected",
)
PROJECT_ROOT = PIPELINE_ROOT.parent
FORMAL_DATASET = PROJECT_ROOT / "SWE-Milestone-data-repartitioned"
FORMAL_PLAN = PROJECT_ROOT / "merge_plan.json"
FORMAL_LEGACY_RAW = (
    PROJECT_ROOT
    / "logs/endpoint_validation/reruns/endpoint-rerun-v1/operation-1/manifest.json"
)
FORMAL_LEGACY_KEY = (
    "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6",
    "M003.3",
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def file_record(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def canonical_hash(value: object) -> str:
    return publisher.sha256_bytes(publisher.canonical_json_bytes(value))


def state(
    requested_ref: str,
    canonical_commit: str,
    runnable_commit: str,
    marker: str,
) -> dict[str, object]:
    return {
        "requested_ref": requested_ref,
        "canonical_commit": canonical_commit,
        "canonical_tree": marker * 40,
        "runnable_commit": runnable_commit,
        "runnable_tree": marker.upper() * 40,
    }


def endpoint_result(
    identifier: str,
    observations: list[tuple[str, str | None]],
) -> dict[str, object]:
    outcomes = [outcome for status, outcome in observations if status == "collected"]
    if len(outcomes) == 3 and len(set(outcomes)) == 1 and outcomes[0] in {
        "passed",
        "failed",
        "none",
    }:
        disposition = "stable"
        outcome = outcomes[0]
    elif len(outcomes) == 3 and len(set(outcomes)) > 1:
        disposition = "flaky"
        outcome = None
    else:
        disposition = "unresolved"
        outcome = None
    return {
        "test_id": identifier,
        "disposition": disposition,
        "outcome": outcome,
        "reason": "synthetic three-run evidence",
        "attempt_observations": [
            {"attempt": index, "status": status, "outcome": observed}
            for index, (status, observed) in enumerate(observations, 1)
        ],
        "oracle": {"status": "portable"},
    }


class SyntheticFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dataset = root / "dataset"
        self.repo = self.dataset / WORKSPACE
        self.plan_path = root / "merge_plan.json"
        self.evidence_path = root / "evidence.json"
        self.classification_path = (
            self.repo / "test_results" / RETAINED / f"{RETAINED}_classification.json"
        )
        self.provenance_path = self.repo / "merge_provenance" / f"{RETAINED}.json"
        self.patch_manifest_path = self.repo / "patches" / RETAINED / "patch_manifest.json"
        self.probe_path = root / "probe.json"
        self.sif_manifest_path = root / "sif_manifest.jsonl"
        self.proof_path = root / "attempt-proof.txt"
        self._build()

    def _build(self) -> None:
        unresolved = [
            {
                "test_id": identifier,
                "entry_start": None,
                "exit_end": "pass",
                "reason": "synthetic unresolved outer endpoint",
            }
            for identifier in CANDIDATES
        ]
        logical = {
            "policy": "ordered_outer_f2p_and_common_p2p_only",
            "outer_transition": {"entry_milestone": ENTRY, "exit_milestone": EXIT},
            "candidate_counts": {
                "source_f2p_union": len(CANDIDATES),
                "source_p2p_union": 0,
                "source_p2p_intersection": 0,
                "source_n2p_union_excluded": 0,
            },
            "converted_f2p_to_p2p": [],
            "excluded_f2p": [],
            "unresolved_outer_evidence": unresolved,
            "excluded_n2p": [],
            "excluded_non_common_p2p": [],
            "manual_test_adjustments": {
                "policy": "explicit_human_review_with_outer_evidence_or_patch_semantics",
                "promote_to_fail_to_pass": [],
                "exclude_from_grading": [
                    {
                        "test_id": "manual_excluded",
                        "status": "EXCLUDED_FROM_GRADING",
                        "reason": "synthetic reviewed supersession",
                        "superseded_active_diagnostics": [
                            {
                                "diagnostic": "unresolved_outer_evidence",
                                "record": {
                                    "test_id": "manual_excluded",
                                    "entry_start": None,
                                    "exit_end": "pass",
                                    "reason": "pre-review diagnostic",
                                },
                                "superseded_by": "EXCLUDED_FROM_GRADING",
                            }
                        ],
                    }
                ],
            },
        }
        stable = {category: [] for category in merged.CLASSIFICATION_CATEGORIES}
        stable["fail_to_pass"] = list(CANDIDATES)
        summary = {category: len(stable[category]) for category in merged.CLASSIFICATION_CATEGORIES}
        classification = {
            "schema_version": 1,
            "classification_scope": "logical_outer_f2p_p2p_contract_n2p_excluded",
            "full_transition_classification_status": "logical_composition_pending_endpoint_rerun",
            "summary": summary,
            "classification": stable,
            "stable_classification": stable,
            "effective_tests": {role: stable[role] for role in merged.ROLES},
            "logical_composition": logical,
        }
        write_json(self.classification_path, classification)

        patch_manifest = {
            "semantic_materialization": {
                "combined_transition_validation": {
                    "status": "exact_on_declared_semantic_paths",
                    "reviewed_hunk_exclusions": [],
                },
                "net_patch": {"reviewed_hunk_exclusions": []},
            }
        }
        write_json(self.patch_manifest_path, patch_manifest)
        source_effective = {
            "fail_to_pass": list(CANDIDATES),
            "none_to_pass": [],
            "pass_to_pass": [],
        }
        provenance = {
            "workspace": WORKSPACE,
            "retained_id": RETAINED,
            "entry_id": ENTRY,
            "exit_id": EXIT,
            "ordered_source_ids": [ENTRY, EXIT],
            "patch_segments": [
                {
                    "milestone_id": ENTRY,
                    "start_ref": "milestone-A-start",
                    "end_ref": "milestone-A-end",
                    "start_ref_original": "1" * 40,
                    "start_commit": "2" * 40,
                    "end_commit": "3" * 40,
                },
                {
                    "milestone_id": EXIT,
                    "start_ref": "milestone-B-start",
                    "end_ref": "milestone-B-end",
                    "start_ref_original": "4" * 40,
                    "start_commit": "5" * 40,
                    "end_commit": "6" * 40,
                },
            ],
            "patch_materialization": {
                "status": "materialized",
                "manifest_sha256": publisher.sha256_file(self.patch_manifest_path),
            },
            "test_contract": {
                "source_results": [
                    {
                        "milestone_id": ENTRY,
                        "effective": {role: [] for role in merged.ROLES},
                    },
                    {"milestone_id": EXIT, "effective": source_effective},
                ],
                "effective_counts": {
                    role: len(stable[role]) for role in merged.ROLES
                },
                "effective_tests": stable,
                "test_origins": {
                    "fail_to_pass": {identifier: [EXIT] for identifier in CANDIDATES},
                    "none_to_pass": {},
                    "pass_to_pass": {},
                },
                "logical_composition": logical,
                "classification_artifact": {
                    "file": f"test_results/{RETAINED}/{RETAINED}_classification.json",
                    "sha256": publisher.sha256_file(self.classification_path),
                },
            },
        }
        write_json(self.provenance_path, provenance)

        plan = {
            "schema_version": 1,
            "operations": [
                {
                    "workspace": WORKSPACE,
                    "retained_id": RETAINED,
                    "entry_id": ENTRY,
                    "exit_id": EXIT,
                    "ordered_source_ids": [ENTRY, EXIT],
                    "expected_effective_test_counts": {
                        "fail_to_pass": len(CANDIDATES),
                        "none_to_pass": 0,
                        "pass_to_pass": 0,
                    },
                }
            ],
        }
        write_json(self.plan_path, plan)
        root_operation = {
            "workspace": WORKSPACE,
            "retained_id": RETAINED,
            "entry_id": ENTRY,
            "exit_id": EXIT,
            "effective_test_counts": {
                "fail_to_pass": len(CANDIDATES),
                "none_to_pass": 0,
                "pass_to_pass": 0,
            },
            "patch_manifest_file": (
                f"{WORKSPACE}/patches/{RETAINED}/patch_manifest.json"
            ),
        }
        root_manifest = {
            "schema_version": 1,
            "operation_count": 1,
            "merge_plan_sha256": publisher.sha256_file(self.plan_path),
            "problem_statement_coverage": {"status": "PASS", "sha256": "0" * 64},
            "full_transition_classification": {"status": "pending"},
            "operations": [root_operation],
        }
        root_bytes = publisher.pretty_json_bytes(root_manifest)
        self.dataset.mkdir(parents=True, exist_ok=True)
        (self.dataset / "REPARTITION_MANIFEST.json").write_bytes(root_bytes)
        (self.dataset / "merge_manifest.json").write_bytes(root_bytes)

        self.sif_manifest_path.write_text("{}\n", encoding="utf-8")
        self.proof_path.write_text("immutable synthetic proof\n", encoding="utf-8")
        images: dict[str, dict[str, object]] = {}
        for position, milestone in (("A", ENTRY), ("B", EXIT)):
            image_path = self.root / f"{position}.sif"
            image_path.write_bytes(f"synthetic {position} image".encode())
            images[position] = {
                **file_record(image_path),
                "source": f"docker://example/{milestone}:v1",
                "destination_rel": f"{WORKSPACE}/{milestone}.sif",
                "milestone_id": milestone,
            }

        source_states = {
            "A": {
                "start": state("milestone-A-start", "a" * 40, "b" * 40, "a"),
                "end": state("milestone-A-end", "3" * 40, "c" * 40, "b"),
            },
            "B": {
                "start": state("milestone-B-start", "4" * 40, "e" * 40, "c"),
                "end": state("milestone-B-end", "d" * 40, "f" * 40, "d"),
            },
        }
        probe = {
            "schema_version": 1,
            "kind": "read_only_four_state_test_definition_probe",
            "workspace": WORKSPACE,
            "retained_id": RETAINED,
            "entry_id": ENTRY,
            "exit_id": EXIT,
            "source_order": [ENTRY, EXIT],
            "input_fingerprints": {
                "merge_provenance": {
                    "path": str(self.provenance_path),
                    "sha256": publisher.sha256_file(self.provenance_path),
                },
                "candidate_set_sha256": canonical_hash(unresolved),
                "sif_manifest": file_record(self.sif_manifest_path),
            },
            "sources": [
                {
                    "position": position,
                    "milestone_id": milestone,
                    "states": source_states[position],
                    "image": images[position],
                }
                for position, milestone in (("A", ENTRY), ("B", EXIT))
            ],
            "candidates": [
                {
                    "test_id": record["test_id"],
                    "candidate_sha256": canonical_hash(record),
                    "unresolved_outer_evidence": record,
                    "definitions": {},
                }
                for record in unresolved
            ],
        }
        write_json(self.probe_path, probe)

        entry_observations: dict[str, list[tuple[str, str | None]]] = {
            "a_fail_to_pass": [("collected", "failed")] * 3,
            "b_pass_to_pass": [("collected", "passed")] * 3,
            "c_none_to_pass": [("collected", "none")] * 3,
            "d_fail_to_fail": [("collected", "failed")] * 3,
            "e_flaky": [
                ("collected", "failed"),
                ("collected", "passed"),
                ("collected", "failed"),
            ],
            "f_uncollected": [("compile_error", None)] * 3,
        }
        exit_observations = {
            identifier: [("collected", "passed")] * 3 for identifier in CANDIDATES
        }
        exit_observations["d_fail_to_fail"] = [("collected", "failed")] * 3
        proof_record = file_record(self.proof_path)
        outer_states = {
            "entry_start": {
                "milestone_id": ENTRY,
                "state": "start",
                "requested_ref": "milestone-A-start",
                "runnable_commit": "b" * 40,
                "canonical_commit": "a" * 40,
                "provenance_snapshot_commit": "1" * 40,
                "probe_vs_provenance_commit_drift": True,
                "sif": images["A"],
            },
            "exit_end": {
                "milestone_id": EXIT,
                "state": "end",
                "requested_ref": "milestone-B-end",
                "runnable_commit": "f" * 40,
                "canonical_commit": "d" * 40,
                "provenance_snapshot_commit": "6" * 40,
                "probe_vs_provenance_commit_drift": True,
                "sif": images["B"],
            },
        }
        endpoint_payloads: dict[str, dict[str, object]] = {}
        endpoint_results: dict[str, dict[str, dict[str, object]]] = {}
        for endpoint_name, observations_by_id in (
            ("entry_start", entry_observations),
            ("exit_end", exit_observations),
        ):
            attempts = []
            for attempt_index in range(3):
                candidates = {
                    identifier: {
                        "status": observations_by_id[identifier][attempt_index][0],
                        "outcome": observations_by_id[identifier][attempt_index][1],
                    }
                    for identifier in CANDIDATES
                }
                attempts.append(
                    {
                        "attempt": attempt_index + 1,
                        "actual_head": outer_states[endpoint_name]["runnable_commit"],
                        "candidates": candidates,
                        **{
                            field: proof_record
                            for field in (
                                "normalized_report",
                                "apptainer_command",
                                "run_script",
                                "apptainer_stdout",
                                "apptainer_stderr",
                            )
                        },
                    }
                )
            results = {
                identifier: endpoint_result(identifier, observations_by_id[identifier])
                for identifier in CANDIDATES
            }
            endpoint_results[endpoint_name] = results
            endpoint_payloads[endpoint_name] = {
                "candidate_ids": list(CANDIDATES),
                "state": outer_states[endpoint_name],
                "attempts": attempts,
                "result_merger": proof_record,
                "candidate_results": [results[identifier] for identifier in CANDIDATES],
            }

        final_candidates = []
        disposition_counts = {
            "resolved": 0,
            "flaky": 0,
            "non_portable": 0,
            "unresolved": 0,
        }
        outcome_map = {"passed": "pass", "failed": "fail", "none": "none"}
        for identifier in CANDIDATES:
            entry_result = endpoint_results["entry_start"][identifier]
            exit_result = endpoint_results["exit_end"][identifier]
            if entry_result["disposition"] == exit_result["disposition"] == "stable":
                disposition = "resolved"
                transition = (
                    f"{outcome_map[str(entry_result['outcome'])]}_to_"
                    f"{outcome_map[str(exit_result['outcome'])]}"
                )
            elif "flaky" in {entry_result["disposition"], exit_result["disposition"]}:
                disposition = "flaky"
                transition = None
            else:
                disposition = "unresolved"
                transition = None
            disposition_counts[disposition] += 1
            final_candidates.append(
                {
                    "test_id": identifier,
                    "entry_start": entry_result,
                    "exit_end": exit_result,
                    "observed_transition": transition,
                    "disposition": disposition,
                }
            )
        evidence = {
            "schema_version": 2,
            "artifact_type": publisher.EVIDENCE_KIND,
            "status": "completed_with_unresolved_evidence",
            "mode": "both_outer_endpoints",
            "workspace": WORKSPACE,
            "retained_id": RETAINED,
            "entry_id": ENTRY,
            "exit_id": EXIT,
            "attempts_required": 3,
            "strategy": "synthetic",
            "inputs": {
                "dataset": str(self.dataset),
                "classification": file_record(self.classification_path),
                "merge_provenance": file_record(self.provenance_path),
                "probe": file_record(self.probe_path),
                "probe_canonical_json_sha256": canonical_hash(probe),
                "sif_manifest": file_record(self.sif_manifest_path),
                "destination_root": str(self.root),
                "images": images,
                "implementation": {
                    **{
                        name: file_record(path)
                        for name, path in publisher.SCHEMA_V2_IMPLEMENTATION_FILES.items()
                    },
                    publisher.SCHEMA_V2_REPORT_PARSER_DEPENDENCY_KEY: {
                        name: file_record(path)
                        for name, path in publisher.SCHEMA_V2_REPORT_PARSER_DIRECT_DEPENDENCIES.items()
                    },
                },
            },
            "canonical_outer_states": outer_states,
            "candidate_input": [
                {
                    "test_id": record["test_id"],
                    "unresolved_outer_evidence": record,
                    "probe_candidate_sha256": canonical_hash(record),
                    "source_f2p_owners": [EXIT],
                    "oracle": {"status": "portable"},
                }
                for record in unresolved
            ],
            "oracle_extractions": [],
            "endpoints": endpoint_payloads,
            "candidates": final_candidates,
            "summary": {"total": len(CANDIDATES), **disposition_counts},
        }
        write_json(self.evidence_path, evidence)

    def prepare(self) -> publisher.PreparedTransaction:
        return publisher.prepare_transaction(
            dataset=self.dataset,
            plan_path=self.plan_path,
            evidence_paths=[self.evidence_path],
            expected_operations=1,
        )

    def make_all_compile_entry_and_pass_exit(self) -> None:
        evidence = publisher.read_json(self.evidence_path)
        endpoint_results: dict[str, dict[str, dict[str, object]]] = {}
        for endpoint_name, status, outcome, disposition in (
            ("entry_start", "compile_error", None, "unresolved"),
            ("exit_end", "collected", "passed", "stable"),
        ):
            endpoint = evidence["endpoints"][endpoint_name]
            for attempt in endpoint["attempts"]:
                attempt["candidates"] = {
                    identifier: {"status": status, "outcome": outcome}
                    for identifier in CANDIDATES
                }
            results = {
                identifier: endpoint_result(
                    identifier, [(status, outcome)] * 3
                )
                for identifier in CANDIDATES
            }
            endpoint["candidate_results"] = [
                results[identifier] for identifier in CANDIDATES
            ]
            endpoint_results[endpoint_name] = results
        evidence["candidates"] = [
            {
                "test_id": identifier,
                "entry_start": endpoint_results["entry_start"][identifier],
                "exit_end": endpoint_results["exit_end"][identifier],
                "observed_transition": None,
                "disposition": "unresolved",
            }
            for identifier in CANDIDATES
        ]
        evidence["status"] = "completed_with_unresolved_evidence"
        evidence["summary"] = {
            "total": len(CANDIDATES),
            "resolved": 0,
            "flaky": 0,
            "non_portable": 0,
            "unresolved": len(CANDIDATES),
        }
        write_json(self.evidence_path, evidence)

    def approved_adjudication_payload(self) -> dict[str, object]:
        unresolved = publisher.read_json(self.classification_path)[
            "logical_composition"
        ]["unresolved_outer_evidence"]
        candidate_ids = sorted(CANDIDATES)
        return {
            "schema_version": 1,
            "artifact_type": "outer_compile_failure_adjudication",
            "status": "approved",
            "workspace": WORKSPACE,
            "retained_id": RETAINED,
            "entry_id": ENTRY,
            "exit_id": EXIT,
            "inputs": {
                "raw_evidence": file_record(self.evidence_path),
                "probe": file_record(self.probe_path),
                "source_classification": {"sha256": "1" * 64},
            },
            "technical_findings": {
                "raw_evidence_schema_version": 2,
                "evidence_semantics": {
                    "entry_start": "reviewed_inference_from_compile_failure",
                    "exit_end": "producer_confirmed_direct_observation",
                    "raw_runner_implementation_pinned": True,
                    "parser_correction_applied": False,
                    "legacy_raw_runner_caveat": None,
                },
                "candidate_set_sha256": canonical_hash(unresolved),
                "candidate_ids_sha256": canonical_hash(candidate_ids),
                "adjudicated_endpoint": "entry_start",
                "adjudicated_outcome": "fail",
                "compile_symbol_caveats": [
                    {
                        "symbol": "class:SyntheticMissingHelper",
                        "supported_by_source_gold_patch": False,
                        "matching_production_paths": [],
                    }
                ],
            },
            "adjudicated_candidates": [
                {
                    "test_id": identifier,
                    "raw_entry_disposition": "unresolved",
                    "raw_entry_statuses": ["compile_error"] * 3,
                    "adjudicated_entry_outcome": "fail",
                    "raw_exit_disposition": "stable",
                    "raw_exit_statuses": ["collected"] * 3,
                    "exit_outcome": "pass",
                    "exit_outcome_source": "producer_confirmed_direct_observation",
                    "resulting_transition": "fail_to_pass",
                }
                for identifier in candidate_ids
            ],
            "review": {
                "status": "approved",
                "reviewer": "synthetic-reviewer",
                "reason": "reviewed exact compile-failure inference",
                "inference_not_raw_observation": True,
                "entry_start_evidence_kind": "reviewed_inference_from_compile_failure",
                "exit_end_evidence_kind": "producer_confirmed_direct_observation",
                "raw_runner_implementation_pinned": True,
                "acknowledged_legacy_raw_runner_caveat": None,
            },
        }


class PublisherProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = SyntheticFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_prepare_is_read_only_and_projects_all_resolution_classes(self) -> None:
        watched = {
            path: path.read_bytes()
            for path in (
                self.fixture.classification_path,
                self.fixture.provenance_path,
                self.fixture.plan_path,
                self.fixture.dataset / "REPARTITION_MANIFEST.json",
                self.fixture.dataset / "merge_manifest.json",
            )
        }
        prepared = self.fixture.prepare()
        self.assertEqual(watched, {path: path.read_bytes() for path in watched})
        outputs = {item.target: json.loads(item.new_bytes) for item in prepared.updates}
        classification = outputs[self.fixture.classification_path.resolve()]
        self.assertEqual(
            ["a_fail_to_pass", "e_flaky", "f_uncollected"],
            classification["stable_classification"]["fail_to_pass"],
        )
        self.assertEqual(
            ["b_pass_to_pass"],
            classification["stable_classification"]["pass_to_pass"],
        )
        self.assertEqual([], classification["stable_classification"]["none_to_pass"])
        logical = classification["logical_composition"]
        self.assertEqual(["b_pass_to_pass"], logical["converted_f2p_to_p2p"])
        self.assertEqual(
            ["c_none_to_pass", "d_fail_to_fail"],
            [item["test_id"] for item in logical["excluded_f2p"]],
        )
        self.assertEqual(
            ["e_flaky", "f_uncollected"],
            [item["test_id"] for item in logical["unresolved_outer_evidence"]],
        )
        self.assertEqual(
            "manual_excluded",
            logical["manual_test_adjustments"]["exclude_from_grading"][0]["test_id"],
        )
        self.assertEqual(
            "unresolved_outer_evidence",
            logical["manual_test_adjustments"]["exclude_from_grading"][0][
                "superseded_active_diagnostics"
            ][0]["diagnostic"],
        )
        endpoint = logical["outer_endpoint_evidence"]
        self.assertEqual(publisher.PARTIAL_STATUS, endpoint["status"])
        self.assertEqual(2, endpoint["producer_schema_version"])
        self.assertEqual(
            {
                *publisher.SCHEMA_V2_IMPLEMENTATION_FILES,
                publisher.SCHEMA_V2_REPORT_PARSER_DEPENDENCY_KEY,
            },
            set(endpoint["producer_inputs"]["implementation"]),
        )
        self.assertEqual(4, endpoint["counts"]["resolved"])
        self.assertEqual(1, endpoint["counts"]["flaky"])
        self.assertEqual(1, endpoint["counts"]["uncollected"])
        self.assertEqual(1, endpoint["counts"]["none_to_pass_not_graded"])
        self.assertTrue(endpoint["endpoints"]["entry_start"]["probe_vs_provenance_commit_drift"])
        plan = outputs[self.fixture.plan_path.resolve()]
        self.assertEqual(
            len(CANDIDATES),
            plan["operations"][0]["expected_effective_test_counts"]["fail_to_pass"],
        )
        self.assertEqual(
            {"fail_to_pass": 3, "none_to_pass": 0, "pass_to_pass": 1},
            plan["operations"][0]["measured_effective_test_counts"],
        )
        root = outputs[(self.fixture.dataset / "REPARTITION_MANIFEST.json").resolve()]
        self.assertEqual(
            {"fail_to_pass": 6, "none_to_pass": 0, "pass_to_pass": 0},
            root["operations"][0]["pre_endpoint_effective_test_counts"],
        )

    def test_stage_publish_and_explicit_rollback_are_transactional(self) -> None:
        prepared = self.fixture.prepare()
        originals = {item.target: item.target.read_bytes() for item in prepared.updates}
        staging = self.fixture.root / "staged"
        publisher.stage_transaction(prepared, staging)
        self.assertEqual(originals, {path: path.read_bytes() for path in originals})
        committed = publisher.publish_staged_transaction(staging)
        self.assertEqual("committed", committed["status"])
        for item in prepared.updates:
            self.assertEqual(item.new_sha256, publisher.sha256_file(item.target))
        root = (self.fixture.dataset / "REPARTITION_MANIFEST.json").read_bytes()
        self.assertEqual(root, (self.fixture.dataset / "merge_manifest.json").read_bytes())
        rolled_back = publisher.rollback_staged_transaction(staging)
        self.assertEqual("rolled_back", rolled_back["status"])
        self.assertEqual(originals, {path: path.read_bytes() for path in originals})

    def test_publish_failure_rolls_back_every_replaced_file(self) -> None:
        prepared = self.fixture.prepare()
        originals = {item.target: item.target.read_bytes() for item in prepared.updates}
        staging = self.fixture.root / "staged-fault"
        publisher.stage_transaction(prepared, staging)

        def fail_after_second(index: int, _target: Path) -> None:
            if index == 1:
                raise RuntimeError("synthetic publisher fault")

        with self.assertRaisesRegex(RuntimeError, "synthetic publisher fault"):
            publisher.publish_staged_transaction(staging, fault_injector=fail_after_second)
        self.assertEqual(originals, {path: path.read_bytes() for path in originals})
        transaction = publisher.read_json(staging / "transaction.json")
        self.assertEqual("rolled_back", transaction["status"])


class PublisherRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = SyntheticFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def mutate(self, callback) -> None:
        value = publisher.read_json(self.fixture.evidence_path)
        callback(value)
        write_json(self.fixture.evidence_path, value)

    def mutate_probe_and_refresh_evidence(self, callback) -> None:
        probe = publisher.read_json(self.fixture.probe_path)
        callback(probe)
        write_json(self.fixture.probe_path, probe)
        evidence = publisher.read_json(self.fixture.evidence_path)
        evidence["inputs"]["probe"] = file_record(self.fixture.probe_path)
        evidence["inputs"]["probe_canonical_json_sha256"] = canonical_hash(probe)
        write_json(self.fixture.evidence_path, evidence)

    def test_rejects_missing_only_and_non_three_run_evidence(self) -> None:
        self.mutate(lambda value: value.__setitem__("mode", "missing_only"))
        with self.assertRaisesRegex(publisher.EvidenceError, "both_outer_endpoints"):
            self.fixture.prepare()

        self.fixture = SyntheticFixture(Path(self.temporary.name) / "second")
        self.mutate(lambda value: value.__setitem__("attempts_required", 2))
        with self.assertRaisesRegex(publisher.EvidenceError, "three attempts"):
            self.fixture.prepare()

    def test_rejects_candidate_set_or_attempt_summary_drift(self) -> None:
        self.mutate(lambda value: value["candidate_input"].pop())
        with self.assertRaisesRegex(publisher.EvidenceError, "candidate_input"):
            self.fixture.prepare()

        self.fixture = SyntheticFixture(Path(self.temporary.name) / "second")
        self.mutate(
            lambda value: value["endpoints"]["entry_start"]["candidate_results"][0][
                "attempt_observations"
            ][0].__setitem__("outcome", "passed")
        )
        with self.assertRaisesRegex(publisher.EvidenceError, "summarized attempt"):
            self.fixture.prepare()

    def test_rejects_counterpart_resolution_and_stale_input_hash(self) -> None:
        def counterpart(value: dict[str, object]) -> None:
            candidates = value["candidates"]
            assert isinstance(candidates, list)
            candidates[0]["disposition"] = "resolved_with_source_classification_counterpart"

        self.mutate(counterpart)
        with self.assertRaisesRegex(publisher.EvidenceError, "final disposition"):
            self.fixture.prepare()

        self.fixture = SyntheticFixture(Path(self.temporary.name) / "second")
        self.mutate(
            lambda value: value["inputs"]["classification"].__setitem__(
                "sha256", "0" * 64
            )
        )
        with self.assertRaisesRegex(publisher.EvidenceError, "SHA-256 is stale"):
            self.fixture.prepare()

    def test_probe_source_milestone_is_authoritative_when_image_copy_is_absent(self) -> None:
        def remove_duplicated_field(probe: dict[str, object]) -> None:
            for source in probe["sources"]:
                source["image"].pop("milestone_id")

        self.mutate_probe_and_refresh_evidence(remove_duplicated_field)
        prepared = self.fixture.prepare()
        outputs = {item.target: json.loads(item.new_bytes) for item in prepared.updates}
        endpoint = outputs[self.fixture.classification_path.resolve()][
            "outer_endpoint_evidence"
        ]
        self.assertEqual(ENTRY, endpoint["endpoints"]["entry_start"]["milestone_id"])
        self.assertEqual(EXIT, endpoint["endpoints"]["exit_end"]["milestone_id"])

    def test_probe_image_and_source_milestone_conflict_fails_closed(self) -> None:
        def conflict(probe: dict[str, object]) -> None:
            probe["sources"][0]["image"]["milestone_id"] = "FORGED"

        self.mutate_probe_and_refresh_evidence(conflict)
        with self.assertRaisesRegex(
            publisher.EvidenceError, "image/source A milestone fields conflict"
        ):
            self.fixture.prepare()


class PublisherAdjudicationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = SyntheticFixture(Path(self.temporary.name))
        self.fixture.make_all_compile_entry_and_pass_exit()
        self.adjudication_path = self.fixture.root / "approved-adjudication.json"
        write_json(
            self.adjudication_path,
            {"workspace": WORKSPACE, "retained_id": RETAINED},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def mutate(self, callback) -> None:
        value = publisher.read_json(self.fixture.evidence_path)
        callback(value)
        write_json(self.fixture.evidence_path, value)

    def test_approved_artifact_is_an_inference_overlay_not_raw_observation(self) -> None:
        artifact = self.fixture.approved_adjudication_payload()
        with mock.patch(
            "agent_pipeline.adjudicate_outer_compile_failures."
            "validate_adjudication_artifact",
            return_value=artifact,
        ):
            prepared = publisher.prepare_transaction(
                dataset=self.fixture.dataset,
                plan_path=self.fixture.plan_path,
                evidence_paths=[self.fixture.evidence_path],
                adjudication_paths=[self.adjudication_path],
                expected_operations=1,
            )
        outputs = {item.target: json.loads(item.new_bytes) for item in prepared.updates}
        classification = outputs[self.fixture.classification_path.resolve()]
        endpoint = classification["outer_endpoint_evidence"]
        self.assertEqual(publisher.COMPLETE_STATUS, endpoint["status"])
        self.assertEqual("approved_applied", endpoint["adjudication"]["status"])
        self.assertTrue(endpoint["adjudication"]["inference_not_raw_observation"])
        self.assertEqual(
            {
                "total": len(CANDIDATES),
                "resolved": 0,
                "flaky": 0,
                "non_portable": 0,
                "unresolved": len(CANDIDATES),
            },
            endpoint["producer_summary"],
        )
        for decision in endpoint["candidate_results"]:
            self.assertEqual("unresolved", decision["producer_disposition"])
            self.assertEqual("resolved", decision["effective_disposition"])
            self.assertTrue(decision["adjudication_applied"])
            self.assertEqual("fail_to_pass", decision["outer_transition"])
            self.assertEqual("uncollected", decision["entry_start"]["evidence_state"])
            self.assertEqual(
                ["compile_error"] * 3,
                decision["entry_start"]["observed_statuses"],
            )

        pre_contract = publisher.read_json(self.fixture.provenance_path)["test_contract"]
        pre_effective = {
            role: {
                publisher.merged.test_id(value)
                for value in pre_contract["effective_tests"][role]
            }
            for role in publisher.merged.ROLES
        }
        pre_logical = pre_contract["logical_composition"]
        projected, _logical, counts, status = (
            publisher.merged.project_measured_outer_endpoint_contract(
                pre_effective,
                pre_logical,
                endpoint,
                label="synthetic/adjudicated",
            )
        )
        self.assertEqual(publisher.COMPLETE_STATUS, status)
        self.assertEqual(len(CANDIDATES), counts["fail_to_pass"])
        self.assertEqual(set(CANDIDATES), projected["fail_to_pass"])

        tampered = copy.deepcopy(endpoint)
        tampered["adjudication"]["artifact_file_sha256"] = "forged"
        with self.assertRaisesRegex(
            publisher.merged.MergeError, "adjudication hash"
        ):
            publisher.merged.project_measured_outer_endpoint_contract(
                pre_effective,
                pre_logical,
                tampered,
                label="synthetic/adjudication-tamper",
            )

    def test_review_required_or_invalid_artifact_is_rejected(self) -> None:
        from agent_pipeline.adjudicate_outer_compile_failures import AdjudicationError

        with mock.patch(
            "agent_pipeline.adjudicate_outer_compile_failures."
            "validate_adjudication_artifact",
            side_effect=AdjudicationError("compile-failure adjudication is not approved"),
        ):
            with self.assertRaisesRegex(publisher.EvidenceError, "not approved"):
                publisher.prepare_transaction(
                    dataset=self.fixture.dataset,
                    plan_path=self.fixture.plan_path,
                    evidence_paths=[self.fixture.evidence_path],
                    adjudication_paths=[self.adjudication_path],
                    expected_operations=1,
                )

    def test_schema_2_rejects_missing_or_extra_implementation_records(self) -> None:
        self.mutate(lambda value: value["inputs"].pop("implementation"))
        with self.assertRaisesRegex(publisher.EvidenceError, "provenance is missing"):
            self.fixture.prepare()

        self.fixture = SyntheticFixture(Path(self.temporary.name) / "second")
        self.mutate(
            lambda value: value["inputs"]["implementation"].__setitem__(
                "undeclared_helper",
                file_record(publisher.SCHEMA_V2_IMPLEMENTATION_FILES["runner"]),
            )
        )
        with self.assertRaisesRegex(publisher.EvidenceError, "keys must be exactly"):
            self.fixture.prepare()

    def test_schema_2_rejects_each_tampered_implementation_hash(self) -> None:
        for index, name in enumerate(publisher.SCHEMA_V2_IMPLEMENTATION_FILES):
            with self.subTest(name=name):
                fixture = SyntheticFixture(
                    Path(self.temporary.name) / f"implementation-{index}"
                )
                value = publisher.read_json(fixture.evidence_path)
                value["inputs"]["implementation"][name]["sha256"] = "0" * 64
                write_json(fixture.evidence_path, value)
                with self.assertRaisesRegex(
                    publisher.EvidenceError, "SHA-256 is stale"
                ):
                    fixture.prepare()

    def test_schema_2_rejects_missing_extra_or_tampered_parser_dependency(self) -> None:
        dependency_key = publisher.SCHEMA_V2_REPORT_PARSER_DEPENDENCY_KEY
        dependency_names = list(
            publisher.SCHEMA_V2_REPORT_PARSER_DIRECT_DEPENDENCIES
        )

        missing = SyntheticFixture(Path(self.temporary.name) / "nested-missing")
        value = publisher.read_json(missing.evidence_path)
        value["inputs"]["implementation"][dependency_key].pop(dependency_names[0])
        write_json(missing.evidence_path, value)
        with self.assertRaisesRegex(publisher.EvidenceError, "keys must be exactly"):
            missing.prepare()

        extra = SyntheticFixture(Path(self.temporary.name) / "nested-extra")
        value = publisher.read_json(extra.evidence_path)
        value["inputs"]["implementation"][dependency_key]["undeclared_parser"] = (
            file_record(
                publisher.SCHEMA_V2_REPORT_PARSER_DIRECT_DEPENDENCIES[
                    dependency_names[0]
                ]
            )
        )
        write_json(extra.evidence_path, value)
        with self.assertRaisesRegex(publisher.EvidenceError, "keys must be exactly"):
            extra.prepare()

        for index, name in enumerate(dependency_names):
            with self.subTest(name=name):
                tampered = SyntheticFixture(
                    Path(self.temporary.name) / f"nested-hash-{index}"
                )
                value = publisher.read_json(tampered.evidence_path)
                value["inputs"]["implementation"][dependency_key][name][
                    "sha256"
                ] = "0" * 64
                write_json(tampered.evidence_path, value)
                with self.assertRaisesRegex(publisher.EvidenceError, "SHA-256 is stale"):
                    tampered.prepare()

        substituted = SyntheticFixture(Path(self.temporary.name) / "nested-path")
        value = publisher.read_json(substituted.evidence_path)
        dependencies = value["inputs"]["implementation"][dependency_key]
        dependencies[dependency_names[0]] = copy.deepcopy(
            dependencies[dependency_names[1]]
        )
        write_json(substituted.evidence_path, value)
        with self.assertRaisesRegex(publisher.EvidenceError, "path resolves to"):
            substituted.prepare()

    def test_schema_2_rejects_non_exact_implementation_file_record(self) -> None:
        self.mutate(
            lambda value: value["inputs"]["implementation"]["runner"].__setitem__(
                "untrusted_note", "ignored by a loose validator"
            )
        )
        with self.assertRaisesRegex(publisher.EvidenceError, "exact file record"):
            self.fixture.prepare()

    def test_schema_2_rejects_implementation_path_substitution(self) -> None:
        def substitute_runner(value: dict[str, object]) -> None:
            implementation = value["inputs"]["implementation"]
            implementation["runner"] = copy.deepcopy(
                implementation["official_report_parser"]
            )

        self.mutate(substitute_runner)
        with self.assertRaisesRegex(publisher.EvidenceError, "path resolves to"):
            self.fixture.prepare()

    def test_schema_1_evidence_without_implementation_remains_compatible(self) -> None:
        def downgrade(value: dict[str, object]) -> None:
            value["schema_version"] = 1
            value["inputs"].pop("implementation")

        self.mutate(downgrade)
        prepared = self.fixture.prepare()
        outputs = {item.target: json.loads(item.new_bytes) for item in prepared.updates}
        classification = outputs[self.fixture.classification_path.resolve()]
        endpoint = classification["outer_endpoint_evidence"]
        self.assertEqual(1, endpoint["producer_schema_version"])
        self.assertNotIn("implementation", endpoint["producer_inputs"])


class FormalLegacyV1PublisherIntegrationTests(unittest.TestCase):
    """Exercise the real Dubbo op1 exception without publishing canonical files."""

    def setUp(self) -> None:
        required = (FORMAL_DATASET, FORMAL_PLAN, FORMAL_LEGACY_RAW)
        if not all(path.exists() for path in required):
            self.fail("required formal Dubbo op1 publisher fixture is not available")
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        temporary = getattr(self, "temporary", None)
        if temporary is not None:
            temporary.cleanup()

    def test_approved_legacy_artifact_is_recomputed_as_effective_overlay(self) -> None:
        plan = merged.read_json(FORMAL_PLAN)
        operation = next(
            value
            for value in merged.validate_plan(plan, PROJECT_ROOT)
            if (value["workspace"], value["retained_id"]) == FORMAL_LEGACY_KEY
        )
        workspace, retained = FORMAL_LEGACY_KEY
        classification_path = (
            FORMAL_DATASET
            / workspace
            / "test_results"
            / retained
            / f"{retained}_classification.json"
        )
        provenance_path = (
            FORMAL_DATASET / workspace / "merge_provenance" / f"{retained}.json"
        )
        source_results, pre_effective, pre_logical = (
            merged.expected_logical_test_prestate(
                PROJECT_ROOT / plan["source_dataset"] / workspace,
                operation,
            )
        )
        preclassification_bytes, preprovenance_bytes = (
            merged.reconstruct_prepublication_test_inputs(
                source_results=source_results,
                pre_effective=pre_effective,
                pre_logical=pre_logical,
                published_classification=merged.read_json(classification_path),
                published_provenance=merged.read_json(provenance_path),
            )
        )
        snapshots = adjudicator.PrepublicationInputSnapshots(
            classification_path=classification_path,
            classification_bytes=preclassification_bytes,
            classification_sha256=hashlib.sha256(
                preclassification_bytes
            ).hexdigest(),
            merge_provenance_path=provenance_path,
            merge_provenance_bytes=preprovenance_bytes,
            merge_provenance_sha256=hashlib.sha256(
                preprovenance_bytes
            ).hexdigest(),
        )
        watched_paths = (
            classification_path,
            provenance_path,
            FORMAL_PLAN,
            FORMAL_DATASET / "REPARTITION_MANIFEST.json",
            FORMAL_DATASET / "merge_manifest.json",
        )
        watched = {path: path.read_bytes() for path in watched_paths}

        approved_path = self.root / "approved-legacy-v1.json"
        artifact = adjudicator.build_adjudication(
            dataset=FORMAL_DATASET,
            raw_evidence_path=FORMAL_LEGACY_RAW,
            approve=True,
            reviewer="formal-publisher-regression",
            approval_reason=(
                "Reviewed A compile inference and B parser-corrected observation."
            ),
            prepublication_input_snapshots=snapshots,
        )
        write_json(approved_path, artifact)
        self.assertEqual(
            artifact,
            adjudicator.validate_adjudication_artifact(
                path=approved_path,
                dataset=FORMAL_DATASET,
                raw_evidence_path=FORMAL_LEGACY_RAW,
                require_approved=True,
                prepublication_input_snapshots=snapshots,
            ),
        )
        classification = merged.read_json(classification_path)
        endpoint = classification["outer_endpoint_evidence"]
        self.assertEqual(publisher.COMPLETE_STATUS, endpoint["status"])
        self.assertEqual(18, endpoint["counts"]["resolved"])
        self.assertEqual(18, endpoint["counts"]["fail_to_pass"])
        self.assertEqual(18, endpoint["producer_summary"]["unresolved"])
        self.assertEqual(
            "parser_corrected_direct_observation",
            endpoint["adjudication"]["exit_end_evidence_kind"],
        )
        self.assertFalse(endpoint["adjudication"]["raw_runner_implementation_pinned"])
        self.assertTrue(endpoint["adjudication"]["parser_correction_applied"])
        for decision in endpoint["candidate_results"]:
            self.assertEqual("unresolved", decision["producer_disposition"])
            self.assertEqual("resolved", decision["effective_disposition"])
            self.assertTrue(decision["adjudication_applied"])
            self.assertEqual("fail_to_pass", decision["outer_transition"])
            self.assertEqual(
                ["zero_selected"] * 3,
                decision["exit_end"]["observed_statuses"],
            )

        measured, _logical, counts, status = (
            merged.project_measured_outer_endpoint_contract(
                pre_effective,
                pre_logical,
                endpoint,
                label="formal-legacy-v1/dubbo-op1",
            )
        )
        self.assertEqual(publisher.COMPLETE_STATUS, status)
        self.assertEqual(18, counts["resolved"])
        self.assertTrue(
            set(endpoint["adjudication"]["candidate_ids"])
            <= measured["fail_to_pass"]
        )
        self.assertEqual(watched, {path: path.read_bytes() for path in watched_paths})


if __name__ == "__main__":
    unittest.main()
