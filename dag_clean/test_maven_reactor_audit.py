from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from maven_reactor_audit import (
    INVALID_END_ORPHAN,
    LEGITIMATE_TASK_INDUCED_UNAVAILABLE,
    MavenReactorAuditError,
    UnavailableTestDecision,
    audit_filesystem,
    audit_git_index,
    audit_git_tree,
)


ROOT_POM = """\
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modules>
    <module>active</module>
    <module>missing</module>
    <module>${dynamic.module}</module>
  </modules>
</project>
"""


class MavenReactorAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, relative: str, content: str = "fixture\n") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _fixture(self) -> None:
        self._write("pom.xml", ROOT_POM)
        self._write(
            "active/pom.xml",
            "<project><modules><module>../sibling</module></modules></project>\n",
        )
        self._write("active/src/test/java/ActiveTest.java")
        self._write("sibling/pom.xml", "<project/>\n")
        self._write("sibling/src/test/java/SiblingTest.java")
        self._write("detached/pom.xml", "<project/>\n")
        self._write("detached/src/test/java/DetachedTest.java")
        self._write("hoisted/src/test/java/FutureModuleTest.java")

    def test_filesystem_report_covers_reactor_and_structural_test_failures(self) -> None:
        self._fixture()
        report = audit_filesystem(self.root, policy_context={"endpoint_id": "M1:end"})

        self.assertEqual(report["reachable_modules"], [".", "active", "sibling"])
        self.assertEqual(report["unreachable_modules"], ["detached"])
        self.assertEqual(report["unreachable_module_poms"], ["detached/pom.xml"])
        self.assertEqual(
            [(item["declaration"], item["reason"]) for item in report["dangling_module_refs"]],
            [("missing", "target_pom_missing")],
        )
        self.assertEqual(
            [item["declaration"] for item in report["unresolved_module_refs"]],
            ["${dynamic.module}"],
        )
        self.assertEqual(
            [item["path"] for item in report["invalid_end_orphan_tests"]],
            [
                "detached/src/test/java/DetachedTest.java",
                "hoisted/src/test/java/FutureModuleTest.java",
            ],
        )
        self.assertEqual(report["status"], "blocked_maven_reactor")

    def test_caller_policy_can_allow_only_bound_task_induced_unavailability(self) -> None:
        self._fixture()

        def policy(orphan, context):
            if (
                context["endpoint_role"] == "start"
                and orphan["path"] == "hoisted/src/test/java/FutureModuleTest.java"
            ):
                return UnavailableTestDecision(
                    LEGITIMATE_TASK_INDUCED_UNAVAILABLE,
                    "M1 adds the hoisted test's module between START and END",
                    {"milestone_id": "M1", "expected_end_module": "hoisted"},
                )
            return UnavailableTestDecision(
                INVALID_END_ORPHAN,
                "no reviewed transition creates this test's module",
            )

        report = audit_filesystem(
            self.root,
            unavailable_test_policy=policy,
            policy_context={"endpoint_id": "M1:start", "endpoint_role": "start"},
        )
        self.assertEqual(
            [item["path"] for item in report["legitimate_task_induced_unavailable_tests"]],
            ["hoisted/src/test/java/FutureModuleTest.java"],
        )
        self.assertEqual(
            [item["path"] for item in report["invalid_end_orphan_tests"]],
            ["detached/src/test/java/DetachedTest.java"],
        )
        self.assertEqual(
            report["legitimate_task_induced_unavailable_tests"][0]["policy_evidence"],
            {"milestone_id": "M1", "expected_end_module": "hoisted"},
        )

    def test_git_tree_and_alternate_index_observe_different_reactors(self) -> None:
        self._write("pom.xml", "<project><modules><module>a</module></modules></project>\n")
        self._write("a/pom.xml", "<project/>\n")
        self._write("a/src/test/java/ATest.java")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.name", "Test"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "base"], check=True)

        tree_report = audit_git_tree(self.root, "HEAD")
        self.assertEqual(tree_report["reachable_modules"], [".", "a"])
        self.assertEqual(tree_report["source"]["kind"], "git_tree")

        alternate_index = self.root / "alternate.index"
        environment = os.environ.copy()
        environment["GIT_INDEX_FILE"] = str(alternate_index)
        subprocess.run(
            ["git", "-C", str(self.root), "read-tree", "HEAD"],
            env=environment,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "update-index", "--force-remove", "a/pom.xml"],
            env=environment,
            check=True,
        )
        index_report = audit_git_index(self.root, index_file=alternate_index)
        self.assertEqual(index_report["reachable_modules"], ["."])
        self.assertEqual(index_report["unreachable_module_poms"], [])
        self.assertEqual(index_report["dangling_module_refs"][0]["target_pom"], "a/pom.xml")
        self.assertEqual(
            index_report["invalid_end_orphan_tests"][0]["path"],
            "a/src/test/java/ATest.java",
        )
        self.assertEqual(index_report["source"]["kind"], "git_index")

    def test_policy_must_return_a_bound_decision(self) -> None:
        self._write("pom.xml", "<project/>\n")
        self._write("missing/src/test/java/Test.java")
        with self.assertRaisesRegex(
            MavenReactorAuditError, "must return UnavailableTestDecision"
        ):
            audit_filesystem(
                self.root,
                unavailable_test_policy=lambda _orphan, _context: None,  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
