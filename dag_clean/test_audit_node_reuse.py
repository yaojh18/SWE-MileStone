#!/usr/bin/env python3

from __future__ import annotations

import unittest

import run_node_tests
from audit_node_reuse import expected_identity


class AuditNodeReuseTest(unittest.TestCase):
    def test_expected_identity_matches_runner_contract(self) -> None:
        node = {"node_id": "M001:start", "clean_sha": "commit", "clean_tree": "tree"}
        modules = ["module-a", "module-b"]
        identity = expected_identity(
            node,
            modules=modules,
            test_command=run_node_tests.DEFAULT_TEST_COMMAND,
            runtime_identity_fields={"runtime_fingerprint_sha256": "runtime"},
            parser_sha256="parser",
            scope_review_sha256="scope",
            runner_sha256="runner",
        )
        self.assertEqual(identity["node_id"], node["node_id"])
        self.assertEqual(identity["test_scope_sha256"], run_node_tests.sha256_text("module-a\nmodule-b\n"))
        self.assertEqual(identity["runtime_fingerprint_sha256"], "runtime")
        self.assertIsInstance(identity["test_command_sha256"], str)


if __name__ == "__main__":
    unittest.main()
