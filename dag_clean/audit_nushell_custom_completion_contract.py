#!/usr/bin/env python3
"""Audit the Rust custom_completion structural contract in endpoint trees."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


SIGNATURE = Path("crates/nu-protocol/src/signature.rs")
SIGNATURE_TEST = Path("crates/nu-protocol/tests/test_signature.rs")
REEDLINE_CONFIG = Path("crates/nu-cli/src/reedline_config.rs")
REPL = Path("crates/nu-cli/src/repl.rs")
TAB_TRAVERSAL_PATTERN = re.compile(
    r'''columnar_menu\s*=\s*match\s+extract_value\("tab_traversal",.*?\{\s*
\s*Ok\(tab_traversal\)\s*=>\s*match\s+tab_traversal\.coerce_str\(\)\?\.as_ref\(\)\s*\{.*?
\s*\},\s*
\s*Err\(_\)\s*=>\s*columnar_menu,\s*
\s*\};''',
    flags=re.DOTALL,
)


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


def constructor_sites(checkout: Path) -> list[dict[str, Any]]:
    """Find Flag/PositionalArg constructors using the reviewed helper rules."""
    rows = []
    for path in sorted(checkout.rglob("*.rs")):
        if "target" in path.parts or ".git" in path.parts:
            continue
        lines = path.read_text(errors="surrogateescape").splitlines()
        for index, line in enumerate(lines):
            if (
                "default_value:" not in line
                or not line.rstrip().endswith(",")
                or "pub default_value" in line
            ):
                continue
            current_indent = len(line) - len(line.lstrip())
            struct = None
            for previous_index in range(
                index - 1, max(index - 30, -1), -1
            ):
                previous = lines[previous_index]
                stripped = previous.strip()
                if (
                    ("Flag {" in previous or "PositionalArg {" in previous)
                    and ".." not in previous
                ):
                    struct = (
                        "PositionalArg"
                        if "PositionalArg {" in previous
                        else "Flag"
                    )
                    break
                if stripped in {"},", "}", ");", ")"}:
                    previous_indent = len(previous) - len(previous.lstrip())
                    if previous_indent <= current_indent:
                        break
            if struct is None:
                continue
            following = lines[index + 1] if index + 1 < len(lines) else ""
            rows.append(
                {
                    "path": path.relative_to(checkout).as_posix(),
                    "line": index + 1,
                    "struct": struct,
                    "has_custom_completion_none": (
                        following.strip() == "custom_completion: None,"
                    ),
                }
            )
    return rows


def audit_custom_completion_contract(checkout: Path) -> dict[str, Any]:
    signature_path = checkout / SIGNATURE
    test_path = checkout / SIGNATURE_TEST
    signature = signature_path.read_text()
    test = test_path.read_text() if test_path.is_file() else ""
    field_count = len(
        re.findall(
            r"^\s*pub custom_completion:\s*Option<DeclId>,\s*$",
            signature,
            flags=re.MULTILINE,
        )
    )
    decl_id_import_count = len(
        re.findall(
            r"use crate::\{[^;]*\bDeclId\b[^;]*\};",
            signature,
            flags=re.DOTALL,
        )
    )
    test_field_count = len(
        re.findall(
            r"^\s*custom_completion:\s*None,\s*$",
            test,
            flags=re.MULTILINE,
        )
    )
    sites = constructor_sites(checkout)
    missing_sites = [
        row for row in sites if not row["has_custom_completion_none"]
    ]
    active = test_field_count > 0 or field_count > 0
    reasons = []
    if active and field_count != 2:
        reasons.append("struct_field_count")
    if active and decl_id_import_count != 1:
        reasons.append("decl_id_import_count")
    if active and missing_sites:
        reasons.append("missing_constructor_fields")
    return {
        "status": "mismatch" if reasons else "aligned",
        "mismatch_count": int(bool(reasons)),
        "reasons": reasons,
        "struct_field_count": field_count,
        "decl_id_import_count": decl_id_import_count,
        "test_field_count": test_field_count,
        "constructor_site_count": len(sites),
        "missing_constructor_field_count": len(missing_sites),
        "missing_constructor_fields": missing_sites,
    }


def reedline_api_transformation(checkout: Path) -> dict[str, Any]:
    """Replay fix_reedline_api.py in memory and report its exact edits."""
    config_path = checkout / REEDLINE_CONFIG
    repl_path = checkout / REPL
    config_before = config_path.read_text()
    config_after, import_middle_count = re.subn(
        r",\s*TraversalDirection\s*,", ",", config_before
    )
    config_after, import_leading_count = re.subn(
        r"TraversalDirection\s*,", "", config_after
    )
    config_after, traversal_block_count = TAB_TRAVERSAL_PATTERN.subn(
        "", config_after
    )

    repl_before = repl_path.read_text()
    lines = repl_before.splitlines(keepends=True)
    new_lines = []
    immediately_accept_removed_count = 0
    semicolon_added_count = 0
    for line in lines:
        if ".with_immediately_accept(" in line:
            if new_lines:
                for index in range(len(new_lines) - 1, -1, -1):
                    previous = new_lines[index].rstrip()
                    if previous and not previous.strip().startswith("//"):
                        if (
                            previous.endswith(")")
                            and not previous.endswith(";")
                            and not previous.endswith("}")
                        ):
                            new_lines[index] = previous + ";\n"
                            semicolon_added_count += 1
                        break
            immediately_accept_removed_count += 1
            continue
        new_lines.append(line)
    repl_after = "".join(new_lines)
    changed_paths = []
    if config_after != config_before:
        changed_paths.append(REEDLINE_CONFIG.as_posix())
    if repl_after != repl_before:
        changed_paths.append(REPL.as_posix())
    return {
        "status": "mismatch" if changed_paths else "aligned",
        "mismatch_count": int(bool(changed_paths)),
        "traversal_direction_import_removal_count": (
            import_middle_count + import_leading_count
        ),
        "tab_traversal_block_removal_count": traversal_block_count,
        "immediately_accept_line_removal_count": (
            immediately_accept_removed_count
        ),
        "repl_semicolon_addition_count": semicolon_added_count,
        "changed_paths": changed_paths,
        "expected_contents": {
            REEDLINE_CONFIG.as_posix(): config_after,
            REPL.as_posix(): repl_after,
        },
    }


def resolved_reedline_contract(
    resolved_lock: Path, cargo_home: Path
) -> dict[str, Any]:
    lock = tomllib.loads(resolved_lock.read_text())
    packages = [
        row for row in lock.get("package", []) if row.get("name") == "reedline"
    ]
    if len(packages) != 1:
        raise RuntimeError(
            f"resolved lock does not have exactly one reedline: {resolved_lock}"
        )
    package = packages[0]
    version = package["version"]
    source = package.get("source")
    if not isinstance(source, str):
        raise RuntimeError(f"reedline source is not locked: {resolved_lock}")
    if source.startswith("registry+"):
        candidates = list(
            (cargo_home / "registry/src").glob(f"*/reedline-{version}")
        )
    elif source.startswith("git+"):
        revision = source.rsplit("#", 1)[-1]
        candidates = list(
            (cargo_home / "git/checkouts").glob(
                f"reedline-*/{revision[:7]}"
            )
        )
    else:
        raise RuntimeError(f"unsupported reedline source: {source}")
    candidates = [path for path in candidates if path.is_dir()]
    if len(candidates) != 1:
        raise RuntimeError(
            f"reedline source checkout is not unique: {source}: {candidates}"
        )
    source_root = candidates[0]
    source_text = "\n".join(
        path.read_text(errors="surrogateescape")
        for path in sorted(source_root.rglob("*.rs"))
        if "target" not in path.parts
    )
    source_text = re.sub(r"/\*.*?\*/", "", source_text, flags=re.DOTALL)
    source_text = re.sub(r"//[^\n]*", "", source_text)
    return {
        "version": version,
        "source": source,
        "source_root": source_root.relative_to(cargo_home).as_posix(),
        "has_traversal_direction": "TraversalDirection" in source_text,
        "has_with_traversal_direction": (
            "with_traversal_direction" in source_text
        ),
        "has_with_immediately_accept": (
            "with_immediately_accept" in source_text
        ),
        "has_text_object_api": all(
            token in source_text
            for token in ("TextObject", "TextObjectScope", "TextObjectType")
        ),
        "edit_command_variants": {
            token: token in source_text
            for token in (
                "CutInsidePair",
                "CopyInsidePair",
                "CutAroundPair",
                "CopyAroundPair",
                "CopyTextObject",
                "CutTextObject",
            )
        },
    }


def audit_reedline_api_contract(
    checkout: Path, resolved_lock: Path, cargo_home: Path
) -> dict[str, Any]:
    row = reedline_api_transformation(checkout)
    row.pop("expected_contents")
    dependency = resolved_reedline_contract(resolved_lock, cargo_home)
    config = (checkout / REEDLINE_CONFIG).read_text()
    repl = (checkout / REPL).read_text()
    config_code = re.sub(r"/\*.*?\*/", "", config, flags=re.DOTALL)
    config_code = re.sub(r"//[^\n]*", "", config_code)
    repl_code = re.sub(r"/\*.*?\*/", "", repl, flags=re.DOTALL)
    repl_code = re.sub(r"//[^\n]*", "", repl_code)
    uses_traversal = (
        "TraversalDirection" in config_code
        or "with_traversal_direction" in config_code
    )
    uses_immediately_accept = ".with_immediately_accept(" in repl_code
    uses_text_object = any(
        token in config_code
        for token in ("TextObject", "TextObjectScope", "TextObjectType")
    )
    used_edit_command_variants = [
        token
        for token in (
            "CutInsidePair",
            "CopyInsidePair",
            "CutAroundPair",
            "CopyAroundPair",
            "CopyTextObject",
            "CutTextObject",
        )
        if token in config_code
    ]
    reasons = []
    if uses_traversal and not (
        dependency["has_traversal_direction"]
        and dependency["has_with_traversal_direction"]
    ):
        reasons.append("missing_traversal_direction_api")
    if (
        uses_immediately_accept
        and not dependency["has_with_immediately_accept"]
    ):
        reasons.append("missing_immediately_accept_api")
    if uses_text_object and not dependency["has_text_object_api"]:
        reasons.append("missing_text_object_api")
    if any(
        not dependency["edit_command_variants"][token]
        for token in used_edit_command_variants
    ):
        reasons.append("missing_edit_command_variants")
    row["helper_change_candidate_count"] = row["mismatch_count"]
    row["status"] = "mismatch" if reasons else "aligned"
    row["mismatch_count"] = int(bool(reasons))
    row["mismatch_reasons"] = reasons
    row["uses_traversal_direction"] = uses_traversal
    row["uses_with_immediately_accept"] = uses_immediately_accept
    row["uses_text_object_api"] = uses_text_object
    row["used_edit_command_variants"] = used_edit_command_variants
    row["resolved_dependency"] = dependency
    return row


def apply_patch(
    checkout: Path, patch: Path, *, expected_sha: str, expected_bytes: int
) -> None:
    if (
        not patch.is_file()
        or patch.is_symlink()
        or patch.stat().st_size != expected_bytes
        or sha256(patch) != expected_sha
    ):
        raise RuntimeError(f"patch identity mismatch: {patch}")
    if expected_bytes:
        run(
            "git",
            "apply",
            "--index",
            "--binary",
            "--whitespace=nowarn",
            str(patch),
            cwd=checkout,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-root", type=Path, required=True)
    parser.add_argument("--environment-repair-root", type=Path, required=True)
    parser.add_argument("--resolved-lock-root", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--compile-repair-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    prepare = args.prepare_root.resolve()
    states_root = prepare / "delivery/states"
    state_manifest = json.loads((states_root / "manifest.json").read_text())
    environment_root = args.environment_repair_root.resolve()
    environment_manifest = json.loads(
        (environment_root / "manifest.json").read_text()
    )
    environment_rows = {
        row["endpoint_id"]: row
        for row in environment_manifest.get("endpoints", [])
    }
    resolved_root = args.resolved_lock_root.resolve()
    resolved_manifest = json.loads((resolved_root / "manifest.json").read_text())
    resolved_rows = {
        row["endpoint_id"]: row
        for row in resolved_manifest.get("endpoints", [])
    }
    cargo_home = args.cargo_home.resolve()
    compile_root = (
        args.compile_repair_root.resolve()
        if args.compile_repair_root is not None
        else None
    )
    compile_manifest = None
    compile_rows = {}
    if compile_root is not None:
        compile_manifest = json.loads((compile_root / "manifest.json").read_text())
        compile_rows = {
            row["endpoint_id"]: row
            for row in compile_manifest.get("endpoints", [])
        }
    if (
        len(state_manifest.get("endpoints", [])) != 42
        or len(environment_rows) != 42
        or len(resolved_rows) != 42
        or (compile_root is not None and len(compile_rows) != 42)
    ):
        raise SystemExit("custom_completion audit inputs are not 42 endpoints")

    endpoint_rows = []
    with tempfile.TemporaryDirectory(prefix="nushell-custom-audit-") as raw:
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
                raise RuntimeError(f"state tree mismatch: {endpoint_id}")
            environment = environment_rows[endpoint_id]
            if environment["original_tree"] != original_tree:
                raise RuntimeError(f"environment tree input mismatch: {endpoint_id}")
            apply_patch(
                checkout,
                environment_root / environment["patch"],
                expected_sha=environment["patch_sha256"],
                expected_bytes=environment["patch_bytes"],
            )
            environment_tree = run(
                "git", "write-tree", cwd=checkout, capture=True
            )
            if environment_tree != environment["environment_tree"]:
                raise RuntimeError(f"environment tree mismatch: {endpoint_id}")
            compile_tree = environment_tree
            if compile_root is not None:
                compile_repair = compile_rows[endpoint_id]
                if compile_repair["environment_tree"] != environment_tree:
                    raise RuntimeError(
                        f"compile repair input mismatch: {endpoint_id}"
                    )
                apply_patch(
                    checkout,
                    compile_root / compile_repair["patch"],
                    expected_sha=compile_repair["patch_sha256"],
                    expected_bytes=compile_repair["patch_bytes"],
                )
                compile_tree = run(
                    "git", "write-tree", cwd=checkout, capture=True
                )
                if compile_tree != compile_repair["compile_tree"]:
                    raise RuntimeError(
                        f"compile repair tree mismatch: {endpoint_id}"
                    )
            contract = audit_custom_completion_contract(checkout)
            resolved = resolved_rows[endpoint_id]
            resolved_lock = resolved_root / resolved["resolved_lock"]
            if sha256(resolved_lock) != resolved["resolved_lock_sha256"]:
                raise RuntimeError(f"resolved lock mismatch: {endpoint_id}")
            reedline_contract = audit_reedline_api_contract(
                checkout, resolved_lock, cargo_home
            )
            endpoint_rows.append(
                {
                    "index": index,
                    "endpoint_id": endpoint_id,
                    "original_tree": original_tree,
                    "environment_tree": environment_tree,
                    "compile_tree": compile_tree,
                    **contract,
                    "reedline_api_status": reedline_contract["status"],
                    "reedline_api_helper_change_candidate_count": (
                        reedline_contract["helper_change_candidate_count"]
                    ),
                    "reedline_api_mismatch_count": reedline_contract[
                        "mismatch_count"
                    ],
                    "reedline_api_traversal_direction_import_removal_count": (
                        reedline_contract[
                            "traversal_direction_import_removal_count"
                        ]
                    ),
                    "reedline_api_tab_traversal_block_removal_count": (
                        reedline_contract["tab_traversal_block_removal_count"]
                    ),
                    "reedline_api_immediately_accept_line_removal_count": (
                        reedline_contract[
                            "immediately_accept_line_removal_count"
                        ]
                    ),
                    "reedline_api_repl_semicolon_addition_count": (
                        reedline_contract["repl_semicolon_addition_count"]
                    ),
                    "reedline_api_changed_paths": reedline_contract[
                        "changed_paths"
                    ],
                    "reedline_api_mismatch_reasons": reedline_contract[
                        "mismatch_reasons"
                    ],
                    "reedline_api_uses_traversal_direction": (
                        reedline_contract["uses_traversal_direction"]
                    ),
                    "reedline_api_uses_with_immediately_accept": (
                        reedline_contract["uses_with_immediately_accept"]
                    ),
                    "reedline_api_uses_text_object_api": (
                        reedline_contract["uses_text_object_api"]
                    ),
                    "reedline_api_used_edit_command_variants": (
                        reedline_contract["used_edit_command_variants"]
                    ),
                    "reedline_resolved_dependency": reedline_contract[
                        "resolved_dependency"
                    ],
                }
            )

    mismatch_rows = [
        row for row in endpoint_rows if row["mismatch_count"]
    ]
    reedline_mismatch_rows = [
        row for row in endpoint_rows if row["reedline_api_mismatch_count"]
    ]
    reedline_helper_change_rows = [
        row
        for row in endpoint_rows
        if row["reedline_api_helper_change_candidate_count"]
    ]
    payload = {
        "schema_version": 1,
        "kind": "nushell_42_endpoint_custom_completion_contract_audit",
        "status": (
            "review_required"
            if mismatch_rows or reedline_mismatch_rows
            else "validated"
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prepare_root": str(prepare),
        "environment_repair_root": str(environment_root),
        "environment_repair_manifest_sha256": sha256(
            environment_root / "manifest.json"
        ),
        "resolved_lock_root": str(resolved_root),
        "resolved_lock_manifest_sha256": sha256(
            resolved_root / "manifest.json"
        ),
        "resolved_lock_index_sha256": sha256(
            resolved_root / "resolved_locks.tsv"
        ),
        "cargo_home": str(cargo_home),
        "compile_repair_root": (
            str(compile_root) if compile_root is not None else None
        ),
        "compile_repair_manifest_sha256": (
            sha256(compile_root / "manifest.json")
            if compile_root is not None
            else None
        ),
        "endpoint_count": 42,
        "endpoint_tree_validated_count": 42,
        "endpoints_with_custom_completion_contract_mismatch_count": len(
            mismatch_rows
        ),
        "custom_completion_contract_mismatch_count": sum(
            row["mismatch_count"] for row in endpoint_rows
        ),
        "endpoints_with_reedline_api_contract_mismatch_count": len(
            reedline_mismatch_rows
        ),
        "reedline_api_contract_mismatch_count": sum(
            row["reedline_api_mismatch_count"] for row in endpoint_rows
        ),
        "endpoints_with_reedline_api_helper_change_candidate_count": len(
            reedline_helper_change_rows
        ),
        "reedline_api_helper_change_candidate_count": sum(
            row["reedline_api_helper_change_candidate_count"]
            for row in endpoint_rows
        ),
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
                "endpoint_count": 42,
                "endpoints_with_custom_completion_contract_mismatch_count": (
                    len(mismatch_rows)
                ),
                "custom_completion_contract_mismatch_count": sum(
                    row["mismatch_count"] for row in endpoint_rows
                ),
                "endpoints_with_reedline_api_contract_mismatch_count": (
                    len(reedline_mismatch_rows)
                ),
                "reedline_api_contract_mismatch_count": sum(
                    row["reedline_api_mismatch_count"]
                    for row in endpoint_rows
                ),
                "endpoints_with_reedline_api_helper_change_candidate_count": (
                    len(reedline_helper_change_rows)
                ),
                "reedline_api_helper_change_candidate_count": sum(
                    row["reedline_api_helper_change_candidate_count"]
                    for row in endpoint_rows
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
