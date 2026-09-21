import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import maven_testcompile_failure_evidence as evidence


TREE = "a" * 40
LOGGER_PATH = (
    "dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java"
)
LOGGER_IDS = [
    "dubbo-common::org.apache.dubbo.common.logger.LoggerTest::testClassLogger",
    "dubbo-common::org.apache.dubbo.common.logger.LoggerTest::testNamedLogger",
]


def testcompile_log(path: str = f"/testbed/{LOGGER_PATH}") -> str:
    return f"""[INFO] --- compiler:3.14.0:testCompile (default-testCompile) @ dubbo-common ---
[WARNING] /testbed/dubbo-common/src/test/java/WarningTest.java:[1,1] warning only
[ERROR] COMPILATION ERROR :
[INFO] -------------------------------------------------------------
[ERROR] {path}:[55,56] error: no suitable method found for getLogger(String,Class<CAP#1>)
    method LoggerAdapter.getLogger(Class<?>) is not applicable
[INFO] 1 error
[INFO] BUILD FAILURE
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.14.0:testCompile (default-testCompile) on project dubbo-common: Compilation failure
[ERROR] {path}:[55,56] error: no suitable method found for getLogger(String,Class<CAP#1>)
[ERROR] -> [Help 1]
"""


class MavenTestCompileEvidenceTests(unittest.TestCase):
    def test_strict_testcompile_failure_infers_only_pre_enumerated_ids(self):
        result = evidence.analyze_maven_log(
            testcompile_log(),
            catalog_paths=[LOGGER_PATH, "dubbo-common/src/test/java/WarningTest.java"],
            java_test_ids_by_path={LOGGER_PATH: LOGGER_IDS},
        )
        self.assertEqual(
            result["classification"], "task_induced_testcompile_unavailable"
        )
        self.assertTrue(result["evidence_complete"])
        self.assertFalse(result["blocking"])
        self.assertEqual(
            [row["nodeid"] for row in result["inferred_failed_tests"]],
            LOGGER_IDS,
        )
        self.assertEqual(
            result["inferred_test_results"]["summary"],
            {"passed": 0, "failed": 2, "error": 0, "skipped": 0, "total": 2},
        )
        self.assertEqual(result["inferred_test_results"]["modules"], ["dubbo-common"])
        error = result["javac_source_errors"][0]
        self.assertEqual(error["catalog_path"], LOGGER_PATH)
        self.assertEqual(error["source_line"], 55)
        self.assertEqual(error["source_column"], 56)
        self.assertEqual(error["maven_log_line"], 5)
        self.assertEqual(error["duplicate_maven_log_lines"], [10])
        self.assertIn("no suitable method", error["error_summary"])
        inference = result["inferred_failed_tests"][0]["inference_source"]
        self.assertEqual(
            inference["kind"],
            "explicit_javac_source_file_to_pre_enumerated_test_ids",
        )
        self.assertEqual(inference["catalog_path"], LOGGER_PATH)
        self.assertNotIn("WarningTest", json.dumps(result["inferred_failed_tests"]))

    def test_main_compile_is_always_blocking(self):
        main_path = "dubbo-common/src/main/java/org/apache/dubbo/Foo.java"
        log = f"""[ERROR] COMPILATION ERROR :
[ERROR] /testbed/{main_path}:[10,4] error: cannot find symbol
[INFO] 1 error
[INFO] BUILD FAILURE
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.14.0:compile (default-compile) on project dubbo-common: Compilation failure
"""
        result = evidence.analyze_maven_log(
            log,
            catalog_paths=[LOGGER_PATH],
            java_test_ids_by_path={LOGGER_PATH: LOGGER_IDS},
        )
        self.assertEqual(result["classification"], "blocking_build_failure")
        self.assertIn("non_testcompile_failed_goal", result["blocking_reason_codes"])
        self.assertIn(
            "product_or_generated_main_source", result["blocking_reason_codes"]
        )
        self.assertEqual(result["inferred_failed_tests"], [])

    def test_dependency_failure_is_blocking_even_for_testcompile_goal(self):
        log = """[INFO] BUILD FAILURE
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.14.0:testCompile (default-testCompile) on project dubbo-common: Could not resolve dependencies for project org.example:dubbo-common:jar:1.0
[ERROR] Could not find artifact org.example:missing:jar:1.0 in central
"""
        result = evidence.analyze_maven_log(
            log,
            catalog_paths=[LOGGER_PATH],
            java_test_ids_by_path={LOGGER_PATH: LOGGER_IDS},
        )
        self.assertEqual(result["classification"], "blocking_build_failure")
        self.assertIn(
            "dependency_resolution_failure", result["blocking_reason_codes"]
        )
        self.assertIn(
            "testcompile_failure_without_explicit_javac_source_location",
            result["blocking_reason_codes"],
        )

    def test_compiler_runtime_failure_is_blocking_even_with_a_source_location(self):
        log = testcompile_log().replace(
            "Compilation failure\n",
            "Fatal error compiling: release version 99 not supported\n",
        )
        result = evidence.analyze_maven_log(
            log,
            catalog_paths=[LOGGER_PATH],
            java_test_ids_by_path={LOGGER_PATH: LOGGER_IDS},
        )
        self.assertEqual(result["classification"], "blocking_build_failure")
        self.assertIn("environment_failure", result["blocking_reason_codes"])
        self.assertIsNone(result["inferred_test_results"])

    def test_pom_and_environment_failures_are_blocking(self):
        for message, reason in (
            (
                "[ERROR] Some problems were encountered while processing the POMs",
                "pom_model_failure",
            ),
            ("[ERROR] No space left on device", "environment_failure"),
        ):
            with self.subTest(reason=reason):
                result = evidence.analyze_maven_log(
                    f"{message}\n[INFO] BUILD FAILURE\n",
                    catalog_paths=[LOGGER_PATH],
                    java_test_ids_by_path={LOGGER_PATH: LOGGER_IDS},
                )
                self.assertEqual(
                    result["classification"], "blocking_build_failure"
                )
                self.assertIn(reason, result["blocking_reason_codes"])
                self.assertEqual(result["inferred_failed_tests"], [])

    def test_untracked_test_source_cannot_be_inferred(self):
        unknown = "other/src/test/java/org/example/UnknownTest.java"
        result = evidence.analyze_maven_log(
            testcompile_log(f"/testbed/{unknown}"),
            catalog_paths=[LOGGER_PATH],
            java_test_ids_by_path={LOGGER_PATH: LOGGER_IDS},
        )
        self.assertEqual(result["classification"], "blocking_build_failure")
        self.assertIn(
            "test_source_outside_catalog", result["blocking_reason_codes"]
        )
        self.assertEqual(result["inferred_failed_tests"], [])

    def test_cataloged_helper_without_ids_is_blocking(self):
        result = evidence.analyze_maven_log(
            testcompile_log(),
            catalog_paths=[LOGGER_PATH],
            java_test_ids_by_path={},
        )
        self.assertEqual(result["classification"], "blocking_build_failure")
        self.assertIn(
            "catalog_test_source_without_enumerated_java_test_ids",
            result["blocking_reason_codes"],
        )
        self.assertEqual(result["inferred_failed_tests"], [])

    def test_no_terminal_status_fails_closed_and_success_is_not_applicable(self):
        missing = evidence.analyze_maven_log(
            "[INFO] process was killed before Maven completed\n",
            catalog_paths=[LOGGER_PATH],
            java_test_ids_by_path={LOGGER_PATH: LOGGER_IDS},
        )
        self.assertEqual(missing["classification"], "blocking_build_failure")
        self.assertIn(
            "missing_terminal_maven_build_status", missing["blocking_reason_codes"]
        )
        success = evidence.analyze_maven_log(
            "[INFO] BUILD SUCCESS\n",
            catalog_paths=[LOGGER_PATH],
            java_test_ids_by_path={LOGGER_PATH: LOGGER_IDS},
        )
        self.assertEqual(success["classification"], "no_build_failure")
        self.assertFalse(success["inference_allowed"])

        empty_catalog = evidence.analyze_maven_log(
            "[INFO] BUILD SUCCESS\n",
            catalog_paths=[],
            java_test_ids_by_path={},
        )
        self.assertEqual(empty_catalog["classification"], "no_build_failure")
        self.assertEqual(empty_catalog["input_binding"]["catalog_path_count"], 0)

    def test_unprefixed_javac_location_inside_compilation_section_is_explicit(self):
        log = f"""[ERROR] COMPILATION ERROR :
[INFO] -------------------------------------------------------------
/testbed/{LOGGER_PATH}:55:56: error: incompatible types
  required: Logger
[INFO] 1 error
[INFO] BUILD FAILURE
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.14.0:testCompile (default-testCompile) on project dubbo-common: Compilation failure
"""
        result = evidence.analyze_maven_log(
            log,
            catalog_paths=[LOGGER_PATH],
            java_test_ids_by_path={LOGGER_PATH: LOGGER_IDS},
        )
        self.assertEqual(
            result["classification"], "task_induced_testcompile_unavailable"
        )
        self.assertEqual(result["javac_source_errors"][0]["source_line"], 55)
        self.assertEqual(result["javac_source_errors"][0]["source_column"], 56)

    def test_non_testcompile_goal_mixed_with_testcompile_is_blocking(self):
        log = testcompile_log() + (
            "[ERROR] Failed to execute goal org.apache.maven.plugins:"
            "maven-surefire-plugin:3.5.3:test (default-test) on project other: "
            "There are test failures\n"
        )
        result = evidence.analyze_maven_log(
            log,
            catalog_paths=[LOGGER_PATH],
            java_test_ids_by_path={LOGGER_PATH: LOGGER_IDS},
        )
        self.assertEqual(result["classification"], "blocking_build_failure")
        self.assertIn("non_testcompile_failed_goal", result["blocking_reason_codes"])
        self.assertEqual(result["inferred_failed_tests"], [])

    def test_catalog_extraction_file_hash_and_cli_output(self):
        catalog = {
            "schema_version": 1,
            "per_tree": [
                {
                    "tree": TREE,
                    "entries": [
                        {"path": LOGGER_PATH, "oid": "b" * 40, "mode": "100644"}
                    ],
                }
            ],
        }
        self.assertEqual(evidence.catalog_paths_for_tree(catalog, TREE), [LOGGER_PATH])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log_path = root / "maven.log"
            catalog_path = root / "catalog.json"
            ids_path = root / "ids.json"
            output_path = root / "evidence.json"
            raw_log = testcompile_log().encode()
            log_path.write_bytes(raw_log)
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            ids_path.write_text(
                json.dumps({"by_path": {LOGGER_PATH: LOGGER_IDS}}),
                encoding="utf-8",
            )
            code = evidence.main(
                [
                    "--maven-log",
                    str(log_path),
                    "--test-catalog",
                    str(catalog_path),
                    "--tree",
                    TREE,
                    "--java-test-ids",
                    str(ids_path),
                    "--output",
                    str(output_path),
                ]
            )
            self.assertEqual(code, 0)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                result["input_binding"]["maven_log_sha256"],
                hashlib.sha256(raw_log).hexdigest(),
            )
            self.assertEqual(
                result["classification"], "task_induced_testcompile_unavailable"
            )

    def test_mapping_rejects_ambiguous_or_non_catalog_ids(self):
        with self.assertRaisesRegex(evidence.EvidenceInputError, "absent from tree"):
            evidence.normalize_inputs(
                [LOGGER_PATH], {"other/src/test/java/OtherTest.java": ["id"]}
            )
        with self.assertRaisesRegex(evidence.EvidenceInputError, "mapped to both"):
            evidence.normalize_inputs(
                [LOGGER_PATH, "other/src/test/java/OtherTest.java"],
                {
                    LOGGER_PATH: ["duplicate"],
                    "other/src/test/java/OtherTest.java": ["duplicate"],
                },
            )


if __name__ == "__main__":
    unittest.main()
