from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

import build_merged_dataset as builder
from agent_pipeline import adjudicate_outer_compile_failures as adjudicator
from agent_pipeline.tests.test_adjudicate_nushell_compile_failures import (
    NushellOp5Fixture,
    write_json as write_fixture_json,
)
from agent_pipeline.tests.test_apply_outer_endpoint_evidence import (
    SyntheticFixture as PublisherSyntheticFixture,
)


ROOT = Path(__file__).resolve().parent
PLAN = builder.read_json(ROOT / "merge_plan.json")
OPERATIONS = builder.validate_plan(PLAN, ROOT)
SOURCE = ROOT / PLAN["source_dataset"]
OUTPUT = ROOT / PLAN["output_dataset"]


class PublishedAdjudicationOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _endpoint(self, fixture: NushellOp5Fixture) -> dict:
        artifact = fixture.approved()
        write_fixture_json(fixture.adjudication_path, artifact)
        artifact_bytes = fixture.adjudication_path.read_bytes()
        evidence_bytes = fixture.raw_path.read_bytes()
        findings = artifact["technical_findings"]
        review = artifact["review"]
        inputs = artifact["inputs"]
        semantics = findings["evidence_semantics"]
        candidate_ids = sorted(
            value["test_id"] for value in artifact["adjudicated_candidates"]
        )
        caveats = findings["compile_symbol_caveats"]
        compact = {
            "status": "approved_applied",
            "policy": (
                "explicitly reviewed compile-failure inference; raw endpoint observations "
                "remain unchanged and are not represented as collected failures"
            ),
            "artifact_type": adjudicator.ARTIFACT_TYPE,
            "artifact_file": str(fixture.adjudication_path.resolve()),
            "artifact_file_bytes": len(artifact_bytes),
            "artifact_file_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "artifact_canonical_json_sha256": builder.canonical_json_sha256(artifact),
            "reviewer": review["reviewer"],
            "approval_reason": review["reason"],
            "inference_not_raw_observation": True,
            "raw_evidence_schema_version": 2,
            "entry_start_evidence_kind": semantics["entry_start"],
            "exit_end_evidence_kind": semantics["exit_end"],
            "raw_runner_implementation_pinned": True,
            "parser_correction_applied": False,
            "legacy_raw_runner_caveat": None,
            "candidate_ids": candidate_ids,
            "candidate_ids_sha256": builder.canonical_json_sha256(candidate_ids),
            "candidate_set_sha256": findings["candidate_set_sha256"],
            "adjudicated_endpoint": "entry_start",
            "adjudicated_outcome": "fail",
            "resulting_transition": "fail_to_pass",
            "raw_evidence_sha256": inputs["raw_evidence"]["sha256"],
            "probe_sha256": inputs["probe"]["sha256"],
            "source_classification_sha256": inputs["source_classification"]["sha256"],
            "compile_symbol_caveat_count": len(caveats),
            "compile_symbol_caveats_sha256": builder.canonical_json_sha256(caveats),
        }
        return {
            "evidence_file": str(fixture.raw_path.resolve()),
            "evidence_file_bytes": len(evidence_bytes),
            "evidence_file_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "evidence_canonical_json_sha256": builder.canonical_json_sha256(
                json.loads(evidence_bytes)
            ),
            "adjudication": compact,
        }

    def test_reopens_recomputes_and_matches_compact_overlay(self) -> None:
        fixture = NushellOp5Fixture(self.root / "valid")
        endpoint = self._endpoint(fixture)
        builder.validate_published_adjudication_overlay(
            endpoint, dataset=fixture.dataset, label="fixture/op5"
        )

        endpoint["adjudication"]["approval_reason"] = "forged compact reason"
        with self.assertRaisesRegex(builder.MergeError, "compact overlay is not canonical"):
            builder.validate_published_adjudication_overlay(
                endpoint, dataset=fixture.dataset, label="fixture/op5"
            )

    def test_rejects_artifact_file_tamper_after_publication(self) -> None:
        fixture = NushellOp5Fixture(self.root / "tamper")
        endpoint = self._endpoint(fixture)
        fixture.adjudication_path.write_text(
            fixture.adjudication_path.read_text(encoding="utf-8") + " ",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(builder.MergeError, "adjudication/raw evidence hash is stale"):
            builder.validate_published_adjudication_overlay(
                endpoint, dataset=fixture.dataset, label="fixture/op5"
            )

    def test_real_op1_and_op5_postpublication_transforms_recompute_exactly(self) -> None:
        if not OUTPUT.is_dir():
            self.fail("required formal postpublication dataset is not available")
        validated: set[tuple[str, str]] = set()
        for operation in OPERATIONS:
            workspace = operation["workspace"]
            retained = operation["retained_id"]
            output_repo = OUTPUT / workspace
            classification_path = (
                output_repo
                / "test_results"
                / retained
                / f"{retained}_classification.json"
            )
            provenance_path = (
                output_repo / "merge_provenance" / f"{retained}.json"
            )
            classification = builder.read_json(classification_path)
            endpoint = classification.get("outer_endpoint_evidence")
            if (
                not isinstance(endpoint, dict)
                or endpoint.get("adjudication", {}).get("status")
                != "approved_applied"
            ):
                continue
            provenance = builder.read_json(provenance_path)
            source_results, pre_effective, pre_logical = (
                builder.expected_logical_test_prestate(
                    SOURCE / workspace, operation
                )
            )
            classification_bytes, provenance_bytes = (
                builder.reconstruct_prepublication_test_inputs(
                    source_results=source_results,
                    pre_effective=pre_effective,
                    pre_logical=pre_logical,
                    published_classification=classification,
                    published_provenance=provenance,
                )
            )
            snapshots = adjudicator.PrepublicationInputSnapshots(
                classification_path=classification_path,
                classification_bytes=classification_bytes,
                classification_sha256=hashlib.sha256(
                    classification_bytes
                ).hexdigest(),
                merge_provenance_path=provenance_path,
                merge_provenance_bytes=provenance_bytes,
                merge_provenance_sha256=hashlib.sha256(
                    provenance_bytes
                ).hexdigest(),
            )
            fingerprints = endpoint["input_fingerprints"]
            self.assertEqual(
                fingerprints["classification_sha256"],
                snapshots.classification_sha256,
            )
            self.assertEqual(
                fingerprints["merge_provenance_sha256"],
                snapshots.merge_provenance_sha256,
            )
            builder.validate_published_adjudication_overlay(
                endpoint,
                dataset=OUTPUT,
                label=f"formal/{workspace}/{retained}",
                prepublication_input_snapshots=snapshots,
            )
            expected_tests, expected_logical, measured_counts, status = (
                builder.project_measured_outer_endpoint_contract(
                    pre_effective,
                    pre_logical,
                    endpoint,
                    label=f"formal-replay/{workspace}/{retained}",
                )
            )
            expected_stable, _ = builder.merge_classification(
                source_results, expected_tests
            )
            replayed_classification, replayed_provenance = (
                builder.replay_endpoint_publication(
                    prepublication_classification_bytes=classification_bytes,
                    prepublication_provenance_bytes=provenance_bytes,
                    endpoint_record=endpoint,
                    expected_stable=expected_stable,
                    expected_logical=expected_logical,
                    measured_counts=measured_counts,
                    status=status,
                    source_order=list(operation["ordered_source_ids"]),
                )
            )
            self.assertEqual(classification, replayed_classification)
            self.assertEqual(provenance, replayed_provenance)

            # A field overwritten by publication is caught by exact forward
            # replay, while a field outside its mutation surface remains in
            # the reverse projection and breaks the old producer hash.
            tampered_overlay = copy.deepcopy(classification)
            tampered_overlay["merged_from_attempts"]["mode"] = "forged"
            self.assertNotEqual(tampered_overlay, replayed_classification)
            tampered_preserved = copy.deepcopy(classification)
            tampered_preserved["flaky_tests"] = list(
                tampered_preserved.get("flaky_tests", [])
            ) + [{"test_id": "forged::flaky"}]
            tampered_classification_bytes, _ = (
                builder.reconstruct_prepublication_test_inputs(
                    source_results=source_results,
                    pre_effective=pre_effective,
                    pre_logical=pre_logical,
                    published_classification=tampered_preserved,
                    published_provenance=provenance,
                )
            )
            self.assertNotEqual(
                fingerprints["classification_sha256"],
                hashlib.sha256(tampered_classification_bytes).hexdigest(),
            )
            validated.add((workspace, retained))

        self.assertEqual(
            {
                ("apache_dubbo_dubbo-3.3.3_dubbo-3.3.6", "M003.3"),
                (
                    "nushell_nushell_0.106.0_0.108.0",
                    "milestone_core_development.4",
                ),
            },
            validated,
        )

    def test_forward_replay_matches_actual_partial_publication(self) -> None:
        fixture = PublisherSyntheticFixture(self.root / "partial-publication")
        preclassification_bytes = fixture.classification_path.read_bytes()
        preprovenance_bytes = fixture.provenance_path.read_bytes()
        prepared = fixture.prepare()
        outputs = {
            update.target: json.loads(update.new_bytes)
            for update in prepared.updates
        }
        classification = outputs[fixture.classification_path.resolve()]
        provenance = outputs[fixture.provenance_path.resolve()]
        endpoint = classification["outer_endpoint_evidence"]
        preclassification = json.loads(preclassification_bytes)
        pre_effective = {
            role: set(preclassification["stable_classification"][role])
            for role in builder.ROLES
        }
        expected_tests, expected_logical, counts, status = (
            builder.project_measured_outer_endpoint_contract(
                pre_effective,
                preclassification["logical_composition"],
                endpoint,
                label="synthetic/partial-publication",
            )
        )
        # The fixture's actual publisher output is the authoritative stable
        # record projection; replay independently covers every other mutated
        # field, including partial/unresolved origin behavior.
        replayed_classification, replayed_provenance = (
            builder.replay_endpoint_publication(
                prepublication_classification_bytes=preclassification_bytes,
                prepublication_provenance_bytes=preprovenance_bytes,
                endpoint_record=endpoint,
                expected_stable=classification["stable_classification"],
                expected_logical=expected_logical,
                measured_counts=counts,
                status=status,
                source_order=["A", "B"],
            )
        )
        self.assertEqual(expected_tests["fail_to_pass"], set(
            builder.test_id(value)
            for value in classification["stable_classification"]["fail_to_pass"]
        ))
        self.assertEqual(classification, replayed_classification)
        self.assertEqual(provenance, replayed_provenance)

        preorigins = json.loads(preprovenance_bytes)["test_contract"]["test_origins"]
        postorigins = provenance["test_contract"]["test_origins"]
        unresolved_ids = {
            decision["test_id"]
            for decision in endpoint["candidate_results"]
            if decision["effective_disposition"] != "resolved"
        }
        self.assertTrue(unresolved_ids)
        for identifier in unresolved_ids:
            self.assertEqual(
                preorigins["fail_to_pass"][identifier],
                postorigins["fail_to_pass"][identifier],
            )


class SourceContractTests(unittest.TestCase):
    def test_all_six_logical_contracts_match_audited_counts_and_exact_ids(self) -> None:
        trailing_space_counts: dict[tuple[str, str], int] = {}
        for operation in OPERATIONS:
            repo = SOURCE / operation["workspace"]
            _, effective, logical = builder.expected_logical_test_prestate(repo, operation)
            pre_counts = {role: len(effective[role]) for role in builder.ROLES}
            self.assertEqual(operation["expected_effective_test_counts"], pre_counts)
            endpoint_summary = operation.get("outer_endpoint_evidence")
            if isinstance(endpoint_summary, dict):
                evidence_counts = endpoint_summary["counts"]
                self.assertEqual(
                    len(logical["unresolved_outer_evidence"]),
                    evidence_counts["candidate_count"],
                )
                self.assertEqual(
                    evidence_counts["resolved"],
                    evidence_counts["fail_to_pass"]
                    + evidence_counts["pass_to_pass"]
                    + evidence_counts["none_to_pass_not_graded"]
                    + evidence_counts["excluded_other"],
                )
                measured_counts = {
                    "fail_to_pass": (
                        pre_counts["fail_to_pass"]
                        - evidence_counts["resolved"]
                        + evidence_counts["fail_to_pass"]
                    ),
                    "none_to_pass": pre_counts["none_to_pass"],
                    "pass_to_pass": (
                        pre_counts["pass_to_pass"] + evidence_counts["pass_to_pass"]
                    ),
                }
                self.assertEqual(
                    operation["measured_effective_test_counts"], measured_counts
                )
            self.assertFalse(effective["fail_to_pass"] & effective["none_to_pass"])
            trailing_space_counts[(operation["workspace"], operation["retained_id"])] = sum(
                identifier != identifier.strip() for identifier in effective["pass_to_pass"]
            )

        self.assertEqual(
            1,
            trailing_space_counts[
                ("element-hq_element-web_v1.11.95_v1.11.97", "feature_enhancements")
            ],
        )
        self.assertEqual(
            7,
            trailing_space_counts[
                ("navidrome_navidrome_v0.57.0_v0.58.0", "milestone_003_sub-04")
            ],
        )

    def test_dubbo_cross_field_functional_filter_matches_official_evaluator(self) -> None:
        repo = SOURCE / "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6"
        result = builder.load_effective_tests(repo, "M003.2")
        self.assertEqual(0, result["effective_counts"]["none_to_pass"])
        self.assertEqual(5, len(result["filtered_out"]["none_to_pass"]))
        self.assertTrue(
            all(
                record["match_mode"] == "exact"
                for record in result["filter_match_provenance"]["none_to_pass"]
            )
        )

    def test_all_catalog_contractions_are_acyclic_and_complete(self) -> None:
        by_workspace: dict[str, list[dict]] = defaultdict(list)
        for operation in OPERATIONS:
            by_workspace[operation["workspace"]].append(operation)
        for workspace, operations in by_workspace.items():
            repo = SOURCE / workspace
            _, rows = builder.read_csv(repo / "milestones.csv")
            source_ids = {row["id"] for row in rows}
            mapping = {
                absorbed: operation["retained_id"]
                for operation in operations
                for absorbed in operation["absorbed_ids"]
            }
            nodes = source_ids - set(mapping)
            edges: set[tuple[str, str]] = set()
            for filename in builder.DEPENDENCY_FILES:
                path = repo / filename
                if path.is_file():
                    _, edge_rows = builder.read_csv(path)
                    contracted = builder.contract_edge_rows(edge_rows, mapping)
                    edges.update((row["source_id"], row["target_id"]) for row in contracted)
            metadata = builder.read_json(repo / "metadata.json")
            preferred = builder.ordered_unique(
                mapping.get(node, node) for node in metadata["topological_order"]["full_order"]
            )
            order = builder.stable_topological_order(nodes, edges, preferred, label=workspace)
            self.assertEqual(nodes, set(order))
            self.assertEqual(len(nodes), len(order))

    def test_real_manual_adjustments_supersede_active_diagnostics(self) -> None:
        adjusted_operations = {
            operation["retained_id"]: operation
            for operation in OPERATIONS
            if operation.get("manual_test_adjustments")
        }
        self.assertEqual(
            {"feature_enhancements", "milestone_core_development.2"},
            set(adjusted_operations),
        )

        for retained_id, operation in adjusted_operations.items():
            repo = SOURCE / operation["workspace"]
            sources = [
                builder.load_effective_tests(repo, source_id)
                for source_id in operation["ordered_source_ids"]
            ]
            effective, proof = builder.compose_logical_tests(sources)
            adjustments = operation["manual_test_adjustments"]
            promoted = {
                record["test_id"]
                for record in adjustments.get("promote_to_fail_to_pass", [])
            }
            excluded = {
                record["test_id"]
                for record in adjustments.get("exclude_from_grading", [])
            }

            if retained_id == "feature_enhancements":
                self.assertEqual(2, len(promoted))
                self.assertTrue(promoted <= set(proof["excluded_non_common_p2p"]))
            else:
                self.assertEqual(3, len(excluded))
                self.assertTrue(
                    excluded
                    <= {record["test_id"] for record in proof["unresolved_outer_evidence"]}
                )

            builder.apply_manual_test_adjustments(
                effective,
                proof,
                sources,
                adjustments,
            )
            adjusted_ids = promoted | excluded
            for diagnostic in builder.ACTIVE_LOGICAL_DIAGNOSTIC_LISTS:
                active_ids = {builder.test_id(record) for record in proof[diagnostic]}
                self.assertFalse(
                    adjusted_ids & active_ids,
                    f"{retained_id} retained adjusted IDs in {diagnostic}",
                )

            manual = proof["manual_test_adjustments"]
            published_adjustments = (
                manual["promote_to_fail_to_pass"] + manual["exclude_from_grading"]
            )
            self.assertEqual(adjusted_ids, {record["test_id"] for record in published_adjustments})
            self.assertTrue(
                all(record["superseded_active_diagnostics"] for record in published_adjustments)
            )


class SyntheticFilterTests(unittest.TestCase):
    def _write_contract(self, root: Path, stable: dict, filters: dict) -> Path:
        repo = root / "fixture_repo"
        test_dir = repo / "test_results" / "M1"
        test_dir.mkdir(parents=True)
        (test_dir / "M1_classification.json").write_text(
            json.dumps({"stable_classification": stable}), encoding="utf-8"
        )
        (test_dir / "M1_filter_list.json").write_text(json.dumps(filters), encoding="utf-8")
        return repo

    def test_normalized_filter_matching_does_not_strip_surviving_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = self._write_contract(
                Path(temporary),
                {"fail_to_pass": ["drop "], "none_to_pass": [], "pass_to_pass": ["keep "]},
                {
                    "invalid_fail_to_pass": ["drop"],
                    "invalid_none_to_pass": [],
                    "invalid_pass_to_pass": [],
                },
            )
            result = builder.load_effective_tests(repo, "M1")
            self.assertEqual([], result["effective"]["fail_to_pass"])
            self.assertEqual(["keep "], result["effective"]["pass_to_pass"])
            self.assertEqual(
                "normalized_whitespace",
                result["filter_match_provenance"]["fail_to_pass"][0]["match_mode"],
            )

    def test_f2p_n2p_conflict_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = self._write_contract(
                Path(temporary),
                {"fail_to_pass": ["same"], "none_to_pass": ["same"], "pass_to_pass": []},
                {
                    "invalid_fail_to_pass": [],
                    "invalid_none_to_pass": [],
                    "invalid_pass_to_pass": [],
                },
            )
            with self.assertRaises(builder.MergeError):
                builder.load_effective_tests(repo, "M1")


class ClassificationMergeTests(unittest.TestCase):
    def test_complete_categories_publish_only_logical_grading_roles(self) -> None:
        def source(mid: str, values: dict[str, list]) -> dict:
            section = {category: [] for category in builder.CLASSIFICATION_CATEGORIES}
            section.update(values)
            return {
                "milestone_id": mid,
                "source_stable_classification": section,
            }

        sources = [
            source(
                "M1",
                {
                    "pass_to_pass": ["regression", "later-functional"],
                    "fail_to_pass": ["first-fix"],
                    "fail_to_fail": ["still-broken"],
                    "none_to_pass": ["new-good", "filtered-new"],
                    "new_tests": [
                        {"test_id": "new-good", "end_outcome": "passed"},
                        {"test_id": "filtered-new", "end_outcome": "passed"},
                    ],
                },
            ),
            source(
                "M2",
                {
                    "pass_to_pass": ["regression"],
                    "fail_to_pass": ["later-functional"],
                    "pass_to_none": ["removed"],
                    "removed_tests": [{"test_id": "removed", "start_outcome": "passed"}],
                },
            ),
        ]
        effective = {
            "fail_to_pass": {"first-fix"},
            "none_to_pass": set(),
            "pass_to_pass": {"regression", "later-functional"},
        }
        section, summary = builder.merge_classification(sources, effective)

        self.assertEqual(set(builder.CLASSIFICATION_CATEGORIES), set(section))
        self.assertEqual(["first-fix"], section["fail_to_pass"])
        self.assertEqual([], section["none_to_pass"])
        self.assertEqual(["later-functional", "regression"], section["pass_to_pass"])
        self.assertEqual([], section["fail_to_fail"])
        self.assertEqual([], section["new_tests"])
        self.assertEqual([], section["removed_tests"])
        self.assertEqual(
            {category: len(section[category]) for category in builder.CLASSIFICATION_CATEGORIES},
            {category: summary[category] for category in builder.CLASSIFICATION_CATEGORIES},
        )

    def test_logical_composition_relabels_internal_regressions_and_drops_final_failures(self) -> None:
        def source(mid: str, transitions: dict[str, list[str]], effective: dict[str, list[str]]) -> dict:
            section = {category: [] for category in builder.CLASSIFICATION_CATEGORIES}
            section.update(transitions)
            return {
                "milestone_id": mid,
                "source_stable_classification": section,
                "effective": effective,
            }

        first = source(
            "A",
            {
                "pass_to_pass": ["regressed-then-fixed", "stable"],
                "fail_to_pass": ["fixed-then-regressed", "first-fix"],
            },
            {
                "fail_to_pass": ["fixed-then-regressed", "first-fix"],
                "none_to_pass": ["new-in-first"],
                "pass_to_pass": ["regressed-then-fixed", "stable"],
            },
        )
        second = source(
            "B",
            {
                "fail_to_pass": ["regressed-then-fixed", "later-fix"],
                "fail_to_fail": ["fixed-then-regressed"],
                "pass_to_pass": ["first-fix", "stable"],
            },
            {
                "fail_to_pass": ["regressed-then-fixed", "later-fix"],
                "none_to_pass": [],
                "pass_to_pass": ["first-fix", "stable"],
            },
        )
        effective, proof = builder.compose_logical_tests([first, second])
        self.assertEqual({"first-fix", "later-fix"}, effective["fail_to_pass"])
        self.assertEqual(set(), effective["none_to_pass"])
        self.assertEqual({"regressed-then-fixed", "stable"}, effective["pass_to_pass"])
        self.assertEqual(
            ["fixed-then-regressed"],
            [item["test_id"] for item in proof["excluded_f2p"]],
        )
        self.assertEqual(["new-in-first"], proof["excluded_n2p"])

    def test_manual_adjustments_require_outer_evidence_and_record_exclusions(self) -> None:
        def source(mid: str, transitions: dict[str, list[str]], effective: dict[str, list[str]]) -> dict:
            section = {category: [] for category in builder.CLASSIFICATION_CATEGORIES}
            section.update(transitions)
            return {
                "milestone_id": mid,
                "source_stable_classification": section,
                "effective": effective,
            }

        first = source(
            "A",
            {
                "fail_to_fail": ["promote"],
                "fail_to_pass": ["exclude"],
            },
            {
                "fail_to_pass": ["exclude"],
                "none_to_pass": [],
                "pass_to_pass": [],
            },
        )
        second = source(
            "B",
            {
                "pass_to_pass": ["promote"],
            },
            {
                "fail_to_pass": [],
                "none_to_pass": [],
                "pass_to_pass": ["promote"],
            },
        )
        effective, proof = builder.compose_logical_tests([first, second])
        builder.apply_manual_test_adjustments(
            effective,
            proof,
            [first, second],
            {
                "promote_to_fail_to_pass": [
                    {"test_id": "promote", "reason": "exact outer endpoint evidence"}
                ],
                "exclude_from_grading": [
                    {"test_id": "exclude", "reason": "absent from reviewed patch"}
                ],
            },
        )

        self.assertEqual({"promote"}, effective["fail_to_pass"])
        self.assertNotIn("exclude", effective["fail_to_pass"])
        manual = proof["manual_test_adjustments"]
        self.assertEqual(
            "PROMOTED_TO_FAIL_TO_PASS",
            manual["promote_to_fail_to_pass"][0]["status"],
        )
        self.assertEqual(
            "EXCLUDED_BY_PATCH_SEMANTICS",
            manual["exclude_from_grading"][0]["status"],
        )
        self.assertEqual(
            ["excluded_non_common_p2p"],
            [
                record["diagnostic"]
                for record in manual["promote_to_fail_to_pass"][0][
                    "superseded_active_diagnostics"
                ]
            ],
        )
        self.assertEqual(
            ["unresolved_outer_evidence"],
            [
                record["diagnostic"]
                for record in manual["exclude_from_grading"][0][
                    "superseded_active_diagnostics"
                ]
            ],
        )
        for diagnostic in builder.ACTIVE_LOGICAL_DIAGNOSTIC_LISTS:
            active_ids = {builder.test_id(record) for record in proof[diagnostic]}
            self.assertFalse({"promote", "exclude"} & active_ids)


class MeasuredEndpointProjectionTests(unittest.TestCase):
    def _stable_endpoint(self, outcome: str) -> dict:
        raw = {"pass": "passed", "fail": "failed", "none": "none"}[outcome]
        observations = [
            {"attempt": attempt, "status": "collected", "outcome": raw}
            for attempt in range(1, 4)
        ]
        return {
            "disposition": "stable",
            "evidence_state": "stable",
            "stable_outcome": outcome,
            "attempt_observations": observations,
            "observed_statuses": ["collected", "collected", "collected"],
            "reason": "",
        }

    def _flaky_endpoint(self) -> dict:
        observations = [
            {"attempt": 1, "status": "collected", "outcome": "passed"},
            {"attempt": 2, "status": "collected", "outcome": "failed"},
            {"attempt": 3, "status": "collected", "outcome": "passed"},
        ]
        return {
            "disposition": "flaky",
            "evidence_state": "flaky",
            "stable_outcome": None,
            "attempt_observations": observations,
            "observed_statuses": ["collected", "collected", "collected"],
            "reason": "inconsistent outcomes",
        }

    def _non_portable_endpoint(self) -> dict:
        observations = [
            {"attempt": 1, "status": "compile_error", "outcome": None},
            {"attempt": 2, "status": "collected", "outcome": "passed"},
            {"attempt": 3, "status": "collected", "outcome": "passed"},
        ]
        return {
            "disposition": "non_portable",
            "evidence_state": "uncollected",
            "stable_outcome": None,
            "attempt_observations": observations,
            "observed_statuses": ["compile_error", "collected", "collected"],
            "reason": "definition is not portable",
        }

    def _fixture(self) -> tuple[dict, dict, dict]:
        identifiers = ["excluded", "f2p", "flaky", "n2p", "nonportable", "p2p"]
        unresolved = [
            {
                "test_id": identifier,
                "entry_start": None,
                "exit_end": "pass",
                "reason": "requires endpoint evidence",
            }
            for identifier in identifiers
        ]
        pre_effective = {
            "fail_to_pass": {"base-f2p", *identifiers},
            "none_to_pass": set(),
            "pass_to_pass": {"base-p2p"},
        }
        manual = {
            "policy": "fixture-manual-review",
            "promote_to_fail_to_pass": [],
            "exclude_from_grading": [
                {
                    "test_id": "manual-old",
                    "superseded_active_diagnostics": [
                        {"diagnostic": "unresolved_outer_evidence"}
                    ],
                }
            ],
        }
        pre_logical = {
            "policy": "ordered_outer_f2p_and_common_p2p_only",
            "converted_f2p_to_p2p": ["old-converted"],
            "excluded_f2p": [
                {
                    "test_id": "old-excluded",
                    "entry_start": "fail",
                    "exit_end": "fail",
                    "reason": "old exclusion",
                }
            ],
            "unresolved_outer_evidence": unresolved,
            "excluded_n2p": ["old-n2p"],
            "excluded_non_common_p2p": ["old-non-common"],
            "manual_test_adjustments": manual,
        }
        specifications = {
            "excluded": ("fail", "fail", "excluded_other_outer_transition", None),
            "f2p": ("fail", "pass", "fail_to_pass", "fail_to_pass"),
            "n2p": ("none", "pass", "none_to_pass_not_graded", None),
            "p2p": ("pass", "pass", "pass_to_pass", "pass_to_pass"),
        }
        decisions: list[dict] = []
        for identifier in identifiers:
            if identifier == "flaky":
                entry = self._flaky_endpoint()
                exit_ = self._stable_endpoint("pass")
                disposition = "flaky"
                transition = None
                resolution = "unresolved_flaky_or_uncollected"
                active_role = "fail_to_pass"
            elif identifier == "nonportable":
                entry = self._non_portable_endpoint()
                exit_ = self._stable_endpoint("pass")
                disposition = "non_portable"
                transition = None
                resolution = "unresolved_flaky_or_uncollected"
                active_role = "fail_to_pass"
            else:
                start, end, resolution, active_role = specifications[identifier]
                entry = self._stable_endpoint(start)
                exit_ = self._stable_endpoint(end)
                disposition = "resolved"
                transition = f"{start}_to_{end}"
            candidate = next(
                record for record in unresolved if record["test_id"] == identifier
            )
            decisions.append(
                {
                    "test_id": identifier,
                    "candidate_sha256": builder.canonical_json_sha256(candidate),
                    "prior_active_roles": ["fail_to_pass"],
                    "resolution": resolution,
                    "active_role": active_role,
                    "producer_disposition": disposition,
                    "effective_disposition": disposition,
                    "adjudication_applied": False,
                    "outer_transition": transition,
                    "entry_start": entry,
                    "exit_end": exit_,
                }
            )
        counts = {
            "candidate_count": 6,
            "resolved": 4,
            "fail_to_pass": 1,
            "pass_to_pass": 1,
            "none_to_pass_not_graded": 1,
            "excluded_other": 1,
            "unresolved": 2,
            "flaky": 1,
            "non_portable": 1,
            "uncollected": 1,
        }
        endpoint_record = {
            "schema_version": 1,
            "status": "outer_endpoint_evidence_partial_unresolved",
            "candidate_set_sha256": builder.canonical_json_sha256(unresolved),
            "adjudication": {
                "status": "not_applied",
                "policy": "fixture uses raw evidence only",
                "candidate_ids": [],
            },
            "producer_summary": {
                "total": 6,
                "resolved": 4,
                "flaky": 1,
                "non_portable": 1,
                "unresolved": 0,
            },
            "counts": counts,
            "candidate_results": decisions,
        }
        return pre_effective, pre_logical, endpoint_record

    def test_projection_recomputes_roles_and_preserves_review_diagnostics(self) -> None:
        pre_effective, pre_logical, endpoint_record = self._fixture()
        projected, logical, counts, status = (
            builder.project_measured_outer_endpoint_contract(
                pre_effective,
                pre_logical,
                endpoint_record,
                label="fixture/M1",
            )
        )

        self.assertEqual(
            {"base-f2p", "f2p", "flaky", "nonportable"},
            projected["fail_to_pass"],
        )
        self.assertEqual(set(), projected["none_to_pass"])
        self.assertEqual({"base-p2p", "p2p"}, projected["pass_to_pass"])
        self.assertEqual(
            ["flaky", "nonportable"],
            [record["test_id"] for record in logical["unresolved_outer_evidence"]],
        )
        self.assertEqual(
            ["old-converted", "p2p"], logical["converted_f2p_to_p2p"]
        )
        self.assertEqual(
            ["excluded", "n2p", "old-excluded"],
            [record["test_id"] for record in logical["excluded_f2p"]],
        )
        self.assertEqual(
            pre_logical["manual_test_adjustments"],
            logical["manual_test_adjustments"],
        )
        self.assertEqual(endpoint_record, logical["outer_endpoint_evidence"])
        self.assertEqual(endpoint_record["counts"], counts)
        self.assertEqual("outer_endpoint_evidence_partial_unresolved", status)

    def test_projection_rejects_self_consistent_but_unproven_edits(self) -> None:
        pre_effective, pre_logical, endpoint_record = self._fixture()
        corruptions = []

        wrong_hash = copy.deepcopy(endpoint_record)
        wrong_hash["candidate_set_sha256"] = "0" * 64
        corruptions.append((pre_effective, wrong_hash))

        wrong_resolution = copy.deepcopy(endpoint_record)
        next(
            item for item in wrong_resolution["candidate_results"] if item["test_id"] == "f2p"
        )["resolution"] = "pass_to_pass"
        corruptions.append((pre_effective, wrong_resolution))

        two_attempts = copy.deepcopy(endpoint_record)
        next(
            item for item in two_attempts["candidate_results"] if item["test_id"] == "f2p"
        )["entry_start"]["attempt_observations"].pop()
        corruptions.append((pre_effective, two_attempts))

        overlapping_prestate = copy.deepcopy(pre_effective)
        overlapping_prestate["pass_to_pass"].add("f2p")
        corruptions.append((overlapping_prestate, endpoint_record))

        for effective, evidence in corruptions:
            with self.subTest(evidence=evidence):
                with self.assertRaises(builder.MergeError):
                    builder.project_measured_outer_endpoint_contract(
                        effective,
                        pre_logical,
                        evidence,
                        label="fixture/M1",
                    )


class PatchProjectionProofTests(unittest.TestCase):
    def _reviewed_materialization(self) -> dict:
        exclusion = {
            "review_id": "element-key-storage-panel-import",
            "reason": "unrelated snapshot drift",
            "path": "res/css/_components.pcss",
            "changed_line": '+@import "./components/views/settings/encryption/_KeyStoragePanel.pcss";',
            "raw_hunk_sha256": "a" * 64,
        }
        shared = {
            "reviewed_hunk_exclusions": [exclusion],
            "raw_semantic_patch_sha256": "b" * 64,
            "reviewed_end_tree": "reviewed-end-tree",
            "reviewed_semantic_projection_tree": "reviewed-projection-tree",
        }
        return {
            "combined_transition_validation": {
                "status": "exact_on_declared_semantic_paths_after_reviewed_hunk_exclusions",
                **shared,
            },
            "net_patch": dict(shared),
        }

    def test_exact_projection_rejects_hidden_review_exclusions(self) -> None:
        exact = {
            "combined_transition_validation": {
                "status": "exact_on_declared_semantic_paths",
            },
            "net_patch": {},
        }
        self.assertEqual(
            "exact_on_declared_semantic_paths",
            builder.validate_publishable_patch_projection(exact, label="fixture/M1"),
        )
        exact["net_patch"]["reviewed_hunk_exclusions"] = [{"review_id": "hidden"}]
        with self.assertRaises(builder.MergeError):
            builder.validate_publishable_patch_projection(exact, label="fixture/M1")

    def test_reviewed_projection_requires_matching_complete_proof_layers(self) -> None:
        reviewed = self._reviewed_materialization()
        self.assertEqual(
            "exact_on_declared_semantic_paths_after_reviewed_hunk_exclusions",
            builder.validate_publishable_patch_projection(reviewed, label="fixture/M1"),
        )

        mismatched = json.loads(json.dumps(reviewed))
        mismatched["net_patch"]["reviewed_end_tree"] = "different"
        with self.assertRaises(builder.MergeError):
            builder.validate_publishable_patch_projection(mismatched, label="fixture/M1")

        malformed = json.loads(json.dumps(reviewed))
        malformed["combined_transition_validation"]["reviewed_hunk_exclusions"][0][
            "raw_hunk_sha256"
        ] = "not-a-sha"
        malformed["net_patch"]["reviewed_hunk_exclusions"] = json.loads(
            json.dumps(
                malformed["combined_transition_validation"]["reviewed_hunk_exclusions"]
            )
        )
        with self.assertRaises(builder.MergeError):
            builder.validate_publishable_patch_projection(malformed, label="fixture/M1")


class TemporaryIntegrationTest(unittest.TestCase):
    def test_single_workspace_build_writes_patch_manifest_and_self_validates(self) -> None:
        operation = next(
            item
            for item in OPERATIONS
            if item["workspace"] == "BurntSushi_ripgrep_14.1.1_15.0.0"
        )
        with tempfile.TemporaryDirectory(prefix="swe-milestone-merge-test-") as temporary:
            output_root = Path(temporary) / "derived"
            output_root.mkdir()
            shutil.copytree(
                SOURCE / operation["workspace"],
                output_root / operation["workspace"],
            )
            builder.build_workspace(
                source_root=SOURCE,
                output_root=output_root,
                plan_root=ROOT,
                workspace=operation["workspace"],
                operations=[operation],
            )
            validation = builder.validate_built_dataset(
                source_root=SOURCE,
                output_root=output_root,
                plan_root=ROOT,
                operations=[operation],
                require_root_manifest=False,
            )
            self.assertEqual(1, validation["operations_checked"])
            patch_path = (
                output_root
                / operation["workspace"]
                / "patches"
                / operation["retained_id"]
                / "patch_manifest.json"
            )
            patch = builder.read_json(patch_path)
            self.assertEqual("pending", patch["materialization_status"])
            self.assertTrue(
                all(
                    {"base_commit", "start_ref", "end_ref", "commits"} <= set(segment)
                    for segment in patch["ordered_source_segments"]
                )
            )
            retained = operation["retained_id"]
            classification = builder.read_json(
                output_root
                / operation["workspace"]
                / "test_results"
                / retained
                / f"{retained}_classification.json"
            )
            self.assertEqual(
                set(builder.CLASSIFICATION_CATEGORIES),
                set(classification["classification"]),
            )
            self.assertEqual(
                classification["classification"],
                classification["stable_classification"],
            )
            for category in builder.CLASSIFICATION_CATEGORIES:
                self.assertEqual(
                    len(classification["classification"][category]),
                    classification["summary"][category],
                )
            merged_filter = builder.read_json(
                output_root
                / operation["workspace"]
                / "test_results"
                / retained
                / f"{retained}_filter_list.json"
            )
            self.assertEqual(
                {f"invalid_{role}" for role in builder.ROLES},
                set(merged_filter),
            )


if __name__ == "__main__":
    unittest.main()
