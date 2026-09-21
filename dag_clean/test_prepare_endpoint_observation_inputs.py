import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import prepare_endpoint_observation_inputs as prepare


class PrepareEndpointObservationInputsTests(unittest.TestCase):
    def test_cli_reports_reactor_bound_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = {"target_count": 2, "alias_count": 3}
            catalog = {
                "union_path_count": 7,
                "per_tree": [
                    {
                        "selected_maven_modules": ["a", "b"],
                        "task_induced_unavailable_oracle_paths": ["future/A.java"],
                    },
                    {
                        "selected_maven_modules": ["c"],
                        "task_induced_unavailable_oracle_paths": [],
                    },
                ],
            }
            manifest = {
                "status": "validated",
                "maven_reactor_audit_sha256": "a" * 64,
                "maven_reactor_audit_tree_count": 2,
            }
            stdout = io.StringIO()
            with (
                mock.patch.object(prepare, "git_object_environment", return_value={}),
                mock.patch.object(
                    prepare,
                    "prepare_observation_inputs",
                    return_value=(plan, catalog, manifest),
                ),
                contextlib.redirect_stdout(stdout),
            ):
                result = prepare.main(
                    [
                        "--state-root", str(root / "states"),
                        "--source-repo", str(root / "repo"),
                        "--output-root", str(root / "output"),
                    ]
                )
            self.assertEqual(result, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["maven_reactor_audit_sha256"], "a" * 64)
            self.assertEqual(payload["reactor_tree_count"], 2)
            self.assertEqual(payload["selected_maven_module_bindings"], 3)
            self.assertEqual(payload["task_induced_unavailable_oracle_paths"], 1)


if __name__ == "__main__":
    unittest.main()
