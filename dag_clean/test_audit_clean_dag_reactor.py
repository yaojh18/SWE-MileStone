import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import audit_clean_dag_reactor as dag_audit
from maven_reactor_audit import LEGITIMATE_TASK_INDUCED_UNAVAILABLE


def _report(*, status="complete", legitimate=0):
    return {
        "status": status,
        "counts": {
            "legitimate_task_induced_unavailable_tests": legitimate,
        },
        "dangling_module_refs": [],
        "unresolved_module_refs": [],
        "pom_parse_errors": [],
        "invalid_end_orphan_tests": [],
    }


class CleanDagReactorAuditTest(unittest.TestCase):
    def test_local_oracle_paths_are_exact_end_minus_start(self):
        causal = {
            "milestones": {
                "M1": {
                    "start_projection": {"stable": {}, "same": {}},
                    "end_projection": {"same": {}, "new": {}},
                    "local_route_ids": ["route-1"],
                }
            }
        }
        paths, route_ids = dag_audit._local_oracle_paths(causal, "M1")
        self.assertEqual(paths, {"new"})
        self.assertEqual(route_ids, ["route-1"])

    def test_audit_binds_endpoint_and_cross_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "controller_repo" / ".git").mkdir(parents=True)
            (root / "states").mkdir()
            (root / "manifest.json").write_text(
                json.dumps({"status": "validated"}), encoding="utf-8"
            )
            (root / "states" / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "validated",
                        "endpoints": [
                            {"endpoint_id": "M1:start", "combined_tree": "a" * 40}
                        ],
                        "cross_compositions": [
                            {
                                "composition_id": "M1:start-implementation+end-tests",
                                "composition_tree": "b" * 40,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            causal_path = root / "dag_causal_test_projections.json"
            causal_path.write_text(
                json.dumps(
                    {
                        "kind": "dag_causal_test_projections",
                        "decision_sha256": "c" * 64,
                        "milestones": {
                            "M1": {
                                "start_projection": {},
                                "end_projection": {"module/src/test/NewTest.java": {}},
                                "local_route_ids": ["route-1"],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            def fake_audit(_snapshot, unavailable_test_policy=None, policy_context=None):
                if unavailable_test_policy is None:
                    return _report()
                decision = unavailable_test_policy(
                    {"path": "module/src/test/NewTest.java"}, policy_context
                )
                self.assertEqual(
                    decision.disposition, LEGITIMATE_TASK_INDUCED_UNAVAILABLE
                )
                return _report(legitimate=1)

            with mock.patch.object(dag_audit.subprocess, "run"), mock.patch.object(
                dag_audit,
                "snapshot_from_git_tree",
                side_effect=lambda _repo, tree: SimpleNamespace(
                    paths=frozenset(), pom_contents={}, source={"tree": tree}
                ),
            ), mock.patch.object(dag_audit, "audit_snapshot", side_effect=fake_audit):
                result = dag_audit.audit_clean_dag(
                    clean_root=root, causal_projection_path=causal_path
                )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(
                result["denominators"],
                {
                    "endpoint_aliases": 1,
                    "cross_composition_aliases": 1,
                    "total_aliases": 2,
                    "unique_trees": 2,
                    "blocking_aliases": 0,
                    "complete_aliases": 2,
                    "legitimate_task_induced_unavailable_test_paths": 1,
                },
            )


if __name__ == "__main__":
    unittest.main()
