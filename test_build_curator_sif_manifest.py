from __future__ import annotations

import unittest

from build_curator_sif_manifest import build_records


class CuratorSifManifestTests(unittest.TestCase):
    def test_builds_one_base_offline_record_per_repository(self) -> None:
        records = build_records(
            {
                "repositories": [
                    {
                        "workspace": "z_repo_v1_v2",
                        "docker_contract": {
                            "standard_hub_agent_image": "example/z:base-offline-v0.9"
                        },
                    },
                    {
                        "workspace": "a_repo_v1_v2",
                        "docker_contract": {
                            "hub_base_offline_image": "example/a:base-offline-v0.9"
                        },
                    },
                ]
            }
        )
        self.assertEqual([item["workspace"] for item in records], ["a_repo_v1_v2", "z_repo_v1_v2"])
        self.assertEqual([item["index"] for item in records], [0, 1])
        self.assertEqual(records[0]["milestone_id"], "base-offline")
        self.assertEqual(records[0]["source"], "docker://example/a:base-offline-v0.9")
        self.assertTrue(records[0]["not_evaluator_authority"])

    def test_rejects_unpinned_or_non_offline_agent_image(self) -> None:
        with self.assertRaisesRegex(ValueError, "pinned base-offline"):
            build_records(
                {
                    "repositories": [
                        {
                            "workspace": "repo",
                            "docker_contract": {
                                "standard_hub_agent_image": "example/repo:base-v0.9"
                            },
                        }
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
