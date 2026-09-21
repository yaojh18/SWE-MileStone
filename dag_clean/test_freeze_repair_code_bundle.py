#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from freeze_repair_code_bundle import REQUIRED_FILES, freeze


class FreezeRepairCodeBundleTest(unittest.TestCase):
    def test_bundle_contract_includes_pre_execution_test_ownership_audit(self) -> None:
        self.assertIn("audit_executable_test_ownership.py", REQUIRED_FILES)

    def test_bundle_is_content_addressed_and_reusable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "output"
            decisions = source / "manual_decisions" / "dubbo"
            decisions.mkdir(parents=True)
            (source / "helper.py").write_text("print('ok')\n", encoding="utf-8")
            (decisions / "decision.json").write_text("{}\n", encoding="utf-8")
            with patch("freeze_repair_code_bundle.REQUIRED_FILES", ("helper.py",)):
                first = freeze(source, output)
                second = freeze(source, output)
            self.assertEqual(first, second)
            self.assertTrue((first / "BUNDLE_READY").is_file())
            self.assertEqual((first / "helper.py").read_text(), "print('ok')\n")


if __name__ == "__main__":
    unittest.main()
