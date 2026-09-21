#!/usr/bin/env python3
"""Preflight all audited test deltas against one coherent baseline.

This is a read-only semantic preflight: each milestone/role gets an isolated
temporary Git index initialized from the selected baseline.  Any hunk that
cannot be placed directionally is reported in one pass so reviews do not have
to be discovered through repeated full endpoint preparation attempts.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from prepare_dubbo_endpoint_states import (
    PreparationError,
    _compose_uniform_milestone_deltas,
    _git,
)


def audit_composition(repo: Path, audit_path: Path, baseline_ref: str) -> dict[str, Any]:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    rows = audit.get("milestones")
    if not isinstance(rows, list):
        raise PreparationError("test-contract audit lacks milestone rows")
    results: list[dict[str, Any]] = []
    for row in rows:
        milestone_id = str(row.get("milestone_id", ""))
        for role in ("start", "end"):
            if milestone_id == "M014":
                results.append(
                    {
                        "milestone_id": milestone_id,
                        "role": role,
                        "status": "requires_materialized_m014_review",
                    }
                )
                continue
            with tempfile.TemporaryDirectory(prefix="uniform-compose-") as temporary:
                index_path = Path(temporary) / "index"
                env = {**os.environ, "GIT_INDEX_FILE": str(index_path)}
                _git(repo, "read-tree", baseline_ref, env=env)
                try:
                    composition = _compose_uniform_milestone_deltas(
                        repo=repo,
                        index_env=env,
                        milestone_id=milestone_id,
                        role=role,
                        audit_row=row,
                        m014_review=None,
                    )
                except PreparationError as exc:
                    results.append(
                        {
                            "milestone_id": milestone_id,
                            "role": role,
                            "status": "review_required",
                            "error": str(exc),
                        }
                    )
                else:
                    results.append(
                        {
                            "milestone_id": milestone_id,
                            "role": role,
                            "status": "clean",
                            "operation_count": composition["path_operation_count"],
                        }
                    )
    review_rows = [
        row
        for row in results
        if row["status"] in {
            "review_required",
            "requires_materialized_m014_review",
        }
    ]
    return {
        "kind": "uniform_test_delta_composition_preflight",
        "baseline_ref": baseline_ref,
        "milestone_count": len(rows),
        "endpoint_count": len(results),
        "review_required_count": len(review_rows),
        "review_required": review_rows,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = audit_composition(
        args.repo.resolve(), args.audit.resolve(), args.baseline_ref
    )
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "milestone_count", "endpoint_count", "review_required_count"
    )}, sort_keys=True))
    return 2 if payload["review_required_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
