#!/usr/bin/env python3

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "element_web_finalize", HERE / "element_web_finalize.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ElementFinalizeTests(unittest.TestCase):
    def test_review_policy_has_exact_decision_scope(self) -> None:
        policy = json.loads(
            (HERE / "element_web_overlay_policy.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(policy["dockerfile_reviews"]), 19)
        self.assertEqual(len(policy["overlay_groups"]), 2)
        self.assertEqual(
            {
                group["group_id"]
                for group in policy["overlay_groups"]
            },
            {"test-utils-hoist", "dependency-manifest-overlay"},
        )
        dropped = {
            key
            for key, row in policy["review_decisions"].items()
            if row.get("decision") == "drop"
        }
        self.assertEqual(
            dropped,
            {
                "common_untracked_docker_artifacts",
                "stale_playwright_helpers",
                "stale_sliding_sync_tests",
            },
        )
        yarn = policy["common_runtime"]["yarn_cache_policy"]
        self.assertEqual(yarn["decision"], "preserve_per_runtime_source")
        self.assertEqual(yarn["observed_conflict"]["default_mode"], "0644")
        self.assertEqual(
            yarn["observed_conflict"]["milestone_seed_8bb4d44_1_mode"],
            "0666",
        )

    def test_strict_merge_rejects_directory_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            external = root / "external"
            (source / "cache").mkdir(parents=True)
            destination.mkdir()
            external.mkdir()
            (destination / "cache").symlink_to(external, target_is_directory=True)
            with self.assertRaises(MODULE.ElementFinalizeError):
                MODULE.merge_tree(source, destination)

    def test_strict_merge_rejects_mode_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "tool").write_bytes(b"same")
            (destination / "tool").write_bytes(b"same")
            (source / "tool").chmod(0o755)
            (destination / "tool").chmod(0o644)
            with self.assertRaises(MODULE.ElementFinalizeError):
                MODULE.merge_tree(source, destination)


if __name__ == "__main__":
    unittest.main()
