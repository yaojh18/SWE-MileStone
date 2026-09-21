#!/usr/bin/env python3
"""Capture the 13 Nushell evaluator SIFs with shell-only target probes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence

import capture_gozero_endpoints as adapter


WORKSPACE = "nushell_nushell_0.106.0_0.108.0"
EXPECTED_MILESTONES = 21
EXPECTED_GAPS = 41
EXPECTED_EVALUATOR_SIFS = {
    "milestone_core_development.1",
    "milestone_core_development.2",
    "milestone_core_development.3",
    "milestone_core_development.4",
    "milestone_g01_48bca0a",
    "milestone_g02_a647707",
    "milestone_g02_da9615f",
    "milestone_g04_1ddae02",
    "milestone_g04_ca0e961",
    "milestone_g05_0b8531e",
    "milestone_g05_be6e868",
    "milestone_m02_parser",
    "milestone_m08_docs",
}


def configure() -> None:
    adapter.WORKSPACE = WORKSPACE
    adapter.EXPECTED_MILESTONES = EXPECTED_MILESTONES
    adapter.EXPECTED_GAPS = EXPECTED_GAPS
    adapter.EXPECTED_EVALUATOR_SIFS = EXPECTED_EVALUATOR_SIFS
    adapter.CAPTURE_SCRIPT = adapter.CAPTURE_SCRIPT.replace(
        "git go bash sh cc gcc python3 python",
        "git cargo rustc rustup nu bash sh cc gcc python3 python",
    )
    adapter.BASE_CAPTURE_SCRIPT = (
        adapter.BASE_CAPTURE_SCRIPT
        .replace("gozero-base", "nushell-base")
        .replace(
            "git go bash sh cc gcc python3 python",
            "git cargo rustc rustup nu bash sh cc gcc python3 python",
        )
    )
    adapter.configure()


def relabel(path: Path) -> None:
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    kind = payload.get("kind")
    if isinstance(kind, str):
        payload["kind"] = kind.replace("ripgrep", "nushell").replace(
            "gozero", "nushell"
        )
    payload["workspace"] = WORKSPACE
    adapter.capture.write_json_atomic(path, payload)


def main(argv: Sequence[str] | None = None) -> int:
    configure()
    args = adapter.capture.parser().parse_args(argv)
    try:
        result = adapter.capture.execute(args)
        output = args.output.resolve()
        relabel(output / "manifest.json")
        relabel(output / "endpoint_consensus.json")
        relabel(output / "capture_progress.json")
        for directory in (output / "captures").iterdir():
            if directory.is_dir():
                relabel(directory / "manifest.json")
        result = json.loads((output / "manifest.json").read_text())
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "validated" else 42
    except (
        adapter.capture.CaptureError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"capture-nushell-endpoints: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
