from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_pipeline.build_task_manifests import (
    load_curator_sif_manifest,
    task_record,
)


WORKSPACE = "example_repo_v1_v2"


class CuratorImageTaskManifestTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / WORKSPACE
        repo.mkdir()
        return repo

    def test_task_keeps_curator_and_evaluator_images_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repo(root)
            evaluator = {
                (WORKSPACE, "m1"): {
                    "workspace": WORKSPACE,
                    "milestone_id": "m1",
                    "source": "docker://example/repo:m1-v0.9",
                    "destination_rel": f"{WORKSPACE}/m1.sif",
                }
            }
            curator_record = {
                "workspace": WORKSPACE,
                "milestone_id": "base-offline",
                "image_kind": "repository_curator_base_offline",
                "not_evaluator_authority": True,
                "source": "docker://example/repo:base-offline-v0.9",
                "destination_rel": f"{WORKSPACE}/base-offline.sif",
            }
            record = task_record(
                task_kind="partition",
                workspace=WORKSPACE,
                milestone_id="M1",
                repo_dir=repo,
                sif_records=evaluator,
                curator_sif_records={WORKSPACE: curator_record},
            )
            self.assertEqual(record["sif_role"], "repository_curator_base_offline")
            self.assertEqual(record["sif_manifest_record"], curator_record)
            self.assertEqual(record["evaluator_sif_manifest_record"], evaluator[(WORKSPACE, "m1")])
            self.assertEqual(record["docker_source_id"], "M1")

    def test_curator_manifest_requires_evaluator_disclaimer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "curator.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "workspace": WORKSPACE,
                        "image_kind": "repository_curator_base_offline",
                        "not_evaluator_authority": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "disclaim evaluator authority"):
                load_curator_sif_manifest(path)


if __name__ == "__main__":
    unittest.main()
