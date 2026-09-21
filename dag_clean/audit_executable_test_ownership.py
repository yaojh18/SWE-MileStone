#!/usr/bin/env python3
"""Fail closed when a clean DAG node contains tests outside a Maven module.

The node-test runner selects the nearest ancestor ``pom.xml`` for every
checked-in ``src/test`` file. A removed module can otherwise make Maven's root
aggregator look like that ancestor even though Maven will never compile the
nested test source. This audit distinguishes a real root-level ``src/test``
tree from such an orphaned module tree.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from maven_reactor_audit import orphaned_test_paths, reachable_maven_modules


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def tree_entries(repo: Path, commit: str) -> dict[str, str]:
    completed = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", commit],
        check=True,
        stdout=subprocess.PIPE,
    )
    entries = {}
    for item in completed.stdout.split(b"\0"):
        if not item:
            continue
        header, raw_path = item.split(b"\t", 1)
        _mode, object_type, object_id = header.decode("ascii").split()
        if object_type == "blob":
            entries[raw_path.decode("utf-8", errors="surrogateescape")] = object_id
    return entries


def read_blobs(repo: Path, object_ids: list[str]) -> dict[str, bytes]:
    if not object_ids:
        return {}
    completed = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input=("\n".join(object_ids) + "\n").encode("ascii"),
        check=True,
        stdout=subprocess.PIPE,
    )
    output = completed.stdout
    offset = 0
    blobs = {}
    for requested in object_ids:
        newline = output.index(b"\n", offset)
        header = output[offset:newline].decode("ascii")
        offset = newline + 1
        object_id, object_type, size_text = header.split()
        if object_type != "blob":
            raise RuntimeError(f"expected blob {requested}, got {header}")
        size = int(size_text)
        blobs[object_id] = output[offset : offset + size]
        offset += size
        if output[offset : offset + 1] != b"\n":
            raise RuntimeError(f"malformed git cat-file output for {requested}")
        offset += 1
    return blobs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--dag-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    dag = json.loads(args.dag_manifest.read_text(encoding="utf-8"))
    records = []
    for node in dag.get("nodes", []):
        node_id = str(node["node_id"])
        commit = str(node["clean_sha"])
        entries = tree_entries(args.repo, commit)
        pom_paths = sorted(
            path for path in entries if path == "pom.xml" or path.endswith("/pom.xml")
        )
        pom_blobs = read_blobs(args.repo, sorted({entries[path] for path in pom_paths}))
        pom_contents = {
            path: pom_blobs[entries[path]].decode("utf-8", errors="replace")
            for path in pom_paths
        }
        reachable = reachable_maven_modules(pom_contents)
        orphaned = orphaned_test_paths(list(entries), reachable)
        records.append(
            {
                "node_id": node_id,
                "clean_sha": commit,
                "clean_tree": node.get("clean_tree"),
                "orphaned_test_count": len(orphaned),
                "orphaned_tests": orphaned,
                "reachable_maven_module_count": len(reachable),
            }
        )

    affected = [record["node_id"] for record in records if record["orphaned_test_count"]]
    payload = {
        "schema_version": 1,
        "workspace": dag.get("workspace"),
        "node_count": len(records),
        "affected_node_count": len(affected),
        "orphaned_test_count": sum(
            record["orphaned_test_count"] for record in records
        ),
        "affected_nodes": affected,
        "status": "complete" if not affected else "blocked_orphaned_tests",
        "nodes": records,
    }
    write_json(args.output, payload)
    print(
        json.dumps(
            {
                key: payload[key]
                for key in (
                    "status",
                    "node_count",
                    "affected_node_count",
                    "orphaned_test_count",
                )
            },
            sort_keys=True,
        )
    )
    return 0 if not affected else 2


if __name__ == "__main__":
    raise SystemExit(main())
