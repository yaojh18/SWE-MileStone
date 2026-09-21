from __future__ import annotations

import copy
import difflib
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


IDENTITY = adjudicator.NUSHELL_OP5_IDENTITY
CANDIDATES = tuple(sorted(adjudicator.NUSHELL_OP5_CANDIDATES))
DETAILS = adjudicator.NUSHELL_OP5_CANDIDATES
ORACLES = {CANDIDATES[0]: "oracle-mut", CANDIDATES[1]: "oracle-repl"}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def record(path: Path) -> dict:
    payload = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def existing_record(path: Path) -> dict:
    return {"exists": True, **record(path)}


def make_tar(path: Path, member_name: str, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:") as archive:
        member = tarfile.TarInfo(member_name)
        member.size = len(payload)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(payload))


def snapshot(path: Path, member_name: str, payload: bytes) -> dict:
    file_value = {
        "path": member_name,
        "mode": "0644",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    return {
        "archive": record(path),
        "allowed_paths": [member_name],
        "files": [file_value],
        "files_canonical_sha256": adjudicator.canonical_json_sha256([file_value]),
    }


def endpoint_result(identifier: str, *, status: str, outcome: str | None, disposition: str) -> dict:
    return {
        "test_id": identifier,
        "disposition": disposition,
        "outcome": outcome,
        "reason": "self-contained strict op5 fixture",
        "attempt_observations": [
            {"attempt": attempt, "status": status, "outcome": outcome}
            for attempt in range(1, 4)
        ],
        "oracle": {"status": "portable"},
    }


class NushellOp5Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dataset = root / "dataset"
        self.repo = self.dataset / IDENTITY["workspace"]
        retained = IDENTITY["retained_id"]
        self.classification_path = (
            self.repo / "test_results" / retained / f"{retained}_classification.json"
        )
        self.provenance_path = self.repo / "merge_provenance" / f"{retained}.json"
        self.source_dir = self.classification_path.parent / "source_results" / IDENTITY["exit_id"]
        self.source_classification_path = self.source_dir / "source-classification.json"
        self.source_filter_path = self.source_dir / "source-filter.json"
        self.probe_path = root / "probe.json"
        self.raw_path = root / "manifest.json"
        self.adjudication_path = root / "adjudication.json"
        self.entry_raw_reports: list[Path] = []
        self.exit_raw_reports: list[Path] = []
        self._build()

    @property
    def unresolved(self) -> list[dict]:
        return [
            {
                "test_id": identifier,
                "entry_start": None,
                "exit_end": "pass",
                "reason": "requires exact outer endpoint evidence",
            }
            for identifier in CANDIDATES
        ]

    def _projection(self, identifier: str) -> tuple[dict, dict, int]:
        detail = DETAILS[identifier]
        path = detail["path"]
        leaf = detail["leaf_name"]
        oracle = ORACLES[identifier]
        directory = self.root / "oracles" / oracle
        directory.mkdir(parents=True, exist_ok=True)
        base = b"#[test]\nfn existing_fixture_test() {}\n"
        source = (
            "#[test]\n"
            f"fn {leaf}() {{\n"
            "    let _opts = NuOpts {\n"
            "        experimental: vec![\"enforce-runtime-annotations\".to_string()],\n"
            "    };\n"
            "}\n"
        ).rstrip("\n").encode("utf-8")
        owner_prefix = b"fn owner_only_helper() {}\n\n"
        target = owner_prefix + source + b"\n"
        source_start = len(owner_prefix)
        source_end = source_start + len(source)
        attribute_start = source_start
        attribute_end = attribute_start + len(b"#[test]")
        separator = b"\n\n" if base.endswith(b"\n") else b"\n\n\n"
        append_payload = separator + source + b"\n"
        projected = base + append_payload
        projected_start = len(base) + len(separator)
        projected_end = projected_start + len(source)
        function = {
            "candidate_id": identifier,
            "leaf_name": leaf,
            "path": path,
            "projection_mode": adjudicator.NUSHELL_EXACT_FUNCTION_PROJECTION_MODE,
            "source_span": {
                "start_byte": source_start,
                "end_byte": source_end,
                "start_line": target[:source_start].count(b"\n") + 1,
                "end_line": target[:source_end].count(b"\n") + 1,
            },
            "source_bytes": len(source),
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "test_attribute_span": {
                "start_byte": attribute_start,
                "end_byte": attribute_end,
            },
            "test_attribute_sha256": hashlib.sha256(b"#[test]").hexdigest(),
            "target_definition_count": 1,
            "base_definition_count": 0,
            "projected_span": {
                "start_byte": projected_start,
                "end_byte": projected_end,
                "start_line": projected[:projected_start].count(b"\n") + 1,
                "end_line": projected[:projected_end].count(b"\n") + 1,
            },
            "projected_source_bytes": len(source),
            "projected_source_sha256": hashlib.sha256(source).hexdigest(),
        }
        file_projection = {
            "path": path,
            "base": {"bytes": len(base), "sha256": hashlib.sha256(base).hexdigest()},
            "owner_target": {
                "bytes": len(target),
                "sha256": hashlib.sha256(target).hexdigest(),
            },
            "projected": {
                "bytes": len(projected),
                "sha256": hashlib.sha256(projected).hexdigest(),
            },
            "append_payload": {
                "bytes": len(append_payload),
                "sha256": hashlib.sha256(append_payload).hexdigest(),
            },
            "appended_functions": [identifier],
        }
        projection = {
            "mode": adjudicator.NUSHELL_EXACT_FUNCTION_PROJECTION_MODE,
            "functions": [function],
            "functions_canonical_sha256": adjudicator.canonical_json_sha256([function]),
            "files": [file_projection],
            "files_canonical_sha256": adjudicator.canonical_json_sha256([file_projection]),
        }
        projection_path = directory / "projection.json"
        write_json(projection_path, projection)
        base_tar = directory / "base.tar"
        target_tar = directory / "target.tar"
        make_tar(base_tar, path, base)
        make_tar(target_tar, path, target)
        base_snapshot = snapshot(base_tar, path, base)
        target_snapshot = snapshot(target_tar, path, target)

        diff = list(
            difflib.unified_diff(
                base.decode().splitlines(keepends=True),
                projected.decode().splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
        patch_path = directory / "projection.patch"
        patch_path.write_text(
            f"diff --git a/{path} b/{path}\n"
            + "index 0000000..1111111 100644\n"
            + "".join(diff),
            encoding="utf-8",
        )
        patch_record = record(patch_path)
        sidecar = {
            "schema_version": 1,
            "artifact_type": "cross_sif_base_worktree_sha256",
            "oracle_id": oracle,
            "patch_kind": "cross_sif_endpoint_tree_to_owner_canonical_start",
            "target_endpoint": "entry_start",
            "requested_base_ref": f"milestone-{IDENTITY['entry_id']}-start",
            "runnable_base_commit": "a" * 40,
            "base_sif": None,
            "snapshot_archive": base_snapshot["archive"],
            "snapshot_extraction_shell_sha256": "b" * 64,
            "allowed_paths": [path],
            "files": base_snapshot["files"],
            "files_canonical_sha256": base_snapshot["files_canonical_sha256"],
        }
        sidecar_path = directory / "base-sidecar.json"
        write_json(sidecar_path, sidecar)
        extraction = {
            "oracle_id": oracle,
            "target_endpoint": "entry_start",
            "status": "portable",
            "source_milestone": IDENTITY["exit_id"],
            "patch_kind": "cross_sif_endpoint_tree_to_owner_canonical_start",
            "requested_base_commit": "a" * 40,
            "requested_base_ref": f"milestone-{IDENTITY['entry_id']}-start",
            "allowed_paths": [path],
            "observed_paths": [path],
            "candidate_ids": [identifier],
            "patch": patch_record,
            "base_snapshot": base_snapshot,
            "target_snapshot": target_snapshot,
            "synthesis": {
                "observed_paths": [path],
                "synthetic_apply_check_returncode": 0,
                "patch": patch_record,
                "projection": projection,
            },
            "projection_mode": adjudicator.NUSHELL_EXACT_FUNCTION_PROJECTION_MODE,
            "projected_functions": [function],
            "projected_functions_canonical_sha256": projection[
                "functions_canonical_sha256"
            ],
            "projection_artifact": record(projection_path),
            "projection_canonical_sha256": adjudicator.canonical_json_sha256(projection),
            "base_worktree_sha256_sidecar": record(sidecar_path),
            "base_worktree_sha256_sidecar_canonical_sha256": (
                adjudicator.canonical_json_sha256(sidecar)
            ),
        }
        fallback = {
            "status": "portable",
            "oracle_id": oracle,
            "patch_kind": "cross_sif_endpoint_tree_to_owner_canonical_start",
            "observed_paths": [path],
            "patch": patch_record,
            "projection_mode": adjudicator.NUSHELL_EXACT_FUNCTION_PROJECTION_MODE,
            "projected_function": function,
            "projection_artifact": record(projection_path),
        }
        experimental_offset = next(
            index
            for index, line in enumerate(source.decode().splitlines())
            if line.lstrip().startswith("experimental:")
        )
        diagnostic_line = function["projected_span"]["start_line"] + experimental_offset
        return extraction, fallback, diagnostic_line

    def _build(self) -> None:
        unresolved = self.unresolved
        logical = {
            "policy": "ordered_outer_f2p_and_common_p2p_only",
            "outer_transition": {
                "entry_milestone": IDENTITY["entry_id"],
                "exit_milestone": IDENTITY["exit_id"],
            },
            "unresolved_outer_evidence": unresolved,
        }
        stable = {"fail_to_pass": list(CANDIDATES), "pass_to_pass": [], "none_to_pass": []}
        write_json(
            self.classification_path,
            {
                "schema_version": 1,
                "stable_classification": stable,
                "logical_composition": logical,
            },
        )
        write_json(
            self.source_classification_path,
            {"schema_version": 1, "stable_classification": stable},
        )
        write_json(self.source_filter_path, {"schema_version": 1, "status": "pass"})
        source_result = {
            "milestone_id": IDENTITY["exit_id"],
            "effective": stable,
            "source_stable_classification": stable,
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
            **IDENTITY,
            "test_contract": {
                "logical_composition": logical,
                "source_results": [
                    {
                        "milestone_id": IDENTITY["entry_id"],
                        "effective": {"fail_to_pass": [], "pass_to_pass": [], "none_to_pass": []},
                        "source_stable_classification": {
                            "fail_to_pass": [],
                            "pass_to_pass": [],
                            "none_to_pass": [],
                        },
                    },
                    source_result,
                ],
            },
        }
        write_json(self.provenance_path, provenance)
        probe = {
            "schema_version": 1,
            **IDENTITY,
            "input_fingerprints": {
                "candidate_set_sha256": adjudicator.canonical_json_sha256(unresolved),
                "merge_provenance": record(self.provenance_path),
            },
            "sources": [
                {"position": "A", "milestone_id": IDENTITY["entry_id"]},
                {"position": "B", "milestone_id": IDENTITY["exit_id"]},
            ],
            "candidates": [],
        }
        for unresolved_record in unresolved:
            identifier = unresolved_record["test_id"]
            detail = DETAILS[identifier]
            absent = {
                "framework": "cargo_rust",
                "matches": [],
                "raw_grep_matches": 0,
                "status": "absent",
            }
            present = {
                "framework": "cargo_rust",
                "matches": [
                    {
                        "path": detail["path"],
                        "line": 10,
                        "line_text": f"fn {detail['leaf_name']}() {{",
                    }
                ],
                "raw_grep_matches": 1,
                "status": "present",
            }
            probe["candidates"].append(
                {
                    "test_id": identifier,
                    "candidate_sha256": adjudicator.canonical_json_sha256(unresolved_record),
                    "definitions": {
                        "a_start": {
                            "canonical": copy.deepcopy(absent),
                            "runnable": copy.deepcopy(absent),
                            "definition_status_agrees": True,
                        },
                        "b_start": {
                            "canonical": copy.deepcopy(present),
                            "runnable": copy.deepcopy(present),
                            "definition_status_agrees": True,
                        },
                    },
                }
            )
        write_json(self.probe_path, probe)

        extractions = []
        fallbacks = {}
        diagnostic_lines = {}
        for identifier in CANDIDATES:
            extraction, fallback, line = self._projection(identifier)
            extractions.append(extraction)
            fallbacks[identifier] = fallback
            diagnostic_lines[identifier] = line

        entry_state = {
            "milestone_id": IDENTITY["entry_id"],
            "state": "start",
            "requested_ref": f"milestone-{IDENTITY['entry_id']}-start",
            "runnable_commit": "a" * 40,
            "canonical_commit": "c" * 40,
        }
        exit_state = {
            "milestone_id": IDENTITY["exit_id"],
            "state": "end",
            "requested_ref": f"milestone-{IDENTITY['exit_id']}-end",
            "runnable_commit": "d" * 40,
            "canonical_commit": "e" * 40,
        }
        entry_results = {
            identifier: endpoint_result(
                identifier, status="compile_error", outcome=None, disposition="unresolved"
            )
            for identifier in CANDIDATES
        }
        exit_results = {
            identifier: endpoint_result(
                identifier, status="collected", outcome="passed", disposition="stable"
            )
            for identifier in CANDIDATES
        }
        oracle_ids = sorted(ORACLES.values())
        entry_attempts = []
        exit_attempts = []
        for attempt in range(1, 4):
            attempt_dir = self.root / f"attempt-{attempt}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            patch_files = {}
            sidecars = {}
            guard_logs = {}
            for identifier in CANDIDATES:
                oracle = ORACLES[identifier]
                extraction = next(value for value in extractions if value["oracle_id"] == oracle)
                patch_copy = attempt_dir / f"{oracle}.patch"
                patch_copy.write_bytes(Path(extraction["patch"]["path"]).read_bytes())
                sidecar_copy = attempt_dir / f"{oracle}.sidecar.json"
                sidecar_copy.write_bytes(
                    Path(extraction["base_worktree_sha256_sidecar"]["path"]).read_bytes()
                )
                guard = attempt_dir / f"{oracle}.guard.log"
                guard.write_bytes(b"")
                patch_files[oracle] = existing_record(patch_copy)
                sidecars[oracle] = existing_record(sidecar_copy)
                guard_logs[oracle] = existing_record(guard)
            entry_executions = []
            exit_executions = []
            for index, identifier in enumerate(CANDIDATES, 1):
                detail = DETAILS[identifier]
                diagnostic = "\n".join(
                    [
                        f"error[E0560]: {adjudicator.NUSHELL_E0560_MESSAGE}",
                        f"  --> {detail['path']}:{diagnostic_lines[identifier]}:9",
                        "   |",
                        f"{diagnostic_lines[identifier]} |         experimental: vec![]",
                        "   |         ^^^^^^^^^^^^ NuOpts does not have this field",
                        "",
                        "error: could not compile `nu` due to 1 previous error",
                        "",
                    ]
                )
                entry_report = attempt_dir / f"entry-{index}.cargo.log"
                entry_report.write_text(diagnostic, encoding="utf-8")
                self.entry_raw_reports.append(entry_report)
                entry_parsed = attempt_dir / f"entry-{index}.parsed.json"
                write_json(entry_parsed, adjudicator.parse_cargo_report(entry_report))
                entry_executions.append(
                    {
                        "name": f"nushell_exact_{index:02d}",
                        "framework": "cargo",
                        "candidate_ids": [identifier],
                        "returncode": 101,
                        "raw_report": record(entry_report),
                        "parsed_report": record(entry_parsed),
                        "exact_candidate_outcomes": {identifier: []},
                    }
                )
                passed_log = "\n".join(
                    [
                        "     Running unittests src/lib.rs (target/debug/deps/nu_fixture)",
                        "running 1 test",
                        f"test {identifier} ... ok",
                        "",
                        "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s",
                        "",
                    ]
                )
                exit_report = attempt_dir / f"exit-{index}.cargo.log"
                exit_report.write_text(passed_log, encoding="utf-8")
                self.exit_raw_reports.append(exit_report)
                exit_parsed = attempt_dir / f"exit-{index}.parsed.json"
                write_json(exit_parsed, adjudicator.parse_cargo_report(exit_report))
                exit_executions.append(
                    {
                        "name": f"nushell_exact_{index:02d}",
                        "framework": "cargo",
                        "candidate_ids": [identifier],
                        "returncode": 0,
                        "raw_report": record(exit_report),
                        "parsed_report": record(exit_parsed),
                        "exact_candidate_outcomes": {identifier: ["passed"]},
                    }
                )
            entry_attempts.append(
                {
                    "attempt": attempt,
                    "setup_returncode": 0,
                    "actual_head": entry_state["runnable_commit"],
                    "compile_error_detected": True,
                    "timed_out": False,
                    "apptainer_returncode": 0,
                    "oracle_application": {oracle: "applied" for oracle in oracle_ids},
                    "oracle_patch_files": patch_files,
                    "oracle_base_worktree_sidecars": sidecars,
                    "oracle_base_verification_logs": guard_logs,
                    "candidates": {
                        identifier: {
                            "status": "compile_error",
                            "outcome": None,
                            "expected_executions": 1,
                        }
                        for identifier in CANDIDATES
                    },
                    "executions": entry_executions,
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
                    "oracle_application": {},
                    "oracle_patch_files": {},
                    "oracle_base_worktree_sidecars": {},
                    "oracle_base_verification_logs": {},
                    "candidates": {
                        identifier: {
                            "status": "collected",
                            "outcome": "passed",
                            "expected_executions": 1,
                        }
                        for identifier in CANDIDATES
                    },
                    "executions": exit_executions,
                }
            )

        raw = {
            "schema_version": 2,
            "artifact_type": adjudicator.RAW_ARTIFACT_TYPE,
            "status": "completed_with_unresolved_evidence",
            "mode": "both_outer_endpoints",
            **IDENTITY,
            "attempts_required": 3,
            "strategy": "nushell_core4_exact_cargo",
            "inputs": {
                "classification": record(self.classification_path),
                "merge_provenance": record(self.provenance_path),
                "probe": record(self.probe_path),
                "probe_canonical_json_sha256": adjudicator.canonical_json_sha256(probe),
                "implementation": {
                    **{
                        name: record(path)
                        for name, path in adjudicator.IMPLEMENTATION_PATHS.items()
                    },
                    adjudicator.REPORT_PARSER_DIRECT_DEPENDENCY_KEY: {
                        name: record(path)
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
                    "test_id": unresolved_record["test_id"],
                    "unresolved_outer_evidence": unresolved_record,
                    "probe_candidate_sha256": adjudicator.canonical_json_sha256(
                        unresolved_record
                    ),
                    "source_f2p_owners": [IDENTITY["exit_id"]],
                    "oracle": {
                        "status": "portable",
                        "fallbacks": {
                            "entry_start": fallbacks[unresolved_record["test_id"]]
                        },
                    },
                }
                for unresolved_record in unresolved
            ],
            "oracle_extractions": extractions,
            "endpoints": {
                "entry_start": {
                    "candidate_ids": list(CANDIDATES),
                    "state": entry_state,
                    "selected_oracle_ids": oracle_ids,
                    "portable_selected_oracle_ids": oracle_ids,
                    "attempts": entry_attempts,
                    "candidate_results": [entry_results[value] for value in CANDIDATES],
                },
                "exit_end": {
                    "candidate_ids": list(CANDIDATES),
                    "state": exit_state,
                    "selected_oracle_ids": [],
                    "portable_selected_oracle_ids": [],
                    "attempts": exit_attempts,
                    "candidate_results": [exit_results[value] for value in CANDIDATES],
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
                "total": 2,
                "resolved": 0,
                "flaky": 0,
                "non_portable": 0,
                "unresolved": 2,
            },
        }
        write_json(self.raw_path, raw)

    def raw(self) -> dict:
        return read_json(self.raw_path)

    def write_raw(self, value: dict) -> None:
        write_json(self.raw_path, value)

    def approved(self) -> dict:
        return adjudicator.build_adjudication(
            dataset=self.dataset,
            raw_evidence_path=self.raw_path,
            approve=True,
            reviewer="self-contained-reviewer",
            approval_reason="Reviewed fixed exact-function op5 E0560 inference only.",
        )


class NushellOp5AdjudicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fixture(self, name: str) -> NushellOp5Fixture:
        return NushellOp5Fixture(self.root / name)

    def test_exact_op5_builds_and_recomputes_approved_artifact(self) -> None:
        fixture = self.fixture("valid")
        raw = fixture.raw()
        raw["endpoints"]["entry_start"]["attempts"][0]["executions"][0][
            "raw_report"
        ]["exists"] = True
        fixture.write_raw(raw)
        artifact = fixture.approved()
        write_json(fixture.adjudication_path, artifact)
        self.assertEqual(
            artifact,
            adjudicator.validate_adjudication_artifact(
                path=fixture.adjudication_path,
                dataset=fixture.dataset,
                raw_evidence_path=fixture.raw_path,
                require_approved=True,
            ),
        )
        findings = artifact["technical_findings"]
        self.assertEqual(2, findings["candidate_count"])
        self.assertEqual("producer_confirmed_direct_observation", findings["exit_end"]["observation_kind"])
        self.assertIn("arbitrary Rust compile failure", findings["compile_symbol_caveats"][0]["statement"])

    def test_postpublication_recomputation_uses_only_exact_two_file_snapshots(self) -> None:
        fixture = self.fixture("postpublication-snapshots")
        artifact = fixture.approved()
        write_json(fixture.adjudication_path, artifact)
        classification_bytes = fixture.classification_path.read_bytes()
        provenance_bytes = fixture.provenance_path.read_bytes()
        snapshots = adjudicator.PrepublicationInputSnapshots(
            classification_path=fixture.classification_path,
            classification_bytes=classification_bytes,
            classification_sha256=hashlib.sha256(classification_bytes).hexdigest(),
            merge_provenance_path=fixture.provenance_path,
            merge_provenance_bytes=provenance_bytes,
            merge_provenance_sha256=hashlib.sha256(provenance_bytes).hexdigest(),
        )

        # Model the in-place replacement performed by endpoint publication.
        classification = read_json(fixture.classification_path)
        classification["full_transition_classification_status"] = (
            "outer_endpoint_evidence_complete"
        )
        classification["outer_endpoint_evidence"] = {"status": "complete"}
        write_json(fixture.classification_path, classification)
        provenance = read_json(fixture.provenance_path)
        provenance["test_contract"]["outer_endpoint_evidence"] = {
            "status": "complete"
        }
        write_json(fixture.provenance_path, provenance)

        with self.assertRaisesRegex(
            adjudicator.AdjudicationError,
            "raw.inputs.classification size or SHA-256 is stale",
        ):
            adjudicator.validate_adjudication_artifact(
                path=fixture.adjudication_path,
                dataset=fixture.dataset,
                raw_evidence_path=fixture.raw_path,
                require_approved=True,
            )
        self.assertEqual(
            artifact,
            adjudicator.validate_adjudication_artifact(
                path=fixture.adjudication_path,
                dataset=fixture.dataset,
                raw_evidence_path=fixture.raw_path,
                require_approved=True,
                prepublication_input_snapshots=snapshots,
            ),
        )

        stale = adjudicator.PrepublicationInputSnapshots(
            classification_path=fixture.classification_path,
            classification_bytes=classification_bytes + b" ",
            classification_sha256=hashlib.sha256(classification_bytes).hexdigest(),
            merge_provenance_path=fixture.provenance_path,
            merge_provenance_bytes=provenance_bytes,
            merge_provenance_sha256=hashlib.sha256(provenance_bytes).hexdigest(),
        )
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError,
            "prepublication snapshot size or SHA-256 is stale",
        ):
            adjudicator.validate_adjudication_artifact(
                path=fixture.adjudication_path,
                dataset=fixture.dataset,
                raw_evidence_path=fixture.raw_path,
                require_approved=True,
                prepublication_input_snapshots=stale,
            )

    def test_rejects_false_or_unknown_file_record_metadata(self) -> None:
        cases = (("false-exists", "exists", False), ("unknown", "forged", True))
        for name, field, value in cases:
            with self.subTest(name=name):
                fixture = self.fixture(name)
                raw = fixture.raw()
                raw["endpoints"]["entry_start"]["attempts"][0]["executions"][0][
                    "raw_report"
                ][field] = value
                fixture.write_raw(raw)
                with self.assertRaisesRegex(
                    adjudicator.AdjudicationError, "is not an exact file record"
                ):
                    fixture.approved()

    def test_rejects_conflicting_optional_classification_identity(self) -> None:
        fixture = self.fixture("classification-identity")
        classification = read_json(fixture.classification_path)
        classification["workspace"] = "forged_workspace"
        write_json(fixture.classification_path, classification)
        raw = fixture.raw()
        raw["inputs"]["classification"] = record(fixture.classification_path)
        fixture.write_raw(raw)
        with self.assertRaisesRegex(
            adjudicator.AdjudicationError, "classification identity differs"
        ):
            fixture.approved()

    def test_rejects_extra_out_of_span_and_nonexact_diagnostics(self) -> None:
        cases = (
            (
                "extra",
                "\nerror[E0308]: mismatched types\n  --> src/main.rs:1:1\n",
                "out-of-policy|non-E0560",
            ),
            (
                "nonexact-line",
                None,
                "out-of-policy",
            ),
        )
        for name, suffix, pattern in cases:
            with self.subTest(name=name):
                fixture = self.fixture(name)
                raw = fixture.raw()
                execution = raw["endpoints"]["entry_start"]["attempts"][0]["executions"][0]
                path = Path(execution["raw_report"]["path"])
                text = path.read_text(encoding="utf-8")
                if suffix is not None:
                    path.write_text(text + suffix, encoding="utf-8")
                else:
                    lines = text.splitlines(keepends=True)
                    location_index = next(
                        index for index, line in enumerate(lines) if line.lstrip().startswith("-->")
                    )
                    prefix, _line, column = lines[location_index].rsplit(":", 2)
                    lines[location_index] = f"{prefix}:99:{column}"
                    path.write_text("".join(lines), encoding="utf-8")
                execution["raw_report"] = record(path)
                parsed = Path(execution["parsed_report"]["path"])
                write_json(parsed, adjudicator.parse_cargo_report(path))
                execution["parsed_report"] = record(parsed)
                fixture.write_raw(raw)
                with self.assertRaisesRegex(adjudicator.AdjudicationError, pattern):
                    fixture.approved()

    def test_rejects_full_file_projection_candidate_drift_and_tamper(self) -> None:
        mutations = []
        full_file = self.fixture("full-file")
        raw = full_file.raw()
        raw["oracle_extractions"][0]["synthesis"]["projection"]["mode"] = "full_file_replace"
        mutations.append((full_file, raw, "projection"))

        drift = self.fixture("candidate-drift")
        raw = drift.raw()
        raw["candidate_input"][0]["test_id"] = "commands::mut_::forged"
        mutations.append((drift, raw, "candidate set|candidate"))

        hash_tamper = self.fixture("hash-tamper")
        raw = hash_tamper.raw()
        raw["oracle_extractions"][0]["projection_artifact"]["sha256"] = "0" * 64
        mutations.append((hash_tamper, raw, "size or SHA-256 is stale"))

        path_tamper = self.fixture("path-tamper")
        raw = path_tamper.raw()
        raw["candidate_input"][0]["oracle"]["fallbacks"]["entry_start"][
            "observed_paths"
        ] = ["tests/forged.rs"]
        mutations.append((path_tamper, raw, "fallback oracle provenance is stale"))

        for fixture, raw, pattern in mutations:
            with self.subTest(case=fixture.root.name):
                fixture.write_raw(raw)
                with self.assertRaisesRegex(adjudicator.AdjudicationError, pattern):
                    fixture.approved()

    def test_rejects_B_nonexact_and_reused_physical_evidence(self) -> None:
        b_fixture = self.fixture("b-nonexact")
        raw = b_fixture.raw()
        execution = raw["endpoints"]["exit_end"]["attempts"][0]["executions"][0]
        execution["exact_candidate_outcomes"][CANDIDATES[0]] = ["failed"]
        b_fixture.write_raw(raw)
        with self.assertRaisesRegex(adjudicator.AdjudicationError, "Cargo result is not exact"):
            b_fixture.approved()

        reused = self.fixture("reused")
        raw = reused.raw()
        first = raw["endpoints"]["entry_start"]["attempts"][0]["executions"][0][
            "raw_report"
        ]
        raw["endpoints"]["entry_start"]["attempts"][1]["executions"][0][
            "raw_report"
        ] = copy.deepcopy(first)
        reused.write_raw(raw)
        with self.assertRaisesRegex(adjudicator.AdjudicationError, "reuses physical evidence"):
            reused.approved()

    def test_parser_consumes_private_snapshot_not_replaceable_original(self) -> None:
        fixture = self.fixture("toctou")
        original_parser = adjudicator.parse_cargo_report
        original_path = fixture.entry_raw_reports[0].resolve()
        parser_paths = []
        replaced = False

        def replacing_parser(path: Path) -> dict:
            nonlocal replaced
            parser_paths.append(path.resolve())
            if not replaced:
                original_path.write_text("forged after authentication\n", encoding="utf-8")
                replaced = True
            return original_parser(path)

        with mock.patch.object(adjudicator, "parse_cargo_report", side_effect=replacing_parser):
            artifact = fixture.approved()
        self.assertEqual("approved", artifact["status"])
        self.assertTrue(parser_paths)
        self.assertTrue(all(path != original_path for path in parser_paths))


if __name__ == "__main__":
    unittest.main()
