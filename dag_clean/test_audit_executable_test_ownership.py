import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from audit_executable_test_ownership import (
    main,
    orphaned_test_paths,
    reachable_maven_modules,
)


class ExecutableTestOwnershipAuditTest(unittest.TestCase):
    def test_distinguishes_root_module_tests_from_orphaned_nested_tests(self):
        paths = [
            "pom.xml",
            "src/test/java/RootTest.java",
            "good/pom.xml",
            "good/src/test/java/GoodTest.java",
            "removed/src/test/java/OrphanTest.java",
            "removed/src/test/resources/test.xml",
        ]
        self.assertEqual(
            [item["path"] for item in orphaned_test_paths(paths)],
            [
                "removed/src/test/java/OrphanTest.java",
                "removed/src/test/resources/test.xml",
            ],
        )

    def test_cli_writes_bound_failure_and_success_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Test"],
                check=True,
            )
            (repo / "pom.xml").write_text("<project/>\n", encoding="utf-8")
            orphan = repo / "removed/src/test/java/OrphanTest.java"
            orphan.parent.mkdir(parents=True)
            orphan.write_text("class OrphanTest {}\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-qm", "orphan"], check=True
            )
            bad = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()

            (repo / "removed/pom.xml").write_text("<project/>\n", encoding="utf-8")
            (repo / "pom.xml").write_text(
                "<project><modules><module>removed</module></modules></project>\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-qm", "owned"], check=True
            )
            good = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()

            manifest = root / "dag.json"
            manifest.write_text(
                json.dumps(
                    {
                        "workspace": "fixture",
                        "nodes": [
                            {
                                "node_id": "bad:start",
                                "clean_sha": bad,
                                "clean_tree": "bad-tree",
                            },
                            {
                                "node_id": "good:end",
                                "clean_sha": good,
                                "clean_tree": "good-tree",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "audit.json"

            import sys

            old = sys.argv
            try:
                sys.argv = [
                    "audit_executable_test_ownership.py",
                    "--repo",
                    str(repo),
                    "--dag-manifest",
                    str(manifest),
                    "--output",
                    str(output),
                ]
                self.assertEqual(main(), 2)
            finally:
                sys.argv = old

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["affected_nodes"], ["bad:start"])
            self.assertEqual(payload["orphaned_test_count"], 1)
            self.assertEqual(payload["status"], "blocked_orphaned_tests")

    def test_flags_a_test_module_detached_from_root_reactor(self):
        poms = {
            "pom.xml": "<project><modules><module>good</module></modules></project>",
            "good/pom.xml": "<project/>",
            "detached/pom.xml": "<project/>",
        }
        reachable = reachable_maven_modules(poms)
        self.assertEqual(reachable, {".", "good"})
        paths = list(poms) + [
            "good/src/test/java/GoodTest.java",
            "detached/src/test/java/DetachedTest.java",
        ]
        self.assertEqual(
            orphaned_test_paths(paths, reachable),
            [
                {
                    "path": "detached/src/test/java/DetachedTest.java",
                    "module_candidate": "detached",
                    "reason": "test_module_not_reachable_from_root_reactor",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
