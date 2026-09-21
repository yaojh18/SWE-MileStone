from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Sequence

from agent_pipeline.probe_outer_endpoint_tests import (
    CandidateSpec,
    CommandResult,
    atomic_write_json,
    candidate_spec,
    discover_merge_operations,
    line_is_definition,
    parse_grep_output,
    probe_definition,
    resolve_canonical_state_in_sif,
)


class FakeGitRunner:
    def __init__(self, responses: dict[tuple[str, ...], CommandResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def git(self, sif: Path, args: Sequence[str]) -> CommandResult:
        key = tuple(args)
        self.calls.append(key)
        if key not in self.responses:
            raise AssertionError(f"unexpected Git command for {sif}: {key!r}")
        return self.responses[key]


class CandidateParsingTests(unittest.TestCase):
    def test_framework_specific_test_ids(self) -> None:
        rust = candidate_spec(
            "BurntSushi_ripgrep_14.1.1_15.0.0",
            "regression::r3127_gitignore_allow_unclosed_class",
        )
        self.assertEqual((rust.framework, rust.leaf), ("cargo_rust", "r3127_gitignore_allow_unclosed_class"))

        element = candidate_spec(
            "element-hq_element-web_v1.11.95_v1.11.97",
            "test/unit/a-test.ts::suite > does the thing",
        )
        self.assertEqual(element.path_hint, "test/unit/a-test.ts")
        self.assertEqual(element.title_chain, ("suite", "does the thing"))

        navidrome = candidate_spec(
            "navidrome_navidrome_v0.57.0_v0.58.0",
            "github.com/navidrome/navidrome/persistence::Album > handles rows",
        )
        self.assertEqual(navidrome.path_hint, "persistence")
        self.assertEqual(navidrome.leaf, "handles rows")

        dubbo = candidate_spec(
            "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6",
            "dubbo-plugin/dubbo-mutiny::org.example.ManyToOneTest::testInvoke",
        )
        self.assertEqual(dubbo.module, "dubbo-plugin/dubbo-mutiny")
        self.assertEqual(dubbo.class_name, "org.example.ManyToOneTest")

    def test_definition_lines_include_rust_macro_leaf(self) -> None:
        rust = CandidateSpec("cargo_rust", "r3127_gitignore_allow_unclosed_class")
        self.assertTrue(line_is_definition(rust, "    fn r3127_gitignore_allow_unclosed_class() {"))
        self.assertTrue(line_is_definition(rust, "    r3127_gitignore_allow_unclosed_class,"))
        self.assertTrue(line_is_definition(rust, "rgtest!(r3127_gitignore_allow_unclosed_class, |dir, cmd| {"))
        self.assertFalse(line_is_definition(rust, "assert!(r3127_gitignore_allow_unclosed_class);"))
        self.assertFalse(line_is_definition(rust, "// [ENV-PATCH] fn r3127_gitignore_allow_unclosed_class() {"))

    def test_parse_grep_output_with_tree_prefix(self) -> None:
        commit = "a" * 40
        parsed = parse_grep_output(
            f"{commit}:tests/regression.rs:37:    target,\n", commit
        )
        self.assertEqual(
            parsed,
            [{"path": "tests/regression.rs", "line": 37, "line_text": "    target,"}],
        )


class CanonicalStateTests(unittest.TestCase):
    def test_resolves_below_runnable_environment_commit(self) -> None:
        runnable = "a" * 40
        canonical = "b" * 40
        runner = FakeGitRunner(
            {
                ("rev-parse", "--verify", "milestone-M1-start^{commit}"): CommandResult(0, runnable + "\n"),
                (
                    "log",
                    "--first-parent",
                    "--max-count=32",
                    "--format=%H%x00%s",
                    runnable,
                ): CommandResult(
                    0,
                    f"{runnable}\x00[ENV-PATCH] compiler fix\n"
                    f"{canonical}\x00Start state for M1\n",
                ),
                ("rev-parse", f"{runnable}^{{tree}}"): CommandResult(0, "c" * 40 + "\n"),
                ("rev-parse", f"{canonical}^{{tree}}"): CommandResult(0, "d" * 40 + "\n"),
            }
        )
        resolved = resolve_canonical_state_in_sif(
            runner, Path("image.sif"), "milestone-M1-start", "M1", "start"
        )
        self.assertEqual(resolved["runnable_commit"], runnable)
        self.assertEqual(resolved["canonical_commit"], canonical)
        self.assertEqual(
            resolved["stripped_environment_commits"],
            [{"commit": runnable, "subject": "[ENV-PATCH] compiler fix"}],
        )


class DefinitionProbeTests(unittest.TestCase):
    def test_zero_literal_match_in_existing_element_path_is_ambiguous(self) -> None:
        commit = "a" * 40
        blob = "b" * 40
        path = "test/unit/a-test.ts"
        runner = FakeGitRunner(
            {
                ("ls-tree", "-r", "--name-only", commit, "--", path): CommandResult(0, path + "\n"),
                ("rev-parse", f"{commit}:{path}"): CommandResult(0, blob + "\n"),
                (
                    "grep",
                    "-n",
                    "-I",
                    "-F",
                    "-e",
                    "does the thing",
                    commit,
                    "--",
                    path,
                ): CommandResult(1, ""),
            }
        )
        result = probe_definition(
            runner,
            Path("element.sif"),
            commit,
            "element-hq_element-web_v1.11.95_v1.11.97",
            f"{path}::suite > does the thing",
        )
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["search_path_blobs"], {path: blob})

    def test_dubbo_qualified_class_records_line_and_blob(self) -> None:
        commit = "a" * 40
        blob = "b" * 40
        module = "dubbo-plugin/dubbo-mutiny"
        path = (
            f"{module}/src/test/java/org/example/ManyToOneMethodHandlerTest.java"
        )
        test_id = (
            f"{module}::org.example.ManyToOneMethodHandlerTest::testInvoker"
        )
        runner = FakeGitRunner(
            {
                ("ls-tree", "-r", "--name-only", commit, "--", module): CommandResult(0, path + "\n"),
                (
                    "grep",
                    "-n",
                    "-I",
                    "-E",
                    "-e",
                    r"testInvoker[[:space:]]*\(",
                    commit,
                    "--",
                    path,
                ): CommandResult(0, f"{commit}:{path}:42:    public void testInvoker() {{\n"),
                ("rev-parse", f"{commit}:{path}"): CommandResult(0, blob + "\n"),
            }
        )
        result = probe_definition(
            runner,
            Path("dubbo.sif"),
            commit,
            "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6",
            test_id,
        )
        self.assertEqual(result["status"], "present")
        self.assertEqual(result["matches"][0]["line"], 42)
        self.assertEqual(result["matches"][0]["blob"], blob)


class InputAndOutputTests(unittest.TestCase):
    def test_discovers_ordered_two_source_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dataset = Path(temporary) / "dataset"
            provenance_dir = dataset / "repo" / "merge_provenance"
            provenance_dir.mkdir(parents=True)
            payload = {
                "workspace": "repo",
                "retained_id": "B",
                "ordered_source_ids": ["A", "B"],
                "patch_segments": [
                    {"milestone_id": "A"},
                    {"milestone_id": "B"},
                ],
                "test_contract": {
                    "logical_composition": {
                        "unresolved_outer_evidence": [
                            {"test_id": "module::test", "entry_start": None, "exit_end": "pass"}
                        ]
                    }
                },
            }
            (provenance_dir / "B.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            operations = discover_merge_operations(
                dataset, expected_operations=None, expected_candidates=None
            )
            self.assertEqual(len(operations), 1)
            self.assertEqual(operations[0].candidates[0]["test_id"], "module::test")

    def test_atomic_json_writer_leaves_complete_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "nested" / "probe.json"
            atomic_write_json(output, {"schema_version": 1, "value": "ok"})
            self.assertEqual(json.loads(output.read_text()), {"schema_version": 1, "value": "ok"})
            self.assertEqual(list(output.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
