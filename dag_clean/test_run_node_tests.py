from __future__ import annotations

import io
import hashlib
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from run_node_tests import (
    DEFAULT_TEST_COMMAND,
    REUSABLE_EVIDENCE_FILES,
    build_test_script,
    discover_test_modules,
    existing_reusable,
    load_test_scope_review,
    runtime_reuse_identity,
    safe_extract,
    scoped_test_command,
    verify_clean_ref,
)


class NodeTestRunnerTests(unittest.TestCase):
    def test_default_maven_execution_is_offline(self) -> None:
        self.assertIn(" -o ", f" {DEFAULT_TEST_COMMAND} ")
        self.assertIn("-DembeddedZookeeperPath=/testbed/.tmp/zookeeper", DEFAULT_TEST_COMMAND)

    def test_default_maven_execution_uses_official_parallelism(self) -> None:
        self.assertIn("-T 8", DEFAULT_TEST_COMMAND)
        self.assertIn("-Dsurefire.forkCount=4", DEFAULT_TEST_COMMAND)
        self.assertIn("-Dsurefire.reuseForks=false", DEFAULT_TEST_COMMAND)
        self.assertIn("-Dsurefire.parallel=none", DEFAULT_TEST_COMMAND)
        self.assertIn("-DenableEmbeddedZookeeper=false", DEFAULT_TEST_COMMAND)
        self.assertIn(
            "-Dzookeeper.connection.address=zookeeper://127.0.0.1:2181",
            DEFAULT_TEST_COMMAND,
        )
        self.assertIn(
            "-Dzookeeper.connection.address.1=zookeeper://127.0.0.1:2181",
            DEFAULT_TEST_COMMAND,
        )
        self.assertIn(
            "-Dzookeeper.connection.address.2=zookeeper://127.0.0.1:2182",
            DEFAULT_TEST_COMMAND,
        )

    def test_node_script_wraps_tests_with_common_services(self) -> None:
        script = build_test_script("mvn -o test")
        self.assertIn("run_test_services.sh", script)
        self.assertIn('start "$service_root"', script)
        self.assertIn('probe "$service_root"', script)
        self.assertIn('trap cleanup_services EXIT', script)
        self.assertIn("test-services.json", script)

    def test_test_scope_selects_nearest_modules_and_omits_no_test_demo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pom.xml").write_text("<project/>", encoding="utf-8")
            tested = root / "library"
            tested.mkdir()
            (tested / "pom.xml").write_text("<project/>", encoding="utf-8")
            test_file = tested / "src/test/java/example/LibraryTest.java"
            test_file.parent.mkdir(parents=True)
            test_file.write_text("class LibraryTest {}", encoding="utf-8")
            nested = tested / "nested"
            nested.mkdir()
            (nested / "pom.xml").write_text("<project/>", encoding="utf-8")
            nested_test = nested / "src/test/resources/fixture.txt"
            nested_test.parent.mkdir(parents=True)
            nested_test.write_text("fixture", encoding="utf-8")
            demo = root / "demo"
            demo.mkdir()
            (demo / "pom.xml").write_text("<project/>", encoding="utf-8")
            demo_source = demo / "src/main/java/example/Demo.java"
            demo_source.parent.mkdir(parents=True)
            demo_source.write_text("class Demo {}", encoding="utf-8")
            scanner = root / "compatibility-scanner"
            scanner.mkdir()
            (scanner / "pom.xml").write_text(
                "<project><dependenciesToScan><dependency>test-jar</dependency></dependenciesToScan></project>",
                encoding="utf-8",
            )

            self.assertEqual(
                discover_test_modules(root),
                ["compatibility-scanner", "library", "library/nested"],
            )

    def test_scoped_command_records_exact_module_closure_request(self) -> None:
        command = scoped_test_command("mvn -o test", ["module-a", "module b"])
        self.assertEqual(command, "mvn -o test -pl 'module-a,module b' -am")

    def test_scope_review_is_bound_to_current_request_subject(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            review = Path(temporary)
            subject = {"policy": "test-modules-plus-dependencies", "count": 59}
            subject_sha = hashlib.sha256(
                json.dumps(subject, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            (review / "request.json").write_text(
                json.dumps(
                    {
                        "review_id": "scope",
                        "subject": subject,
                        "review_subject_sha256": subject_sha,
                    }
                ),
                encoding="utf-8",
            )
            (review / "decision.json").write_text(
                json.dumps(
                    {
                        "decision": "approve_dag_wide_executable_test_module_scope",
                        "review_subject_sha256": subject_sha,
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_test_scope_review(review)
            self.assertEqual(loaded["review_subject_sha256"], subject_sha)

            changed = json.loads((review / "request.json").read_text(encoding="utf-8"))
            changed["subject"]["count"] = 58
            (review / "request.json").write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "fingerprint"):
                load_test_scope_review(review)

    def test_common_service_disables_noncontractual_zookeeper_jmx(self) -> None:
        service = (Path(__file__).parent / "run_dubbo_test_services.sh").read_text(encoding="utf-8")
        self.assertIn("JMXDISABLE=true", service)
        self.assertIn("-XX:-UseContainerSupport", service)
        self.assertIn("ZOOPIDFILE=", service)
        self.assertIn("for port in 2181 2182", service)
        self.assertIn("zookeeper_ready", service)
        self.assertIn('[[ "$response" == "imok" ]]', service)
        self.assertIn("preexisting_listener", service)
        self.assertIn("services_healthy", service)
        self.assertIn('"$consecutive_ready" -ge 20', service)

    def test_resume_requires_exact_identity_and_complete_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = output / "result.json"
            identity = {"clean_sha": "a", "sif_sha256": "b"}
            evidence = {}
            for name, filename in REUSABLE_EVIDENCE_FILES.items():
                path = output / filename
                path.write_text(name, encoding="utf-8")
                evidence[name] = {
                    "filename": filename,
                    "sha256": hashlib.sha256(name.encode()).hexdigest(),
                    "exists": True,
                }
            result.write_text(
                json.dumps({"status": "complete", "identity": identity, "evidence": evidence}),
                encoding="utf-8",
            )
            self.assertTrue(existing_reusable(result, identity))
            self.assertFalse(existing_reusable(result, {"clean_sha": "changed", "sif_sha256": "b"}))
            (output / REUSABLE_EVIDENCE_FILES["maven_log"]).write_text("changed", encoding="utf-8")
            self.assertFalse(existing_reusable(result, identity))
            result.write_text(json.dumps({"status": "build_failure", "identity": identity}), encoding="utf-8")
            self.assertFalse(existing_reusable(result, identity))

    def test_legacy_runtime_identity_remains_full_sif_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sif = Path(temporary) / "runtime.sif"
            sif.write_bytes(b"legacy image")
            identity, provenance = runtime_reuse_identity(sif, None)
            expected = hashlib.sha256(b"legacy image").hexdigest()
            self.assertEqual(identity, {"sif_sha256": expected})
            self.assertEqual(provenance["mode"], "legacy_full_sif_sha256")
            self.assertEqual(provenance["sif_sha256"], expected)

    def test_declared_runtime_identity_ignores_sif_tag_bundle_but_not_runtime_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sif = root / "runtime.sif"
            fingerprint = root / "common-runtime.fingerprint"
            sif.write_bytes(b"image with node tag set A")
            fingerprint.write_bytes(b"common-runtime-v1\n")

            first, provenance = runtime_reuse_identity(sif, fingerprint)
            sif.write_bytes(b"image with a different unrelated node tag set B")
            second, _ = runtime_reuse_identity(sif, fingerprint)
            self.assertEqual(first, second)
            self.assertNotIn("sif_sha256", first)
            self.assertEqual(provenance["mode"], "declared_common_runtime_environment")

            fingerprint.write_bytes(b"common-runtime-v2\n")
            changed, _ = runtime_reuse_identity(sif, fingerprint)
            self.assertNotEqual(first, changed)

    def test_declared_runtime_identity_rejects_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sif = root / "runtime.sif"
            fingerprint = root / "common-runtime.fingerprint"
            sif.write_bytes(b"image")
            fingerprint.touch()
            with self.assertRaisesRegex(RuntimeError, "empty"):
                runtime_reuse_identity(sif, fingerprint)

    def test_clean_ref_is_bound_to_manifest_commit_and_tree(self) -> None:
        expected_sha = "a" * 40
        expected_tree = "b" * 40
        responses = [
            subprocess.CompletedProcess([], 0, stdout=f"{expected_sha}\n".encode(), stderr=b""),
            subprocess.CompletedProcess([], 0, stdout=f"{expected_tree}\n".encode(), stderr=b""),
        ]
        with patch("run_node_tests.run_capture", side_effect=responses) as mocked:
            verify_clean_ref(
                "apptainer",
                Path("common.sif"),
                "dag-clean-posthoist-v1/M001/start",
                expected_sha,
                expected_tree,
                {},
            )
        self.assertEqual(mocked.call_count, 2)

    def test_clean_ref_mismatch_is_rejected_even_with_common_runtime_identity(self) -> None:
        expected_sha = "a" * 40
        expected_tree = "b" * 40
        responses = [
            subprocess.CompletedProcess([], 0, stdout=f"{'c' * 40}\n".encode(), stderr=b""),
            subprocess.CompletedProcess([], 0, stdout=f"{expected_tree}\n".encode(), stderr=b""),
        ]
        with patch("run_node_tests.run_capture", side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                verify_clean_ref(
                    "apptainer",
                    Path("common.sif"),
                    "dag-clean-posthoist-v1/M001/start",
                    expected_sha,
                    expected_tree,
                    {},
                )

    def test_safe_extract_rejects_parent_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "bad.tar"
            with tarfile.open(archive, "w") as handle:
                info = tarfile.TarInfo("../escape")
                content = b"bad"
                info.size = len(content)
                handle.addfile(info, io.BytesIO(content))
            with self.assertRaisesRegex(RuntimeError, "escapes destination"):
                safe_extract(archive, Path(temporary) / "out")


if __name__ == "__main__":
    unittest.main()
