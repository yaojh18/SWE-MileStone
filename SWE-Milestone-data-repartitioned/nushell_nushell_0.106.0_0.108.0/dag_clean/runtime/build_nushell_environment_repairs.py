#!/usr/bin/env python3
"""Build reviewed endpoint-keyed Cargo environment repair patches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from audit_nushell_path_dependency_versions import (
    audit_checkout,
    audit_ureq_feature_contract,
    load_toml,
)


CANONICAL_ENV_COMMIT = "6e049c334c0"
CANONICAL_ENV_SUBJECT = (
    "[ENV-PATCH] Fix version mismatch - downgrade root to 0.106.1 to match crates"
)
NU_MCP_EVIDENCE = (
    "delivery/transitions/transitions/"
    "gap_milestone_G05_0b8531e_end-_milestone_core_development.4_start"
    "--da77ea4480/implementation.patch"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(
    *args: str, cwd: Path, capture: bool = False, binary: bool = False
) -> str | bytes:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=not binary,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
    )
    if not capture:
        return b"" if binary else ""
    return result.stdout


def root_version(lines: list[str]) -> str:
    section = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        if section == "[package]":
            match = re.fullmatch(r'\s*version\s*=\s*"([^"]+)"\s*', line)
            if match:
                return match.group(1)
    raise RuntimeError("root Cargo.toml has no [package].version")


def set_root_version(lines: list[str], expected: str) -> tuple[list[str], str]:
    result = []
    section = None
    before = None
    replaced = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
        if section == "[package]":
            match = re.fullmatch(
                r'(\s*version\s*=\s*")([^"]+)("\s*)', line
            )
            if match:
                before = match.group(2)
                line = f"{match.group(1)}{expected}{match.group(3)}"
                replaced += 1
        result.append(line)
    if replaced != 1 or before is None:
        raise RuntimeError("root package version replacement is not unique")
    return result, before


def replace_dependency_version(
    lines: list[str], alias: str, before: str, after: str
) -> list[str]:
    pattern = re.compile(
        rf'^(\s*{re.escape(alias)}\s*=\s*\{{.*\bversion\s*=\s*")'
        rf'{re.escape(before)}(".*\}}\s*)$'
    )
    result = []
    replaced = 0
    for line in lines:
        match = pattern.fullmatch(line)
        if match:
            line = f"{match.group(1)}{after}{match.group(2)}"
            replaced += 1
        result.append(line)
    if replaced != 1:
        raise RuntimeError(
            f"dependency replacement is not unique: {alias} "
            f"{before}->{after}: {replaced}"
        )
    return result


def disable_missing_nu_mcp(lines: list[str]) -> tuple[list[str], int]:
    result = []
    replacements = 0
    exact = {
        '"crates/nu-mcp",',
        '"dep:nu-mcp",',
        '"nu-mcp/mcp",',
    }
    for line in lines:
        stripped = line.strip()
        if stripped in exact or stripped.startswith("nu-mcp = {"):
            indentation = line[: len(line) - len(line.lstrip())]
            line = f"{indentation}# [ENV-PATCH] {stripped}"
            replacements += 1
        result.append(line)
    if replacements != 4:
        raise RuntimeError(
            f"nu-mcp environment repair expected 4 lines, found {replacements}"
        )
    return result, replacements


def internal_consensus(checkout: Path, mismatches: list[dict]) -> str:
    versions = []
    for mismatch in mismatches:
        target = load_toml(checkout / mismatch["target_manifest"])
        value = target.get("package", {}).get("version")
        if not isinstance(value, str):
            raise RuntimeError(
                "non-literal internal version: "
                f"{mismatch['dependency_alias']}"
            )
        versions.append(value)
    counts = Counter(versions)
    if len(counts) != 1:
        raise RuntimeError(f"internal nu-* versions lack consensus: {counts}")
    return next(iter(counts))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    prepare = args.prepare_root.resolve()
    audit_path = args.audit.resolve()
    output_root = args.output_root.resolve()
    audit = json.loads(audit_path.read_text())
    expected_mismatch_endpoints = {
        "milestone_G05_ac3f93f:start",
        "milestone_G05_ac3f93f:end",
        "milestone_M03_polars:start",
        "milestone_M03_polars:end",
        "milestone_M04_std:start",
        "milestone_M04_std:end",
        "milestone_core_development.2:end",
    }
    expected_missing_mcp = {
        "milestone_M03_polars:start",
        "milestone_M03_polars:end",
        "milestone_core_development.4:end",
    }
    expected_ureq_repairs = {
        "milestone_G05_ac3f93f:start",
        "milestone_G05_ac3f93f:end",
        "milestone_M04_std:start",
        "milestone_M04_std:end",
        "milestone_core_development.2:end",
    }
    observed_mismatch = {
        row["endpoint_id"]
        for row in audit["endpoints"]
        if row["mismatch_count"]
    }
    observed_missing_mcp = {
        row["endpoint_id"]
        for row in audit["endpoints"]
        if row["unresolved_count"]
    }
    if (
        audit.get("endpoint_count") != 42
        or audit.get("endpoint_tree_validated_count") != 42
        or audit.get("mismatch_count") != 140
        or audit.get("unresolved_count") != 3
        or observed_mismatch != expected_mismatch_endpoints
        or observed_missing_mcp != expected_missing_mcp
    ):
        raise SystemExit("path dependency audit differs from reviewed matrix")

    states_root = prepare / "delivery/states"
    state_manifest = json.loads((states_root / "manifest.json").read_text())
    endpoint_audit = {
        row["endpoint_id"]: row for row in audit["endpoints"]
    }
    if output_root.exists():
        if not args.replace:
            raise SystemExit(f"repair bundle already exists: {output_root}")
        existing = json.loads((output_root / "manifest.json").read_text())
        if existing.get("kind") != "nushell_42_endpoint_environment_repairs":
            raise SystemExit(f"refusing to replace unknown directory: {output_root}")
    staging = output_root.with_name(
        f".{output_root.name}.tmp.{os.getpid()}"
    )
    staging.mkdir(parents=True)
    patches = staging / "patches"
    patches.mkdir()
    rows = []
    try:
        with tempfile.TemporaryDirectory(prefix="nushell-repair-build-") as raw:
            checkout = Path(raw) / "checkout"
            run(
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                str(prepare / "agent-anchor"),
                str(checkout),
                cwd=Path(raw),
            )
            for index, endpoint in enumerate(state_manifest["endpoints"], 1):
                endpoint_id = endpoint["endpoint_id"]
                review = endpoint_audit[endpoint_id]
                run("git", "reset", "--hard", "-q", "HEAD", cwd=checkout)
                run("git", "clean", "-fdx", "-q", cwd=checkout)
                for state_name in ("implementation_state", "test_state"):
                    patch = states_root / endpoint[state_name]["patch"]["path"]
                    if patch.stat().st_size:
                        run(
                            "git",
                            "apply",
                            "--index",
                            "--binary",
                            "--whitespace=nowarn",
                            str(patch),
                            cwd=checkout,
                        )
                original_tree = str(
                    run("git", "write-tree", cwd=checkout, capture=True)
                ).strip()
                if original_tree != endpoint["combined_tree"]:
                    raise RuntimeError(f"original tree mismatch: {endpoint_id}")

                cargo_path = checkout / "Cargo.toml"
                lines = cargo_path.read_text().splitlines()
                root_before = root_version(lines)
                dependency_replacements = []
                mismatch_aliases = set()
                if review["mismatch_count"]:
                    targets = {
                        row["target_version"] for row in review["mismatches"]
                    }
                    if targets != {"0.106.1"}:
                        raise RuntimeError(
                            f"unexpected version target: {endpoint_id}: {targets}"
                        )
                    mismatch_aliases = {
                        row["dependency_alias"]
                        for row in review["mismatches"]
                    }
                    if len(mismatch_aliases) != 20:
                        raise RuntimeError(
                            f"expected 20 internal dependencies: {endpoint_id}"
                        )
                    consensus = internal_consensus(
                        checkout, review["mismatches"]
                    )
                    if consensus != "0.106.1":
                        raise RuntimeError(
                            f"unexpected internal consensus: {endpoint_id}"
                        )
                    lines, observed_root_before = set_root_version(
                        lines, consensus
                    )
                    if observed_root_before != root_before:
                        raise RuntimeError("root version changed during review")
                    for mismatch in review["mismatches"]:
                        lines = replace_dependency_version(
                            lines,
                            mismatch["dependency_alias"],
                            mismatch["requirement"],
                            mismatch["target_version"],
                        )
                        dependency_replacements.append(
                            {
                                "dependency": mismatch["dependency_package"],
                                "before": mismatch["requirement"],
                                "after": mismatch["target_version"],
                                "target_manifest": mismatch["target_manifest"],
                            }
                        )
                nu_mcp_replacements = 0
                if review["unresolved_count"]:
                    if endpoint_id not in expected_missing_mcp:
                        raise RuntimeError(
                            f"unreviewed unresolved dependency: {endpoint_id}"
                        )
                    lines, nu_mcp_replacements = disable_missing_nu_mcp(lines)
                cargo_path.write_text("\n".join(lines) + "\n")
                run("git", "add", "Cargo.toml", cwd=checkout)
                ureq_mismatches = audit_ureq_feature_contract(checkout)
                ureq_replacement_count = 0
                if ureq_mismatches:
                    if (
                        endpoint_id not in expected_ureq_repairs
                        or len(ureq_mismatches) != 1
                        or ureq_mismatches[0]["requirement"] != "=3.0.12"
                        or ureq_mismatches[0]["requested_features"]
                        != ["ureq/tls"]
                    ):
                        raise RuntimeError(
                            f"unreviewed ureq feature conflict: "
                            f"{endpoint_id}: {ureq_mismatches}"
                        )
                    lines = replace_dependency_version(
                        cargo_path.read_text().splitlines(),
                        "ureq",
                        "=3.0.12",
                        "2.12",
                    )
                    cargo_path.write_text("\n".join(lines) + "\n")
                    run("git", "add", "Cargo.toml", cwd=checkout)
                    ureq_replacement_count = 1
                environment_tree = str(
                    run("git", "write-tree", cwd=checkout, capture=True)
                ).strip()
                repaired_mismatches, repaired_unresolved = audit_checkout(
                    checkout
                )
                repaired_feature_mismatches = audit_ureq_feature_contract(
                    checkout
                )
                if (
                    repaired_mismatches
                    or repaired_unresolved
                    or repaired_feature_mismatches
                ):
                    raise RuntimeError(
                        f"repair incomplete: {endpoint_id}: "
                        f"{repaired_mismatches} {repaired_unresolved}"
                        f" {repaired_feature_mismatches}"
                    )
                root_after = root_version(
                    cargo_path.read_text().splitlines()
                )
                if mismatch_aliases:
                    consensus = internal_consensus(
                        checkout, review["mismatches"]
                    )
                    if root_after != consensus:
                        raise RuntimeError(
                            f"root/internal consensus gate failed: {endpoint_id}"
                        )

                safe = re.sub(r"[^A-Za-z0-9._-]", "_", endpoint_id)
                patch_name = f"{safe}.patch"
                patch_path = patches / patch_name
                patch_bytes = run(
                    "git",
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-ext-diff",
                    "--no-renames",
                    original_tree,
                    environment_tree,
                    "--",
                    "Cargo.toml",
                    cwd=checkout,
                    capture=True,
                    binary=True,
                )
                patch_path.write_bytes(patch_bytes)
                repair_sha = sha256(patch_path)
                reasons = []
                if dependency_replacements:
                    reasons.append("align_root_and_path_versions")
                if nu_mcp_replacements:
                    reasons.append("disable_missing_nu_mcp")
                if ureq_replacement_count:
                    reasons.append("align_ureq_feature_contract")
                if not reasons:
                    reasons.append("none")
                rows.append(
                    {
                        "index": index,
                        "endpoint_id": endpoint_id,
                        "patch": f"patches/{patch_name}",
                        "patch_sha256": repair_sha,
                        "patch_bytes": patch_path.stat().st_size,
                        "original_tree": original_tree,
                        "environment_tree": environment_tree,
                        "reasons": reasons,
                        "root_version_before": root_before,
                        "root_version_after": root_after,
                        "path_version_replacement_count": len(
                            dependency_replacements
                        ),
                        "path_version_replacements": dependency_replacements,
                        "nu_mcp_line_replacement_count": nu_mcp_replacements,
                        "ureq_feature_replacement_count": (
                            ureq_replacement_count
                        ),
                    }
                )

        tsv = staging / "repairs.tsv"
        tsv.write_text(
            "".join(
                "\t".join(
                    [
                        row["endpoint_id"],
                        row["patch"],
                        row["patch_sha256"],
                        row["original_tree"],
                        row["environment_tree"],
                        ",".join(row["reasons"]),
                    ]
                )
                + "\n"
                for row in rows
            )
        )
        manifest = {
            "schema_version": 1,
            "kind": "nushell_42_endpoint_environment_repairs",
            "status": "validated",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "endpoint_count": 42,
            "repaired_endpoint_count": sum(
                row["reasons"] != ["none"] for row in rows
            ),
            "version_aligned_endpoint_count": sum(
                "align_root_and_path_versions" in row["reasons"] for row in rows
            ),
            "missing_nu_mcp_disabled_endpoint_count": sum(
                "disable_missing_nu_mcp" in row["reasons"] for row in rows
            ),
            "path_version_replacement_count": sum(
                row["path_version_replacement_count"] for row in rows
            ),
            "nu_mcp_line_replacement_count": sum(
                row["nu_mcp_line_replacement_count"] for row in rows
            ),
            "ureq_feature_aligned_endpoint_count": sum(
                row["ureq_feature_replacement_count"] for row in rows
            ),
            "path_dependency_audit": str(audit_path),
            "path_dependency_audit_sha256": sha256(audit_path),
            "canonical_environment_commit": CANONICAL_ENV_COMMIT,
            "canonical_environment_subject": CANONICAL_ENV_SUBJECT,
            "nu_mcp_environment_evidence": NU_MCP_EVIDENCE,
            "repairs_index": "repairs.tsv",
            "repairs_index_sha256": sha256(tsv),
            "endpoints": rows,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        if output_root.exists():
            backup = output_root.with_name(
                f".{output_root.name}.replaced.{os.getpid()}"
            )
            os.replace(output_root, backup)
            os.replace(staging, output_root)
            shutil.rmtree(backup)
        else:
            os.replace(staging, output_root)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    print(
        json.dumps(
            {
                "endpoint_count": 42,
                "repaired_endpoint_count": manifest[
                    "repaired_endpoint_count"
                ],
                "version_aligned_endpoint_count": manifest[
                    "version_aligned_endpoint_count"
                ],
                "missing_nu_mcp_disabled_endpoint_count": manifest[
                    "missing_nu_mcp_disabled_endpoint_count"
                ],
                "path_version_replacement_count": manifest[
                    "path_version_replacement_count"
                ],
                "ureq_feature_aligned_endpoint_count": manifest[
                    "ureq_feature_aligned_endpoint_count"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
