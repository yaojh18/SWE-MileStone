#!/usr/bin/env python3
"""Capture Navidrome evaluator SIFs and audit all runnable endpoint refs.

The capture engine is shared with the already exercised ripgrep pipeline, but
all catalog denominators, image identities, tool probes, and artifact kinds are
set explicitly for the Navidrome DAG.  Python is used only in the outer
container; target images are inspected through POSIX shell and Git.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import capture_ripgrep_endpoints as engine


WORKSPACE = "navidrome_navidrome_v0.57.0_v0.58.0"
EXPECTED_MILESTONES = 10
EXPECTED_GAPS = 8
EXPECTED_EVALUATOR_SIFS = {
    "milestone_001",
    "milestone_002",
    "milestone_003_sub-01",
    "milestone_003_sub-02",
    "milestone_003_sub-03",
    "milestone_003_sub-04",
    "milestone_004",
    "milestone_006",
    "milestone_007",
}


def _rewrite(path: Path, **updates: Any) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(updates)
    engine.write_json_atomic(path, payload)
    return payload


def configure_engine() -> None:
    engine.WORKSPACE = WORKSPACE
    engine.EXPECTED_MILESTONES = EXPECTED_MILESTONES
    engine.EXPECTED_GAPS = EXPECTED_GAPS
    engine.EXPECTED_EVALUATOR_SIFS = EXPECTED_EVALUATOR_SIFS
    engine.CAPTURE_SCRIPT = engine.CAPTURE_SCRIPT.replace(
        "git cargo rustc rustup bash sh cc gcc python3 python",
        "git go node npm yarn bash sh cc gcc python3 python",
    )

    original_capture_one = engine.capture_one
    original_build_consensus = engine.build_consensus
    original_execute = engine.execute

    def capture_one(**kwargs: Any) -> dict[str, Any]:
        result = original_capture_one(**kwargs)
        manifest_path = Path(kwargs["output"]) / "manifest.json"
        return _rewrite(
            manifest_path,
            kind="navidrome_evaluator_sif_capture",
            workspace=WORKSPACE,
        )

    def build_consensus(
        *,
        ids: Sequence[str],
        metadata: Mapping[str, Mapping[str, str]],
        captures: Sequence[Mapping[str, Any]],
        output: Path,
    ) -> dict[str, Any]:
        original_build_consensus(
            ids=ids,
            metadata=metadata,
            captures=captures,
            output=output,
        )
        return _rewrite(
            output / "endpoint_consensus.json",
            kind="navidrome_endpoint_ref_consensus",
            workspace=WORKSPACE,
        )

    def execute(args: argparse.Namespace) -> dict[str, Any]:
        original_execute(args)
        output = args.output.resolve()
        progress = output / "capture_progress.json"
        if progress.is_file():
            _rewrite(progress, kind="navidrome_capture_progress")
        return _rewrite(
            output / "manifest.json",
            kind="navidrome_capture_preflight",
            workspace=WORKSPACE,
        )

    engine.capture_one = capture_one
    engine.build_consensus = build_consensus
    engine.execute = execute


def main(argv: Sequence[str] | None = None) -> int:
    configure_engine()
    try:
        result = engine.execute(engine.parser().parse_args(argv))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "validated" else 42
    except (
        engine.CaptureError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"capture-navidrome-endpoints: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
