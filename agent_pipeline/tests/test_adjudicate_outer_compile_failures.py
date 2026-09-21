from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PIPELINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE_ROOT.parent))

from agent_pipeline import adjudicate_outer_compile_failures as adjudicator  # noqa: E402
import build_merged_dataset as builder  # noqa: E402


PROJECT_ROOT = PIPELINE_ROOT.parent
FORMAL_DATASET = PROJECT_ROOT / "SWE-Milestone-data-repartitioned"
FORMAL_LEGACY_RAW = (
    PROJECT_ROOT
    / "logs/endpoint_validation/reruns/endpoint-rerun-v1/operation-1/manifest.json"
)
FORMAL_PLAN = PROJECT_ROOT / "merge_plan.json"


WORKSPACE = "example_org_example_repo_v1_v2"
RETAINED = "M002"
ENTRY = "M001"
EXIT = "M002"
CANDIDATES = (
    "module::AlphaTest::testAlpha",
    "module::BetaTest::testBeta",
)
TEST_PATHS = {
    CANDIDATES[0]: "module/src/test/java/com/acme/AlphaTest.java",
    CANDIDATES[1]: "module/src/test/java/com/acme/BetaTest.java",
}
ORACLES = {
    CANDIDATES[0]: "oracle-alpha",
    CANDIDATES[1]: "oracle-beta",
}
TRANSITION_CATEGORIES = (
    "pass_to_pass",
    "pass_to_fail",
    "pass_to_skipped",
    "fail_to_pass",
    "fail_to_fail",
    "fail_to_skipped",
    "skipped_to_pass",
    "skipped_to_fail",
    "skipped_to_skipped",
    "none_to_pass",
    "none_to_fail",
    "none_to_skipped",
    "pass_to_none",
    "fail_to_none",
    "skipped_to_none",
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def file_record(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def canonical_hash(value: object) -> str:
    return adjudicator.canonical_json_sha256(value)


def endpoint_result(
    identifier: str, *, status: str, outcome: str | None, disposition: str
) -> dict[str, object]:
    return {
        "test_id": identifier,
        "disposition": disposition,
        "outcome": outcome,
        "reason": "synthetic exact three-run observation",
        "attempt_observations": [
            {"attempt": attempt, "status": status, "outcome": outcome}
            for attempt in range(1, 4)
        ],
        "oracle": {"status": "portable"},
    }


class CompileAdjudicationFixture:
    """Small but complete schema-v2 rerun/source/probe evidence graph."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dataset = root / "dataset"
        self.repo = self.dataset / WORKSPACE
        self.classification_path = (
            self.repo / "test_results" / RETAINED / f"{RETAINED}_classification.json"
        )
        self.provenance_path = self.repo / "merge_provenance" / f"{RETAINED}.json"
        self.source_dir = (
            self.classification_path.parent / "source_results" / EXIT
        )
        self.source_classification_path = self.source_dir / "classification.json"
        self.source_filter_path = self.source_dir / "filter.json"
        self.patch_dir = self.repo / "patches" / RETAINED
        self.gold_patch_path = self.patch_dir / "gold.patch"
        self.patch_manifest_path = self.patch_dir / "patch_manifest.json"
        self.probe_path = root / "probe.json"
        self.entry_report_paths = [
            root / f"entry-start.attempt-{attempt}.maven.log"
            for attempt in range(1, 4)
        ]
        self.exit_report_paths = [
            root / f"exit-end.attempt-{attempt}.maven.log"
            for attempt in range(1, 4)
        ]
        self.surefire_archive_paths = [
            root / f"exit-end.attempt-{attempt}.surefire_reports.tar.gz"
            for attempt in range(1, 4)
        ]
        self.exit_parsed_paths = [
            root / f"exit-end.attempt-{attempt}.parsed.json"
            for attempt in range(1, 4)
        ]
        # Compatibility aliases used by mutation tests; they name attempt 1.
        self.report_path = self.entry_report_paths[0]
        self.exit_report_path = self.exit_report_paths[0]
        self.surefire_archive_path = self.surefire_archive_paths[0]
        self.exit_parsed_path = self.exit_parsed_paths[0]
        self.raw_path = root / "raw-evidence.json"
        self.adjudication_path = root / "adjudication.json"
        self._build()

    @property
    def unresolved(self) -> list[dict[str, object]]:
        return [
            {
                "test_id": identifier,
                "entry_start": None,
                "exit_end": "pass",
                "reason": "synthetic unresolved outer endpoint",
            }
            for identifier in CANDIDATES
        ]

    def compile_log(
        self,
        *,
        alpha_path: str = TEST_PATHS[CANDIDATES[0]],
        alpha_message: str = "cannot find symbol",
        alpha_symbol: str = "[ERROR]   symbol:   class MissingAlpha",
    ) -> str:
        return "\n".join(
            [
                "[ERROR] COMPILATION ERROR : ",
                f"[ERROR] /testbed/{alpha_path}:[10,5] error: {alpha_message}",
                alpha_symbol,
                "[ERROR]   location: class com.acme.AlphaTest",
                (
                    "[ERROR] /testbed/"
                    f"{TEST_PATHS[CANDIDATES[1]]}:[20,7] error: "
                    "package com.acme.missing does not exist"
                ),
                "[INFO] 2 errors",
                "",
            ]
        )

    def _build(self) -> None:
        unresolved = self.unresolved
        logical = {
            "policy": "ordered_outer_f2p_and_common_p2p_only",
            "outer_transition": {
                "entry_milestone": ENTRY,
                "exit_milestone": EXIT,
            },
            "unresolved_outer_evidence": unresolved,
        }
        stable = {category: [] for category in TRANSITION_CATEGORIES}
        stable["fail_to_pass"] = list(CANDIDATES)
        classification = {
            "schema_version": 1,
            "workspace": WORKSPACE,
            "retained_id": RETAINED,
            "stable_classification": stable,
            "logical_composition": logical,
        }
        write_json(self.classification_path, classification)

        source_stable = {category: [] for category in TRANSITION_CATEGORIES}
        source_stable["fail_to_pass"] = list(CANDIDATES)
        write_json(
            self.source_classification_path,
            {
                "schema_version": 1,
                "stable_classification": source_stable,
            },
        )
        write_json(
            self.source_filter_path,
            {"schema_version": 1, "status": "synthetic_filter_pass"},
        )
        self.gold_patch_path.parent.mkdir(parents=True, exist_ok=True)
        self.gold_patch_path.write_text(
            """diff --git a/module/src/main/java/com/acme/MissingAlpha.java b/module/src/main/java/com/acme/MissingAlpha.java
new file mode 100644
--- /dev/null
+++ b/module/src/main/java/com/acme/MissingAlpha.java
@@ -0,0 +1,3 @@
+package com.acme;
+public class MissingAlpha {}
+
diff --git a/module/src/main/java/com/acme/missing/Added.java b/module/src/main/java/com/acme/missing/Added.java
new file mode 100644
--- /dev/null
+++ b/module/src/main/java/com/acme/missing/Added.java
@@ -0,0 +1,3 @@
+package com.acme.missing;
+public class Added {}
+
""",
            encoding="utf-8",
        )
        write_json(
            self.patch_manifest_path,
            {
                "schema_version": 1,
                "materialization_status": "materialized",
                "gold_patch_sha256": adjudicator.sha256_file(self.gold_patch_path),
            },
        )
        source_result = {
            "milestone_id": EXIT,
            "effective": {
                "fail_to_pass": list(CANDIDATES),
                "none_to_pass": [],
                "pass_to_pass": [],
            },
            "source_stable_classification": source_stable,
            "classification_artifact": {
                "file": self.source_classification_path.name,
                "sha256": adjudicator.sha256_file(self.source_classification_path),
            },
            "filter_artifacts": [
                {
                    "file": self.source_filter_path.name,
                    "sha256": adjudicator.sha256_file(self.source_filter_path),
                }
            ],
        }
        provenance = {
            "schema_version": 1,
            "workspace": WORKSPACE,
            "retained_id": RETAINED,
            "entry_id": ENTRY,
            "exit_id": EXIT,
            "patch_materialization": {
                "status": "materialized",
                "manifest": f"patches/{RETAINED}/patch_manifest.json",
                "manifest_sha256": adjudicator.sha256_file(self.patch_manifest_path),
                "gold_patch": f"patches/{RETAINED}/gold.patch",
                "gold_patch_sha256": adjudicator.sha256_file(self.gold_patch_path),
            },
            "test_contract": {
                "logical_composition": logical,
                "source_results": [
                    {
                        "milestone_id": ENTRY,
                        "effective": {
                            "fail_to_pass": [],
                            "none_to_pass": [],
                            "pass_to_pass": [],
                        },
                    },
                    source_result,
                ],
            },
        }
        write_json(self.provenance_path, provenance)

        probe = {
            "schema_version": 1,
            "workspace": WORKSPACE,
            "retained_id": RETAINED,
            "entry_id": ENTRY,
            "exit_id": EXIT,
            "input_fingerprints": {
                "candidate_set_sha256": canonical_hash(unresolved),
                "merge_provenance": {
                    "path": str(self.provenance_path.resolve()),
                    "sha256": adjudicator.sha256_file(self.provenance_path),
                },
            },
            "sources": [
                {"position": "A", "milestone_id": ENTRY},
                {"position": "B", "milestone_id": EXIT},
            ],
            "candidates": [
                {
                    "test_id": record["test_id"],
                    "candidate_sha256": canonical_hash(record),
                    "unresolved_outer_evidence": record,
                    "definitions": {
                        "b_start": {
                            "canonical": {
                                "status": "present",
                                "matches": [
                                    {"path": TEST_PATHS[str(record["test_id"])]}
                                ],
                            }
                        }
                    },
                }
                for record in unresolved
            ],
        }
        write_json(self.probe_path, probe)

        for path in self.entry_report_paths:
            path.write_text(self.compile_log(), encoding="utf-8")
        for path in self.exit_report_paths:
            path.write_text("[INFO] BUILD SUCCESS\n", encoding="utf-8")
        xml_payload = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="synthetic" tests="2" failures="0" errors="0" skipped="0" time="0.02">
  <testcase classname="AlphaTest" name="testAlpha" time="0.01" />
  <testcase classname="BetaTest" name="testBeta" time="0.01" />
</testsuite>
""".encode("utf-8")
        with tarfile.open(self.surefire_archive_path, "w:gz") as archive:
            member = tarfile.TarInfo("surefire_reports/module/TEST-synthetic.xml")
            member.size = len(xml_payload)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(xml_payload))
        for path in self.surefire_archive_paths[1:]:
            path.write_bytes(self.surefire_archive_path.read_bytes())
        parsed_exit = adjudicator.parse_maven_report(
            self.exit_report_path, surefire_path=self.surefire_archive_path
        )
        self.assert_exact_parsed_ids = sorted(
            str(item["nodeid"]) for item in parsed_exit["tests"]
        )
        for path in self.exit_parsed_paths:
            write_json(path, parsed_exit)
        patch_records: dict[str, dict[str, object]] = {}
        for identifier in CANDIDATES:
            patch_path = self.root / f"{ORACLES[identifier]}.patch"
            patch_path.write_text(
                (
                    f"diff --git a/{TEST_PATHS[identifier]} "
                    f"b/{TEST_PATHS[identifier]}\n"
                ),
                encoding="utf-8",
            )
            patch_records[identifier] = file_record(patch_path)

        entry_state = {
            "milestone_id": ENTRY,
            "state": "start",
            "requested_ref": "milestone-M001-start",
            "runnable_commit": "a" * 40,
            "canonical_commit": "b" * 40,
        }
        exit_state = {
            "milestone_id": EXIT,
            "state": "end",
            "requested_ref": "milestone-M002-end",
            "runnable_commit": "c" * 40,
            "canonical_commit": "d" * 40,
        }
        entry_results = {
            identifier: endpoint_result(
                identifier,
                status="compile_error",
                outcome=None,
                disposition="unresolved",
            )
            for identifier in CANDIDATES
        }
        exit_results = {
            identifier: endpoint_result(
                identifier,
                status="collected",
                outcome="passed",
                disposition="stable",
            )
            for identifier in CANDIDATES
        }
        oracle_application = {
            oracle_id: "applied" for oracle_id in sorted(ORACLES.values())
        }
        entry_attempts = []
        exit_attempts = []
        for attempt in range(1, 4):
            entry_attempts.append(
                {
                    "attempt": attempt,
                    "setup_returncode": 0,
                    "actual_head": entry_state["runnable_commit"],
                    "compile_error_detected": True,
                    "timed_out": False,
                    "apptainer_returncode": 0,
                    "oracle_application": oracle_application,
                    "candidates": {
                        identifier: {
                            "status": "compile_error",
                            "outcome": None,
                            "expected_executions": 1,
                        }
                        for identifier in CANDIDATES
                    },
                    "executions": [
                        {
                            "name": "synthetic_maven_test_compile",
                            "framework": "maven",
                            "candidate_ids": list(CANDIDATES),
                            "returncode": 1,
                            "raw_report": file_record(
                                self.entry_report_paths[attempt - 1]
                            ),
                        }
                    ],
                }
            )
            exit_attempts.append(
                {
                    "attempt": attempt,
                    "setup_returncode": 0,
                    "actual_head": exit_state["runnable_commit"],
                    "compile_error_detected": False,
                    "timed_out": False,
                    "apptainer_returncode": 0,
                    "candidates": {
                        identifier: {
                            "status": "collected",
                            "outcome": "passed",
                            "expected_executions": 1,
                        }
                        for identifier in CANDIDATES
                    },
                    "executions": [
                        {
                            "name": "synthetic_maven",
                            "framework": "maven",
                            "candidate_ids": list(CANDIDATES),
                            "returncode": 0,
                            "raw_report": file_record(
                                self.exit_report_paths[attempt - 1]
                            ),
                            "surefire_archive": file_record(
                                self.surefire_archive_paths[attempt - 1]
                            ),
                            "parsed_report": file_record(
                                self.exit_parsed_paths[attempt - 1]
                            ),
                            "exact_candidate_outcomes": {
                                identifier: ["passed"] for identifier in CANDIDATES
                            },
                        }
                    ],
                }
            )

        raw = {
            "schema_version": 2,
            "artifact_type": adjudicator.RAW_ARTIFACT_TYPE,
            "status": "completed_with_unresolved_evidence",
            "mode": "both_outer_endpoints",
            "workspace": WORKSPACE,
            "retained_id": RETAINED,
            "entry_id": ENTRY,
            "exit_id": EXIT,
            "attempts_required": 3,
            "strategy": "synthetic_maven_test_compile",
            "inputs": {
                "dataset": str(self.dataset.resolve()),
                "classification": file_record(self.classification_path),
                "merge_provenance": file_record(self.provenance_path),
                "probe": file_record(self.probe_path),
                "probe_canonical_json_sha256": canonical_hash(probe),
                "implementation": {
                    **{
                        name: file_record(path)
                        for name, path in adjudicator.IMPLEMENTATION_PATHS.items()
                    },
                    adjudicator.REPORT_PARSER_DIRECT_DEPENDENCY_KEY: {
                        name: file_record(path)
                        for name, path in adjudicator.REPORT_PARSER_DIRECT_DEPENDENCY_PATHS.items()
                    },
                },
            },
            "canonical_outer_states": {
                "entry_start": entry_state,
                "exit_end": exit_state,
            },
            "candidate_input": [
                {
                    "test_id": record["test_id"],
                    "unresolved_outer_evidence": record,
                    "probe_candidate_sha256": canonical_hash(record),
                    "source_f2p_owners": [EXIT],
                    "oracle": {
                        "status": "portable",
                        "fallbacks": {
                            "entry_start": {
                                "status": "portable",
                                "oracle_id": ORACLES[str(record["test_id"])],
                                "patch_kind": (
                                    "endpoint_tree_to_owner_canonical_start"
                                ),
                                "observed_paths": [
                                    TEST_PATHS[str(record["test_id"])]
                                ],
                                "patch": patch_records[str(record["test_id"])],
                            }
                        },
                    },
                }
                for record in unresolved
            ],
            "oracle_extractions": [
                {
                    "oracle_id": ORACLES[identifier],
                    "target_endpoint": "entry_start",
                    "status": "portable",
                    "source_milestone": EXIT,
                    "patch_kind": "endpoint_tree_to_owner_canonical_start",
                    "allowed_paths": [TEST_PATHS[identifier]],
                    "observed_paths": [TEST_PATHS[identifier]],
                    "candidate_ids": [identifier],
                    "patch": patch_records[identifier],
                }
                for identifier in CANDIDATES
            ],
            "endpoints": {
                "entry_start": {
                    "candidate_ids": list(CANDIDATES),
                    "state": entry_state,
                    "attempts": entry_attempts,
                    "candidate_results": [
                        entry_results[identifier] for identifier in CANDIDATES
                    ],
                },
                "exit_end": {
                    "candidate_ids": list(CANDIDATES),
                    "state": exit_state,
                    "attempts": exit_attempts,
                    "candidate_results": [
                        exit_results[identifier] for identifier in CANDIDATES
                    ],
                },
            },
            "candidates": [
                {
                    "test_id": identifier,
                    "entry_start": entry_results[identifier],
                    "exit_end": exit_results[identifier],
                    "observed_transition": None,
                    "disposition": "unresolved",
                }
                for identifier in CANDIDATES
            ],
            "summary": {
                "total": len(CANDIDATES),
                "resolved": 0,
                "flaky": 0,
                "non_portable": 0,
                "unresolved": len(CANDIDATES),
            },
        }
        write_json(self.raw_path, raw)

    def raw(self) -> dict[str, object]:
        return read_json(self.raw_path)

    def write_raw(self, raw: dict[str, object]) -> None:
        write_json(self.raw_path, raw)

    def refresh_report_records(self, raw: dict[str, object]) -> None:
        payload = self.report_path.read_bytes()
        for path in self.entry_report_paths[1:]:
            path.write_bytes(payload)
        endpoints = raw["endpoints"]
        assert isinstance(endpoints, dict)
        entry = endpoints["entry_start"]
        assert isinstance(entry, dict)
        attempts = entry["attempts"]
        assert isinstance(attempts, list)
        for attempt_index, attempt in enumerate(attempts):
            assert isinstance(attempt, dict)
            executions = attempt["executions"]
            assert isinstance(executions, list)
            execution = executions[0]
            assert isinstance(execution, dict)
            execution["raw_report"] = file_record(
                self.entry_report_paths[attempt_index]
            )

    def approved(self) -> dict[str, object]:
        return adjudicator.build_adjudication(
            dataset=self.dataset,
            raw_evidence_path=self.raw_path,
            approve=True,
            reviewer="human-reviewer",
            approval_reason="All narrow compile-failure invariants reviewed.",
        )

    def write_approved(self) -> dict[str, object]:
        artifact = self.approved()
        write_json(self.adjudication_path, artifact)
        return artifact


class CompileFailureAdjudicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fixture(self, name: str = "case") -> CompileAdjudicationFixture:
        return CompileAdjudicationFixture(self.root / name)

    def test_default_is_review_required_and_records_complete_finding(self) -> None:
        fixture = self.fixture()
        artifact = adjudicator.build_adjudication(
            dataset=fixture.dataset,
            raw_evidence_path=fixture.raw_path,
        )

        self.assertEqual("review_required", artifact["status"])
        self.assertEqual("review_required", artifact["review"]["status"])
        self.assertIsNone(artifact["review"]["decision"])
        findings = artifact["technical_findings"]
        self.assertEqual(2, findings["candidate_count"])
        self.assertEqual(sorted(TEST_PATHS.values()), findings["allowed_injected_test_files"])
        self.assertEqual(2, findings["compile_error_count_per_attempt"])
        self.assertEqual(3, len(findings["entry_start_attempts"]))
        self.assertEqual(
            list(CANDIDATES),
            [item["test_id"] for item in artifact["adjudicated_candidates"]],
        )
        self.assertEqual(
            {
                *adjudicator.IMPLEMENTATION_PATHS,
                adjudicator.REPORT_PARSER_DIRECT_DEPENDENCY_KEY,
            },
            set(artifact["inputs"]["implementation"]),
        )
        self.assertEqual(
            set(adjudicator.REPORT_PARSER_DIRECT_DEPENDENCY_PATHS),
            set(
                artifact["inputs"]["implementation"][
                    adjudicator.REPORT_PARSER_DIRECT_DEPENDENCY_KEY
                ]
            ),
        )

    def test_approval_requires_explicit_reviewer_and_reason(self) -> None:
        fixture = self.fixture()
        for reviewer, reason in ((None, "reason"), ("reviewer", None), (" ", "reason")):
            with self.subTest(reviewer=reviewer, reason=reason):
                with self.assertRaisesRegex(
                    adjudicator.AdjudicationError, "requires non-empty reviewer"
                ):
                    adjudicator.build_adjudication(
                        dataset=fixture.dataset,
                        raw_evidence_path=fixture.raw_path,
                        approve=True,
                        reviewer=reviewer,
                        approval_reason=reason,
                    )

        artifact = fixture.approved()
        self.assertEqual("approved", artifact["status"])
        self.assertEqual(
            "adjudicate_entry_compile_failure_as_failed",
            artifact["review"]["decision"],
        )

    def test_dry_run_prints_artifact_without_creating_output(self) -> None:
        fixture = self.fixture()
        output = fixture.root / "must-not-exist.json"
        before = {path: path.read_bytes() for path in fixture.root.rglob("*") if path.is_file()}
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            returncode = adjudicator.main(
                [
                    "--dataset",
                    str(fixture.dataset),
                    "--raw-evidence",
                    str(fixture.raw_path),
                    "--dry-run",
                ]
            )

        self.assertEqual(0, returncode)
        self.assertEqual("review_required", json.loads(stdout.getvalue())["status"])
        self.assertFalse(output.exists())
        self.assertEqual(
            before,
            {path: path.read_bytes() for path in fixture.root.rglob("*") if path.is_file()},
        )

    def test_approved_artifact_validates_only_when_exactly_recomputable(self) -> None:
        fixture = self.fixture()
        artifact = fixture.write_approved()
        self.assertEqual(
            artifact,
            adjudicator.validate_adjudication_artifact(
                path=fixture.adjudication_path,
                dataset=fixture.dataset,
                raw_evidence_path=fixture.raw_path,
            ),
        )

        artifact["technical_findings"]["entry_start_attempts"][0]["compile_errors"][
            "error_paths"
        ][0] = "module/src/test/java/com/acme/ForgedTest.java"
        write_json(fixture.adjudication_path, artifact)
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "differs from recomputed"
        ):
            adjudicator.validate_adjudication_artifact(
                path=fixture.adjudication_path,
                dataset=fixture.dataset,
                raw_evidence_path=fixture.raw_path,
            )

    def test_rejects_semantic_maven_error_path_tamper(self) -> None:
        fixture = self.fixture()
        fixture.write_approved()
        fixture.report_path.write_text(
            fixture.compile_log(
                alpha_path="other/src/test/java/com/acme/UnrelatedTest.java"
            ),
            encoding="utf-8",
        )
        raw = fixture.raw()
        fixture.refresh_report_records(raw)
        fixture.write_raw(raw)

        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "not confined to all injected test files"
        ):
            adjudicator.validate_adjudication_artifact(
                path=fixture.adjudication_path,
                dataset=fixture.dataset,
                raw_evidence_path=fixture.raw_path,
            )

    def test_rejects_stale_maven_report_hash(self) -> None:
        fixture = self.fixture()
        fixture.write_approved()
        fixture.report_path.write_text(
            fixture.report_path.read_text(encoding="utf-8") + "tamper\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(adjudicator.AdjudicationError, "size or SHA-256 is stale"):
            adjudicator.validate_adjudication_artifact(
                path=fixture.adjudication_path,
                dataset=fixture.dataset,
                raw_evidence_path=fixture.raw_path,
            )

    def test_rejects_exit_reparse_and_implementation_tamper(self) -> None:
        outcome_fixture = self.fixture("exit-outcome")
        outcome_fixture.write_approved()
        raw = outcome_fixture.raw()
        raw["endpoints"]["exit_end"]["attempts"][0]["executions"][0][
            "exact_candidate_outcomes"
        ][CANDIDATES[0]] = ["failed"]
        outcome_fixture.write_raw(raw)
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "one-pass-per-candidate"
        ):
            adjudicator.validate_adjudication_artifact(
                path=outcome_fixture.adjudication_path,
                dataset=outcome_fixture.dataset,
                raw_evidence_path=outcome_fixture.raw_path,
            )

        implementation_fixture = self.fixture("implementation")
        implementation_fixture.write_approved()
        raw = implementation_fixture.raw()
        raw["inputs"]["implementation"]["official_report_parser"]["sha256"] = (
            "0" * 64
        )
        implementation_fixture.write_raw(raw)
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "size or SHA-256 is stale"
        ):
            adjudicator.validate_adjudication_artifact(
                path=implementation_fixture.adjudication_path,
                dataset=implementation_fixture.dataset,
                raw_evidence_path=implementation_fixture.raw_path,
            )

    def test_rejects_oracle_and_source_ownership_tamper(self) -> None:
        oracle_fixture = self.fixture("oracle")
        oracle_fixture.write_approved()
        raw = oracle_fixture.raw()
        raw["candidate_input"][0]["oracle"]["fallbacks"]["entry_start"][
            "oracle_id"
        ] = "forged-oracle"
        oracle_fixture.write_raw(raw)
        with self.assertRaisesRegex(adjudicator.AdjudicationError, "extraction set is not exact"):
            adjudicator.validate_adjudication_artifact(
                path=oracle_fixture.adjudication_path,
                dataset=oracle_fixture.dataset,
                raw_evidence_path=oracle_fixture.raw_path,
            )

        owner_fixture = self.fixture("owner")
        owner_fixture.write_approved()
        raw = owner_fixture.raw()
        raw["candidate_input"][0]["source_f2p_owners"] = [ENTRY, EXIT]
        owner_fixture.write_raw(raw)
        with self.assertRaisesRegex(adjudicator.AdjudicationError, "exact exit-source F2P ownership"):
            adjudicator.validate_adjudication_artifact(
                path=owner_fixture.adjudication_path,
                dataset=owner_fixture.dataset,
                raw_evidence_path=owner_fixture.raw_path,
            )

    def test_rejects_nested_parser_dependency_missing_extra_and_tamper(self) -> None:
        dependency_key = adjudicator.REPORT_PARSER_DIRECT_DEPENDENCY_KEY
        dependency_names = list(adjudicator.REPORT_PARSER_DIRECT_DEPENDENCY_PATHS)

        missing = self.fixture("nested-missing")
        missing.write_approved()
        raw = missing.raw()
        raw["inputs"]["implementation"][dependency_key].pop(dependency_names[0])
        missing.write_raw(raw)
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "direct-dependency provenance is incomplete"
        ):
            adjudicator.validate_adjudication_artifact(
                path=missing.adjudication_path,
                dataset=missing.dataset,
                raw_evidence_path=missing.raw_path,
            )

        extra = self.fixture("nested-extra")
        extra.write_approved()
        raw = extra.raw()
        raw["inputs"]["implementation"][dependency_key]["undeclared_parser"] = (
            copy.deepcopy(
                raw["inputs"]["implementation"][dependency_key][dependency_names[0]]
            )
        )
        extra.write_raw(raw)
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "direct-dependency provenance is incomplete"
        ):
            adjudicator.validate_adjudication_artifact(
                path=extra.adjudication_path,
                dataset=extra.dataset,
                raw_evidence_path=extra.raw_path,
            )

        tampered = self.fixture("nested-tamper")
        tampered.write_approved()
        raw = tampered.raw()
        raw["inputs"]["implementation"][dependency_key][dependency_names[0]][
            "sha256"
        ] = "0" * 64
        tampered.write_raw(raw)
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "size or SHA-256 is stale"
        ):
            adjudicator.validate_adjudication_artifact(
                path=tampered.adjudication_path,
                dataset=tampered.dataset,
                raw_evidence_path=tampered.raw_path,
            )

    def test_rejects_review_tamper_and_unapproved_artifacts(self) -> None:
        fixture = self.fixture()
        artifact = fixture.write_approved()
        artifact["review"]["decision"] = "forged_auto_failure_decision"
        write_json(fixture.adjudication_path, artifact)
        with self.assertRaisesRegex(adjudicator.AdjudicationError, "differs from recomputed"):
            adjudicator.validate_adjudication_artifact(
                path=fixture.adjudication_path,
                dataset=fixture.dataset,
                raw_evidence_path=fixture.raw_path,
            )

        review_fixture = self.fixture("review-required")
        review_required = adjudicator.build_adjudication(
            dataset=review_fixture.dataset,
            raw_evidence_path=review_fixture.raw_path,
        )
        write_json(review_fixture.adjudication_path, review_required)
        with self.assertRaisesRegex(adjudicator.AdjudicationError, "is not approved"):
            adjudicator.validate_adjudication_artifact(
                path=review_fixture.adjudication_path,
                dataset=review_fixture.dataset,
                raw_evidence_path=review_fixture.raw_path,
            )

    def test_ordinary_compile_and_infrastructure_failures_are_not_adjudicable(self) -> None:
        compile_fixture = self.fixture("ordinary-compile")
        compile_fixture.report_path.write_text(
            compile_fixture.compile_log(
                alpha_message="incompatible types: int cannot be converted to String",
                alpha_symbol="[ERROR]   symbol:   class MissingAlpha",
            ),
            encoding="utf-8",
        )
        raw = compile_fixture.raw()
        compile_fixture.refresh_report_records(raw)
        compile_fixture.write_raw(raw)
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "not an absent product symbol"
        ):
            adjudicator.build_adjudication(
                dataset=compile_fixture.dataset,
                raw_evidence_path=compile_fixture.raw_path,
                approve=True,
                reviewer="human-reviewer",
                approval_reason="must still fail closed",
            )

        infra_fixture = self.fixture("infrastructure")
        raw = infra_fixture.raw()
        raw["endpoints"]["entry_start"]["attempts"][0]["setup_returncode"] = 1
        infra_fixture.write_raw(raw)
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "successful setup plus compile failure"
        ):
            adjudicator.build_adjudication(
                dataset=infra_fixture.dataset,
                raw_evidence_path=infra_fixture.raw_path,
                approve=True,
                reviewer="human-reviewer",
                approval_reason="must still fail closed",
            )

    def test_rejects_reused_maven_report_and_surefire_archive_evidence(self) -> None:
        entry_fixture = self.fixture("reused-entry-report")
        raw = entry_fixture.raw()
        raw["endpoints"]["entry_start"]["attempts"][1]["executions"][0][
            "raw_report"
        ] = copy.deepcopy(
            raw["endpoints"]["entry_start"]["attempts"][0]["executions"][0][
                "raw_report"
            ]
        )
        entry_fixture.write_raw(raw)
        with self.assertRaisesRegex(adjudicator.AdjudicationError, "reuses physical evidence"):
            entry_fixture.approved()

        archive_fixture = self.fixture("reused-surefire-archive")
        raw = archive_fixture.raw()
        raw["endpoints"]["exit_end"]["attempts"][1]["executions"][0][
            "surefire_archive"
        ] = copy.deepcopy(
            raw["endpoints"]["exit_end"]["attempts"][0]["executions"][0][
                "surefire_archive"
            ]
        )
        archive_fixture.write_raw(raw)
        with self.assertRaisesRegex(adjudicator.AdjudicationError, "reuses physical evidence"):
            archive_fixture.approved()

    def test_maven_safety_scan_and_parser_share_private_hash_pinned_copies(self) -> None:
        fixture = self.fixture("maven-toctou")
        original_parser = adjudicator.parse_maven_report
        original_report = fixture.exit_report_path.resolve()
        original_archive = fixture.surefire_archive_path.resolve()
        parser_paths: list[tuple[Path, Path]] = []
        replaced = False

        def replacing_parser(report_path: Path, *, surefire_path: Path) -> dict:
            nonlocal replaced
            parser_paths.append((report_path.resolve(), surefire_path.resolve()))
            if not replaced:
                original_report.write_text("forged after authentication\n", encoding="utf-8")
                original_archive.write_bytes(b"not a tar archive")
                replaced = True
            return original_parser(report_path, surefire_path=surefire_path)

        with mock.patch.object(
            adjudicator, "parse_maven_report", side_effect=replacing_parser
        ):
            artifact = fixture.approved()
        self.assertEqual("approved", artifact["status"])
        self.assertTrue(parser_paths)
        self.assertTrue(
            all(
                report != original_report and archive != original_archive
                for report, archive in parser_paths
            )
        )


class FormalLegacyV1AdjudicationTests(unittest.TestCase):
    """Lock the sole schema-v1 exception to the exact formal Dubbo op1 evidence."""

    def setUp(self) -> None:
        if (
            not FORMAL_LEGACY_RAW.is_file()
            or not FORMAL_DATASET.is_dir()
            or not FORMAL_PLAN.is_file()
        ):
            self.fail("required formal Dubbo op1 adjudication fixture is not available")
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        plan = builder.read_json(FORMAL_PLAN)
        operations = builder.validate_plan(plan, PROJECT_ROOT)
        operation = next(
            value
            for value in operations
            if value["workspace"] == adjudicator.LEGACY_V1_IDENTITY["workspace"]
            and value["retained_id"]
            == adjudicator.LEGACY_V1_IDENTITY["retained_id"]
        )
        workspace = operation["workspace"]
        retained = operation["retained_id"]
        source_results, pre_effective, pre_logical = (
            builder.expected_logical_test_prestate(
                PROJECT_ROOT / plan["source_dataset"] / workspace,
                operation,
            )
        )
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
        classification_bytes, provenance_bytes = (
            builder.reconstruct_prepublication_test_inputs(
                source_results=source_results,
                pre_effective=pre_effective,
                pre_logical=pre_logical,
                published_classification=builder.read_json(classification_path),
                published_provenance=builder.read_json(provenance_path),
            )
        )
        self.prepublication_input_snapshots = (
            adjudicator.PrepublicationInputSnapshots(
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
        )

    def tearDown(self) -> None:
        temporary = getattr(self, "temporary", None)
        if temporary is not None:
            temporary.cleanup()

    def test_exact_manifest_records_reviewed_A_and_parser_corrected_B(self) -> None:
        artifact = adjudicator.build_adjudication(
            dataset=FORMAL_DATASET,
            raw_evidence_path=FORMAL_LEGACY_RAW,
            prepublication_input_snapshots=self.prepublication_input_snapshots,
        )

        self.assertEqual("review_required", artifact["status"])
        findings = artifact["technical_findings"]
        semantics = findings["evidence_semantics"]
        self.assertEqual(1, findings["raw_evidence_schema_version"])
        self.assertEqual(18, findings["candidate_count"])
        self.assertEqual(6, len(findings["allowed_injected_test_files"]))
        self.assertEqual(34, findings["compile_error_count_per_attempt"])
        self.assertEqual(
            "reviewed_inference_from_compile_failure", semantics["entry_start"]
        )
        self.assertEqual(
            "parser_corrected_direct_observation", semantics["exit_end"]
        )
        self.assertFalse(semantics["raw_runner_implementation_pinned"])
        self.assertTrue(semantics["parser_correction_applied"])
        self.assertIn("not pinned", semantics["legacy_raw_runner_caveat"])
        self.assertEqual("zero_selected", findings["exit_end"]["raw_producer_status"])
        self.assertTrue(
            all(
                execution["parse_mode"] == "surefire_xml"
                and execution["raw_parser_mode"] == "console_log"
                for attempt in findings["exit_end"]["attempts"]
                for execution in attempt["executions"]
            )
        )
        self.assertEqual(
            ["class:CreateObserverAdapter"],
            [item["symbol"] for item in findings["compile_symbol_caveats"]],
        )

    def test_exact_manifest_can_be_approved_and_recomputed(self) -> None:
        path = self.root / "approved.json"
        artifact = adjudicator.build_adjudication(
            dataset=FORMAL_DATASET,
            raw_evidence_path=FORMAL_LEGACY_RAW,
            approve=True,
            reviewer="formal-regression-reviewer",
            approval_reason=(
                "A compile inference and B parser-corrected observation were reviewed."
            ),
            prepublication_input_snapshots=self.prepublication_input_snapshots,
        )
        write_json(path, artifact)

        self.assertEqual(
            artifact,
            adjudicator.validate_adjudication_artifact(
                path=path,
                dataset=FORMAL_DATASET,
                raw_evidence_path=FORMAL_LEGACY_RAW,
                prepublication_input_snapshots=self.prepublication_input_snapshots,
            ),
        )
        self.assertEqual(
            artifact["technical_findings"]["evidence_semantics"][
                "legacy_raw_runner_caveat"
            ],
            artifact["review"]["acknowledged_legacy_raw_runner_caveat"],
        )

    def test_schema_v1_exception_requires_exact_suffix_hash_and_identity(self) -> None:
        exact_copy = (
            self.root
            / "endpoint-rerun-v1/operation-1/manifest.json"
        )
        exact_copy.parent.mkdir(parents=True)
        exact_copy.write_bytes(FORMAL_LEGACY_RAW.read_bytes())
        copied = adjudicator.build_adjudication(
            dataset=FORMAL_DATASET,
            raw_evidence_path=exact_copy,
            prepublication_input_snapshots=self.prepublication_input_snapshots,
        )
        self.assertEqual("review_required", copied["status"])

        wrong_suffix = self.root / "wrong-location/manifest.json"
        wrong_suffix.parent.mkdir(parents=True)
        wrong_suffix.write_bytes(FORMAL_LEGACY_RAW.read_bytes())
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "exact pinned Dubbo op1 manifest"
        ):
            adjudicator.build_adjudication(
                dataset=FORMAL_DATASET,
                raw_evidence_path=wrong_suffix,
            )

        tampered = read_json(exact_copy)
        tampered["strategy"] = "forged"
        write_json(exact_copy, tampered)
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "exact pinned Dubbo op1 manifest"
        ):
            adjudicator.build_adjudication(
                dataset=FORMAL_DATASET,
                raw_evidence_path=exact_copy,
            )


if __name__ == "__main__":
    unittest.main()
