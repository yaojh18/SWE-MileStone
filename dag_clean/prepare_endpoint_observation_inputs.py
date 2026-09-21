#!/usr/bin/env python3
"""Prepare one immutable observation plan/catalog before multi-rank execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from run_endpoint_state_tests import (
    git_object_environment,
    prepare_observation_inputs,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    git_env = git_object_environment(args.source_repo, args.state_root / "git_objects")
    plan, catalog, manifest = prepare_observation_inputs(
        state_root=args.state_root,
        source_repo=args.source_repo,
        git_env=git_env,
        output_root=args.output_root,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "target_count": plan["target_count"],
                "alias_count": plan["alias_count"],
                "test_path_union_count": catalog["union_path_count"],
                "maven_reactor_audit_sha256": manifest[
                    "maven_reactor_audit_sha256"
                ],
                "reactor_tree_count": manifest[
                    "maven_reactor_audit_tree_count"
                ],
                "selected_maven_module_bindings": sum(
                    len(row["selected_maven_modules"])
                    for row in catalog["per_tree"]
                ),
                "task_induced_unavailable_oracle_paths": sum(
                    len(row["task_induced_unavailable_oracle_paths"])
                    for row in catalog["per_tree"]
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
