from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


PIPELINE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(PIPELINE_ROOT.parent))

from agent_pipeline.synthesize_patches import materialize_subtask_artifacts  # noqa: E402


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class SynthesizePatchTests(unittest.TestCase):
    def test_materialization_writes_expanded_concrete_tests(self) -> None:
        view = load_json("partition_view.json")
        manifest = copy.deepcopy(load_json("partition_output_valid.json"))
        manifest["subtasks"][0]["tests"] = {
            "f2p": {"mode": "explicit", "include_ids": ["tests::alpha"], "exclude_ids": []},
            "n2p": {"mode": "all_original", "include_ids": [], "exclude_ids": ["tests::obsolete"]},
            "p2p": {"mode": "all_original", "include_ids": [], "exclude_ids": []},
        }
        manifest["subtasks"][1]["tests"] = {
            "f2p": {"mode": "explicit", "include_ids": [], "exclude_ids": []},
            "n2p": {"mode": "explicit", "include_ids": ["tests::new_beta"], "exclude_ids": []},
            "p2p": {
                "mode": "all_original",
                "include_ids": ["tests::alpha"],
                "exclude_ids": [],
            },
        }
        units = [
            {"unit_id": "u-001", "source_loc": 140, "diff": "one\n"},
            {"unit_id": "u-002", "source_loc": 160, "diff": "two\n"},
            {"unit_id": "u-003", "source_loc": 120, "diff": "three\n"},
        ]
        original_patch = "".join(unit["diff"] for unit in units)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            view_dir = root / "view"
            output_dir = root / "synthesized"
            view_dir.mkdir()
            (view_dir / "input.json").write_text(
                json.dumps(
                    {
                        "schema_version": view["schema_version"],
                        "task_kind": view["task_kind"],
                        "workspace": view["workspace"],
                        "milestone_id": view["milestone_id"],
                        "input_hash": view["input_hash"],
                    }
                ),
                encoding="utf-8",
            )
            (view_dir / "change_units.jsonl").write_text(
                "".join(json.dumps(unit) + "\n" for unit in units),
                encoding="utf-8",
            )
            (view_dir / "tests.jsonl").write_text(
                "".join(json.dumps(test) + "\n" for test in view["tests"]),
                encoding="utf-8",
            )
            (view_dir / "patch_manifest.json").write_text(
                json.dumps(
                    {"original_patch_sha256": hashlib.sha256(original_patch.encode()).hexdigest()}
                ),
                encoding="utf-8",
            )

            result = materialize_subtask_artifacts(
                view_dir=view_dir,
                manifest=manifest,
                output_dir=output_dir,
            )

            expected_first = {
                "f2p": ["tests::alpha"],
                "n2p": [],
                "p2p": ["tests::regression"],
            }
            expected_second = {
                "f2p": [],
                "n2p": ["tests::new_beta"],
                "p2p": ["tests::regression", "tests::alpha"],
            }
            self.assertEqual(manifest["subtasks"][0]["tests"], expected_first)
            self.assertEqual(manifest["subtasks"][1]["tests"], expected_second)
            self.assertEqual(result["subtasks"][0]["tests"], expected_first)
            self.assertEqual(result["subtasks"][1]["tests"], expected_second)
            self.assertEqual(
                json.loads((output_dir / "01_M100.sub-01" / "tests.json").read_text()),
                expected_first,
            )
            self.assertEqual(
                json.loads((output_dir / "02_M100.sub-02" / "tests.json").read_text()),
                expected_second,
            )


if __name__ == "__main__":
    unittest.main()
