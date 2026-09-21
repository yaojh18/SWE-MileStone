from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


PIPELINE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(PIPELINE_ROOT.parent))

from agent_pipeline.validate_partition import (  # noqa: E402
    expand_partition_tests,
    validate_partition,
)
from agent_pipeline.validate_test_quality import (  # noqa: E402
    canonical_expanded_decisions_bytes,
    expand_test_quality_records,
    main as validate_quality_main,
    parse_decisions_jsonl,
    validate_test_quality,
)
from agent_pipeline.validation import load_task_view  # noqa: E402


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class PartitionValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.view = load_json("partition_view.json")
        self.output = load_json("partition_output_valid.json")

    def test_valid_partition(self) -> None:
        report = validate_partition(self.view, self.output)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.metrics["assigned_change_units"], 3)
        self.assertEqual(report.metrics["observed_test_ids"], 3)

    def compact_output(self):
        output = copy.deepcopy(self.output)
        output["subtasks"][0]["tests"] = {
            "f2p": {"mode": "explicit", "include_ids": ["tests::alpha"], "exclude_ids": []},
            "n2p": {"mode": "all_original", "include_ids": [], "exclude_ids": ["tests::obsolete"]},
            "p2p": {"mode": "all_original", "include_ids": [], "exclude_ids": []},
        }
        output["subtasks"][1]["tests"] = {
            "f2p": {"mode": "explicit", "include_ids": [], "exclude_ids": []},
            "n2p": {"mode": "explicit", "include_ids": ["tests::new_beta"], "exclude_ids": []},
            "p2p": {
                "mode": "all_original",
                "include_ids": ["tests::alpha"],
                "exclude_ids": [],
            },
        }
        return output

    def test_compact_selectors_expand_deterministically(self) -> None:
        output = self.compact_output()
        report = validate_partition(self.view, output)
        self.assertTrue(report.valid, report.errors)
        expanded = expand_partition_tests(self.view, output)
        self.assertEqual(expanded["M100.sub-01"]["p2p"], ["tests::regression"])
        self.assertEqual(
            expanded["M100.sub-02"]["p2p"],
            ["tests::regression", "tests::alpha"],
        )

    def test_all_original_remove_requires_exclusion_everywhere(self) -> None:
        output = self.compact_output()
        output["subtasks"][0]["tests"]["n2p"]["exclude_ids"] = []
        report = validate_partition(self.view, output)
        self.assertFalse(report.valid)
        self.assertTrue(any("removed test 'tests::obsolete' is still assigned" in error for error in report.errors))

    def test_cross_role_assignment_requires_reclassify_adjustment(self) -> None:
        output = self.compact_output()
        output["test_adjustments"] = output["test_adjustments"][:-1]
        report = validate_partition(self.view, output)
        self.assertFalse(report.valid)
        self.assertTrue(any("without a matching reclassify adjustment" in error for error in report.errors))

    def test_added_test_cannot_have_undeclared_assignment(self) -> None:
        output = self.compact_output()
        output["subtasks"][0]["tests"]["n2p"]["include_ids"] = ["tests::new_beta"]
        report = validate_partition(self.view, output)
        self.assertFalse(report.valid)
        self.assertTrue(any("has undeclared assignments" in error for error in report.errors))

    def test_explicit_selector_rejects_exclusions(self) -> None:
        output = self.compact_output()
        output["subtasks"][0]["tests"]["f2p"]["exclude_ids"] = ["tests::alpha"]
        report = validate_partition(self.view, output)
        self.assertFalse(report.valid)
        self.assertTrue(any("must be empty when mode is explicit" in error for error in report.errors))

    def test_change_units_must_be_exactly_once(self) -> None:
        output = copy.deepcopy(self.output)
        output["subtasks"][1]["change_unit_ids"].append("u-001")
        output["subtasks"][1]["source_loc"] = 260
        report = validate_partition(self.view, output)
        self.assertFalse(report.valid)
        self.assertTrue(any("multiply assigned change units" in error for error in report.errors))

    def test_computed_source_loc_must_be_in_range(self) -> None:
        view = copy.deepcopy(self.view)
        output = copy.deepcopy(self.output)
        view["change_units"][2]["source_loc"] = 90
        output["subtasks"][1]["source_loc"] = 90
        report = validate_partition(view, output)
        self.assertFalse(report.valid)
        self.assertTrue(any("outside inclusive range 100..1000" in error for error in report.errors))

    def test_test_union_rejects_missing_declared_addition(self) -> None:
        output = copy.deepcopy(self.output)
        output["subtasks"][1]["tests"]["n2p"] = []
        report = validate_partition(self.view, output)
        self.assertFalse(report.valid)
        self.assertTrue(any("test union is missing IDs" in error for error in report.errors))

    def test_non_synthesized_addition_requires_commit(self) -> None:
        output = copy.deepcopy(self.output)
        del output["test_adjustments"][1]["provenance"]["commit"]
        report = validate_partition(self.view, output)
        self.assertFalse(report.valid)
        self.assertTrue(any("provenance.commit" in error for error in report.errors))

    def test_dependency_cycle_is_rejected(self) -> None:
        output = copy.deepcopy(self.output)
        output["subtasks"][0]["depends_on"] = ["M100.sub-02"]
        report = validate_partition(self.view, output)
        self.assertFalse(report.valid)
        self.assertTrue(any("dependency cycle" in error for error in report.errors))


class TestQualityValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.view = load_json("test_quality_view.json")
        self.output = load_json("test_quality_output_valid.json")
        self.raw = (FIXTURES / "test_decisions_valid.jsonl").read_bytes()
        self.decisions = parse_decisions_jsonl(self.raw)
        self.output["decisions_sha256"] = hashlib.sha256(self.raw).hexdigest()

    def test_valid_quality_review(self) -> None:
        report = validate_test_quality(
            self.view, self.output, self.decisions, decisions_bytes=self.raw
        )
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.metrics["decision_rows"], 3)

    def test_each_original_pair_must_appear_once(self) -> None:
        decisions = copy.deepcopy(self.decisions)
        decisions[2] = copy.deepcopy(decisions[0])
        raw = b"".join(
            (json.dumps(row, sort_keys=True) + "\n").encode("utf-8") for row in decisions
        )
        output = copy.deepcopy(self.output)
        output["decisions_sha256"] = hashlib.sha256(raw).hexdigest()
        output["summary"] = {
            "keep_direct_target": 2,
            "keep_regression": 1,
            "reclassify": 0,
            "remove_unrelated": 0,
            "remove_flaky": 0,
            "needs_human": 0,
        }
        report = validate_test_quality(self.view, output, decisions, decisions_bytes=raw)
        self.assertFalse(report.valid)
        self.assertTrue(any("missing original" in error for error in report.errors))
        self.assertTrue(any("duplicate original" in error for error in report.errors))

    def test_checksum_must_match_exact_jsonl_bytes(self) -> None:
        output = copy.deepcopy(self.output)
        output["decisions_sha256"] = "0" * 64
        report = validate_test_quality(
            self.view, output, self.decisions, decisions_bytes=self.raw
        )
        self.assertFalse(report.valid)
        self.assertTrue(any("decisions_sha256" in error for error in report.errors))

    def test_addition_provenance_is_required(self) -> None:
        output = copy.deepcopy(self.output)
        del output["additions"][0]["provenance"]["behavior"]
        report = validate_test_quality(
            self.view, output, self.decisions, decisions_bytes=self.raw
        )
        self.assertFalse(report.valid)
        self.assertTrue(any("provenance" in error for error in report.errors))

    def test_filtered_invalid_tests_are_context_not_effective_pairs(self) -> None:
        report = validate_test_quality(
            self.view, self.output, self.decisions, decisions_bytes=self.raw
        )
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.metrics["original_test_pairs"], 3)


class CompactTestQualityValidatorTests(unittest.TestCase):
    @staticmethod
    def decision(verdict: str, rationale: str) -> dict:
        return {
            "verdict": verdict,
            "related_requirement_ids": [],
            "rationale": rationale,
            "confidence": 0.9,
            "evidence": [
                {
                    "kind": "test_group",
                    "reference": "test_focus.json",
                    "detail": "The selected tests share the inspected component boundary.",
                }
            ],
        }

    def setUp(self) -> None:
        self.view = {
            "schema_version": 1,
            "task_kind": "test_quality",
            "workspace": "example_org_repo_v1_v2",
            "milestone_id": "M200",
            "input_hash": "e" * 64,
            "tests": [
                {"test_id": "functional::f", "original_role": "f2p", "status": "effective"},
                {"test_id": "functional::n", "original_role": "n2p", "status": "effective"},
                {"test_id": "alpha::one", "original_role": "p2p", "status": "effective"},
                {"test_id": "alpha::two", "original_role": "p2p", "status": "effective"},
                {"test_id": "beta::one", "original_role": "p2p", "status": "effective"},
                {"test_id": "misc::one", "original_role": "p2p", "status": "effective"},
            ],
        }
        functional_f = {
            "record_type": "functional_decision",
            "test_id": "functional::f",
            "original_role": "f2p",
            **self.decision("keep_direct_target", "Directly covers the changed behavior."),
        }
        functional_n = {
            "record_type": "functional_decision",
            "test_id": "functional::n",
            "original_role": "n2p",
            **self.decision("reclassify", "Start-state evidence makes this a regression test."),
            "proposed_role": "p2p",
        }
        self.records = [
            functional_f,
            functional_n,
            {
                "record_type": "p2p_default",
                "decision": self.decision(
                    "keep_regression", "The inspected remainder is a proportionate regression suite."
                ),
            },
            {
                "record_type": "p2p_group_rule",
                "rule_id": "alpha-component",
                "selector": {"kind": "prefix", "value": "alpha::"},
                "decision": self.decision(
                    "keep_direct_target", "The alpha group directly exercises the modified API."
                ),
            },
            {
                "record_type": "p2p_exception",
                "test_id": "beta::one",
                "decision": self.decision(
                    "remove_unrelated", "The beta-only test has no dependency on the change."
                ),
            },
        ]
        self.output = {
            "schema_version": 2,
            "task_kind": "test_quality",
            "source": {
                "workspace": self.view["workspace"],
                "milestone_id": self.view["milestone_id"],
                "input_hash": self.view["input_hash"],
            },
            "status": "reviewed",
            "decisions_file": "test_quality_records.jsonl",
            "decisions_sha256": "",
            "record_count": len(self.records),
            "decision_count": 6,
            "expansion_format": "functional-explicit-p2p-rules-v1",
            "additions": [],
            "summary": {
                "keep_direct_target": 3,
                "keep_regression": 1,
                "reclassify": 1,
                "remove_unrelated": 1,
                "remove_flaky": 0,
                "needs_human": 0,
            },
            "notes": [],
        }

    @staticmethod
    def serialized(records: list[dict]) -> bytes:
        return b"".join(
            (json.dumps(row, sort_keys=True) + "\n").encode("utf-8") for row in records
        )

    def validate(self, records: list[dict] | None = None, output: dict | None = None):
        records = copy.deepcopy(self.records if records is None else records)
        output = copy.deepcopy(self.output if output is None else output)
        raw = self.serialized(records)
        output["decisions_sha256"] = hashlib.sha256(raw).hexdigest()
        output["record_count"] = len(records)
        return validate_test_quality(self.view, output, records, decisions_bytes=raw)

    def test_compact_rules_expand_in_view_order(self) -> None:
        report = self.validate()
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.metrics["record_rows"], 5)
        self.assertEqual(report.metrics["decision_rows"], 6)

        expanded, expansion = expand_test_quality_records(self.view, self.records)
        self.assertTrue(expansion.valid, expansion.errors)
        self.assertEqual(
            [(row["test_id"], row["original_role"]) for row in expanded],
            [(row["test_id"], row["original_role"]) for row in self.view["tests"]],
        )
        beta = next(row for row in expanded if row["test_id"] == "beta::one")
        self.assertEqual(beta["verdict"], "remove_unrelated")
        self.assertEqual(
            hashlib.sha256(canonical_expanded_decisions_bytes(expanded)).hexdigest(),
            report.metrics["expanded_decisions_sha256"],
        )

    def test_uncovered_p2p_fails_closed(self) -> None:
        records = [row for row in self.records if row["record_type"] != "p2p_default"]
        report = self.validate(records)
        self.assertFalse(report.valid)
        self.assertTrue(any("not covered by a rule" in error for error in report.errors))

    def test_overlapping_group_rules_are_ambiguous_even_with_exception(self) -> None:
        records = copy.deepcopy(self.records)
        records.append(
            {
                "record_type": "p2p_group_rule",
                "rule_id": "alpha-overlap",
                "selector": {"kind": "glob", "value": "alpha::*"},
                "decision": self.decision("keep_regression", "A second alpha grouping."),
            }
        )
        report = self.validate(records)
        self.assertFalse(report.valid)
        self.assertTrue(any("ambiguous overlapping" in error for error in report.errors))

    def test_dead_group_rule_is_rejected(self) -> None:
        records = copy.deepcopy(self.records)
        records.append(
            {
                "record_type": "p2p_group_rule",
                "rule_id": "typo-group",
                "selector": {"kind": "prefix", "value": "does-not-exist::"},
                "decision": self.decision("keep_regression", "This selector is a typo."),
            }
        )
        report = self.validate(records)
        self.assertFalse(report.valid)
        self.assertTrue(any("match no original tests" in error for error in report.errors))

    def test_fully_shadowed_group_rule_is_rejected(self) -> None:
        records = copy.deepcopy(self.records)
        for test_id in ("alpha::one", "alpha::two"):
            records.append(
                {
                    "record_type": "p2p_exception",
                    "test_id": test_id,
                    "decision": self.decision(
                        "keep_direct_target",
                        "This exact alpha test retains the group decision.",
                    ),
                }
            )
        report = self.validate(records)
        self.assertFalse(report.valid)
        self.assertTrue(any("fully shadowed" in error for error in report.errors))

    def test_unused_default_is_rejected(self) -> None:
        records = copy.deepcopy(self.records)
        records.append(
            {
                "record_type": "p2p_group_rule",
                "rule_id": "misc-component",
                "selector": {"kind": "prefix", "value": "misc::"},
                "decision": self.decision(
                    "keep_regression",
                    "The misc component is an inspected regression boundary.",
                ),
            }
        )
        report = self.validate(records)
        self.assertFalse(report.valid)
        self.assertTrue(any("p2p_default is dead" in error for error in report.errors))

    def test_functional_tests_cannot_be_covered_by_a_rule(self) -> None:
        records = [
            row
            for row in self.records
            if not (
                row["record_type"] == "functional_decision"
                and row["test_id"] == "functional::n"
            )
        ]
        report = self.validate(records)
        self.assertFalse(report.valid)
        self.assertTrue(any("missing explicit functional" in error for error in report.errors))

    def test_one_default_expands_thousands_without_thousands_of_records(self) -> None:
        count = 5000
        view = copy.deepcopy(self.view)
        view["tests"] = [
            {"test_id": f"regression::{index}", "original_role": "p2p", "status": "effective"}
            for index in range(count)
        ]
        records = [
            {
                "record_type": "p2p_default",
                "decision": self.decision(
                    "keep_regression", "The inspected suite is a homogeneous regression gate."
                ),
            }
        ]
        raw = self.serialized(records)
        output = copy.deepcopy(self.output)
        output["decisions_sha256"] = hashlib.sha256(raw).hexdigest()
        output["record_count"] = 1
        output["decision_count"] = count
        output["summary"] = {
            "keep_direct_target": 0,
            "keep_regression": count,
            "reclassify": 0,
            "remove_unrelated": 0,
            "remove_flaky": 0,
            "needs_human": 0,
        }
        report = validate_test_quality(view, output, records, decisions_bytes=raw)
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.metrics["record_rows"], 1)
        self.assertEqual(report.metrics["decision_rows"], count)

    def test_cli_materializes_canonical_expansion(self) -> None:
        raw = self.serialized(self.records)
        output = copy.deepcopy(self.output)
        output["decisions_sha256"] = hashlib.sha256(raw).hexdigest()
        expanded, report = expand_test_quality_records(self.view, self.records)
        self.assertTrue(report.valid, report.errors)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            view_path = root / "view.json"
            manifest_path = root / "manifest.json"
            records_path = root / "test_quality_records.jsonl"
            expanded_path = root / "test_decisions.expanded.jsonl"
            view_path.write_text(json.dumps(self.view), encoding="utf-8")
            manifest_path.write_text(json.dumps(output), encoding="utf-8")
            records_path.write_bytes(raw)
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = validate_quality_main(
                    [
                        "--view",
                        str(view_path),
                        "--output",
                        str(manifest_path),
                        "--decisions",
                        str(records_path),
                        "--expanded-output",
                        str(expanded_path),
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                expanded_path.read_bytes(),
                canonical_expanded_decisions_bytes(expanded),
            )

    def test_v1_cli_materializes_explicit_rows_in_view_order(self) -> None:
        view = load_json("test_quality_view.json")
        output = load_json("test_quality_output_valid.json")
        records = parse_decisions_jsonl(
            (FIXTURES / "test_decisions_valid.jsonl").read_bytes()
        )
        records.reverse()
        raw = self.serialized(records)
        output["decisions_sha256"] = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            view_path = root / "view.json"
            manifest_path = root / "manifest.json"
            records_path = root / "v1.jsonl"
            expanded_path = root / "expanded.jsonl"
            view_path.write_text(json.dumps(view), encoding="utf-8")
            manifest_path.write_text(json.dumps(output), encoding="utf-8")
            records_path.write_bytes(raw)
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = validate_quality_main(
                    [
                        "--view",
                        str(view_path),
                        "--output",
                        str(manifest_path),
                        "--decisions",
                        str(records_path),
                        "--expanded-output",
                        str(expanded_path),
                    ]
                )
            self.assertEqual(exit_code, 0)
            expanded = parse_decisions_jsonl(expanded_path.read_bytes())
            self.assertEqual(
                [row["test_id"] for row in expanded],
                ["tests::alpha", "tests::regression", "tests::wrong_role"],
            )


class ContractFilesTests(unittest.TestCase):
    def test_task_directory_hydrates_build_views_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_dir = Path(temporary)
            input_payload = {
                "schema_version": 1,
                "task_kind": "partition",
                "workspace": "org_repo_v1_v2",
                "milestone_id": "M1",
                "input_hash": "d" * 64,
            }
            (task_dir / "input.json").write_text(json.dumps(input_payload), encoding="utf-8")
            (task_dir / "change_units.jsonl").write_text(
                json.dumps({"unit_id": "u1", "source_loc": 100}) + "\n",
                encoding="utf-8",
            )
            (task_dir / "tests.jsonl").write_text(
                json.dumps(
                    {
                        "test_id": "test_a",
                        "original_role": "fail_to_pass",
                        "status": "effective",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            hydrated = load_task_view(task_dir)
            self.assertEqual(hydrated["change_units"][0]["unit_id"], "u1")
            self.assertEqual(hydrated["tests"][0]["original_role"], "fail_to_pass")

    def test_task_directory_rejects_tampered_hashed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_dir = Path(temporary)
            change_units = b'{"source_loc": 100, "unit_id": "u1"}\n'
            tests = b'{"original_role": "fail_to_pass", "status": "effective", "test_id": "t1"}\n'
            (task_dir / "change_units.jsonl").write_bytes(change_units)
            (task_dir / "tests.jsonl").write_bytes(tests)
            file_hashes = {
                "change_units.jsonl": hashlib.sha256(change_units).hexdigest(),
                "tests.jsonl": hashlib.sha256(tests).hexdigest(),
            }
            input_hash = hashlib.sha256(
                json.dumps(
                    file_hashes,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            (task_dir / "input.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "task_kind": "partition",
                        "workspace": "org_repo_v1_v2",
                        "milestone_id": "M1",
                        "input_hash": input_hash,
                        "file_sha256": file_hashes,
                    }
                ),
                encoding="utf-8",
            )
            (task_dir / "tests.jsonl").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                load_task_view(task_dir)

    def test_schema_files_are_json(self) -> None:
        for name in ("partition_output.schema.json", "test_quality_output.schema.json"):
            payload = json.loads((PIPELINE_ROOT / "schemas" / name).read_text(encoding="utf-8"))
            self.assertEqual(payload["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_prompts_define_submission_marker_and_read_only_boundary(self) -> None:
        for name in ("partition.yaml", "test_quality.yaml"):
            prompt = (PIPELINE_ROOT / "config" / name).read_text(encoding="utf-8")
            self.assertIn("COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", prompt)
            self.assertIn("must not modify the repository", prompt)

    def test_partition_prompt_uses_bounded_test_focus_and_compact_selectors(self) -> None:
        prompt = (PIPELINE_ROOT / "config" / "partition.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("test_focus.json", prompt)
        self.assertIn("all_original", prompt)
        self.assertIn("include_ids", prompt)
        self.assertIn("exclude_ids", prompt)
        self.assertIn("MUST NOT cat", prompt)
        self.assertIn("Never enumerate the full original P2P suite", prompt)


if __name__ == "__main__":
    unittest.main()
