from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_pipeline import run_outer_endpoint_rerun as rerun


WORKSPACE = "BurntSushi_ripgrep_14.1.1_15.0.0"
RETAINED = "milestone_seed_5f5da48_1_sub-02"
ENTRY = "milestone_seed_5f5da48_1_sub-01"
EXIT = RETAINED
TEST_ID = "regression::r3127_gitignore_allow_unclosed_class"
TEST_PATH = "tests/regression.rs"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_meta(root: Path, paths: list[str]) -> dict:
    files = []
    for relative in paths:
        path = root / relative
        files.append(
            {
                "path": relative,
                "mode": "0644",
                "bytes": path.stat().st_size,
                "sha256": _sha(path),
            }
        )
    return {
        "files": files,
        "files_canonical_sha256": rerun.canonical_json_hash(files),
    }


def _write_tree_file(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _rust_oracle_spec(
    root: Path, candidates: tuple[str, ...], paths: tuple[str, ...]
) -> rerun.OracleSpec:
    return rerun.OracleSpec(
        oracle_id="rust-exact",
        source_id=EXIT,
        source_position="B",
        source_sif=root / "owner.sif",
        canonical_start="c" * 40,
        canonical_start_parent="",
        paths=paths,
        candidate_ids=candidates,
        patch_file=root / "oracle.patch",
        patch_kind=rerun.CROSS_SIF_PATCH_KIND,
        base_commit="a" * 40,
        target_endpoint="entry_start",
        base_sif=root / "endpoint.sif",
        base_requested_ref=f"milestone-{ENTRY}-start",
    )


def _state(state_key: str, ref: str, canonical: str, runnable: str) -> dict:
    return {
        "state_key": state_key,
        "requested_ref": ref,
        "canonical_commit": canonical,
        "canonical_tree": f"tree-{canonical}",
        "runnable_commit": runnable,
        "runnable_tree": f"tree-{runnable}",
    }


def _definition(canonical: str, runnable: str, present: bool) -> dict:
    payload = {
        "status": "present" if present else "absent",
        "framework": "cargo",
        "selector": TEST_ID,
        "search_paths": [TEST_PATH],
        "matches": (
            [{"path": TEST_PATH, "line": 10, "line_text": "fn target() {}", "blob": "b"}]
            if present
            else []
        ),
        "raw_grep_matches": [],
    }
    return {
        "canonical_commit": canonical,
        "runnable_commit": runnable,
        "canonical": payload,
        "runnable": payload,
        "definition_status_agrees": True,
    }


class EndpointRerunTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> argparse.Namespace:
        dataset = root / "dataset"
        destination = root / "images"
        output = root / "evidence"
        repo = dataset / WORKSPACE
        test_dir = repo / "test_results" / RETAINED
        provenance_dir = repo / "merge_provenance"
        test_dir.mkdir(parents=True)
        provenance_dir.mkdir(parents=True)
        destination.mkdir(parents=True)

        classification = {
            "logical_composition": {
                "unresolved_outer_evidence": [
                    {
                        "test_id": TEST_ID,
                        "entry_start": None,
                        "exit_end": "pass",
                        "reason": "needs rerun",
                    }
                ]
            }
        }
        (test_dir / f"{RETAINED}_classification.json").write_text(
            json.dumps(classification), encoding="utf-8"
        )

        a_start = "a" * 40
        a_end = "b" * 40
        b_start = "c" * 40
        b_end = "d" * 40
        provenance = {
            "workspace": WORKSPACE,
            "retained_id": RETAINED,
            "entry_id": ENTRY,
            "exit_id": EXIT,
            "ordered_source_ids": [ENTRY, EXIT],
            "patch_segments": [
                {
                    "milestone_id": ENTRY,
                    "start_ref": f"milestone-{ENTRY}-start",
                    "end_ref": f"milestone-{ENTRY}-end",
                    "start_commit": "1" * 40,
                    "start_ref_original": a_start,
                    "end_commit": a_end,
                },
                {
                    "milestone_id": EXIT,
                    "start_ref": f"milestone-{EXIT}-start",
                    "end_ref": f"milestone-{EXIT}-end",
                    "start_commit": "2" * 40,
                    "start_ref_original": b_start,
                    "end_commit": b_end,
                },
            ],
            "test_contract": {
                "source_results": [
                    {
                        "milestone_id": ENTRY,
                        "effective": {"fail_to_pass": []},
                    },
                    {
                        "milestone_id": EXIT,
                        "effective": {"fail_to_pass": [TEST_ID]},
                    },
                ]
            },
        }
        (provenance_dir / f"{RETAINED}.json").write_text(
            json.dumps(provenance), encoding="utf-8"
        )

        image_records = {}
        manifest_records = []
        for milestone, content in ((ENTRY, b"entry-sif"), (EXIT, b"exit-sif")):
            relative = f"{WORKSPACE}/{milestone}.sif"
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            image_records[milestone] = {
                "path": str(path),
                "resolved_path": str(path.resolve()),
                "bytes": path.stat().st_size,
                "sha256": _sha(path),
                "source": f"docker://example/{milestone}:v0.9",
                "destination_rel": relative,
            }
            manifest_records.append(
                {
                    "workspace": WORKSPACE,
                    "milestone_id": milestone,
                    "source": f"docker://example/{milestone}:v0.9",
                    "destination_rel": relative,
                }
            )
        sif_manifest = root / "sif_manifest.jsonl"
        sif_manifest.write_text(
            "\n".join(json.dumps(value) for value in manifest_records) + "\n",
            encoding="utf-8",
        )

        sources = []
        for position, milestone, start, end in (
            ("A", ENTRY, a_start, a_end),
            ("B", EXIT, b_start, b_end),
        ):
            runnable_start = f"r{position.lower()}s"
            runnable_end = f"r{position.lower()}e"
            sources.append(
                {
                    "position": position,
                    "milestone_id": milestone,
                    "image": image_records[milestone],
                    "test_config": {
                        "path": "test_config.json",
                        "sha256": "0" * 64,
                        "canonical_json_sha256": "0" * 64,
                    },
                    "states": {
                        "start": _state(
                            f"{position.lower()}_start",
                            f"milestone-{milestone}-start",
                            start,
                            runnable_start,
                        ),
                        "end": _state(
                            f"{position.lower()}_end",
                            f"milestone-{milestone}-end",
                            end,
                            runnable_end,
                        ),
                    },
                    "oracle_source_state": {
                        "canonical_start_parent_commit": f"parent-{position}",
                        "canonical_start_parent_tree": f"parent-tree-{position}",
                        "canonical_start_commit": start,
                        "canonical_start_tree": f"tree-{start}",
                    },
                    "oracle_injection_parent_to_canonical_start": {
                        "changed_paths": [TEST_PATH]
                    },
                    "environment_injection_canonical_start_to_runnable_start": {
                        "changed_paths": []
                    },
                }
            )

        probe = {
            "schema_version": 1,
            "workspace": WORKSPACE,
            "retained_id": RETAINED,
            "entry_id": ENTRY,
            "exit_id": EXIT,
            "source_order": [ENTRY, EXIT],
            "sources": sources,
            "candidates": [
                {
                    "test_id": TEST_ID,
                    "candidate_sha256": "f" * 64,
                    "unresolved_outer_evidence": {
                        "entry_start": None,
                        "exit_end": "pass",
                        "reason": "needs rerun",
                    },
                    "definitions": {
                        "a_start": _definition(a_start, "ras", False),
                        "a_end": _definition(a_end, "rae", False),
                        "b_start": _definition(b_start, "rbs", True),
                        "b_end": _definition(b_end, "rbe", True),
                    },
                }
            ],
        }
        probe_path = root / "probe.json"
        probe_path.write_text(json.dumps(probe), encoding="utf-8")
        return argparse.Namespace(
            dataset=dataset,
            sif_manifest=sif_manifest,
            probe=probe_path,
            destination_root=destination,
            output_dir=output,
            attempts=3,
            workers=4,
            timeout=60,
            missing_only=False,
            preflight_only=True,
        )

    def test_preflight_writes_two_endpoints_without_invoking_apptainer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.make_fixture(Path(tmp))
            with mock.patch.object(
                rerun.subprocess, "run", side_effect=AssertionError("must not execute")
            ):
                manifest = rerun.execute(args)
            self.assertEqual(manifest["status"], "preflight")
            self.assertEqual(set(manifest["endpoints"]), set(rerun.ENDPOINT_KEYS))
            for endpoint in rerun.ENDPOINT_KEYS:
                self.assertEqual(
                    len(manifest["endpoints"][endpoint]["attempt_plans"]), 3
                )
                argv = manifest["endpoints"][endpoint]["attempt_plans"][0][
                    "apptainer_argv"
                ]
                self.assertEqual(
                    argv[1:8],
                    [
                        "exec",
                        "--writable-tmpfs",
                        "--cleanenv",
                        "--no-home",
                        "--pwd",
                        "/testbed",
                        "--bind",
                    ],
                )
            persisted = json.loads((args.output_dir / "manifest.json").read_text())
            self.assertEqual(persisted["inputs"]["probe"]["sha256"], _sha(args.probe))
            self.assertEqual(persisted["mode"], "both_outer_endpoints")
            fallbacks = [
                item
                for item in persisted["oracle_extractions"]
                if item["patch_kind"] == rerun.CROSS_SIF_PATCH_KIND
            ]
            self.assertEqual(len(fallbacks), 1)
            self.assertEqual(fallbacks[0]["target_endpoint"], "entry_start")
            self.assertEqual(
                fallbacks[0]["base_sif"],
                str(args.destination_root / WORKSPACE / f"{ENTRY}.sif"),
            )
            self.assertIn("base_snapshot", fallbacks[0]["plan"])
            self.assertTrue(
                persisted["endpoints"]["entry_start"]["selected_oracle_ids"]
            )
            self.assertEqual(
                persisted["endpoints"]["exit_end"]["selected_oracle_ids"], []
            )
            implementation = persisted["inputs"]["implementation"]
            self.assertEqual(
                implementation["runner"]["sha256"],
                _sha(Path(rerun.__file__)),
            )
            self.assertEqual(
                implementation["official_report_parser"]["sha256"],
                _sha(rerun.REPORT_PARSER_PATH),
            )
            self.assertEqual(
                implementation["maven_surefire_xml_utils"]["sha256"],
                _sha(rerun.MAVEN_SUREFIRE_UTIL_PATH),
            )
            dependencies = implementation[
                "official_report_parser_direct_dependencies"
            ]
            self.assertEqual(
                set(dependencies), set(rerun.REPORT_PARSER_DEPENDENCY_PATHS)
            )
            for name, path in rerun.REPORT_PARSER_DEPENDENCY_PATHS.items():
                self.assertEqual(dependencies[name]["sha256"], _sha(path))

    def test_same_sif_fallback_retains_single_image_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.make_fixture(Path(tmp))
            probe = json.loads(args.probe.read_text())
            sources = {source["position"]: source for source in probe["sources"]}
            candidates = {
                candidate["test_id"]: candidate for candidate in probe["candidates"]
            }
            records = rerun.read_jsonl(args.sif_manifest)
            records[1]["destination_rel"] = records[0]["destination_rel"]
            endpoint_path = args.destination_root / records[0]["destination_rel"]
            endpoint = rerun.EndpointSpec(
                key="entry_start",
                source_position="A",
                source_id=ENTRY,
                state_name="start",
                state_key="a_start",
                requested_ref=f"milestone-{ENTRY}-start",
                runnable_commit="ras",
                canonical_commit="a" * 40,
                sif_path=endpoint_path,
                sif_manifest_record=records[0],
                probe_source=sources["A"],
            )
            specs = rerun.build_fallback_oracle_specs(
                candidates=candidates,
                owners={TEST_ID: (EXIT,)},
                sources=sources,
                endpoints={"entry_start": endpoint},
                sif_records=records,
                destination_root=args.destination_root,
                workspace=WORKSPACE,
                oracle_dir=args.output_dir / "oracles",
            )
            self.assertEqual(len(specs), 1)
            self.assertEqual(
                specs[0].patch_kind,
                "endpoint_tree_to_owner_canonical_start",
            )
            self.assertIsNone(specs[0].base_sif)

    def test_cross_sif_snapshot_synthesis_is_exact_and_binary_safe(self) -> None:
        binary_path = "tests/fixtures/payload.bin"

        def write_archive(path: Path, payload: bytes, mode: int = 0o644) -> None:
            with tarfile.open(path, "w") as archive:
                info = tarfile.TarInfo(binary_path)
                info.mode = mode
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_archive = root / "base.tar"
            target_archive = root / "target.tar"
            # Real git archives in the milestone SIFs can expose a legal
            # 100644 blob as 0664 due to the image's archive umask.
            write_archive(base_archive, b"before\x00test\n", 0o664)
            write_archive(target_archive, b"after\x00test\n")
            base_root = root / "base"
            target_root = root / "target"
            base_meta = rerun.validate_snapshot_archive(
                base_archive, [binary_path], base_root
            )
            target_meta = rerun.validate_snapshot_archive(
                target_archive, [binary_path], target_root
            )
            spec = rerun.OracleSpec(
                oracle_id="cross",
                source_id=EXIT,
                source_position="B",
                source_sif=root / "owner.sif",
                canonical_start="c" * 40,
                canonical_start_parent="",
                paths=(binary_path,),
                candidate_ids=(TEST_ID,),
                patch_file=root / "oracle.patch",
                patch_kind=rerun.CROSS_SIF_PATCH_KIND,
                base_commit="a" * 40,
                target_endpoint="entry_start",
                base_sif=root / "endpoint.sif",
                base_requested_ref=f"milestone-{ENTRY}-start",
            )
            synthesis = rerun.synthesize_cross_sif_patch(
                spec=spec,
                base_snapshot_root=base_root,
                target_snapshot_root=target_root,
                base_snapshot=base_meta,
                target_snapshot=target_meta,
                scratch_root=root / "scratch",
            )
            self.assertEqual(synthesis["observed_paths"], [binary_path])
            self.assertEqual(base_meta["files"][0]["bytes"], 12)
            self.assertEqual(target_meta["files"][0]["bytes"], 11)
            self.assertIn("GIT binary patch", spec.patch_file.read_text())

    def test_rust_cross_sif_projects_only_exact_test_functions(self) -> None:
        path = "tests/exact.rs"
        base_source = '''use crate::{helper, ResultType};

fn helper_with_literals() {
    let _ = r###"braces { } and /* comments */"###;
    // A brace in a comment must not affect extraction: }
    /* nested { /* block */ } */
}
'''
        target_source = base_source + '''
#[test]
fn first_exact() -> ResultType {
    let value = "a } brace";
    let raw = r###"macro_like!(#[test] fn fake() { })"###;
    /* delimiters in comments do not nest the projected item: ([{ */
    let _ = raw;
    helper(value)
}

#[ignore = "kept with the item"]
#[test]
fn second_exact() {
    assert_eq!('{', '{');
}

#[test]
fn unrelated_new_test() {
    unsupported_new_api();
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_root = root / "base"
            target_root = root / "target"
            _write_tree_file(base_root, path, base_source)
            _write_tree_file(target_root, path, target_source)
            spec = _rust_oracle_spec(
                root,
                ("module::first_exact", "module::second_exact"),
                (path,),
            )
            synthesis = rerun.synthesize_cross_sif_patch(
                spec=spec,
                base_snapshot_root=base_root,
                target_snapshot_root=target_root,
                base_snapshot=_snapshot_meta(base_root, [path]),
                target_snapshot=_snapshot_meta(target_root, [path]),
                scratch_root=root / "scratch",
            )
            projection = synthesis["projection"]
            self.assertEqual(projection["mode"], "rust_exact_test_function_append")
            self.assertEqual(
                [item["candidate_id"] for item in projection["functions"]],
                ["module::first_exact", "module::second_exact"],
            )
            self.assertEqual(
                projection["functions_canonical_sha256"],
                rerun.canonical_json_hash(projection["functions"]),
            )
            self.assertEqual(projection["files"][0]["appended_functions"], [
                "module::first_exact",
                "module::second_exact",
            ])
            patch = spec.patch_file.read_text(encoding="utf-8")
            self.assertIn("fn first_exact", patch)
            self.assertIn("fn second_exact", patch)
            self.assertNotIn("unrelated_new_test", patch)
            for function in projection["functions"]:
                self.assertEqual(function["target_definition_count"], 1)
                self.assertEqual(function["base_definition_count"], 0)
                self.assertRegex(function["source_sha256"], r"^[0-9a-f]{64}$")
                self.assertLess(
                    function["source_span"]["start_byte"],
                    function["source_span"]["end_byte"],
                )
                self.assertLess(
                    function["projected_span"]["start_byte"],
                    function["projected_span"]["end_byte"],
                )
                self.assertEqual(
                    function["projected_source_sha256"],
                    function["source_sha256"],
                )

    def test_rust_exact_projection_rejects_unsafe_definitions(self) -> None:
        path = "tests/exact.rs"
        cases = {
            "missing": ("fn helper() {}\n", "owner target function definitions"),
            "duplicate": (
                "#[test]\nfn wanted() {}\n#[test]\nfn wanted() {}\n",
                "2 owner target function definitions",
            ),
            "non_test": ("fn wanted() {}\n", "exactly one #\\[test\\]"),
            "duplicate_test_attribute": (
                "#[test]\n#[test]\nfn wanted() {}\n",
                "exactly one #\\[test\\]",
            ),
            "nested": (
                "mod nested { #[test] fn wanted() {} }\n",
                "not a top-level test function",
            ),
            "paren_macro_token_tree": (
                "some_macro!(#[test] fn wanted() {});\n",
                "not a top-level test function",
            ),
            "bracket_macro_token_tree": (
                "some_macro![#[test] fn wanted() {}];\n",
                "not a top-level test function",
            ),
            "malformed_string": (
                '#[test]\nfn wanted() { let value = "unterminated; }\n',
                "unterminated Rust string literal",
            ),
            "malformed_comment": (
                "#[test]\nfn wanted() { /* unterminated }\n",
                "unterminated Rust block comment",
            ),
            "malformed_brace": (
                "#[test]\nfn wanted() { assert!(true);\n",
                "unclosed Rust delimiter",
            ),
            "malformed_paren": (
                "some_macro!(#[test] fn wanted() {}\n",
                "unclosed Rust delimiter",
            ),
            "malformed_bracket": (
                "some_macro![#[test] fn wanted() {}\n",
                "unclosed Rust delimiter",
            ),
        }
        for name, (target_source, error) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                base_root = root / "base"
                target_root = root / "target"
                repository = root / "repository"
                repository.mkdir()
                _write_tree_file(base_root, path, "fn existing() {}\n")
                _write_tree_file(target_root, path, target_source)
                spec = _rust_oracle_spec(root, ("module::wanted",), (path,))
                with self.assertRaisesRegex(rerun.EvidenceError, error):
                    rerun.build_rust_exact_function_projection(
                        spec=spec,
                        base_snapshot_root=base_root,
                        target_snapshot_root=target_root,
                        repository=repository,
                        base_records={path: _snapshot_meta(base_root, [path])["files"][0]},
                        target_records={path: _snapshot_meta(target_root, [path])["files"][0]},
                    )

    def test_rust_exact_projection_rejects_ambiguous_paths_and_existing_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = ("tests/one.rs", "tests/two.rs")
            base_root = root / "base"
            target_root = root / "target"
            repository = root / "repository"
            repository.mkdir()
            for path in paths:
                _write_tree_file(base_root, path, "fn existing() {}\n")
                _write_tree_file(target_root, path, "#[test]\nfn wanted() {}\n")
            spec = _rust_oracle_spec(root, ("module::wanted",), paths)
            with self.assertRaisesRegex(rerun.EvidenceError, "2 owner target"):
                rerun.build_rust_exact_function_projection(
                    spec=spec,
                    base_snapshot_root=base_root,
                    target_snapshot_root=target_root,
                    repository=repository,
                    base_records={
                        record["path"]: record
                        for record in _snapshot_meta(base_root, list(paths))["files"]
                    },
                    target_records={
                        record["path"]: record
                        for record in _snapshot_meta(target_root, list(paths))["files"]
                    },
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_root = root / "base"
            target_root = root / "target"
            repository = root / "repository"
            repository.mkdir()
            _write_tree_file(base_root, path := "tests/exact.rs", "fn wanted() {}\n")
            _write_tree_file(target_root, path, "#[test]\nfn wanted() {}\n")
            spec = _rust_oracle_spec(root, ("module::wanted",), (path,))
            with self.assertRaisesRegex(rerun.EvidenceError, "already has 1"):
                rerun.build_rust_exact_function_projection(
                    spec=spec,
                    base_snapshot_root=base_root,
                    target_snapshot_root=target_root,
                    repository=repository,
                    base_records={path: _snapshot_meta(base_root, [path])["files"][0]},
                    target_records={path: _snapshot_meta(target_root, [path])["files"][0]},
                )

    def test_rust_cross_sif_snapshot_tamper_fails_closed(self) -> None:
        path = "tests/exact.rs"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_root = root / "base"
            target_root = root / "target"
            _write_tree_file(base_root, path, "fn existing() {}\n")
            _write_tree_file(target_root, path, "#[test]\nfn wanted() {}\n")
            base_meta = _snapshot_meta(base_root, [path])
            target_meta = _snapshot_meta(target_root, [path])
            _write_tree_file(target_root, path, "#[test]\nfn wanted() { tampered(); }\n")
            spec = _rust_oracle_spec(root, ("module::wanted",), (path,))
            with self.assertRaisesRegex(rerun.EvidenceError, "hash mismatch"):
                rerun.synthesize_cross_sif_patch(
                    spec=spec,
                    base_snapshot_root=base_root,
                    target_snapshot_root=target_root,
                    base_snapshot=base_meta,
                    target_snapshot=target_meta,
                    scratch_root=root / "scratch",
                )

    def test_cross_sif_snapshot_missing_or_extra_paths_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = root / "empty.tar"
            with tarfile.open(empty, "w"):
                pass
            with self.assertRaisesRegex(rerun.EvidenceError, "missing allowed paths"):
                rerun.validate_snapshot_archive(empty, [TEST_PATH], root / "missing")

            extra = root / "extra.tar"
            with tarfile.open(extra, "w") as archive:
                for name in (TEST_PATH, "src/lib.rs"):
                    data = b"x"
                    info = tarfile.TarInfo(name)
                    info.mode = 0o644
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            with self.assertRaisesRegex(rerun.EvidenceError, "outside the allowlist"):
                rerun.validate_snapshot_archive(extra, [TEST_PATH], root / "extra")

    def test_cross_sif_generated_shells_are_valid_bash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = rerun.OracleSpec(
                oracle_id="cross",
                source_id=EXIT,
                source_position="B",
                source_sif=root / "owner.sif",
                canonical_start="c" * 40,
                canonical_start_parent="",
                paths=(TEST_PATH,),
                candidate_ids=(TEST_ID,),
                patch_file=root / "oracle.patch",
                patch_kind=rerun.CROSS_SIF_PATCH_KIND,
                base_commit="a" * 40,
                target_endpoint="entry_start",
                base_sif=root / "endpoint.sif",
                base_requested_ref=f"milestone-{ENTRY}-start",
            )
            _, base_shell = rerun.snapshot_archive_command(
                sif=spec.base_sif,
                commit=spec.base_commit,
                paths=spec.paths,
                archive_path=root / "base.tar",
                output_dir=root,
            )
            _, runnable_shell = rerun.runnable_snapshot_archive_command(
                sif=spec.base_sif,
                requested_ref=spec.base_requested_ref,
                runnable_commit=spec.base_commit,
                paths=spec.paths,
                archive_path=root / "runnable.tar",
                output_dir=root,
            )
            _, apply_shell, _ = rerun.cross_sif_apply_check_command(
                spec, root, preflight=True
            )
            self.assertIn("apply_patches.sh", runnable_shell)
            self.assertIn(spec.base_requested_ref, runnable_shell)
            for shell in (base_shell, runnable_shell, apply_shell):
                checked = subprocess.run(
                    ["bash", "-n"], input=shell, text=True, capture_output=True
                )
                self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn("git clean -fd", runnable_shell)
            self.assertIn("git clean -fd", apply_shell)
            self.assertIn("base_snapshot_runtime_env.txt", runnable_shell)
            self.assertIn("runtime_env.txt", apply_shell)

    def test_cross_sif_sidecar_detects_changed_post_setup_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / TEST_PATH
            source.parent.mkdir(parents=True)
            source.write_bytes(b"stable base\n")
            base_sif = root / "endpoint.sif"
            base_sif.write_bytes(b"sif")
            patch_file = root / "oracle.patch"
            patch_file.write_text(
                "diff --git a/tests/regression.rs b/tests/regression.rs\n",
                encoding="utf-8",
            )
            spec = rerun.OracleSpec(
                oracle_id="cross",
                source_id=EXIT,
                source_position="B",
                source_sif=root / "owner.sif",
                canonical_start="c" * 40,
                canonical_start_parent="",
                paths=(TEST_PATH,),
                candidate_ids=(TEST_ID,),
                patch_file=patch_file,
                patch_kind=rerun.CROSS_SIF_PATCH_KIND,
                base_commit="a" * 40,
                target_endpoint="entry_start",
                base_sif=base_sif,
                base_requested_ref=f"milestone-{ENTRY}-start",
            )
            files = [
                {
                    "path": TEST_PATH,
                    "mode": "0644",
                    "bytes": source.stat().st_size,
                    "sha256": _sha(source),
                }
            ]
            payload, record = rerun.write_cross_sif_base_sidecar(
                spec=spec,
                base_snapshot={
                    "archive": {"path": "base.tar", "bytes": 1, "sha256": "1" * 64},
                    "files": files,
                    "files_canonical_sha256": rerun.canonical_json_hash(files),
                },
                base_extraction={"shell_sha256": "2" * 64},
            )
            bound = rerun.bind_cross_sif_base_sidecar(spec)
            self.assertEqual(bound.base_worktree_sidecar_sha256, record["sha256"])
            self.assertEqual(payload["files_canonical_sha256"], rerun.canonical_json_hash(files))

            lines, variable = rerun.cross_sif_base_verification_lines(
                bound,
                sidecar_path=str(bound.base_worktree_sidecar),
                verification_log=str(root / "verify.log"),
            )
            script = "\n".join(
                ["set -u", *lines, f'[ "${variable}" -eq 1 ]']
            )
            good = subprocess.run(
                ["bash", "-c", script], cwd=root, text=True, capture_output=True
            )
            self.assertEqual(good.returncode, 0, good.stderr)
            source.write_bytes(b"nondeterministic setup\n")
            bad = subprocess.run(
                ["bash", "-c", script], cwd=root, text=True, capture_output=True
            )
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("base file SHA-256 mismatch", (root / "verify.log").read_text())
            patch_file.write_text("tampered patch\n", encoding="utf-8")
            attempt = root / "attempt"
            attempt.mkdir()
            with self.assertRaisesRegex(rerun.EvidenceError, "patch changed"):
                rerun._copy_oracles_into_attempt([bound], attempt)

    def test_probe_candidate_set_must_equal_current_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.make_fixture(Path(tmp))
            probe = json.loads(args.probe.read_text())
            probe["candidates"].append(
                {
                    "test_id": "extra::test",
                    "unresolved_outer_evidence": {
                        "entry_start": None,
                        "exit_end": "pass",
                    },
                }
            )
            args.probe.write_text(json.dumps(probe))
            with self.assertRaisesRegex(rerun.EvidenceError, "candidate set"):
                rerun.execute(args)

    def test_output_inside_dataset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = self.make_fixture(Path(tmp))
            args.output_dir = args.dataset / "evidence"
            with self.assertRaisesRegex(rerun.EvidenceError, "outside"):
                rerun.execute(args)

    def test_oracle_path_guard_rejects_production_paths(self) -> None:
        safe = [
            "tests/regression.rs",
            "src/test/java/example/ThingTest.java",
            "test/unit/foo-test.ts",
            "crates/foo/tests/fixtures/input.txt",
        ]
        for path in safe:
            self.assertTrue(rerun.is_recognized_test_or_fixture_path(path), path)
        unsafe = [
            "src/lib.rs",
            "src/main/java/example/Thing.java",
            "/tests/test_x.py",
            "tests/../src/lib.rs",
        ]
        for path in unsafe:
            self.assertFalse(rerun.is_recognized_test_or_fixture_path(path), path)
        with self.assertRaisesRegex(rerun.EvidenceError, "outside the allowlist"):
            rerun.validate_oracle_patch(
                "diff --git a/src/lib.rs b/src/lib.rs\n", ["tests/test_x.rs"]
            )

    def test_stability_requires_all_identical_gradeable_attempts(self) -> None:
        def attempts(*outcomes: str) -> list[dict]:
            return [
                {
                    "candidates": {
                        TEST_ID: {"status": "collected", "outcome": outcome}
                    }
                }
                for outcome in outcomes
            ]

        stable = rerun.summarize_candidate_attempts(
            TEST_ID, attempts("failed", "failed", "failed"), 3, {"status": "portable"}
        )
        self.assertEqual((stable["disposition"], stable["outcome"]), ("stable", "failed"))
        flaky = rerun.summarize_candidate_attempts(
            TEST_ID, attempts("failed", "passed", "failed"), 3, {"status": "portable"}
        )
        self.assertEqual(flaky["disposition"], "flaky")
        skipped = rerun.summarize_candidate_attempts(
            TEST_ID, attempts("skipped", "skipped", "skipped"), 3, {"status": "portable"}
        )
        self.assertEqual(skipped["disposition"], "unresolved")

    def test_six_targeted_strategies(self) -> None:
        cases = [
            (
                WORKSPACE,
                RETAINED,
                [TEST_ID],
                "ripgrep_default_and_pcre2_leaf",
                2,
            ),
            (
                "element-hq_element-web_v1.11.95_v1.11.97",
                "feature_enhancements",
                [
                    "test/unit-tests/stores/notifications/RoomNotificationState-test.ts::"
                    "RoomNotificationState > computed attributes > should notify"
                ],
                "element_single_jest_file",
                1,
            ),
            (
                "navidrome_navidrome_v0.57.0_v0.58.0",
                "milestone_003_sub-04",
                [
                    "github.com/navidrome/navidrome/persistence::AlbumRepository > "
                    "Participant Foreign Key Handling > handles invalid IDs"
                ],
                "navidrome_focused_persistence_ginkgo",
                1,
            ),
            (
                "nushell_nushell_0.106.0_0.108.0",
                "milestone_core_development.4",
                ["commands::mut_::mut_path_operator_assign_should_error_enforce_runtime"],
                "nushell_core4_exact_cargo",
                1,
            ),
            (
                "nushell_nushell_0.106.0_0.108.0",
                "milestone_core_development.2",
                ["commands::break_::break_outside_loop"],
                "nushell_core2_exact_cargo",
                1,
            ),
            (
                "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6",
                "M003.3",
                ["dubbo-plugin/dubbo-mutiny::ManyToOneMethodHandlerTest::testError"],
                "dubbo_mutiny_module_surefire",
                1,
            ),
        ]
        for workspace, retained, identifiers, expected, count in cases:
            with self.subTest(expected):
                strategy, specs = rerun.build_execution_specs(
                    workspace, retained, identifiers, 4
                )
                self.assertEqual(strategy, expected)
                self.assertEqual(len(specs), count)
        _, ripgrep_specs = rerun.build_execution_specs(WORKSPACE, RETAINED, [TEST_ID], 4)
        self.assertNotIn("--exact", ripgrep_specs[0].command)
        self.assertIn("--features pcre2", ripgrep_specs[1].command)

    def test_generated_shells_are_valid_bash_including_surefire(self) -> None:
        endpoint = rerun.EndpointSpec(
            key="exit_end",
            source_position="B",
            source_id="M003.3",
            state_name="end",
            state_key="b_end",
            requested_ref="milestone-M003.3-end",
            runnable_commit="d" * 40,
            canonical_commit="c" * 40,
            sif_path=Path("/tmp/example.sif"),
            sif_manifest_record={},
            probe_source={},
        )
        cases = [
            rerun.build_execution_specs(WORKSPACE, RETAINED, [TEST_ID], 4)[1],
            rerun.build_execution_specs(
                "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6",
                "M003.3",
                ["dubbo-plugin/dubbo-mutiny::ManyToOneMethodHandlerTest::testError"],
                4,
            )[1],
        ]
        for executions in cases:
            shell = rerun.build_attempt_shell(endpoint, executions, [])
            self.assertIn("/usr/local/cargo/bin", shell)
            self.assertIn("runtime_env.txt", shell)
            self.assertIn("export RUSTUP_HOME=/usr/local/rustup", shell)
            checked = subprocess.run(
                ["bash", "-n"], input=shell, text=True, capture_output=True
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_official_surefire_parser_discovers_nested_module_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "payload" / "surefire_reports" / "dubbo-plugin" / "dubbo-mutiny"
            reports.mkdir(parents=True)
            (reports / "TEST-org.example.ExampleTest.xml").write_text(
                """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<testsuite name=\"org.example.ExampleTest\" tests=\"1\" failures=\"0\" errors=\"0\" skipped=\"0\" time=\"0.01\">
  <testcase name=\"testWorks\" classname=\"org.example.ExampleTest\" time=\"0.01\" />
</testsuite>
""",
                encoding="utf-8",
            )
            archive = root / "surefire_reports.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(root / "payload" / "surefire_reports", arcname="surefire_reports")

            parsed = rerun.parse_maven_report(
                root / "missing-console.log", surefire_path=archive
            )
            self.assertEqual(parsed["summary"]["total"], 1)
            self.assertEqual(
                parsed["tests"][0]["nodeid"],
                "dubbo-plugin/dubbo-mutiny::org.example.ExampleTest::testWorks",
            )
            self.assertEqual(parsed["tests"][0]["outcome"], "passed")


if __name__ == "__main__":
    unittest.main()
