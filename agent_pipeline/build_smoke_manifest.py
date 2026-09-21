#!/usr/bin/env python3
"""Build the pinned one-image manifest used by the curator smoke test."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


WORK_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_MANIFEST = WORK_ROOT / "manifests" / "swe_milestone_sif_manifest.jsonl"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "manifests" / "dubbo_m003_2_smoke.jsonl"

# M003.2 is the entry node for the retained merged M003.3 task. Keep this
# contract pinned so a reordered or regenerated full manifest cannot silently
# switch the image used by the only model-backed smoke run.
EXPECTED_RECORD: dict[str, Any] = {
    "index": 16,
    "workspace": "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6",
    "milestone_id": "m003.2",
    "is_graded": False,
    "source": "docker://hyd2apse/dubbo:m003.2-v0.9",
    "destination_rel": "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/m003.2.sif",
    "image_version": "v0.9",
}
RETAINED_MILESTONE_ID = "M003.3"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"expected an object at {path}:{line_number}")
        records.append(payload)
    return records


def select_pinned_record(records: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [record for record in records if record.get("index") == EXPECTED_RECORD["index"]]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one source record at index {EXPECTED_RECORD['index']}, "
            f"found {len(matches)}"
        )

    record = matches[0]
    mismatches = {
        key: {"expected": expected, "actual": record.get(key)}
        for key, expected in EXPECTED_RECORD.items()
        if record.get(key) != expected
    }
    if mismatches:
        raise ValueError(
            "pinned SWE-Milestone smoke record changed: "
            + json.dumps(mismatches, sort_keys=True)
        )
    return {key: record[key] for key in EXPECTED_RECORD}


def write_atomic_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Select the original manifest's pinned Dubbo M003.2 entry image for "
            "the retained M003.3 partition smoke test."
        )
    )
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source = args.source_manifest.resolve()
    output = args.output.resolve()
    if source == output:
        raise ValueError("refusing to overwrite the source SIF manifest")
    if not source.is_file() or source.stat().st_size <= 0:
        raise FileNotFoundError(f"missing or empty source manifest: {source}")

    record = select_pinned_record(load_jsonl(source))
    write_atomic_jsonl(output, record)
    print(
        "wrote smoke manifest "
        f"records=1 entry_milestone={record['milestone_id']} "
        f"retained_milestone={RETAINED_MILESTONE_ID} output={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
