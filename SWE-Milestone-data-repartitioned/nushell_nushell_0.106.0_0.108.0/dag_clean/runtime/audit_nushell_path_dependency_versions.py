#!/usr/bin/env python3
"""Audit Cargo path dependency requirements in all 42 endpoint trees."""

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
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


DEPENDENCY_TABLES = {
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(*args: str, cwd: Path, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
    )
    return result.stdout.strip() if capture else ""


def version_tuple(raw: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?\s*", raw)
    if match is None:
        return None
    return tuple(int(value or 0) for value in match.groups())


def caret_accepts(requirement: str, actual: str) -> bool | None:
    requested = version_tuple(requirement)
    observed = version_tuple(actual)
    if requested is None or observed is None:
        return None
    if requested[0] > 0:
        upper = (requested[0] + 1, 0, 0)
    elif requested[1] > 0:
        upper = (0, requested[1] + 1, 0)
    else:
        upper = (0, 0, requested[2] + 1)
    return requested <= observed < upper


def requirement_accepts(requirement: str, actual: str) -> bool | None:
    requirement = requirement.strip()
    if "||" in requirement or "," in requirement:
        return None
    if requirement in {"", "*"}:
        return True
    if requirement.startswith("="):
        return version_tuple(requirement[1:]) == version_tuple(actual)
    if requirement.startswith("^"):
        return caret_accepts(requirement[1:], actual)
    if requirement.startswith("~"):
        requested = version_tuple(requirement[1:])
        observed = version_tuple(actual)
        if requested is None or observed is None:
            return None
        return requested <= observed < (requested[0], requested[1] + 1, 0)
    if "*" in requirement or requirement.endswith(".x"):
        expected = requirement.replace("x", "*").split(".")
        observed = actual.split(".")
        return all(
            wanted == "*" or index < len(observed) and wanted == observed[index]
            for index, wanted in enumerate(expected)
        )
    return caret_accepts(requirement, actual)


def load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text())


def package_version(
    manifest: dict[str, Any], workspace: dict[str, Any]
) -> str | None:
    value = manifest.get("package", {}).get("version")
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("workspace") is True:
        inherited = workspace.get("workspace", {}).get("package", {}).get(
            "version"
        )
        return inherited if isinstance(inherited, str) else None
    return None


def dependency_specs(
    value: Any, location: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], str, dict[str, Any]]]:
    rows = []
    if not isinstance(value, dict):
        return rows
    for key, child in value.items():
        next_location = (*location, key)
        if key in DEPENDENCY_TABLES and isinstance(child, dict):
            for alias, spec in child.items():
                if isinstance(spec, dict) and isinstance(spec.get("path"), str):
                    rows.append((next_location, alias, spec))
        rows.extend(dependency_specs(child, next_location))
    return rows


def audit_checkout(checkout: Path) -> tuple[list[dict[str, Any]], list[str]]:
    workspace_manifest = load_toml(checkout / "Cargo.toml")
    mismatches = []
    unresolved = []
    manifests = sorted(checkout.rglob("Cargo.toml"))
    for manifest_path in manifests:
        if ".git" in manifest_path.parts or "target" in manifest_path.parts:
            continue
        try:
            manifest = load_toml(manifest_path)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            unresolved.append(f"{manifest_path.relative_to(checkout)}: {exc}")
            continue
        for table, alias, spec in dependency_specs(manifest):
            requirement = spec.get("version")
            if not isinstance(requirement, str):
                continue
            target_manifest = (
                manifest_path.parent / spec["path"] / "Cargo.toml"
            ).resolve()
            try:
                target_manifest.relative_to(checkout.resolve())
            except ValueError:
                unresolved.append(
                    f"{manifest_path.relative_to(checkout)}:{alias}: "
                    f"path escapes checkout: {spec['path']}"
                )
                continue
            if not target_manifest.is_file():
                unresolved.append(
                    f"{manifest_path.relative_to(checkout)}:{alias}: "
                    f"missing {target_manifest}"
                )
                continue
            target = load_toml(target_manifest)
            actual = package_version(target, workspace_manifest)
            if actual is None:
                unresolved.append(
                    f"{target_manifest.relative_to(checkout)} has no "
                    "resolvable package version"
                )
                continue
            compatible = requirement_accepts(requirement, actual)
            if compatible is False or compatible is None:
                mismatches.append(
                    {
                        "source_manifest": manifest_path.relative_to(
                            checkout
                        ).as_posix(),
                        "dependency_table": ".".join(table),
                        "dependency_alias": alias,
                        "dependency_package": spec.get("package", alias),
                        "path": spec["path"],
                        "requirement": requirement,
                        "target_manifest": target_manifest.relative_to(
                            checkout
                        ).as_posix(),
                        "target_package": target.get("package", {}).get("name"),
                        "target_version": actual,
                        "compatibility": (
                            "mismatch" if compatible is False else "unparsed"
                        ),
                    }
                )
    return mismatches, unresolved


def audit_ureq_feature_contract(checkout: Path) -> list[dict[str, Any]]:
    root = load_toml(checkout / "Cargo.toml")
    command = load_toml(checkout / "crates/nu-command/Cargo.toml")
    ureq = root.get("workspace", {}).get("dependencies", {}).get("ureq", {})
    requirement = ureq.get("version") if isinstance(ureq, dict) else ureq
    rustls_features = command.get("features", {}).get("rustls-tls", [])
    references = sorted(
        feature
        for feature in rustls_features
        if isinstance(feature, str) and feature.startswith("ureq/")
    )
    match = re.search(r"(\d+)\.", str(requirement))
    major = int(match.group(1)) if match else None
    mismatch = (
        major == 3 and "ureq/tls" in references
    ) or (
        major == 2 and "ureq/rustls" in references
    )
    if not mismatch:
        return []
    return [{
        "dependency": "ureq",
        "requirement": requirement,
        "requested_features": references,
        "reason": f"ureq-{major}-feature-contract-mismatch",
    }]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-root", type=Path, required=True)
    parser.add_argument("--repair-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    prepare = args.prepare_root.resolve()
    anchor = prepare / "agent-anchor"
    states_root = prepare / "delivery/states"
    manifest = json.loads((states_root / "manifest.json").read_text())
    endpoints = manifest.get("endpoints", [])
    if len(endpoints) != 42:
        raise SystemExit("state manifest does not contain 42 endpoints")
    repair_root = args.repair_root.resolve() if args.repair_root else None
    repairs = {}
    repair_manifest = None
    if repair_root is not None:
        repair_manifest = json.loads((repair_root / "manifest.json").read_text())
        if (
            repair_manifest.get("status") != "validated"
            or repair_manifest.get("endpoint_count") != 42
            or len(repair_manifest.get("endpoints", [])) != 42
        ):
            raise SystemExit("environment repair manifest is not 42 endpoints")
        repairs = {
            row["endpoint_id"]: row for row in repair_manifest["endpoints"]
        }
        if len(repairs) != 42:
            raise SystemExit("environment repair endpoint IDs are not unique")

    endpoint_rows = []
    with tempfile.TemporaryDirectory(prefix="nushell-path-audit-") as raw:
        checkout = Path(raw) / "checkout"
        run(
            "git",
            "clone",
            "--quiet",
            "--no-hardlinks",
            str(anchor),
            str(checkout),
            cwd=Path(raw),
        )
        for index, endpoint in enumerate(endpoints, 1):
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
            original_tree = run(
                "git", "write-tree", cwd=checkout, capture=True
            )
            if original_tree != endpoint["combined_tree"]:
                raise SystemExit(
                    f"tree mismatch for {endpoint['endpoint_id']}: "
                    f"{original_tree} != {endpoint['combined_tree']}"
                )
            repair = repairs.get(endpoint["endpoint_id"])
            if repair_root is not None:
                if repair is None or repair["original_tree"] != original_tree:
                    raise SystemExit(
                        f"repair original tree mismatch: {endpoint['endpoint_id']}"
                    )
                patch = repair_root / repair["patch"]
                if (
                    not patch.is_file()
                    or patch.is_symlink()
                    or sha256(patch) != repair["patch_sha256"]
                    or patch.stat().st_size != repair["patch_bytes"]
                ):
                    raise SystemExit(
                        f"repair patch identity mismatch: {endpoint['endpoint_id']}"
                    )
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
                environment_tree = run(
                    "git", "write-tree", cwd=checkout, capture=True
                )
                if environment_tree != repair["environment_tree"]:
                    raise SystemExit(
                        f"repair environment tree mismatch: "
                        f"{endpoint['endpoint_id']}"
                    )
                if "align_root_and_path_versions" in repair["reasons"]:
                    root = load_toml(checkout / "Cargo.toml")
                    observed_root = root.get("package", {}).get("version")
                    target_versions = {
                        row["after"]
                        for row in repair["path_version_replacements"]
                    }
                    if (
                        target_versions != {observed_root}
                        or observed_root != repair["root_version_after"]
                        or repair["path_version_replacement_count"] != 20
                    ):
                        raise SystemExit(
                            f"root/internal version consensus failed: "
                            f"{endpoint['endpoint_id']}"
                        )
            else:
                environment_tree = original_tree
            mismatches, unresolved = audit_checkout(checkout)
            feature_mismatches = audit_ureq_feature_contract(checkout)
            endpoint_rows.append(
                {
                    "index": index,
                    "endpoint_id": endpoint["endpoint_id"],
                    "combined_tree": original_tree,
                    "environment_tree": environment_tree,
                    "mismatch_count": len(mismatches),
                    "unresolved_count": len(unresolved),
                    "feature_contract_mismatch_count": len(
                        feature_mismatches
                    ),
                    "mismatches": mismatches,
                    "unresolved": unresolved,
                    "feature_contract_mismatches": feature_mismatches,
                }
            )

    mismatch_rows = [
        mismatch
        for endpoint in endpoint_rows
        for mismatch in endpoint["mismatches"]
    ]
    feature_mismatch_rows = [
        mismatch
        for endpoint in endpoint_rows
        for mismatch in endpoint["feature_contract_mismatches"]
    ]
    signatures = Counter(
        (
            row["dependency_package"],
            row["requirement"],
            row["target_package"],
            row["target_version"],
        )
        for row in mismatch_rows
    )
    payload = {
        "schema_version": 1,
        "kind": (
            "nushell_42_endpoint_path_dependency_version_audit"
            if repair_root is None
            else "nushell_42_endpoint_repaired_path_dependency_audit"
        ),
        "status": (
            "review_required"
            if (
                mismatch_rows
                or any(row["unresolved"] for row in endpoint_rows)
                or feature_mismatch_rows
            )
            else "validated"
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prepare_root": str(prepare),
        "repair_root": str(repair_root) if repair_root is not None else None,
        "repair_manifest_sha256": (
            sha256(repair_root / "manifest.json")
            if repair_root is not None
            else None
        ),
        "endpoint_count": 42,
        "endpoint_tree_validated_count": 42,
        "endpoints_with_mismatch_count": sum(
            bool(row["mismatches"]) for row in endpoint_rows
        ),
        "mismatch_count": len(mismatch_rows),
        "endpoints_with_unresolved_count": sum(
            bool(row["unresolved"]) for row in endpoint_rows
        ),
        "unresolved_count": sum(
            len(row["unresolved"]) for row in endpoint_rows
        ),
        "endpoints_with_feature_contract_mismatch_count": sum(
            bool(row["feature_contract_mismatches"]) for row in endpoint_rows
        ),
        "feature_contract_mismatch_count": len(feature_mismatch_rows),
        "unique_mismatch_signatures": [
            {
                "dependency_package": signature[0],
                "requirement": signature[1],
                "target_package": signature[2],
                "target_version": signature[3],
                "occurrence_count": count,
            }
            for signature, count in sorted(signatures.items())
        ],
        "endpoints": endpoint_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=args.output.parent, prefix=f".{args.output.name}."
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, args.output)
    print(
        json.dumps(
            {
                key: payload[key]
                for key in (
                    "endpoint_count",
                    "endpoints_with_mismatch_count",
                    "mismatch_count",
                    "endpoints_with_unresolved_count",
                    "unresolved_count",
                    "endpoints_with_feature_contract_mismatch_count",
                    "feature_contract_mismatch_count",
                )
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
