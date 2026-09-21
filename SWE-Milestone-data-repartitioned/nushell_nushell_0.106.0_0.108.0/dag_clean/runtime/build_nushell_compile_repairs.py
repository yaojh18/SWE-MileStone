#!/usr/bin/env python3
"""Build reviewed Rust/test compatibility repairs without changing Cargo closure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from audit_nushell_custom_completion_contract import (
    REEDLINE_CONFIG,
    REPL,
    SIGNATURE,
    SIGNATURE_TEST,
    audit_custom_completion_contract,
    audit_reedline_api_contract,
    reedline_api_transformation,
)


EXPECTED_CUSTOM_COMPLETION_ENDPOINTS = {
    "milestone_G05_ac3f93f:start",
    "milestone_G05_ac3f93f:end",
    "milestone_core_development.2:end",
}
EXPECTED_REEDLINE_API_ENDPOINTS = {
    "milestone_G05_ac3f93f:start",
    "milestone_G05_ac3f93f:end",
    "milestone_M04_std:start",
    "milestone_M04_std:end",
    "milestone_M09_datetime:start",
    "milestone_M09_datetime:end",
    "milestone_core_development.2:end",
}
FULL_REEDLINE_HELPER_ENDPOINTS = {
    "milestone_G05_ac3f93f:start",
    "milestone_G05_ac3f93f:end",
    "milestone_M04_std:start",
    "milestone_M04_std:end",
}
M09_INLINE_REEDLINE_ENDPOINTS = {
    "milestone_M09_datetime:start",
    "milestone_M09_datetime:end",
}
MINIMAL_TRAVERSAL_ENDPOINTS = {
    "milestone_core_development.2:end",
}
M04_TEST_HOIST_CORRECTION_ENDPOINTS = {
    "milestone_M04_std:start",
    "milestone_M04_std:end",
}
M09_COMPATIBILITY_ENDPOINTS = {
    "milestone_M09_datetime:start",
    "milestone_M09_datetime:end",
}
M03_DURATION_COMPATIBILITY_ENDPOINTS = {
    "milestone_M03_polars:start",
    "milestone_M03_polars:end",
}
M07_PIPELINE_REFACTOR_ENDPOINTS = {
    "milestone_M07_refactor:end",
}
COREDEV1_STABLE_FEATURE_GATE_ENDPOINTS = {
    "milestone_core_development.1:start",
    "milestone_core_development.1:end",
}
COREDEV4_EXPERIMENTAL_METADATA_ENDPOINTS = {
    "milestone_core_development.4:end",
}
EXPECTED_SCHEMA4_REPAIR_ENDPOINTS = (
    EXPECTED_CUSTOM_COMPLETION_ENDPOINTS
    | EXPECTED_REEDLINE_API_ENDPOINTS
    | M04_TEST_HOIST_CORRECTION_ENDPOINTS
    | M09_COMPATIBILITY_ENDPOINTS
    | M03_DURATION_COMPATIBILITY_ENDPOINTS
    | M07_PIPELINE_REFACTOR_ENDPOINTS
    | COREDEV1_STABLE_FEATURE_GATE_ENDPOINTS
    | COREDEV4_EXPERIMENTAL_METADATA_ENDPOINTS
)
EXPECTED_CUSTOM_COMPLETION_PATHS = {
    "crates/nu-parser/src/parse_keywords.rs",
    "crates/nu-parser/src/parser.rs",
    "crates/nu-protocol/src/signature.rs",
}
EXPECTED_REEDLINE_PATHS = {
    REEDLINE_CONFIG.as_posix(),
    REPL.as_posix(),
}
M09_DOCKER_EVIDENCE = (
    "SWE-Milestone-data-repartitioned/"
    "nushell_nushell_0.106.0_0.108.0/dockerfiles/"
    "milestone_M09_datetime/Dockerfile"
)
M04_DOCKER_EVIDENCE = (
    "SWE-Milestone-data-repartitioned/"
    "nushell_nushell_0.106.0_0.108.0/dockerfiles/"
    "milestone_M04_std/Dockerfile"
)
M04_PIPEFAIL_OWNER_MILESTONE = "milestone_core_development.4"
M04_PIPEFAIL_OWNER_COMMIT = "f712c37"
M04_TEST_SIGNATURE = Path("crates/nu-protocol/tests/test_signature.rs")
M04_EXTERNAL_TEST = Path("tests/shell/pipeline/commands/external.rs")
M09_SQLITE = Path("crates/nu-command/src/database/values/sqlite.rs")
M09_COMPLETER = Path("crates/nu-cli/src/completions/completer.rs")
M09_PARSER_TEST = Path("crates/nu-parser/tests/test_parser.rs")
M09_COMPLETIONS_HELPERS_TEST = Path(
    "crates/nu-cli/tests/completions/support/completions_helpers.rs"
)
M09_CONFIG_ENV_TEST = Path(
    "crates/nu-command/tests/commands/config_env_default.rs"
)
M09_CONFIG_NU_TEST = Path(
    "crates/nu-command/tests/commands/config_nu_default.rs"
)
M09_UUTILS_PATHS = (
    Path("crates/nu-command/src/filesystem/ucp.rs"),
    Path("crates/nu-command/src/filesystem/umkdir.rs"),
    Path("crates/nu-command/src/filesystem/umv.rs"),
    Path("crates/nu-command/src/filesystem/utouch.rs"),
    Path("crates/nu-command/src/platform/whoami.rs"),
    Path("crates/nu-command/src/system/uname.rs"),
    Path("crates/nu-command/src/filesystem/mktemp.rs"),
)
M09_VALUE_PATTERN_PATHS = (
    Path("crates/nu-cmd-extra/src/extra/filters/update_cells.rs"),
    Path("crates/nu-command/src/filesystem/save.rs"),
    Path("crates/nu-command/src/generators/seq_date.rs"),
    Path("crates/nu-command/src/math/abs.rs"),
    Path("crates/nu-command/src/math/ceil.rs"),
    Path("crates/nu-command/src/math/floor.rs"),
    Path("crates/nu-command/src/math/log.rs"),
    Path("crates/nu-command/src/math/round.rs"),
    Path("crates/nu-command/src/math/sqrt.rs"),
)
M03_FROM_VALUE = Path("crates/nu-protocol/src/value/from_value.rs")
M03_DOCKER_EVIDENCE = (
    "SWE-Milestone-data-repartitioned/"
    "nushell_nushell_0.106.0_0.108.0/dockerfiles/"
    "milestone_M03_polars/Dockerfile"
)
M07_METADATA_SET_TEST = Path(
    "crates/nu-command/tests/commands/debug/metadata_set.rs"
)
M07_HTTP_GET_TEST = Path(
    "crates/nu-command/tests/commands/network/http/get.rs"
)
COREDEV1_PARSER_LIB = Path("crates/nu-parser/src/lib.rs")
COREDEV4_EXPERIMENTAL_PATHS = (
    Path("crates/nu-experimental/src/lib.rs"),
    Path("crates/nu-experimental/src/options/mod.rs"),
    Path("crates/nu-experimental/src/options/example.rs"),
    Path("crates/nu-experimental/src/options/pipefail.rs"),
    Path("crates/nu-experimental/src/options/reorder_cell_paths.rs"),
)
DURATION_POSITIVE_DOCKER_EVIDENCE = (
    "SWE-Milestone-data-repartitioned/"
    "nushell_nushell_0.106.0_0.108.0/dockerfiles/"
    "milestone_core_development.1/Dockerfile"
)
DURATION_POSITIVE_COMMIT_EVIDENCE = (
    "79a6c78032f93e448287740aaefa959570351391"
)
DURATION_RAW_BLOB = "708f1f69c5b617f9629f321df131181f1c8692ff"
DURATION_POSITIVE_BLOB = "1822abbcda48799636661645d7caa90656f5121d"
G02_EXPERIMENTAL_METADATA_POSITIVE_ENDPOINT = (
    "milestone_G02_0f505d0:end"
)
REEDLINE_HELPER_EVIDENCE = (
    "delivery/transitions/transitions/"
    "milestone_milestone_G02_da9615f--0760c7d0c5/"
    "implementation.patch"
)
LEGACY_INDEX_FINGERPRINT = (
    "93f142f7ec279bf57934bbdc84c81b6bc58eee3de086d9876e1384acfccb6b34"
)
LEGACY_COMPILE_FINGERPRINT = (
    "8b06b0bec25aff24fb7c262f33aea435eb834022cecbfb3d352759da465342c8"
)
PRIOR_V5_COMPILE_FINGERPRINT = (
    "912ae1b77adc787aa3f57e1f56fef6eb7f4cebff9934fa60fdad6371ca765659"
)
PRIOR_V6_COMPILE_FINGERPRINT = (
    "87fd194677341eb0801b282870c54072a2ea432fd080e51bb357838ef515c7b6"
)
PRIOR_SCHEMA5_MANIFEST_SHA256 = (
    "f5f3a77224d36d7b611d2df9223c5d5c4b1951f43aa3b327201c7cb229e11a21"
)
PRIOR_SCHEMA5_REPAIRS_SHA256 = (
    "a906e1786aeaf80173a3988f51a3059fc95eef1bcbaabe48da267285e3d8dc48"
)
PRIOR_SCHEMA5_CLOSURE_SHA256 = (
    "680f47974a6bdf5d6da319bd6f1e7f6eddbe215989711a8ca82cda3982bbfa06"
)
PRIOR_SCHEMA6_MANIFEST_SHA256 = (
    "86f4fd945f062ace757951475a34475eb32328e271d008c938cbebe84a558f41"
)
PRIOR_SCHEMA6_REPAIRS_SHA256 = (
    "6e14c8ef4ade06f3bb8012374e8a418390ba3bc2accb30456e3d0894fadbc0b2"
)
PRIOR_SCHEMA6_CLOSURE_SHA256 = (
    "9c485a9c46c101eec5b2abb81c80ba370666284154c529fc015a3cbc3ad075fe"
)
EXPECTED_PRIOR_V5_CHECKPOINT_ENDPOINTS = {
    "milestone_G05_ac3f93f:start",
    "milestone_G05_ac3f93f:end",
    "milestone_M04_std:start",
    "milestone_M04_std:end",
    "milestone_M09_datetime:start",
    "milestone_M09_datetime:end",
    "milestone_M07_refactor:end",
    "milestone_core_development.1:start",
    "milestone_core_development.1:end",
    "milestone_core_development.2:end",
}
EXPECTED_SCHEMA7_REQUIRED_COMPILE_ENDPOINTS = (
    M03_DURATION_COMPATIBILITY_ENDPOINTS
    | COREDEV4_EXPERIMENTAL_METADATA_ENDPOINTS
)
SCHEMA6_PARSER_FIXTURE_ENDPOINTS = (
    M03_DURATION_COMPATIBILITY_ENDPOINTS
    | COREDEV4_EXPERIMENTAL_METADATA_ENDPOINTS
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


def cargo_manifest_identity(checkout: Path) -> tuple[str, list[dict[str, str]]]:
    rows = []
    for path in sorted(checkout.rglob("Cargo.toml")):
        if ".git" in path.parts or "target" in path.parts:
            continue
        rows.append(
            {
                "path": path.relative_to(checkout).as_posix(),
                "sha256": sha256(path),
            }
        )
    payload = "".join(
        f"{row['path']}\t{row['sha256']}\n" for row in rows
    ).encode()
    return bytes_sha256(payload), rows


def add_custom_completion_fields(checkout: Path) -> tuple[int, int, list[str]]:
    """Apply the exact reviewed fix_custom_completion.py transformation."""
    signature_path = checkout / SIGNATURE
    content = signature_path.read_text()
    if "custom_completion" in content:
        return 0, 0, []
    content, import_count = re.subn(
        r"(use crate::\{\s*\n?\s*)BlockId,",
        r"\1BlockId, DeclId,",
        content,
    )
    content, flag_count = re.subn(
        r"(pub struct Flag \{[^}]*pub default_value: Option<Value>,)",
        r"\1\n    pub custom_completion: Option<DeclId>,",
        content,
        flags=re.DOTALL,
    )
    content, positional_count = re.subn(
        r"(pub struct PositionalArg \{[^}]*pub default_value: Option<Value>,)",
        r"\1\n    pub custom_completion: Option<DeclId>,",
        content,
        flags=re.DOTALL,
    )
    if (import_count, flag_count, positional_count) != (1, 1, 1):
        raise RuntimeError(
            "custom_completion definition edit is not exactly 1+1+1: "
            f"{import_count}+{flag_count}+{positional_count}"
        )
    signature_path.write_text(content)

    constructor_count = 0
    changed_paths = {SIGNATURE.as_posix()}
    for path in sorted(checkout.rglob("*.rs")):
        if "target" in path.parts or ".git" in path.parts:
            continue
        content = path.read_text(errors="surrogateescape")
        if re.search(
            r"default_value:.*\n\s*custom_completion: None,", content
        ):
            continue
        if "Flag {" not in content and "PositionalArg {" not in content:
            continue
        lines = content.split("\n")
        new_lines = []
        modified = False
        for index, line in enumerate(lines):
            new_lines.append(line)
            if (
                "default_value:" not in line
                or not line.rstrip().endswith(",")
                or "pub default_value" in line
            ):
                continue
            current_indent = len(line) - len(line.lstrip())
            in_struct = False
            for previous_index in range(
                index - 1, max(index - 30, -1), -1
            ):
                previous = lines[previous_index]
                stripped = previous.strip()
                if (
                    ("Flag {" in previous or "PositionalArg {" in previous)
                    and ".." not in previous
                ):
                    in_struct = True
                    break
                if stripped in {"},", "}", ");", ")"}:
                    previous_indent = len(previous) - len(previous.lstrip())
                    if previous_indent <= current_indent:
                        break
            if in_struct:
                new_lines.append(
                    f"{' ' * current_indent}custom_completion: None,"
                )
                constructor_count += 1
                modified = True
        if modified:
            path.write_text("\n".join(new_lines), errors="surrogateescape")
            changed_paths.add(path.relative_to(checkout).as_posix())
    return 3, constructor_count, sorted(changed_paths)


def extract_added_file(patch: Path, target: str) -> str:
    lines = patch.read_text().splitlines()
    marker = f"+++ b/{target}"
    try:
        start = lines.index(marker) + 1
    except ValueError as exc:
        raise RuntimeError(f"official helper is absent: {target}") from exc
    while start < len(lines) and not lines[start].startswith("@@"):
        start += 1
    if start == len(lines):
        raise RuntimeError(f"official helper hunk is absent: {target}")
    result = []
    for line in lines[start + 1 :]:
        if line.startswith("diff --git "):
            break
        if line.startswith("+") and not line.startswith("+++"):
            result.append(line[1:])
        elif line.startswith("\\ No newline"):
            continue
        else:
            raise RuntimeError(
                f"official helper is not a pure added file: {target}: {line}"
            )
    return "\n".join(result) + "\n"


def load_official_reedline_helper(evidence: Path) -> tuple[dict, str]:
    source = extract_added_file(evidence, "fix_reedline_api.py")
    namespace = {"__name__": "official_fix_reedline_api"}
    exec(compile(source, str(evidence), "exec"), namespace)
    if not all(
        callable(namespace.get(name))
        for name in ("fix_reedline_config", "fix_repl")
    ):
        raise RuntimeError("official reedline helper functions are absent")
    return namespace, bytes_sha256(source.encode())


def apply_official_reedline_helper(
    checkout: Path, helper: dict
) -> tuple[dict, list[str]]:
    expected = reedline_api_transformation(checkout)
    helper["fix_reedline_config"](checkout / REEDLINE_CONFIG)
    helper["fix_repl"](checkout / REPL)
    observed = {
        REEDLINE_CONFIG.as_posix(): (checkout / REEDLINE_CONFIG).read_text(),
        REPL.as_posix(): (checkout / REPL).read_text(),
    }
    if observed != expected["expected_contents"]:
        raise RuntimeError("official reedline helper replay differs from audit")
    return expected, sorted(expected["changed_paths"])


def apply_traversal_only(checkout: Path) -> dict:
    path = checkout / REEDLINE_CONFIG
    before = path.read_text()
    after, import_middle_count = re.subn(
        r",\s*TraversalDirection\s*,", ",", before
    )
    after, import_leading_count = re.subn(
        r"TraversalDirection\s*,", "", after
    )
    after, method_count = re.subn(
        r"\.with_traversal_direction\(TraversalDirection::\w+\)",
        "",
        after,
    )
    path.write_text(after)
    return {
        "traversal_direction_import_removal_count": (
            import_middle_count + import_leading_count
        ),
        "traversal_method_removal_count": method_count,
        "tab_traversal_block_removal_count": 0,
        "immediately_accept_line_removal_count": 0,
        "repl_semicolon_addition_count": 0,
        "text_object_import_replacement_count": 0,
        "nested_edit_command_replacement_count": 0,
        "simple_edit_command_replacement_count": 0,
        "parse_text_object_compatibility_rewrite_count": 0,
        "changed_paths": (
            [REEDLINE_CONFIG.as_posix()] if after != before else []
        ),
    }


def apply_m09_reedline_inline(checkout: Path) -> dict:
    """Replay only M09 Dockerfile's complete reedline_config.rs block."""
    path = checkout / REEDLINE_CONFIG
    before = path.read_text()
    content, text_object_import_count = re.subn(
        (
            r"MenuBuilder, Reedline, ReedlineEvent, ReedlineMenu, "
            r"TextObject, TextObjectScope,"
        ),
        "MenuBuilder, Reedline, ReedlineEvent, ReedlineMenu,",
        before,
    )
    content, traversal_import_count = re.subn(
        r"TextObjectType, TraversalDirection,\s*", "", content
    )
    content, traversal_method_count = re.subn(
        r"\.with_traversal_direction\(TraversalDirection::\w+\)",
        "",
        content,
    )
    def unsupported(command: str, validation: str) -> str:
        return (
            "{\n"
            f"{validation}"
            "            return Err(ShellError::GenericError {\n"
            f'                error: "reedline EditCommand {command} is unavailable".into(),\n'
            "                msg: \"the resolved reedline API does not provide this command\".into(),\n"
            "                span: Some(span),\n"
            "                help: None,\n"
            "                inner: vec![],\n"
            "            });\n"
            "        }"
        )

    pair_validation = (
        '            let value = extract_value("left", record, span)?;\n'
        "            let _left = extract_char(value)?;\n"
        '            let value = extract_value("right", record, span)?;\n'
        "            let _right = extract_char(value)?;\n"
    )
    text_object_validation = (
        "            parse_text_object(record, config, span)?;\n"
    )
    nested_count = 0
    for command in (
        "cutinsidepair",
        "copyinsidepair",
        "cutaroundpair",
        "copyaroundpair",
    ):
        pattern = rf'"{command}"\s*=>\s*\{{[^{{}}]*\{{[^{{}}]*\}}\s*\}}'
        content, count = re.subn(
            pattern,
            f'"{command}" => {unsupported(command, pair_validation)}',
            content,
            flags=re.DOTALL,
        )
        nested_count += count
    simple_count = 0
    for command in ("copytextobject", "cuttextobject"):
        pattern = rf'"{command}"\s*=>\s*EditCommand::\w+\s*\{{[^}}]*\}},'
        content, count = re.subn(
            pattern,
            f'"{command}" => {unsupported(command, text_object_validation)}',
            content,
            flags=re.DOTALL,
        )
        simple_count += count
    content, parse_count = re.subn(
        (
            r"(fn parse_text_object\(.*?\n"
            r"\) -> Result<TextObject, ShellError> \{.*?\n\})"
        ),
        (
            "fn parse_text_object(\n"
            "    record: &Record,\n"
            "    config: &Config,\n"
            "    span: Span,\n"
            ") -> Result<(), ShellError> {\n"
            '    let scope_value = extract_value("scope", record, span)?;\n'
            "    let scope_str = scope_value\n"
            '        .to_expanded_string("", config)\n'
            "        .to_ascii_lowercase();\n"
            "    match scope_str.as_str() {\n"
            '        "inner" | "around" => (),\n'
            "        str => {\n"
            "            return Err(ShellError::InvalidValue {\n"
            "                valid: \"'inner' or 'around'\".into(),\n"
            "                actual: format!(\"'{str}'\"),\n"
            "                span: scope_value.span(),\n"
            "            });\n"
            "        }\n"
            "    }\n\n"
            '    let type_value = extract_value("object_type", record, span)?;\n'
            "    let type_str = type_value\n"
            '        .to_expanded_string("", config)\n'
            "        .to_ascii_lowercase();\n"
            "    match type_str.as_str() {\n"
            '        "word" | "bigword" | "brackets" | "bracket" | "quote" | "quotes" => (),\n'
            "        str => {\n"
            "            return Err(ShellError::InvalidValue {\n"
            "                valid: \"'word', 'bigword', 'brackets', or 'quote'\".into(),\n"
            "                actual: format!(\"'{str}'\"),\n"
            "                span: type_value.span(),\n"
            "            });\n"
            "        }\n"
            "    }\n\n"
            "    Ok(())\n"
            "}"
        ),
        content,
        flags=re.DOTALL,
    )
    path.write_text(content)
    return {
        "traversal_direction_import_removal_count": traversal_import_count,
        "traversal_method_removal_count": traversal_method_count,
        "tab_traversal_block_removal_count": 0,
        "immediately_accept_line_removal_count": 0,
        "repl_semicolon_addition_count": 0,
        "text_object_import_replacement_count": text_object_import_count,
        "nested_edit_command_replacement_count": nested_count,
        "simple_edit_command_replacement_count": simple_count,
        "parse_text_object_compatibility_rewrite_count": parse_count,
        "changed_paths": (
            [REEDLINE_CONFIG.as_posix()] if content != before else []
        ),
    }


def test_edit_record(
    checkout: Path,
    path: Path,
    before: str,
    after: str,
    *,
    reason: str,
    policy: str,
    edit_count: int,
    evidence: dict,
) -> dict:
    if before == after or edit_count <= 0:
        raise RuntimeError(f"empty test correction: {path}")
    return {
        "path": path.as_posix(),
        "before_sha256": bytes_sha256(before.encode()),
        "after_sha256": bytes_sha256(after.encode()),
        "reason": reason,
        "policy": policy,
        "edit_count": edit_count,
        "evidence": evidence,
    }


def apply_m04_test_hoist_corrections(
    checkout: Path, *, docker_sha: str
) -> tuple[dict, list[str], list[dict]]:
    """Remove two reviewed pieces of later-test contamination from M04."""
    signature_path = checkout / M04_TEST_SIGNATURE
    signature_before = signature_path.read_text()
    signature_after, completion_count = re.subn(
        r"^\s*completion: None,\n",
        "",
        signature_before,
        flags=re.MULTILINE,
    )
    if completion_count != 5:
        raise RuntimeError(
            f"M04 completion fixture correction is not 5: {completion_count}"
        )
    signature_path.write_text(signature_after)

    external_path = checkout / M04_EXTERNAL_TEST
    external_before = external_path.read_text()
    external_without_import, rstest_import_count = replace_exact(
        external_before,
        "use rstest::rstest;\n",
        "",
        expected=1,
        label="M04 future rstest import",
    )
    pipefail_pattern = (
        r"\n// FIXME: ignore these cases for now, the value inside a pipeline\n"
        r"// makes all previous exit status untracked\.\n"
        r"// #\[case\(\"nu --testbin fail 10 \| nu --testbin fail 20 \| 10\", 10\)\]\n"
        r"// #\[case\(\"nu --testbin fail 20 \| 10 \| nu --testbin fail\", 20\)\]\n"
        r"// #\[case\(\"30 \| nu --testbin fail \| nu --testbin fail 30\", 1\)\]\n"
        r"#\[rstest\]\n"
        r"(?:#\[case\([^\n]+\)\]\n){6}"
        r"fn pipefail_feature\([^\n]+\) \{\n"
        r".*?\n"
        r"\}\n"
    )
    external_after, pipefail_block_count = re.subn(
        pipefail_pattern,
        "",
        external_without_import,
        flags=re.DOTALL,
    )
    if pipefail_block_count != 1:
        raise RuntimeError(
            "M04 pipefail contamination correction is not exactly one block: "
            f"{pipefail_block_count}"
        )
    external_path.write_text(external_after)
    external_blob = str(
        run(
            "git",
            "hash-object",
            M04_EXTERNAL_TEST.as_posix(),
            cwd=checkout,
            capture=True,
        )
    ).strip()
    if not external_blob.startswith("5b42798b"):
        raise RuntimeError(
            f"M04 canonical external.rs blob mismatch: {external_blob}"
        )

    evidence = {
        "dockerfile": M04_DOCKER_EVIDENCE,
        "dockerfile_sha256": docker_sha,
        "canonical_owner_milestone": M04_PIPEFAIL_OWNER_MILESTONE,
        "canonical_owner_commit": M04_PIPEFAIL_OWNER_COMMIT,
        "reason": (
            "the M04 owned eight-commit set excludes f712c37 and its raw "
            "start/end product trees omit the pipefail implementation; the "
            "synthetic final tests hoisted the core_development.4-owned test"
        ),
    }
    test_edits = [
        test_edit_record(
            checkout,
            M04_TEST_SIGNATURE,
            signature_before,
            signature_after,
            reason="remove_future_completion_fixture_fields",
            policy="schema_correction_without_assertion_suppression",
            edit_count=completion_count,
            evidence={
                "dockerfile": M04_DOCKER_EVIDENCE,
                "dockerfile_sha256": docker_sha,
                "docker_lines": ["53", "68"],
            },
        ),
        test_edit_record(
            checkout,
            M04_EXTERNAL_TEST,
            external_before,
            external_after,
            reason="remove_future_pipefail_test_hoist_contamination",
            policy="canonical_ownership_test_state_correction",
            edit_count=pipefail_block_count + rstest_import_count,
            evidence=evidence,
        ),
    ]
    return (
        {
            "completion_fixture_field_removal_count": completion_count,
            "pipefail_test_block_removal_count": pipefail_block_count,
            "pipefail_active_case_removal_count": 6,
            "pipefail_rstest_import_removal_count": rstest_import_count,
            "canonical_external_test_blob": external_blob,
        },
        sorted(
            [
                M04_TEST_SIGNATURE.as_posix(),
                M04_EXTERNAL_TEST.as_posix(),
            ]
        ),
        test_edits,
    )


def replace_exact(
    content: str,
    old: str,
    new: str,
    *,
    expected: int,
    label: str,
) -> tuple[str, int]:
    observed = content.count(old)
    if observed != expected:
        raise RuntimeError(f"{label} is not {expected}: {observed}")
    return content.replace(old, new), observed


def add_rest_to_multiline_value_pattern(
    content: str, variant: str, *, expected: int
) -> tuple[str, int]:
    pattern = re.compile(
        rf"(Value::{variant}\s*\{{\s*ref val,\s*internal_span,\n)(\s*)\}}"
    )

    def replacement(match: re.Match[str]) -> str:
        closing_indent = match.group(2)
        return (
            match.group(1)
            + closing_indent
            + "    ..\n"
            + closing_indent
            + "}"
        )

    result, count = pattern.subn(replacement, content)
    if count != expected:
        raise RuntimeError(
            f"Value::{variant} rest-pattern count is not {expected}: {count}"
        )
    return result, count


def apply_reviewed_resolved_api_subset(
    checkout: Path,
    *,
    include_save: bool,
    include_reedline_pair_variants: bool,
    label: str,
) -> tuple[dict, list[str]]:
    """Apply only the reviewed source subset shared with the passing M09 patch."""
    changed_paths: set[str] = set()
    counts = {
        "sqlite_import_replacement_count": 0,
        "sqlite_main_db_replacement_count": 0,
        "completer_invalid_dereference_semantic_replacement_count": 0,
        "reedline_pair_variant_replacement_count": 0,
        "value_non_exhaustive_rest_addition_count": 0,
        "operation_count": 0,
    }

    sqlite_path = checkout / M09_SQLITE
    content = sqlite_path.read_text()
    before = content
    content, count = replace_exact(
        content,
        "Connection, DatabaseName, Error as SqliteError",
        "Connection, Error as SqliteError, MAIN_DB",
        expected=1,
        label=f"{label} rusqlite import",
    )
    counts["sqlite_import_replacement_count"] += count
    content, count = replace_exact(
        content,
        "DatabaseName::Main",
        "MAIN_DB",
        expected=2,
        label=f"{label} rusqlite main database",
    )
    counts["sqlite_main_db_replacement_count"] += count
    sqlite_path.write_text(content)
    if content != before:
        changed_paths.add(M09_SQLITE.as_posix())

    completer_path = checkout / M09_COMPLETER
    content = completer_path.read_text()
    before = content
    content, parameter_count = replace_exact(
        content,
        "        need_fallback: bool,\n",
        "        mut need_fallback: bool,\n",
        expected=1,
        label=f"{label} mutable need_fallback parameter",
    )
    content, assignment_count = replace_exact(
        content,
        "                *need_fallback = false;\n",
        "                need_fallback = false;\n",
        expected=1,
        label=f"{label} need_fallback assignment",
    )
    counts[
        "completer_invalid_dereference_semantic_replacement_count"
    ] = parameter_count + assignment_count
    completer_path.write_text(content)
    if content != before:
        changed_paths.add(M09_COMPLETER.as_posix())

    if include_reedline_pair_variants:
        reedline_path = checkout / REEDLINE_CONFIG
        content = reedline_path.read_text()
        before = content
        for old, new in (
            ("EditCommand::CutInside", "EditCommand::CutInsidePair"),
            ("EditCommand::YankInside", "EditCommand::CopyInsidePair"),
        ):
            content, count = replace_exact(
                content,
                old,
                new,
                expected=1,
                label=f"{label} reedline variant {old}",
            )
            counts["reedline_pair_variant_replacement_count"] += count
        reedline_path.write_text(content)
        if content != before:
            changed_paths.add(REEDLINE_CONFIG.as_posix())

    update_path = checkout / M09_VALUE_PATTERN_PATHS[0]
    content = update_path.read_text()
    content, count = re.subn(
        (
            r"(Value::Record \{\n"
            r"\s*ref mut val,\n"
            r"\s*internal_span,\n)"
            r"(\s*)\}"
        ),
        lambda match: (
            match.group(1)
            + match.group(2)
            + "    ..\n"
            + match.group(2)
            + "}"
        ),
        content,
        count=1,
    )
    if count != 1:
        raise RuntimeError(
            f"{label} Value::Record rest addition is not 1: {count}"
        )
    counts["value_non_exhaustive_rest_addition_count"] += count
    update_path.write_text(content)
    changed_paths.add(M09_VALUE_PATTERN_PATHS[0].as_posix())

    if include_save:
        save_path = checkout / M09_VALUE_PATTERN_PATHS[1]
        content = save_path.read_text()
        content, count = replace_exact(
            content,
            "Value::Custom { val, internal_span }",
            "Value::Custom { val, internal_span, .. }",
            expected=1,
            label=f"{label} Value::Custom rest addition",
        )
        counts["value_non_exhaustive_rest_addition_count"] += count
        save_path.write_text(content)
        changed_paths.add(M09_VALUE_PATTERN_PATHS[1].as_posix())

    seq_path = checkout / M09_VALUE_PATTERN_PATHS[2]
    content = seq_path.read_text()
    for variant in ("Int", "Duration"):
        content, count = replace_exact(
            content,
            f"Value::{variant} {{ val, internal_span }}",
            f"Value::{variant} {{ val, internal_span, .. }}",
            expected=1,
            label=f"{label} Value::{variant} rest addition",
        )
        counts["value_non_exhaustive_rest_addition_count"] += count
    seq_path.write_text(content)
    changed_paths.add(M09_VALUE_PATTERN_PATHS[2].as_posix())

    for relative in M09_VALUE_PATTERN_PATHS[3:]:
        path = checkout / relative
        content, count = add_rest_to_multiline_value_pattern(
            path.read_text(), "Range", expected=2
        )
        counts["value_non_exhaustive_rest_addition_count"] += count
        path.write_text(content)
        changed_paths.add(relative.as_posix())

    expected_value_count = 16 if include_save else 15
    expected_path_count = (
        11
        if include_save or include_reedline_pair_variants
        else 10
    )
    counts["operation_count"] = (
        counts["sqlite_import_replacement_count"]
        + counts["sqlite_main_db_replacement_count"]
        + counts[
            "completer_invalid_dereference_semantic_replacement_count"
        ]
        + counts["reedline_pair_variant_replacement_count"]
        + counts["value_non_exhaustive_rest_addition_count"]
    )
    expected_operation_count = (
        21 if include_save else 20
    ) + (2 if include_reedline_pair_variants else 0)
    if (
        counts["value_non_exhaustive_rest_addition_count"]
        != expected_value_count
        or counts["operation_count"] != expected_operation_count
        or len(changed_paths) != expected_path_count
    ):
        raise RuntimeError(
            f"{label} reviewed source subset mismatch: "
            f"{counts}/{sorted(changed_paths)}"
        )
    return counts, sorted(changed_paths)


def apply_m03_signature_fixture_correction(
    checkout: Path, *, docker_sha: str
) -> tuple[dict, list[str], list[dict]]:
    """Remove only the five impossible future completion fields from M03."""
    path = checkout / M04_TEST_SIGNATURE
    before = path.read_text()
    if bytes_sha256(before.encode()) != (
        "eeb9c44a51edda253e94a84d83983da2b03e167f72e2a90e802c7190b036dad4"
    ):
        raise RuntimeError("M03 signature fixture before identity mismatch")
    after, count = re.subn(
        r"^\s*completion: None,\n",
        "",
        before,
        flags=re.MULTILINE,
    )
    if count != 5 or bytes_sha256(after.encode()) != (
        "98cf616d4d71ddf256ad6fb901e74aa1ba8e299997342861188132276bd2a984"
    ):
        raise RuntimeError(
            f"M03 completion fixture correction is not exact: {count}"
        )
    path.write_text(after)
    edit = test_edit_record(
        checkout,
        M04_TEST_SIGNATURE,
        before,
        after,
        reason="remove_future_completion_fixture_fields",
        policy="schema_correction_without_assertion_suppression",
        edit_count=count,
        evidence={
            "dockerfile": M03_DOCKER_EVIDENCE,
            "dockerfile_sha256": docker_sha,
            "reviewed_before_git_blob": (
                "e2d0d3b9c074faf86e8351bcec0f471d170d16e3"
            ),
            "reviewed_after_git_blob": (
                "011607959a8fb30bdd3a378a9f079b8c4b7dde88"
            ),
        },
    )
    return (
        {"completion_fixture_field_removal_count": count},
        [M04_TEST_SIGNATURE.as_posix()],
        [edit],
    )


def apply_m09_test_api_compatibility(
    checkout: Path,
    *,
    docker_sha: str,
    label: str = "M09",
    docker_evidence: str = M09_DOCKER_EVIDENCE,
    failure_job_id: str | None = None,
) -> tuple[dict, list[str], list[dict]]:
    """Align three tests to APIs already implemented by the runnable tree."""
    specifications = (
        (
            M09_COMPLETIONS_HELPERS_TEST,
            """        Value::List {
            vals: vec![Value::String {
                val: dir_str,
                internal_span,
            }],
            internal_span,
        },""",
            (
                "        Value::list("
                "vec![Value::string(dir_str, internal_span)], internal_span),"
            ),
            "05d977149b160e647bac03b77f95c26bd2b0f10a3b31fd7956f470322e9cc3e5",
            "9c766a4a7f2c50e1321437a75b12727d322dcb791f7596ed63fb9b3caeb05390",
            {
                "passing_endpoint_patch": (
                    "delivery/states/endpoints/"
                    "milestone_G01_48bca0a_start--f2bd5f266d/test.patch"
                ),
                "passing_endpoint_patch_lines": "39-55",
                "passing_endpoint_after_git_blob": (
                    "a6e429eb5d2842dc935de0505cbc1afdca6e6c61"
                ),
            },
        ),
        (
            M09_CONFIG_ENV_TEST,
            """nu_utils::ConfigFileKind::Env
            .default()
            .replace(['\\n', '\\r'], "")""",
            """nu_utils::get_default_env().replace(['\\n', '\\r'], "")""",
            "8d86182db1dcf7678d02cab8f9581d5a2f5fb1dc0e89cd2db295940d67f40bea",
            "7a7e9d70bc3728650c528c53527564e599a1b08b7c083899c682001ee1a35f8f",
            {
                "runnable_command_implementation": (
                    "crates/nu-command/src/env/config/config_env.rs"
                ),
                "runnable_command_api": "nu_utils::get_default_env()",
            },
        ),
        (
            M09_CONFIG_NU_TEST,
            """nu_utils::ConfigFileKind::Config
            .default()
            .replace(['\\n', '\\r'], "")""",
            """nu_utils::get_default_config().replace(['\\n', '\\r'], "")""",
            "7fc02a92396daa04b812dc7b438994c567497500918afdd2d917a61085fe73a7",
            "d722698a4bdd3336b366ab36815124d1c188c92cd0c2f5567445cc867bff27b5",
            {
                "runnable_command_implementation": (
                    "crates/nu-command/src/env/config/config_nu.rs"
                ),
                "runnable_command_api": "nu_utils::get_default_config()",
            },
        ),
    )
    changed_paths: list[str] = []
    test_edits: list[dict] = []
    for path, old, new, before_sha, after_sha, evidence in specifications:
        target = checkout / path
        before = target.read_text()
        if bytes_sha256(before.encode()) != before_sha:
            raise RuntimeError(
                f"{label} test before identity mismatch: {path}"
            )
        after, count = replace_exact(
            before,
            old,
            new,
            expected=1,
            label=f"{label} reviewed test API alignment {path}",
        )
        if bytes_sha256(after.encode()) != after_sha:
            raise RuntimeError(
                f"{label} test after identity mismatch: {path}"
            )
        target.write_text(after)
        changed_paths.append(path.as_posix())
        test_edits.append(
            test_edit_record(
                checkout,
                path,
                before,
                after,
                reason="align_test_fixture_with_runnable_source_api",
                policy="exact_positive_evidence_without_test_suppression",
                edit_count=count,
                evidence={
                    **evidence,
                    "dockerfile": docker_evidence,
                    "dockerfile_sha256": docker_sha,
                    "runtime_failure_job_id": failure_job_id,
                },
            )
        )
    return (
        {
            "test_api_alignment_record_count": len(test_edits),
            "test_api_alignment_operation_count": sum(
                edit["edit_count"] for edit in test_edits
            ),
        },
        sorted(changed_paths),
        test_edits,
    )


def apply_config_default_test_api_compatibility(
    checkout: Path,
    *,
    label: str,
    docker_evidence: str,
    docker_sha: str,
    failure_job_id: str,
) -> tuple[dict, list[str], list[dict]]:
    """Align only the two default-config fixtures to the runnable API."""
    specifications = (
        (
            M09_CONFIG_ENV_TEST,
            """nu_utils::ConfigFileKind::Env
            .default()
            .replace(['\\n', '\\r'], "")""",
            """nu_utils::get_default_env().replace(['\\n', '\\r'], "")""",
            "8d86182db1dcf7678d02cab8f9581d5a2f5fb1dc0e89cd2db295940d67f40bea",
            "7a7e9d70bc3728650c528c53527564e599a1b08b7c083899c682001ee1a35f8f",
            "nu_utils::get_default_env()",
        ),
        (
            M09_CONFIG_NU_TEST,
            """nu_utils::ConfigFileKind::Config
            .default()
            .replace(['\\n', '\\r'], "")""",
            """nu_utils::get_default_config().replace(['\\n', '\\r'], "")""",
            "7fc02a92396daa04b812dc7b438994c567497500918afdd2d917a61085fe73a7",
            "d722698a4bdd3336b366ab36815124d1c188c92cd0c2f5567445cc867bff27b5",
            "nu_utils::get_default_config()",
        ),
    )
    changed_paths: list[str] = []
    test_edits: list[dict] = []
    for path, old, new, before_sha, after_sha, runnable_api in specifications:
        target = checkout / path
        before = target.read_text()
        if bytes_sha256(before.encode()) != before_sha:
            raise RuntimeError(
                f"{label} config test before identity mismatch: {path}"
            )
        after, count = replace_exact(
            before,
            old,
            new,
            expected=1,
            label=f"{label} reviewed config test API alignment {path}",
        )
        if bytes_sha256(after.encode()) != after_sha:
            raise RuntimeError(
                f"{label} config test after identity mismatch: {path}"
            )
        target.write_text(after)
        changed_paths.append(path.as_posix())
        test_edits.append(
            test_edit_record(
                checkout,
                path,
                before,
                after,
                reason="align_test_fixture_with_runnable_source_api",
                policy="exact_positive_evidence_without_test_suppression",
                edit_count=count,
                evidence={
                    "runnable_api": runnable_api,
                    "positive_reference_endpoints": [
                        "milestone_M09_datetime:start",
                        "milestone_M09_datetime:end",
                    ],
                    "dockerfile": docker_evidence,
                    "dockerfile_sha256": docker_sha,
                    "runtime_failure_job_id": failure_job_id,
                },
            )
        )
    return (
        {
            "config_test_api_alignment_record_count": len(test_edits),
            "config_test_api_alignment_operation_count": sum(
                edit["edit_count"] for edit in test_edits
            ),
        },
        sorted(changed_paths),
        test_edits,
    )


def apply_m03_pipefail_test_hoist_correction(
    checkout: Path, *, docker_sha: str
) -> tuple[dict, list[str], list[dict]]:
    """Remove the exact future-owned pipefail test hoisted into both M03 trees."""
    path = checkout / M04_EXTERNAL_TEST
    before = path.read_text()
    if bytes_sha256(before.encode()) != (
        "f6f17454bc814390d15f92b28fa6550592b581b0e272106337ec5c6ed8fef3b0"
    ):
        raise RuntimeError("M03 external test before identity mismatch")
    without_import, import_count = replace_exact(
        before,
        "use rstest::rstest;\n",
        "",
        expected=1,
        label="M03 future rstest import",
    )
    pipefail_pattern = (
        r"\n// FIXME: ignore these cases for now, the value inside a pipeline\n"
        r"// makes all previous exit status untracked\.\n"
        r"// #\[case\(\"nu --testbin fail 10 \| nu --testbin fail 20 \| 10\", 10\)\]\n"
        r"// #\[case\(\"nu --testbin fail 20 \| 10 \| nu --testbin fail\", 20\)\]\n"
        r"// #\[case\(\"30 \| nu --testbin fail \| nu --testbin fail 30\", 1\)\]\n"
        r"#\[rstest\]\n"
        r"(?:#\[case\([^\n]+\)\]\n){6}"
        r"fn pipefail_feature\([^\n]+\) \{\n"
        r".*?\n"
        r"\}\n"
    )
    after, block_count = re.subn(
        pipefail_pattern,
        "",
        without_import,
        flags=re.DOTALL,
    )
    if (
        block_count != 1
        or bytes_sha256(after.encode())
        != "d69f4925d50de7649e1c252aeee510752add2d81d48bcb062c07cb22ef37f932"
    ):
        raise RuntimeError(
            f"M03 pipefail hoist correction is not exact: {block_count}"
        )
    path.write_text(after)
    after_blob = str(
        run(
            "git",
            "hash-object",
            M04_EXTERNAL_TEST.as_posix(),
            cwd=checkout,
            capture=True,
        )
    ).strip()
    if after_blob != "5b42798ba5e2d960c792de09613bb40eedc809ec":
        raise RuntimeError(f"M03 external test after blob mismatch: {after_blob}")
    edit = test_edit_record(
        checkout,
        M04_EXTERNAL_TEST,
        before,
        after,
        reason="remove_future_pipefail_test_hoist_contamination",
        policy="canonical_ownership_test_state_correction",
        edit_count=import_count + block_count,
        evidence={
            "runtime_failure_job_id": "14289040",
            "positive_reference_endpoints": [
                "milestone_M04_std:start",
                "milestone_M04_std:end",
            ],
            "canonical_owner_milestone": M04_PIPEFAIL_OWNER_MILESTONE,
            "canonical_owner_commit": M04_PIPEFAIL_OWNER_COMMIT,
            "dockerfile": M03_DOCKER_EVIDENCE,
            "dockerfile_sha256": docker_sha,
            "reason": (
                "M03 product NuOpts has no experimental field and its owned "
                "tree predates the core_development.4 pipefail implementation"
            ),
        },
    )
    return (
        {
            "pipefail_test_block_removal_count": block_count,
            "pipefail_active_case_removal_count": 6,
            "pipefail_rstest_import_removal_count": import_count,
            "canonical_external_test_blob": after_blob,
        },
        [M04_EXTERNAL_TEST.as_posix()],
        [edit],
    )


def apply_schema6_parser_fixture_correction(
    checkout: Path, *, endpoint_id: str
) -> tuple[dict, list[str], list[dict]]:
    """Apply the exact passing M09 parser assertion hunk to three v0.106 trees."""
    path = checkout / M09_PARSER_TEST
    before = path.read_text()
    before_sha = bytes_sha256(before.encode())
    if before_sha != (
        "210406daef09fc526cd74feb27d7e95f84029aace9f456f010064517678b4336"
    ):
        raise RuntimeError(
            f"schema6 parser fixture before identity mismatch: {endpoint_id}"
        )
    after, count = replace_exact(
        before,
        "Some(ParseError::InvalidBinaryString(_, _))",
        "Some(ParseError::IncorrectValue(_, _, _))",
        expected=2,
        label=f"schema6 parser error fixture {endpoint_id}",
    )
    after_sha = bytes_sha256(after.encode())
    if after_sha != (
        "c2e996966b5eb795aa7056d72be84acbe9e8cbb072d4f7603deb24ca31f3b0b1"
    ):
        raise RuntimeError(
            f"schema6 parser fixture after identity mismatch: {endpoint_id}"
        )
    path.write_text(after)
    edit = test_edit_record(
        checkout,
        M09_PARSER_TEST,
        before,
        after,
        reason="align_parser_error_fixture_with_runnable_parser",
        policy="exact_prior_schema5_hunk_without_test_suppression",
        edit_count=count,
        evidence={
            "failure_job_id": "14288485",
            "failure_kind": "2x_E0599_InvalidBinaryString_absent",
            "runnable_parser_implementation": (
                "crates/nu-parser/src/parser.rs"
            ),
            "runnable_parser_emits": "ParseError::IncorrectValue",
            "prior_schema5_reference_endpoint": (
                "milestone_M09_datetime:start"
            ),
            "prior_schema5_reference_patch_sha256": (
                "7add449972fd6c27b5de1bf490c8e6cf5ae5a5e7613d5635575d63bf3d3a3545"
            ),
            "prior_schema5_reference_before_sha256": (
                "3ea1b504b03b8d471bd0dcaa6cb380e4fbd3e6caed1c99aee90646b351da0003"
            ),
            "prior_schema5_reference_after_sha256": (
                "4054ee1818060cf9eacf99a7c34843b2c10e897da68a803f01d52a0a0a8c6bee"
            ),
            "hunk_equivalence": (
                "exact_two_assertion_patterns; whole_file_differs_only_by_"
                "an_unrelated_trailing_parser_test"
            ),
            "reviewed_before_git_blob": (
                "c1668d8993d1914d5d9d028bf6114efa0d7255e4"
            ),
            "reviewed_after_git_blob": (
                "a9695d1a0f1af5b0e802a65ea143c8875e288287"
            ),
        },
    )
    return (
        {
            "parser_error_fixture_replacement_count": count,
            "before_sha256": before_sha,
            "after_sha256": after_sha,
        },
        [M09_PARSER_TEST.as_posix()],
        [edit],
    )


def apply_m09_compatibility(
    checkout: Path, *, docker_sha: str
) -> tuple[dict, list[str], list[dict]]:
    """Apply API-equivalent M09 fixes; reject Docker feature/test suppression."""
    def rest_line_counts(*, trailing_comma: bool) -> dict[str, int]:
        suffix = r"\.\.," if trailing_comma else r"\.\."
        pattern = re.compile(rf"^[ \t]*{suffix}[ \t]*$", re.MULTILINE)
        return {
            relative.as_posix(): len(
                pattern.findall((checkout / relative).read_text())
            )
            for relative in M09_VALUE_PATTERN_PATHS
        }

    preexisting_legal_rest_counts = rest_line_counts(trailing_comma=False)
    preexisting_trailing_comma_rest_counts = rest_line_counts(
        trailing_comma=True
    )
    changed_paths: set[str] = set()
    counts = {
        "sqlite_import_replacement_count": 0,
        "sqlite_main_db_replacement_count": 0,
        "completer_invalid_dereference_semantic_replacement_count": 0,
        "uucore_import_removal_count": 0,
        "uucore_localization_setup_removal_count": 0,
        "uucore_error_message_preservation_count": 0,
        "ucp_error_alias_count": 0,
        "uutils_update_variant_replacement_count": 0,
        "uutils_backup_variant_replacement_count": 0,
        "uutils_obsolete_option_field_removal_count": 0,
        "umkdir_api_replacement_count": 0,
        "value_non_exhaustive_rest_addition_count": 0,
        "multiline_value_rest_without_trailing_comma_count": 0,
        "invalid_rest_trailing_comma_count": 0,
        "preexisting_rest_trailing_comma_count": 0,
        "preserved_preexisting_rest_trailing_comma_count": 0,
        "rest_trailing_comma_patch_addition_count": 0,
        "rest_trailing_comma_patch_deletion_count": 0,
        "parser_error_fixture_replacement_count": 0,
    }

    sqlite_path = checkout / M09_SQLITE
    content = sqlite_path.read_text()
    before = content
    content, count = replace_exact(
        content,
        "Connection, DatabaseName, Error as SqliteError",
        "Connection, Error as SqliteError, MAIN_DB",
        expected=1,
        label="M09 rusqlite import",
    )
    counts["sqlite_import_replacement_count"] += count
    content, count = replace_exact(
        content,
        "DatabaseName::Main",
        "MAIN_DB",
        expected=2,
        label="M09 rusqlite main database",
    )
    counts["sqlite_main_db_replacement_count"] += count
    sqlite_path.write_text(content)
    if content != before:
        changed_paths.add(M09_SQLITE.as_posix())

    completer_path = checkout / M09_COMPLETER
    content = completer_path.read_text()
    before = content
    content, parameter_count = replace_exact(
        content,
        "        need_fallback: bool,\n",
        "        mut need_fallback: bool,\n",
        expected=1,
        label="M09 mutable need_fallback parameter",
    )
    content, assignment_count = replace_exact(
        content,
        "                *need_fallback = false;\n",
        "                need_fallback = false;\n",
        expected=1,
        label="M09 need_fallback assignment",
    )
    if (parameter_count, assignment_count) != (1, 1):
        raise RuntimeError(
            "M09 invalid bool dereference semantic replacement is not 1+1: "
            f"{parameter_count}+{assignment_count}"
        )
    counts[
        "completer_invalid_dereference_semantic_replacement_count"
    ] += (parameter_count + assignment_count)
    completer_path.write_text(content)
    if content != before:
        changed_paths.add(M09_COMPLETER.as_posix())

    expected_message_counts = {
        "crates/nu-command/src/filesystem/ucp.rs": 1,
        "crates/nu-command/src/filesystem/umkdir.rs": 1,
        "crates/nu-command/src/filesystem/umv.rs": 1,
        "crates/nu-command/src/filesystem/utouch.rs": 2,
        "crates/nu-command/src/platform/whoami.rs": 1,
        "crates/nu-command/src/system/uname.rs": 1,
        "crates/nu-command/src/filesystem/mktemp.rs": 1,
    }
    for relative in M09_UUTILS_PATHS:
        path = checkout / relative
        content = path.read_text()
        before = content
        content, import_count = re.subn(
            r"^use uucore::\{localized_help_template, translate\};\n",
            "",
            content,
            flags=re.MULTILINE,
        )
        content, setup_count = re.subn(
            r"^\s*let _ = localized_help_template\(\"[a-z]+\"\);\n",
            "",
            content,
            flags=re.MULTILINE,
        )
        content, message_count = re.subn(
            r"translate!\(&([a-z]+)\.to_string\(\)\)",
            r"\1.to_string()",
            content,
        )
        expected_messages = expected_message_counts[relative.as_posix()]
        if (
            import_count != 1
            or setup_count != 1
            or message_count != expected_messages
        ):
            raise RuntimeError(
                f"M09 uucore compatibility mismatch {relative}: "
                f"{import_count}/{setup_count}/{message_count}"
            )
        counts["uucore_import_removal_count"] += import_count
        counts["uucore_localization_setup_removal_count"] += setup_count
        counts["uucore_error_message_preservation_count"] += message_count
        path.write_text(content)
        if content != before:
            changed_paths.add(relative.as_posix())

    ucp_path = checkout / "crates/nu-command/src/filesystem/ucp.rs"
    content = ucp_path.read_text()
    content, count = replace_exact(
        content,
        "use uu_cp::{BackupMode, CopyMode, CpError, UpdateMode};",
        "use uu_cp::{BackupMode, CopyMode, Error as CpError, UpdateMode};",
        expected=1,
        label="M09 uu_cp Error alias",
    )
    counts["ucp_error_alias_count"] += count
    for old, new in (
        ("UpdateMode::IfOlder", "UpdateMode::ReplaceIfOlder"),
        ("UpdateMode::All", "UpdateMode::ReplaceAll"),
    ):
        content, count = replace_exact(
            content,
            old,
            new,
            expected=1,
            label=f"M09 ucp {old}",
        )
        counts["uutils_update_variant_replacement_count"] += count
    content, count = replace_exact(
        content,
        "BackupMode::None",
        "BackupMode::NoBackup",
        expected=1,
        label="M09 ucp BackupMode",
    )
    counts["uutils_backup_variant_replacement_count"] += count
    content, set_context_count = re.subn(
        r"^\s*set_selinux_context: false,\n",
        "",
        content,
        flags=re.MULTILINE,
    )
    content, context_count = re.subn(
        r"^\s*context: None,\n",
        "",
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if (set_context_count, context_count) != (1, 1):
        raise RuntimeError(
            "M09 ucp obsolete Options fields are not exactly 1+1: "
            f"{set_context_count}+{context_count}"
        )
    counts["uutils_obsolete_option_field_removal_count"] += (
        set_context_count + context_count
    )
    ucp_path.write_text(content)

    umv_path = checkout / "crates/nu-command/src/filesystem/umv.rs"
    content = umv_path.read_text()
    for old, new in (
        ("UpdateMode::IfOlder", "UpdateMode::ReplaceIfOlder"),
        ("UpdateMode::All", "UpdateMode::ReplaceAll"),
    ):
        content, count = replace_exact(
            content,
            old,
            new,
            expected=1,
            label=f"M09 umv {old}",
        )
        counts["uutils_update_variant_replacement_count"] += count
    content, count = replace_exact(
        content,
        "BackupMode::None",
        "BackupMode::NoBackup",
        expected=1,
        label="M09 umv BackupMode",
    )
    counts["uutils_backup_variant_replacement_count"] += count
    content, count = re.subn(
        r"^\s*context: None,\n",
        "",
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError(f"M09 umv obsolete context field is not 1: {count}")
    counts["uutils_obsolete_option_field_removal_count"] += count
    umv_path.write_text(content)

    umkdir_path = checkout / "crates/nu-command/src/filesystem/umkdir.rs"
    content = umkdir_path.read_text()
    content, config_count = re.subn(
        r"\n\s*let config = uu_mkdir::Config \{\n.*?\n\s*\};\n",
        "\n",
        content,
        count=1,
        flags=re.DOTALL,
    )
    content, call_count = re.subn(
        r"mkdir\(&dir,\s*&config\)",
        "mkdir(&dir, IS_RECURSIVE, get_mode(), is_verbose)",
        content,
        count=1,
    )
    if (config_count, call_count) != (1, 1):
        raise RuntimeError(
            "M09 uu_mkdir API replacement is not exactly 1+1: "
            f"{config_count}+{call_count}"
        )
    counts["umkdir_api_replacement_count"] += config_count + call_count
    umkdir_path.write_text(content)

    update_path = checkout / M09_VALUE_PATTERN_PATHS[0]
    content = update_path.read_text()
    content, count = re.subn(
        (
            r"(Value::Record \{\n"
            r"\s*ref mut val,\n"
            r"\s*internal_span,\n)"
            r"(\s*)\}"
        ),
        lambda match: (
            match.group(1)
            + match.group(2)
            + "    ..\n"
            + match.group(2)
            + "}"
        ),
        content,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"M09 Value::Record rest addition is not 1: {count}")
    counts["value_non_exhaustive_rest_addition_count"] += count
    counts["multiline_value_rest_without_trailing_comma_count"] += count
    update_path.write_text(content)
    changed_paths.add(M09_VALUE_PATTERN_PATHS[0].as_posix())

    save_path = checkout / M09_VALUE_PATTERN_PATHS[1]
    content = save_path.read_text()
    content, count = replace_exact(
        content,
        "Value::Custom { val, internal_span }",
        "Value::Custom { val, internal_span, .. }",
        expected=1,
        label="M09 Value::Custom rest addition",
    )
    counts["value_non_exhaustive_rest_addition_count"] += count
    save_path.write_text(content)
    changed_paths.add(M09_VALUE_PATTERN_PATHS[1].as_posix())

    seq_path = checkout / M09_VALUE_PATTERN_PATHS[2]
    content = seq_path.read_text()
    for variant in ("Int", "Duration"):
        content, count = replace_exact(
            content,
            f"Value::{variant} {{ val, internal_span }}",
            f"Value::{variant} {{ val, internal_span, .. }}",
            expected=1,
            label=f"M09 Value::{variant} rest addition",
        )
        counts["value_non_exhaustive_rest_addition_count"] += count
    seq_path.write_text(content)
    changed_paths.add(M09_VALUE_PATTERN_PATHS[2].as_posix())

    for relative in M09_VALUE_PATTERN_PATHS[3:]:
        path = checkout / relative
        content = path.read_text()
        content, count = add_rest_to_multiline_value_pattern(
            content, "Range", expected=2
        )
        counts["value_non_exhaustive_rest_addition_count"] += count
        counts["multiline_value_rest_without_trailing_comma_count"] += count
        path.write_text(content)
        changed_paths.add(relative.as_posix())

    final_legal_rest_counts = rest_line_counts(trailing_comma=False)
    final_trailing_comma_rest_counts = rest_line_counts(
        trailing_comma=True
    )
    preexisting_legal_rest_count = sum(
        preexisting_legal_rest_counts.values()
    )
    final_legal_rest_count = sum(final_legal_rest_counts.values())
    preexisting_trailing_comma_rest_count = sum(
        preexisting_trailing_comma_rest_counts.values()
    )
    final_trailing_comma_rest_count = sum(
        final_trailing_comma_rest_counts.values()
    )
    counts["invalid_rest_trailing_comma_count"] = (
        final_trailing_comma_rest_count
        - preexisting_trailing_comma_rest_count
    )
    counts["preexisting_rest_trailing_comma_count"] = (
        preexisting_trailing_comma_rest_count
    )
    counts["preserved_preexisting_rest_trailing_comma_count"] = (
        final_trailing_comma_rest_count
    )
    if (
        counts["multiline_value_rest_without_trailing_comma_count"] != 13
        or final_legal_rest_count - preexisting_legal_rest_count != 13
        or final_trailing_comma_rest_counts
        != preexisting_trailing_comma_rest_counts
        or counts["invalid_rest_trailing_comma_count"] != 0
    ):
        raise RuntimeError(
            "M09 multiline rest syntax gate failed: "
            f"{counts['multiline_value_rest_without_trailing_comma_count']}/"
            f"{final_legal_rest_count - preexisting_legal_rest_count}/"
            f"{counts['invalid_rest_trailing_comma_count']}"
        )

    parser_path = checkout / M09_PARSER_TEST
    parser_before = parser_path.read_text()
    parser_after, parser_count = replace_exact(
        parser_before,
        "Some(ParseError::InvalidBinaryString(_, _))",
        "Some(ParseError::IncorrectValue(_, _, _))",
        expected=2,
        label="M09 parser error fixture",
    )
    counts["parser_error_fixture_replacement_count"] += parser_count
    parser_path.write_text(parser_after)
    changed_paths.add(M09_PARSER_TEST.as_posix())
    test_edits = [
        test_edit_record(
            checkout,
            M09_PARSER_TEST,
            parser_before,
            parser_after,
            reason="align_parser_error_fixture_with_runnable_parser",
            policy="assertion_variant_schema_correction",
            edit_count=parser_count,
            evidence={
                "dockerfile": M09_DOCKER_EVIDENCE,
                "dockerfile_sha256": docker_sha,
                "docker_suppression_rejected": True,
                "runnable_parser_emits": "ParseError::IncorrectValue",
            },
        )
    ]

    expected_changed = {
        M09_SQLITE.as_posix(),
        M09_COMPLETER.as_posix(),
        M09_PARSER_TEST.as_posix(),
        *(path.as_posix() for path in M09_UUTILS_PATHS),
        *(path.as_posix() for path in M09_VALUE_PATTERN_PATHS),
    }
    if changed_paths != expected_changed or len(changed_paths) != 19:
        raise RuntimeError(
            f"M09 compatibility path scope is not 19: {sorted(changed_paths)}"
        )
    return counts, sorted(changed_paths), test_edits


def apply_m03_duration_compatibility(
    checkout: Path,
) -> tuple[dict, list[str]]:
    """Transplant the exact missing positive Duration FromValue prerequisite."""
    path = checkout / M03_FROM_VALUE
    before = path.read_text()
    before_blob = str(
        run(
            "git",
            "hash-object",
            M03_FROM_VALUE.as_posix(),
            cwd=checkout,
            capture=True,
        )
    ).strip()
    if before_blob != DURATION_RAW_BLOB:
        raise RuntimeError(
            f"M03 raw from_value.rs blob differs from evidence: {before_blob}"
        )
    block = """/// This implementation supports **positive** durations only.
impl FromValue for std::time::Duration {
    fn from_value(v: Value) -> Result<Self, ShellError> {
        match v {
            Value::Duration { val, .. } => {
                let nanos = u64::try_from(val)
                    .map_err(|_| ShellError::NeedsPositiveValue { span: v.span() })?;
                Ok(Self::from_nanos(nanos))
            }
            v => Err(ShellError::CantConvert {
                to_type: Self::expected_type().to_string(),
                from_type: v.get_type().to_string(),
                span: v.span(),
                help: None,
            }),
        }
    }

    fn expected_type() -> Type {
        Type::Duration
    }
}

"""
    anchor = (
        "//\n"
        "// We can not use impl<T: FromValue> FromValue for NonZero<T> "
        "as NonZero requires an unstable trait"
    )
    after, count = replace_exact(
        before,
        anchor,
        block + anchor,
        expected=1,
        label="M03 positive Duration FromValue prerequisite",
    )
    path.write_text(after)
    after_blob = str(
        run(
            "git",
            "hash-object",
            M03_FROM_VALUE.as_posix(),
            cwd=checkout,
            capture=True,
        )
    ).strip()
    if after_blob != DURATION_POSITIVE_BLOB:
        raise RuntimeError(
            "M03 positive from_value.rs blob differs from evidence: "
            f"{after_blob}"
        )
    return (
        {
            "duration_from_value_prerequisite_transplant_count": count,
            "duration_from_value_impl_line_count": len(
                block.rstrip("\n").splitlines()
            ),
            "task_implementation_rewrite_count": 0,
            "evidence": {
                "kind": "official_positive_duration_from_value_commit",
                "commit": DURATION_POSITIVE_COMMIT_EVIDENCE,
                "raw_blob": before_blob,
                "positive_blob": after_blob,
            },
        },
        [M03_FROM_VALUE.as_posix()],
    )


def apply_m07_pipeline_refactor_completion(
    checkout: Path, *, evidence: dict
) -> tuple[dict, list[str], list[dict]]:
    """Finish the test-side pipeline-helper removal already made by M07."""
    metadata_path = checkout / M07_METADATA_SET_TEST
    metadata_before = metadata_path.read_text()
    wrapper_pattern = re.compile(
        r"""    let actual = nu!\(
        cwd: "\.", pipeline\(
        r#"\n(?P<body>.*?)\n        "#
    \)\);""",
        flags=re.DOTALL,
    )

    def unwrap_metadata(match: re.Match[str]) -> str:
        return (
            '    let actual = nu!(cwd: ".", r#"\n'
            + match.group("body")
            + '\n    "#);'
        )

    metadata_after, metadata_count = wrapper_pattern.subn(
        unwrap_metadata, metadata_before
    )
    if metadata_count != 3 or "pipeline(" in metadata_after:
        raise RuntimeError(
            "M07 metadata_set pipeline refactor is not exactly 3 complete "
            f"wrappers: {metadata_count}"
        )
    metadata_path.write_text(metadata_after)

    http_path = checkout / M07_HTTP_GET_TEST
    http_before = http_path.read_text()
    http_old = """    let actual = nu!(pipeline(
        format!(
            r#"http get --raw {url} | metadata | get http_response | get status"#,
            url = server.url()
        )
        .as_str()
    ));"""
    http_new = """    let actual = nu!(format!(
        r#"http get --raw {url} | metadata | get http_response | get status"#,
        url = server.url()
    ));"""
    http_after, http_count = replace_exact(
        http_before,
        http_old,
        http_new,
        expected=1,
        label="M07 HTTP response metadata pipeline refactor",
    )
    if "pipeline(" in http_after:
        raise RuntimeError("M07 HTTP get retains a pipeline helper call")
    http_path.write_text(http_after)

    test_edits = [
        test_edit_record(
            checkout,
            M07_METADATA_SET_TEST,
            metadata_before,
            metadata_after,
            reason="complete_m07_pipeline_helper_removal",
            policy="finish_same_upstream_test_refactor_without_import_restore",
            edit_count=metadata_count,
            evidence=evidence,
        ),
        test_edit_record(
            checkout,
            M07_HTTP_GET_TEST,
            http_before,
            http_after,
            reason="complete_m07_pipeline_helper_removal",
            policy="finish_same_upstream_test_refactor_without_import_restore",
            edit_count=http_count,
            evidence=evidence,
        ),
    ]
    return (
        {
            "metadata_pipeline_wrapper_removal_count": metadata_count,
            "http_pipeline_wrapper_removal_count": http_count,
            "pipeline_import_restoration_count": 0,
        },
        sorted(
            [
                M07_METADATA_SET_TEST.as_posix(),
                M07_HTTP_GET_TEST.as_posix(),
            ]
        ),
        test_edits,
    )


def apply_coredev1_stable_feature_gate_removal(
    checkout: Path,
) -> tuple[dict, list[str]]:
    """Remove a nightly gate for let-chains, stable in the pinned Rust 1.88."""
    path = checkout / COREDEV1_PARSER_LIB
    before = path.read_text()
    after, count = replace_exact(
        before,
        "#![feature(let_chains)]\n",
        "",
        expected=1,
        label="coredev1 stable let_chains feature gate",
    )
    if after.startswith("#![feature(let_chains)]"):
        raise RuntimeError("coredev1 let_chains feature gate remains")
    path.write_text(after)
    return (
        {
            "stable_feature_gate_removal_count": count,
            "pinned_rust_version": "1.88.0",
            "feature": "let_chains",
        },
        [COREDEV1_PARSER_LIB.as_posix()],
    )


def apply_coredev4_experimental_metadata_prerequisite(
    checkout: Path,
    *,
    positive_contents: dict[Path, str],
    positive_evidence: dict,
) -> tuple[dict, list[str]]:
    """Transplant only the exact G02 metadata prerequisite into coredev4."""
    lib_path = COREDEV4_EXPERIMENTAL_PATHS[0]
    mod_path = COREDEV4_EXPERIMENTAL_PATHS[1]
    example_path = COREDEV4_EXPERIMENTAL_PATHS[2]
    pipefail_path = COREDEV4_EXPERIMENTAL_PATHS[3]
    reorder_path = COREDEV4_EXPERIMENTAL_PATHS[4]

    accessors = """    pub fn since(&self) -> Version {
        self.marker.since()
    }

    pub fn issue_id(&self) -> u32 {
        self.marker.issue()
    }

    pub fn issue_url(&self) -> String {
        format!(
            "https://github.com/nushell/nushell/issues/{}",
            self.marker.issue()
        )
    }

"""
    dynamic_trait = """    fn since(&self) -> Version;
    fn issue(&self) -> u32;
"""
    dynamic_impl = """
    fn since(&self) -> Version {
        M::SINCE
    }

    fn issue(&self) -> u32 {
        M::ISSUE
    }
"""
    marker_contract = """
    /// Nushell version since this experimental option is available.
    ///
    /// These three values represent major.minor.patch version.
    /// Don't use some macro to generate this dynamically as this would defeat the purpose of having
    /// a historic record.
    const SINCE: Version;

    /// Github issue that tracks this experimental option.
    ///
    /// Experimental options are expected to end their lifetime by either getting a default feature
    /// or by getting removed.
    /// To track this we want to have a respective issue on Github that tracks the status.
    const ISSUE: u32;
"""
    option_metadata = {
        example_path: (
            "    const STATUS: Status = Status::DeprecatedDiscard;\n",
            """    const STATUS: Status = Status::DeprecatedDiscard;
    const SINCE: Version = (0, 105, 2);
    const ISSUE: u32 = 0;
""",
        ),
        pipefail_path: (
            "    const STATUS: Status = Status::OptIn;\n",
            """    const STATUS: Status = Status::OptIn;
    const SINCE: Version = (0, 107, 1);
    const ISSUE: u32 = 16760;
""",
        ),
        reorder_path: (
            "    const STATUS: Status = Status::OptIn;\n",
            """    const STATUS: Status = Status::OptIn;
    const SINCE: Version = (0, 105, 2);
    const ISSUE: u32 = 16766;
""",
        ),
    }
    positive_required = {
        lib_path: (accessors, dynamic_trait, dynamic_impl),
        mod_path: (
            "pub(crate) type Version = (u16, u16, u16);",
            marker_contract,
        ),
        example_path: (
            "    const SINCE: Version = (0, 105, 2);",
            "    const ISSUE: u32 = 0;",
        ),
        pipefail_path: (
            "    const SINCE: Version = (0, 107, 1);",
            "    const ISSUE: u32 = 16760;",
        ),
        reorder_path: (
            "    const SINCE: Version = (0, 105, 2);",
            "    const ISSUE: u32 = 16766;",
        ),
    }
    for path, snippets in positive_required.items():
        positive = positive_contents[path]
        for snippet in snippets:
            if snippet not in positive:
                raise RuntimeError(
                    f"G02 positive prerequisite fragment is absent: {path}"
                )

    path = checkout / lib_path
    content = path.read_text()
    content, accessor_anchor_count = replace_exact(
        content,
        """    pub fn status(&self) -> Status {
        self.marker.status()
    }

    pub fn get(&self) -> bool {""",
        """    pub fn status(&self) -> Status {
        self.marker.status()
    }

"""
        + accessors
        + "    pub fn get(&self) -> bool {",
        expected=1,
        label="coredev4 metadata accessors",
    )
    content, trait_count = replace_exact(
        content,
        """    fn description(&self) -> &'static str;
    fn status(&self) -> Status;
}""",
        """    fn description(&self) -> &'static str;
    fn status(&self) -> Status;
"""
        + dynamic_trait
        + "}",
        expected=1,
        label="coredev4 dynamic metadata trait",
    )
    content, impl_count = replace_exact(
        content,
        """    fn status(&self) -> Status {
        M::STATUS
    }
}""",
        """    fn status(&self) -> Status {
        M::STATUS
    }
"""
        + dynamic_impl
        + "}",
        expected=1,
        label="coredev4 dynamic metadata implementation",
    )
    path.write_text(content)

    path = checkout / mod_path
    content = path.read_text()
    content, version_count = replace_exact(
        content,
        "mod reorder_cell_paths;\n\n/// Marker trait",
        "mod reorder_cell_paths;\n\n"
        "pub(crate) type Version = (u16, u16, u16);\n\n"
        "/// Marker trait",
        expected=1,
        label="coredev4 experimental metadata Version alias",
    )
    content, contract_count = replace_exact(
        content,
        "    const STATUS: Status;\n}",
        "    const STATUS: Status;\n" + marker_contract + "}",
        expected=1,
        label="coredev4 experimental marker metadata contract",
    )
    path.write_text(content)

    option_count = 0
    for relative, (old, new) in option_metadata.items():
        path = checkout / relative
        content, count = replace_exact(
            path.read_text(),
            old,
            new,
            expected=1,
            label=f"coredev4 G02 option metadata {relative}",
        )
        path.write_text(content)
        option_count += count

    return (
        {
            "lib_accessor_block_transplant_count": accessor_anchor_count,
            "dynamic_trait_block_transplant_count": trait_count,
            "dynamic_impl_block_transplant_count": impl_count,
            "version_alias_transplant_count": version_count,
            "marker_contract_transplant_count": contract_count,
            "option_metadata_block_transplant_count": option_count,
            "option_metadata_const_addition_count": option_count * 2,
            "option_status_change_count": 0,
            "endpoint_option_status_preserved": True,
            "positive_evidence": positive_evidence,
        },
        sorted(path.as_posix() for path in COREDEV4_EXPERIMENTAL_PATHS),
    )


def marker_fields(path: Path) -> list[str]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"checkpoint is absent or unsafe: {path}")
    fields = path.read_text().rstrip("\n").split("\t")
    if len(fields) != 5:
        raise RuntimeError(f"checkpoint is malformed: {path}")
    return fields


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-root", type=Path, required=True)
    parser.add_argument("--environment-repair-root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--resolved-lock-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    prepare = args.prepare_root.resolve()
    states_root = prepare / "delivery/states"
    state_manifest = json.loads((states_root / "manifest.json").read_text())
    state_rows = {
        row["endpoint_id"]: row for row in state_manifest["endpoints"]
    }
    environment_root = args.environment_repair_root.resolve()
    environment_manifest = json.loads(
        (environment_root / "manifest.json").read_text()
    )
    environment_rows = {
        row["endpoint_id"]: row for row in environment_manifest["endpoints"]
    }
    audit_path = args.audit.resolve()
    audit = json.loads(audit_path.read_text())
    audit_rows = {row["endpoint_id"]: row for row in audit["endpoints"]}
    observed_custom_repairs = {
        row["endpoint_id"]
        for row in audit["endpoints"]
        if row["mismatch_count"]
    }
    observed_reedline_repairs = {
        row["endpoint_id"]
        for row in audit["endpoints"]
        if row["reedline_api_mismatch_count"]
    }
    observed_reedline_helper_candidates = {
        row["endpoint_id"]
        for row in audit["endpoints"]
        if row["reedline_api_helper_change_candidate_count"]
    }
    resolved_root = args.resolved_lock_root.resolve()
    resolved_manifest = json.loads((resolved_root / "manifest.json").read_text())
    resolved_rows = {
        row["endpoint_id"]: row for row in resolved_manifest["endpoints"]
    }
    if (
        len(state_manifest.get("endpoints", [])) != 42
        or len(environment_rows) != 42
        or len(audit_rows) != 42
        or len(resolved_rows) != 42
        or observed_custom_repairs
        != EXPECTED_CUSTOM_COMPLETION_ENDPOINTS
        or observed_reedline_repairs != EXPECTED_REEDLINE_API_ENDPOINTS
        or len(observed_reedline_helper_candidates) != 24
        or audit.get(
            "endpoints_with_custom_completion_contract_mismatch_count"
        )
        != 3
        or audit.get("custom_completion_contract_mismatch_count") != 3
        or audit.get(
            "endpoints_with_reedline_api_helper_change_candidate_count"
        )
        != 24
        or audit.get("reedline_api_helper_change_candidate_count") != 24
        or audit.get("endpoints_with_reedline_api_contract_mismatch_count")
        != 7
        or audit.get("reedline_api_contract_mismatch_count") != 7
        or len(EXPECTED_SCHEMA4_REPAIR_ENDPOINTS) != 13
    ):
        raise SystemExit("custom_completion inputs differ from reviewed 3/42")
    if (
        environment_manifest.get("status") != "validated"
        or environment_manifest.get("endpoint_count") != 42
        or resolved_manifest.get("status") != "validated_online"
        or resolved_manifest.get("endpoint_count") != 42
        or resolved_manifest.get("environment_repairs_manifest_sha256")
        != sha256(environment_root / "manifest.json")
    ):
        raise SystemExit("environment/lock inputs are not validated")

    output_root = args.output_root.resolve()
    if output_root.exists():
        if not args.replace:
            raise SystemExit(f"compile repair bundle exists: {output_root}")
        existing = json.loads((output_root / "manifest.json").read_text())
        if (
            existing.get("kind") != "nushell_42_endpoint_compile_repairs"
            or existing.get("schema_version") != 6
            or existing.get("compatibility_fingerprint_label")
            != "nushell-compatibility-v6"
            or sha256(output_root / "manifest.json")
            != PRIOR_SCHEMA6_MANIFEST_SHA256
            or sha256(output_root / "repairs.tsv")
            != PRIOR_SCHEMA6_REPAIRS_SHA256
            or sha256(output_root / "closure_reuse_audit.json")
            != PRIOR_SCHEMA6_CLOSURE_SHA256
        ):
            raise SystemExit(f"refusing to replace unknown output: {output_root}")
    staging = output_root.with_name(f".{output_root.name}.tmp.{os.getpid()}")
    staging.mkdir(parents=True)
    patches = staging / "patches"
    patches.mkdir()
    rows = []
    migration_rows = []
    cache_root = prepare / "runtime_cache_v1"
    cargo_home = cache_root / "cargo_home"
    online_root = cache_root / "index_closure_v1/online"
    offline_root = cache_root / "index_closure_v1/offline"
    compile_marker_root = cache_root / "validated"
    helper_evidence = prepare / REEDLINE_HELPER_EVIDENCE
    official_reedline_helper, official_reedline_helper_sha = (
        load_official_reedline_helper(helper_evidence)
    )
    work_root = prepare.parents[3]
    m09_docker_path = work_root / M09_DOCKER_EVIDENCE
    m09_docker_sha = sha256(m09_docker_path)
    m03_docker_path = work_root / M03_DOCKER_EVIDENCE
    m03_docker_sha = sha256(m03_docker_path)
    m04_docker_path = work_root / M04_DOCKER_EVIDENCE
    m04_docker_sha = sha256(m04_docker_path)
    prior_schema6_root = (
        prepare
        / "runtime_compile_repairs_v1_superseded_schema6_20260723T1054"
    )
    if (
        sha256(prior_schema6_root / "manifest.json")
        != PRIOR_SCHEMA6_MANIFEST_SHA256
        or sha256(prior_schema6_root / "repairs.tsv")
        != PRIOR_SCHEMA6_REPAIRS_SHA256
        or sha256(prior_schema6_root / "closure_reuse_audit.json")
        != PRIOR_SCHEMA6_CLOSURE_SHA256
    ):
        raise SystemExit("archived schema6 compile bundle identity differs")
    prior_schema6_manifest = json.loads(
        (prior_schema6_root / "manifest.json").read_text()
    )
    prior_schema6_rows = {
        row["endpoint_id"]: row
        for row in prior_schema6_manifest["endpoints"]
    }
    if (
        prior_schema6_manifest.get("schema_version") != 6
        or prior_schema6_manifest.get("status") != "validated"
        or prior_schema6_manifest.get("endpoint_count") != 42
        or prior_schema6_manifest.get("repaired_endpoint_count") != 13
        or prior_schema6_manifest.get("compatibility_fingerprint_label")
        != "nushell-compatibility-v6"
        or len(prior_schema6_rows) != 42
    ):
        raise SystemExit("archived schema6 compile bundle is not exact")
    prior_compile_root = (
        prepare
        / "runtime_compile_repairs_v1_superseded_schema5_20260723T1040"
    )
    prior_compile_manifest_path = prior_compile_root / "manifest.json"
    prior_compile_repairs_path = prior_compile_root / "repairs.tsv"
    prior_compile_closure_path = prior_compile_root / "closure_reuse_audit.json"
    prior_compile_manifest_sha = sha256(prior_compile_manifest_path)
    if (
        prior_compile_manifest_sha != PRIOR_SCHEMA5_MANIFEST_SHA256
        or sha256(prior_compile_repairs_path)
        != PRIOR_SCHEMA5_REPAIRS_SHA256
        or sha256(prior_compile_closure_path)
        != PRIOR_SCHEMA5_CLOSURE_SHA256
    ):
        raise SystemExit("formal schema5 compile bundle identity differs")
    prior_compile_manifest = json.loads(
        prior_compile_manifest_path.read_text()
    )
    prior_compile_rows = {
        row["endpoint_id"]: row
        for row in prior_compile_manifest["endpoints"]
    }
    if (
        prior_compile_manifest.get("schema_version") != 5
        or prior_compile_manifest.get("status") != "validated"
        or prior_compile_manifest.get("endpoint_count") != 42
        or prior_compile_manifest.get("repaired_endpoint_count") != 13
        or prior_compile_manifest.get("compatibility_fingerprint_label")
        != "nushell-compatibility-v5"
        or len(prior_compile_rows) != 42
        or {
            endpoint_id
            for endpoint_id, row in prior_compile_rows.items()
            if row["reasons"] != ["none"]
        }
        != EXPECTED_SCHEMA4_REPAIR_ENDPOINTS
    ):
        raise SystemExit("prior schema5 compile bundle is not exact")
    expected_marker_endpoint_ids = (
        set(state_rows) - EXPECTED_SCHEMA7_REQUIRED_COMPILE_ENDPOINTS
    )
    actual_marker_endpoint_ids = {
        endpoint_id
        for endpoint_id in state_rows
        if (
            compile_marker_root
            / (
                re.sub(r"[^A-Za-z0-9._-]", "_", endpoint_id)
                + ".tsv"
            )
        ).is_file()
    }
    actual_marker_files = {
        path.name
        for path in compile_marker_root.glob("*.tsv")
        if path.is_file() and not path.is_symlink()
    }
    expected_marker_files = {
        re.sub(r"[^A-Za-z0-9._-]", "_", endpoint_id) + ".tsv"
        for endpoint_id in expected_marker_endpoint_ids
    }
    if (
        actual_marker_endpoint_ids != expected_marker_endpoint_ids
        or actual_marker_files != expected_marker_files
        or len(actual_marker_files) != 39
        or len(EXPECTED_PRIOR_V5_CHECKPOINT_ENDPOINTS) != 10
        or len(EXPECTED_SCHEMA7_REQUIRED_COMPILE_ENDPOINTS) != 3
    ):
        raise SystemExit(
            "current compile marker snapshot is not "
            "39 = 29 legacy + 10 prior-v5 with three schema7 repairs absent"
        )
    full_helper_docker_roots = {
        "milestone_G05_ac3f93f": (
            work_root
            / "SWE-Milestone-data-repartitioned/"
            "nushell_nushell_0.106.0_0.108.0/dockerfiles/"
            "milestone_G05_ac3f93f"
        ),
        "milestone_M04_std": (
            work_root
            / "SWE-Milestone-data-repartitioned/"
            "nushell_nushell_0.106.0_0.108.0/dockerfiles/"
            "milestone_M04_std"
        ),
    }
    for docker_root in full_helper_docker_roots.values():
        if (
            sha256(docker_root / "env_patch_script/fix_reedline_api.py")
            != official_reedline_helper_sha
        ):
            raise SystemExit(
                f"Docker helper differs from official evidence: {docker_root}"
            )
    try:
        with tempfile.TemporaryDirectory(
            prefix="nushell-compile-repair-"
        ) as raw:
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
            positive_checkout = Path(raw) / "g02-positive"
            run(
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                str(prepare / "agent-anchor"),
                str(positive_checkout),
                cwd=Path(raw),
            )
            positive_endpoint = state_rows[
                G02_EXPERIMENTAL_METADATA_POSITIVE_ENDPOINT
            ]
            positive_environment = environment_rows[
                G02_EXPERIMENTAL_METADATA_POSITIVE_ENDPOINT
            ]
            for state_name in ("implementation_state", "test_state"):
                patch = (
                    states_root
                    / positive_endpoint[state_name]["patch"]["path"]
                )
                if patch.stat().st_size:
                    run(
                        "git",
                        "apply",
                        "--index",
                        "--binary",
                        "--whitespace=nowarn",
                        str(patch),
                        cwd=positive_checkout,
                    )
            positive_original_tree = str(
                run(
                    "git",
                    "write-tree",
                    cwd=positive_checkout,
                    capture=True,
                )
            ).strip()
            if positive_original_tree != positive_endpoint["combined_tree"]:
                raise RuntimeError("G02 positive combined tree mismatch")
            apply_patch(
                positive_checkout,
                environment_root / positive_environment["patch"],
                expected_sha=positive_environment["patch_sha256"],
                expected_bytes=positive_environment["patch_bytes"],
            )
            positive_environment_tree = str(
                run(
                    "git",
                    "write-tree",
                    cwd=positive_checkout,
                    capture=True,
                )
            ).strip()
            if (
                positive_environment_tree
                != positive_environment["environment_tree"]
            ):
                raise RuntimeError("G02 positive environment tree mismatch")
            positive_contents = {
                path: (positive_checkout / path).read_text()
                for path in COREDEV4_EXPERIMENTAL_PATHS
            }
            positive_evidence = {
                "endpoint_id": G02_EXPERIMENTAL_METADATA_POSITIVE_ENDPOINT,
                "combined_tree": positive_original_tree,
                "environment_tree": positive_environment_tree,
                "environment_patch_sha256": positive_environment[
                    "patch_sha256"
                ],
                "path_sha256": {
                    path.as_posix(): bytes_sha256(
                        positive_contents[path].encode()
                    )
                    for path in COREDEV4_EXPERIMENTAL_PATHS
                },
                "validated_compile_checkpoint": str(
                    compile_marker_root
                    / (
                        re.sub(
                            r"[^A-Za-z0-9._-]",
                            "_",
                            G02_EXPERIMENTAL_METADATA_POSITIVE_ENDPOINT,
                        )
                        + ".tsv"
                    )
                ),
            }
            for index, endpoint in enumerate(state_manifest["endpoints"], 1):
                endpoint_id = endpoint["endpoint_id"]
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", endpoint_id)
                environment = environment_rows[endpoint_id]
                resolved = resolved_rows[endpoint_id]
                review = audit_rows[endpoint_id]
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
                if (
                    original_tree != endpoint["combined_tree"]
                    or environment["original_tree"] != original_tree
                    or resolved["original_tree"] != original_tree
                ):
                    raise RuntimeError(f"original tree mismatch: {endpoint_id}")
                apply_patch(
                    checkout,
                    environment_root / environment["patch"],
                    expected_sha=environment["patch_sha256"],
                    expected_bytes=environment["patch_bytes"],
                )
                environment_tree = str(
                    run("git", "write-tree", cwd=checkout, capture=True)
                ).strip()
                if (
                    environment_tree != environment["environment_tree"]
                    or resolved["environment_tree"] != environment_tree
                    or resolved["environment_repair_patch_sha256"]
                    != environment["patch_sha256"]
                ):
                    raise RuntimeError(
                        f"environment repair mismatch: {endpoint_id}"
                    )

                cargo_identity_before, cargo_rows_before = (
                    cargo_manifest_identity(checkout)
                )
                cargo_lock_before = (
                    sha256(checkout / "Cargo.lock")
                    if (checkout / "Cargo.lock").is_file()
                    else None
                )
                test_before = sha256(checkout / SIGNATURE_TEST)
                contract_before = audit_custom_completion_contract(checkout)
                if contract_before["mismatch_count"] != review["mismatch_count"]:
                    raise RuntimeError(f"audit replay mismatch: {endpoint_id}")

                resolved_path = resolved_root / resolved["resolved_lock"]
                if (
                    not resolved_path.is_file()
                    or resolved_path.is_symlink()
                    or sha256(resolved_path)
                    != resolved["resolved_lock_sha256"]
                ):
                    raise RuntimeError(f"resolved lock mismatch: {endpoint_id}")
                reedline_before = audit_reedline_api_contract(
                    checkout, resolved_path, cargo_home
                )
                if (
                    reedline_before["mismatch_count"]
                    != review["reedline_api_mismatch_count"]
                    or reedline_before["helper_change_candidate_count"]
                    != review[
                        "reedline_api_helper_change_candidate_count"
                    ]
                ):
                    raise RuntimeError(
                        f"reedline audit replay mismatch: {endpoint_id}"
                    )
                shutil.copyfile(resolved_path, checkout / "Cargo.lock")
                run("git", "add", "Cargo.lock", cwd=checkout)
                closure_runtime_tree = str(
                    run("git", "write-tree", cwd=checkout, capture=True)
                ).strip()
                if closure_runtime_tree != resolved["normalized_runtime_tree"]:
                    raise RuntimeError(
                        f"resolved runtime tree mismatch: {endpoint_id}: "
                        f"{closure_runtime_tree} != "
                        f"{resolved['normalized_runtime_tree']}"
                    )
                run(
                    "git",
                    "read-tree",
                    "--reset",
                    "-u",
                    environment_tree,
                    cwd=checkout,
                )

                definition_count = 0
                constructor_count = 0
                changed_paths = set()
                test_edits: list[dict] = []
                m04_edits = {
                    "completion_fixture_field_removal_count": 0,
                    "pipefail_test_block_removal_count": 0,
                    "pipefail_active_case_removal_count": 0,
                    "pipefail_rstest_import_removal_count": 0,
                    "canonical_external_test_blob": None,
                }
                m09_edits = {
                    "sqlite_import_replacement_count": 0,
                    "sqlite_main_db_replacement_count": 0,
                    "completer_invalid_dereference_semantic_replacement_count": 0,
                    "uucore_import_removal_count": 0,
                    "uucore_localization_setup_removal_count": 0,
                    "uucore_error_message_preservation_count": 0,
                    "ucp_error_alias_count": 0,
                    "uutils_update_variant_replacement_count": 0,
                    "uutils_backup_variant_replacement_count": 0,
                    "uutils_obsolete_option_field_removal_count": 0,
                    "umkdir_api_replacement_count": 0,
                    "value_non_exhaustive_rest_addition_count": 0,
                    "multiline_value_rest_without_trailing_comma_count": 0,
                    "invalid_rest_trailing_comma_count": 0,
                    "preexisting_rest_trailing_comma_count": 0,
                    "preserved_preexisting_rest_trailing_comma_count": 0,
                    "rest_trailing_comma_patch_addition_count": 0,
                    "rest_trailing_comma_patch_deletion_count": 0,
                    "parser_error_fixture_replacement_count": 0,
                    "test_api_alignment_record_count": 0,
                    "test_api_alignment_operation_count": 0,
                }
                m03_edits = {
                    "duration_from_value_prerequisite_transplant_count": 0,
                    "duration_from_value_impl_line_count": 0,
                    "task_implementation_rewrite_count": 0,
                    "evidence": None,
                }
                m03_resolved_api_edits = {
                    "sqlite_import_replacement_count": 0,
                    "sqlite_main_db_replacement_count": 0,
                    "completer_invalid_dereference_semantic_replacement_count": 0,
                    "reedline_pair_variant_replacement_count": 0,
                    "value_non_exhaustive_rest_addition_count": 0,
                    "operation_count": 0,
                }
                m03_test_corrections = {
                    "completion_fixture_field_removal_count": 0,
                    "test_api_alignment_record_count": 0,
                    "test_api_alignment_operation_count": 0,
                    "pipefail_test_block_removal_count": 0,
                    "pipefail_active_case_removal_count": 0,
                    "pipefail_rstest_import_removal_count": 0,
                    "canonical_external_test_blob": None,
                }
                m07_edits = {
                    "metadata_pipeline_wrapper_removal_count": 0,
                    "http_pipeline_wrapper_removal_count": 0,
                    "pipeline_import_restoration_count": 0,
                }
                coredev1_edits = {
                    "stable_feature_gate_removal_count": 0,
                    "pinned_rust_version": None,
                    "feature": None,
                }
                coredev4_edits = {
                    "lib_accessor_block_transplant_count": 0,
                    "dynamic_trait_block_transplant_count": 0,
                    "dynamic_impl_block_transplant_count": 0,
                    "version_alias_transplant_count": 0,
                    "marker_contract_transplant_count": 0,
                    "option_metadata_block_transplant_count": 0,
                    "option_metadata_const_addition_count": 0,
                    "option_status_change_count": 0,
                    "endpoint_option_status_preserved": None,
                    "positive_evidence": None,
                    "config_test_api_alignment_record_count": 0,
                    "config_test_api_alignment_operation_count": 0,
                }
                coredev4_resolved_api_edits = {
                    "sqlite_import_replacement_count": 0,
                    "sqlite_main_db_replacement_count": 0,
                    "completer_invalid_dereference_semantic_replacement_count": 0,
                    "reedline_pair_variant_replacement_count": 0,
                    "value_non_exhaustive_rest_addition_count": 0,
                    "operation_count": 0,
                }
                schema6_parser_fixture_edits = {
                    "parser_error_fixture_replacement_count": 0,
                    "before_sha256": None,
                    "after_sha256": None,
                }
                if endpoint_id in EXPECTED_CUSTOM_COMPLETION_ENDPOINTS:
                    custom_paths = []
                    definition_count, constructor_count, custom_paths = (
                        add_custom_completion_fields(checkout)
                    )
                    if (
                        definition_count != 3
                        or constructor_count != 16
                        or set(custom_paths)
                        != EXPECTED_CUSTOM_COMPLETION_PATHS
                    ):
                        raise RuntimeError(
                            f"reviewed edit count/scope mismatch: {endpoint_id}: "
                            f"{definition_count}/{constructor_count}/"
                            f"{custom_paths}"
                        )
                    changed_paths.update(custom_paths)
                reedline_edits = {
                    "traversal_direction_import_removal_count": 0,
                    "traversal_method_removal_count": 0,
                    "tab_traversal_block_removal_count": 0,
                    "immediately_accept_line_removal_count": 0,
                    "repl_semicolon_addition_count": 0,
                    "text_object_import_replacement_count": 0,
                    "nested_edit_command_replacement_count": 0,
                    "simple_edit_command_replacement_count": 0,
                    "parse_text_object_compatibility_rewrite_count": 0,
                }
                official_helper_equivalent = False
                repair_policy = "none"
                repair_evidence = None
                if endpoint_id in FULL_REEDLINE_HELPER_ENDPOINTS:
                    transformation, reedline_paths = (
                        apply_official_reedline_helper(
                            checkout, official_reedline_helper
                        )
                    )
                    if (
                        set(reedline_paths) != EXPECTED_REEDLINE_PATHS
                        or transformation[
                            "traversal_direction_import_removal_count"
                        ]
                        != 1
                        or transformation[
                            "tab_traversal_block_removal_count"
                        ]
                        != 1
                        or transformation[
                            "immediately_accept_line_removal_count"
                        ]
                        != 2
                        or transformation["repl_semicolon_addition_count"]
                        != 1
                    ):
                        raise RuntimeError(
                            f"official reedline edit count/scope mismatch: "
                            f"{endpoint_id}: {transformation}"
                        )
                    reedline_edits = {
                        "traversal_direction_import_removal_count": (
                            transformation[
                                "traversal_direction_import_removal_count"
                            ]
                        ),
                        "traversal_method_removal_count": 2,
                        "tab_traversal_block_removal_count": transformation[
                            "tab_traversal_block_removal_count"
                        ],
                        "immediately_accept_line_removal_count": transformation[
                            "immediately_accept_line_removal_count"
                        ],
                        "repl_semicolon_addition_count": transformation[
                            "repl_semicolon_addition_count"
                        ],
                        "text_object_import_replacement_count": 0,
                        "nested_edit_command_replacement_count": 0,
                        "simple_edit_command_replacement_count": 0,
                        "parse_text_object_compatibility_rewrite_count": 0,
                    }
                    official_helper_equivalent = True
                    repair_policy = "docker_full_fix_reedline_api_helper"
                    milestone_key = endpoint_id.split(":", 1)[0]
                    docker_root = full_helper_docker_roots[milestone_key]
                    repair_evidence = {
                        "kind": "milestone_docker_full_helper",
                        "helper_sha256": official_reedline_helper_sha,
                        "helper_source": REEDLINE_HELPER_EVIDENCE,
                        "dockerfile": str(
                            (docker_root / "Dockerfile").relative_to(work_root)
                        ),
                        "dockerfile_sha256": sha256(
                            docker_root / "Dockerfile"
                        ),
                        "docker_helper": str(
                            (
                                docker_root
                                / "env_patch_script/fix_reedline_api.py"
                            ).relative_to(work_root)
                        ),
                    }
                    changed_paths.update(reedline_paths)
                elif endpoint_id in M09_INLINE_REEDLINE_ENDPOINTS:
                    reedline_edits = apply_m09_reedline_inline(checkout)
                    if (
                        reedline_edits[
                            "traversal_direction_import_removal_count"
                        ]
                        != 1
                        or reedline_edits["traversal_method_removal_count"] != 2
                        or reedline_edits[
                            "text_object_import_replacement_count"
                        ]
                        != 1
                        or reedline_edits[
                            "nested_edit_command_replacement_count"
                        ]
                        != 4
                        or reedline_edits[
                            "simple_edit_command_replacement_count"
                        ]
                        != 2
                        or reedline_edits[
                            "parse_text_object_compatibility_rewrite_count"
                        ]
                        != 1
                        or set(reedline_edits["changed_paths"])
                        != {REEDLINE_CONFIG.as_posix()}
                    ):
                        raise RuntimeError(
                            f"M09 inline reedline edit mismatch: "
                            f"{endpoint_id}: {reedline_edits}"
                        )
                    repair_policy = "milestone_m09_docker_inline_reedline_block"
                    repair_evidence = {
                        "kind": "milestone_docker_inline_block",
                        "dockerfile": M09_DOCKER_EVIDENCE,
                        "dockerfile_sha256": m09_docker_sha,
                        "line_ranges": ["68-106", "404-442"],
                    }
                    changed_paths.update(reedline_edits["changed_paths"])
                elif endpoint_id in MINIMAL_TRAVERSAL_ENDPOINTS:
                    reedline_edits = apply_traversal_only(checkout)
                    if (
                        reedline_edits[
                            "traversal_direction_import_removal_count"
                        ]
                        != 1
                        or reedline_edits["traversal_method_removal_count"] != 2
                        or set(reedline_edits["changed_paths"])
                        != {REEDLINE_CONFIG.as_posix()}
                    ):
                        raise RuntimeError(
                            f"minimal traversal edit mismatch: "
                            f"{endpoint_id}: {reedline_edits}"
                        )
                    repair_policy = "resolved_api_minimal_traversal_only"
                    repair_evidence = {
                        "kind": "resolved_dependency_api_audit",
                        "reason": "no_endpoint_docker_helper",
                    }
                    changed_paths.update(reedline_edits["changed_paths"])
                if endpoint_id in M04_TEST_HOIST_CORRECTION_ENDPOINTS:
                    m04_edits, m04_paths, m04_test_edits = (
                        apply_m04_test_hoist_corrections(
                            checkout, docker_sha=m04_docker_sha
                        )
                    )
                    if (
                        m04_edits["completion_fixture_field_removal_count"] != 5
                        or m04_edits["pipefail_test_block_removal_count"] != 1
                        or m04_edits["pipefail_active_case_removal_count"] != 6
                        or m04_edits["pipefail_rstest_import_removal_count"] != 1
                        or len(m04_paths) != 2
                        or len(m04_test_edits) != 2
                    ):
                        raise RuntimeError(
                            f"M04 reviewed test correction mismatch: "
                            f"{endpoint_id}: {m04_edits}"
                        )
                    changed_paths.update(m04_paths)
                    test_edits.extend(m04_test_edits)
                if endpoint_id in M09_COMPATIBILITY_ENDPOINTS:
                    m09_edits, m09_paths, m09_test_edits = (
                        apply_m09_compatibility(
                            checkout, docker_sha=m09_docker_sha
                        )
                    )
                    if (
                        len(m09_paths) != 19
                        or len(m09_test_edits) != 1
                        or m09_edits[
                            "value_non_exhaustive_rest_addition_count"
                        ]
                        != 16
                        or m09_edits[
                            "parser_error_fixture_replacement_count"
                        ]
                        != 2
                        or m09_edits[
                            "multiline_value_rest_without_trailing_comma_count"
                        ]
                        != 13
                        or m09_edits["invalid_rest_trailing_comma_count"] != 0
                    ):
                        raise RuntimeError(
                            f"M09 reviewed compatibility mismatch: "
                            f"{endpoint_id}: {m09_edits}"
                        )
                    (
                        m09_test_api_edits,
                        m09_test_api_paths,
                        m09_test_api_records,
                    ) = apply_m09_test_api_compatibility(
                        checkout, docker_sha=m09_docker_sha
                    )
                    if (
                        len(m09_test_api_paths) != 3
                        or len(m09_test_api_records) != 3
                        or m09_test_api_edits[
                            "test_api_alignment_record_count"
                        ]
                        != 3
                        or m09_test_api_edits[
                            "test_api_alignment_operation_count"
                        ]
                        != 3
                    ):
                        raise RuntimeError(
                            f"M09 reviewed test API mismatch: "
                            f"{endpoint_id}: {m09_test_api_edits}"
                        )
                    m09_edits.update(m09_test_api_edits)
                    changed_paths.update(m09_paths)
                    changed_paths.update(m09_test_api_paths)
                    test_edits.extend(m09_test_edits)
                    test_edits.extend(m09_test_api_records)
                if endpoint_id in M03_DURATION_COMPATIBILITY_ENDPOINTS:
                    m03_edits, m03_paths = (
                        apply_m03_duration_compatibility(checkout)
                    )
                    if (
                        len(m03_paths) != 1
                        or m03_edits[
                            "duration_from_value_prerequisite_transplant_count"
                        ]
                        != 1
                        or m03_edits["duration_from_value_impl_line_count"]
                        != 22
                        or m03_edits["task_implementation_rewrite_count"]
                        != 0
                    ):
                        raise RuntimeError(
                            f"M03 Duration prerequisite mismatch: "
                            f"{endpoint_id}: {m03_edits}"
                        )
                    m03_resolved_api_edits, m03_api_paths = (
                        apply_reviewed_resolved_api_subset(
                            checkout,
                            include_save=False,
                            include_reedline_pair_variants=True,
                            label="M03",
                        )
                    )
                    (
                        m03_test_corrections,
                        m03_test_paths,
                        m03_test_edits,
                    ) = apply_m03_signature_fixture_correction(
                        checkout, docker_sha=m03_docker_sha
                    )
                    if (
                        len(m03_api_paths) != 11
                        or m03_resolved_api_edits["operation_count"] != 22
                        or m03_resolved_api_edits[
                            "value_non_exhaustive_rest_addition_count"
                        ]
                        != 15
                        or len(m03_test_paths) != 1
                        or len(m03_test_edits) != 1
                        or m03_test_corrections[
                            "completion_fixture_field_removal_count"
                        ]
                        != 5
                    ):
                        raise RuntimeError(
                            f"M03 reviewed runtime compatibility mismatch: "
                            f"{endpoint_id}: {m03_resolved_api_edits}/"
                            f"{m03_test_corrections}"
                        )
                    (
                        m03_test_api_corrections,
                        m03_test_api_paths,
                        m03_test_api_edits,
                    ) = apply_m09_test_api_compatibility(
                        checkout,
                        docker_sha=m03_docker_sha,
                        label="M03 schema7",
                        docker_evidence=M03_DOCKER_EVIDENCE,
                        failure_job_id="14289040",
                    )
                    (
                        m03_pipefail_correction,
                        m03_pipefail_paths,
                        m03_pipefail_edits,
                    ) = apply_m03_pipefail_test_hoist_correction(
                        checkout, docker_sha=m03_docker_sha
                    )
                    if (
                        len(m03_test_api_paths) != 3
                        or len(m03_test_api_edits) != 3
                        or m03_test_api_corrections[
                            "test_api_alignment_record_count"
                        ]
                        != 3
                        or m03_test_api_corrections[
                            "test_api_alignment_operation_count"
                        ]
                        != 3
                        or len(m03_pipefail_paths) != 1
                        or len(m03_pipefail_edits) != 1
                        or m03_pipefail_correction[
                            "pipefail_test_block_removal_count"
                        ]
                        != 1
                        or m03_pipefail_correction[
                            "pipefail_rstest_import_removal_count"
                        ]
                        != 1
                    ):
                        raise RuntimeError(
                            f"M03 schema7 test correction mismatch: "
                            f"{endpoint_id}: {m03_test_api_corrections}/"
                            f"{m03_pipefail_correction}"
                        )
                    m03_test_corrections.update(m03_test_api_corrections)
                    m03_test_corrections.update(m03_pipefail_correction)
                    changed_paths.update(m03_paths)
                    changed_paths.update(m03_api_paths)
                    changed_paths.update(m03_test_paths)
                    changed_paths.update(m03_test_api_paths)
                    changed_paths.update(m03_pipefail_paths)
                    test_edits.extend(m03_test_edits)
                    test_edits.extend(m03_test_api_edits)
                    test_edits.extend(m03_pipefail_edits)
                if endpoint_id in M07_PIPELINE_REFACTOR_ENDPOINTS:
                    m07_evidence = {
                        "kind": "complete_same_m07_test_refactor",
                        "endpoint_id": endpoint_id,
                        "test_state_patch": endpoint["test_state"]["patch"][
                            "path"
                        ],
                        "test_state_patch_sha256": endpoint["test_state"][
                            "patch"
                        ]["sha256"],
                    }
                    m07_edits, m07_paths, m07_test_edits = (
                        apply_m07_pipeline_refactor_completion(
                            checkout, evidence=m07_evidence
                        )
                    )
                    if (
                        len(m07_paths) != 2
                        or len(m07_test_edits) != 2
                        or m07_edits[
                            "metadata_pipeline_wrapper_removal_count"
                        ]
                        != 3
                        or m07_edits[
                            "http_pipeline_wrapper_removal_count"
                        ]
                        != 1
                        or m07_edits["pipeline_import_restoration_count"]
                        != 0
                    ):
                        raise RuntimeError(
                            f"M07 pipeline refactor mismatch: "
                            f"{endpoint_id}: {m07_edits}"
                        )
                    changed_paths.update(m07_paths)
                    test_edits.extend(m07_test_edits)
                if endpoint_id in COREDEV1_STABLE_FEATURE_GATE_ENDPOINTS:
                    coredev1_edits, coredev1_paths = (
                        apply_coredev1_stable_feature_gate_removal(checkout)
                    )
                    if (
                        len(coredev1_paths) != 1
                        or coredev1_edits[
                            "stable_feature_gate_removal_count"
                        ]
                        != 1
                    ):
                        raise RuntimeError(
                            f"coredev1 stable feature mismatch: "
                            f"{endpoint_id}: {coredev1_edits}"
                        )
                    changed_paths.update(coredev1_paths)
                if endpoint_id in COREDEV4_EXPERIMENTAL_METADATA_ENDPOINTS:
                    coredev4_edits, coredev4_paths = (
                        apply_coredev4_experimental_metadata_prerequisite(
                            checkout,
                            positive_contents=positive_contents,
                            positive_evidence=positive_evidence,
                        )
                    )
                    if (
                        len(coredev4_paths) != 5
                        or coredev4_edits[
                            "lib_accessor_block_transplant_count"
                        ]
                        != 1
                        or coredev4_edits[
                            "dynamic_trait_block_transplant_count"
                        ]
                        != 1
                        or coredev4_edits[
                            "dynamic_impl_block_transplant_count"
                        ]
                        != 1
                        or coredev4_edits[
                            "version_alias_transplant_count"
                        ]
                        != 1
                        or coredev4_edits[
                            "marker_contract_transplant_count"
                        ]
                        != 1
                        or coredev4_edits[
                            "option_metadata_block_transplant_count"
                        ]
                        != 3
                        or coredev4_edits["option_status_change_count"]
                        != 0
                        or not coredev4_edits[
                            "endpoint_option_status_preserved"
                        ]
                    ):
                        raise RuntimeError(
                            f"coredev4 G02 prerequisite mismatch: "
                            f"{endpoint_id}: {coredev4_edits}"
                        )
                    coredev4_resolved_api_edits, coredev4_api_paths = (
                        apply_reviewed_resolved_api_subset(
                            checkout,
                            include_save=True,
                            include_reedline_pair_variants=False,
                            label="coredev4",
                        )
                    )
                    if (
                        len(coredev4_api_paths) != 11
                        or coredev4_resolved_api_edits["operation_count"] != 21
                        or coredev4_resolved_api_edits[
                            "value_non_exhaustive_rest_addition_count"
                        ]
                        != 16
                    ):
                        raise RuntimeError(
                            f"coredev4 reviewed M09 subset mismatch: "
                            f"{endpoint_id}: {coredev4_resolved_api_edits}"
                        )
                    (
                        coredev4_test_api_edits,
                        coredev4_test_api_paths,
                        coredev4_test_api_records,
                    ) = apply_config_default_test_api_compatibility(
                        checkout,
                        label="coredev4 schema7",
                        docker_evidence=(
                            "SWE-Milestone-data-repartitioned/"
                            "nushell_nushell_0.106.0_0.108.0/dockerfiles/"
                            "milestone_core_development.4/Dockerfile"
                        ),
                        docker_sha=sha256(
                            work_root
                            / "SWE-Milestone-data-repartitioned/"
                            "nushell_nushell_0.106.0_0.108.0/dockerfiles/"
                            "milestone_core_development.4/Dockerfile"
                        ),
                        failure_job_id="14289040",
                    )
                    if (
                        len(coredev4_test_api_paths) != 2
                        or len(coredev4_test_api_records) != 2
                        or coredev4_test_api_edits[
                            "config_test_api_alignment_record_count"
                        ]
                        != 2
                        or coredev4_test_api_edits[
                            "config_test_api_alignment_operation_count"
                        ]
                        != 2
                    ):
                        raise RuntimeError(
                            f"coredev4 schema7 test correction mismatch: "
                            f"{endpoint_id}: {coredev4_test_api_edits}"
                        )
                    coredev4_edits.update(coredev4_test_api_edits)
                    changed_paths.update(coredev4_paths)
                    changed_paths.update(coredev4_api_paths)
                    changed_paths.update(coredev4_test_api_paths)
                    test_edits.extend(coredev4_test_api_records)
                if endpoint_id in SCHEMA6_PARSER_FIXTURE_ENDPOINTS:
                    (
                        schema6_parser_fixture_edits,
                        schema6_parser_paths,
                        schema6_parser_test_edits,
                    ) = apply_schema6_parser_fixture_correction(
                        checkout, endpoint_id=endpoint_id
                    )
                    if (
                        len(schema6_parser_paths) != 1
                        or len(schema6_parser_test_edits) != 1
                        or schema6_parser_fixture_edits[
                            "parser_error_fixture_replacement_count"
                        ]
                        != 2
                    ):
                        raise RuntimeError(
                            f"schema6 parser fixture mismatch: "
                            f"{endpoint_id}: {schema6_parser_fixture_edits}"
                        )
                    changed_paths.update(schema6_parser_paths)
                    test_edits.extend(schema6_parser_test_edits)
                changed_paths = sorted(changed_paths)
                run("git", "add", "-u", cwd=checkout)
                compile_tree = str(
                    run("git", "write-tree", cwd=checkout, capture=True)
                ).strip()
                contract_after = audit_custom_completion_contract(checkout)
                reedline_after = audit_reedline_api_contract(
                    checkout, resolved_path, cargo_home
                )
                if (
                    contract_after["mismatch_count"]
                    or reedline_after["mismatch_count"]
                ):
                    raise RuntimeError(
                        f"compile repair incomplete: {endpoint_id}: "
                        f"{contract_after} {reedline_after}"
                    )
                cargo_identity_after, cargo_rows_after = (
                    cargo_manifest_identity(checkout)
                )
                cargo_lock_after = (
                    sha256(checkout / "Cargo.lock")
                    if (checkout / "Cargo.lock").is_file()
                    else None
                )
                test_after = sha256(checkout / SIGNATURE_TEST)
                signature_test_change_expected = (
                    endpoint_id in M04_TEST_HOIST_CORRECTION_ENDPOINTS
                    or endpoint_id in M03_DURATION_COMPATIBILITY_ENDPOINTS
                )
                if (
                    cargo_identity_after != cargo_identity_before
                    or cargo_rows_after != cargo_rows_before
                    or cargo_lock_after != cargo_lock_before
                    or ((test_after != test_before) != signature_test_change_expected)
                ):
                    raise RuntimeError(
                        f"Cargo/test-scope preservation gate failed: {endpoint_id}"
                    )

                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", endpoint_id)
                patch_path = patches / f"{safe_name}.patch"
                patch_bytes = run(
                    "git",
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-ext-diff",
                    "--no-renames",
                    environment_tree,
                    compile_tree,
                    cwd=checkout,
                    capture=True,
                    binary=True,
                )
                if endpoint_id in M09_COMPATIBILITY_ENDPOINTS:
                    patch_text = patch_bytes.decode("utf-8")
                    legal_rest_additions = len(
                        re.findall(
                            r"^\+[ \t]*\.\.[ \t]*$",
                            patch_text,
                            flags=re.MULTILINE,
                        )
                    )
                    m09_edits[
                        "rest_trailing_comma_patch_addition_count"
                    ] = len(
                        re.findall(
                            r"^\+[ \t]*\.\.,[ \t]*$",
                            patch_text,
                            flags=re.MULTILINE,
                        )
                    )
                    m09_edits[
                        "rest_trailing_comma_patch_deletion_count"
                    ] = len(
                        re.findall(
                            r"^-[ \t]*\.\.,[ \t]*$",
                            patch_text,
                            flags=re.MULTILINE,
                        )
                    )
                    if (
                        legal_rest_additions != 13
                        or m09_edits[
                            "rest_trailing_comma_patch_addition_count"
                        ]
                        != 0
                        or m09_edits[
                            "rest_trailing_comma_patch_deletion_count"
                        ]
                        != 0
                    ):
                        raise RuntimeError(
                            "M09 compile patch rest-line invariant failed: "
                            f"{endpoint_id}: legal={legal_rest_additions}, "
                            "invalid_added="
                            f"{m09_edits['rest_trailing_comma_patch_addition_count']}, "
                            "preexisting_deleted="
                            f"{m09_edits['rest_trailing_comma_patch_deletion_count']}"
                        )
                patch_path.write_bytes(patch_bytes)
                observed_paths = set(
                    str(
                        run(
                            "git",
                            "diff",
                            "--name-only",
                            environment_tree,
                            compile_tree,
                            cwd=checkout,
                            capture=True,
                        )
                    ).splitlines()
                )
                if observed_paths != set(changed_paths):
                    raise RuntimeError(
                        f"compile patch scope mismatch: {endpoint_id}: "
                        f"{observed_paths} != {set(changed_paths)}"
                    )
                observed_test_paths = {
                    path
                    for path in observed_paths
                    if path.startswith("tests/") or "/tests/" in path
                }
                declared_test_paths = {
                    edit["path"] for edit in test_edits
                }
                if observed_test_paths != declared_test_paths:
                    raise RuntimeError(
                        f"undeclared test edit scope: {endpoint_id}: "
                        f"{observed_test_paths} != {declared_test_paths}"
                    )
                for edit in test_edits:
                    edited_path = checkout / edit["path"]
                    if (
                        not edited_path.is_file()
                        or edited_path.stat().st_size == 0
                        or sha256(edited_path) != edit["after_sha256"]
                    ):
                        raise RuntimeError(
                            f"test edit identity mismatch: "
                            f"{endpoint_id}: {edit['path']}"
                        )

                shutil.copyfile(resolved_path, checkout / "Cargo.lock")
                run("git", "add", "Cargo.lock", cwd=checkout)
                compile_runtime_tree = str(
                    run("git", "write-tree", cwd=checkout, capture=True)
                ).strip()
                compile_patch_sha = sha256(patch_path)
                reasons = []
                if definition_count:
                    reasons.append("align_custom_completion_contract")
                if official_helper_equivalent:
                    reasons.append("align_reedline_api_contract")
                elif repair_policy != "none":
                    reasons.append("align_reedline_api_contract")
                if endpoint_id in M04_TEST_HOIST_CORRECTION_ENDPOINTS:
                    reasons.append("correct_m04_test_hoist_contamination")
                if endpoint_id in M09_COMPATIBILITY_ENDPOINTS:
                    reasons.append("align_m09_resolved_dependency_apis")
                if endpoint_id in M03_DURATION_COMPATIBILITY_ENDPOINTS:
                    reasons.append("align_m03_resolved_dependency_apis")
                    reasons.append("correct_m03_test_hoist_contamination")
                    reasons.append(
                        "align_m03_test_fixtures_with_runnable_apis"
                    )
                    reasons.append(
                        "remove_m03_future_pipefail_test_hoist_contamination"
                    )
                    reasons.append(
                        "transplant_positive_duration_from_value_prerequisite"
                    )
                if endpoint_id in M07_PIPELINE_REFACTOR_ENDPOINTS:
                    reasons.append("complete_m07_pipeline_helper_refactor")
                if endpoint_id in COREDEV1_STABLE_FEATURE_GATE_ENDPOINTS:
                    reasons.append("remove_stable_let_chains_feature_gate")
                if endpoint_id in COREDEV4_EXPERIMENTAL_METADATA_ENDPOINTS:
                    reasons.append("align_coredev4_resolved_dependency_apis")
                    reasons.append(
                        "align_coredev4_test_fixtures_with_runnable_apis"
                    )
                    reasons.append(
                        "transplant_g02_experimental_metadata_prerequisite"
                    )
                if endpoint_id in SCHEMA6_PARSER_FIXTURE_ENDPOINTS:
                    reasons.append(
                        "align_parser_error_fixture_with_runnable_parser"
                    )
                if not reasons:
                    reasons.append("none")
                source_repaired = reasons != ["none"]
                row = {
                    "index": index,
                    "endpoint_id": endpoint_id,
                    "patch": f"patches/{patch_path.name}",
                    "patch_sha256": compile_patch_sha,
                    "patch_bytes": patch_path.stat().st_size,
                    "original_tree": original_tree,
                    "environment_tree": environment_tree,
                    "compile_tree": compile_tree,
                    "resolved_lock_sha256": resolved["resolved_lock_sha256"],
                    "closure_runtime_tree": closure_runtime_tree,
                    "compile_runtime_tree": compile_runtime_tree,
                    "reasons": reasons,
                    "custom_completion_definition_edit_count": definition_count,
                    "custom_completion_constructor_edit_count": constructor_count,
                    "reedline_traversal_direction_import_removal_count": (
                        reedline_edits[
                            "traversal_direction_import_removal_count"
                        ]
                    ),
                    "reedline_tab_traversal_block_removal_count": (
                        reedline_edits["tab_traversal_block_removal_count"]
                    ),
                    "reedline_traversal_method_removal_count": (
                        reedline_edits["traversal_method_removal_count"]
                    ),
                    "reedline_immediately_accept_line_removal_count": (
                        reedline_edits[
                            "immediately_accept_line_removal_count"
                        ]
                    ),
                    "reedline_repl_semicolon_addition_count": (
                        reedline_edits["repl_semicolon_addition_count"]
                    ),
                    "reedline_text_object_import_replacement_count": (
                        reedline_edits[
                            "text_object_import_replacement_count"
                        ]
                    ),
                    "reedline_nested_edit_command_replacement_count": (
                        reedline_edits[
                            "nested_edit_command_replacement_count"
                        ]
                    ),
                    "reedline_simple_edit_command_replacement_count": (
                        reedline_edits[
                            "simple_edit_command_replacement_count"
                        ]
                    ),
                    "reedline_parse_text_object_compatibility_rewrite_count": (
                        reedline_edits[
                            "parse_text_object_compatibility_rewrite_count"
                        ]
                    ),
                    "reedline_official_helper_equivalent": (
                        official_helper_equivalent
                    ),
                    "reedline_repair_policy": repair_policy,
                    "reedline_repair_evidence": repair_evidence,
                    "reedline_resolved_dependency": reedline_before[
                        "resolved_dependency"
                    ],
                    "m04_test_corrections": m04_edits,
                    "m09_compatibility_edits": m09_edits,
                    "m03_duration_from_value_edits": m03_edits,
                    "m03_resolved_api_edits": m03_resolved_api_edits,
                    "m03_test_corrections": m03_test_corrections,
                    "m07_pipeline_refactor_edits": m07_edits,
                    "coredev1_stable_feature_gate_edits": coredev1_edits,
                    "coredev4_experimental_metadata_edits": coredev4_edits,
                    "coredev4_resolved_api_edits": (
                        coredev4_resolved_api_edits
                    ),
                    "schema6_parser_fixture_edits": (
                        schema6_parser_fixture_edits
                    ),
                    "test_edits": test_edits,
                    "changed_paths": changed_paths,
                    "cargo_manifest_identity_before": cargo_identity_before,
                    "cargo_manifest_identity_after": cargo_identity_after,
                    "cargo_lock_sha256_before": cargo_lock_before,
                    "cargo_lock_sha256_after": cargo_lock_after,
                    "signature_test_sha256_before": test_before,
                    "signature_test_sha256_after": test_after,
                }
                rows.append(row)

                online_fields = marker_fields(online_root / f"{safe}.tsv")
                offline_fields = marker_fields(offline_root / f"{safe}.tsv")
                expected_online_command = (
                    "cargo-metadata-normalize-online-fetch-locked-v6-"
                    f"{LEGACY_INDEX_FINGERPRINT}-"
                    f"{environment['patch_sha256']}-{environment_tree}"
                )
                expected_offline_command = (
                    "cargo-metadata-fetch-locked-offline-resolved-v6-"
                    f"{LEGACY_INDEX_FINGERPRINT}-"
                    f"{environment['patch_sha256']}-{environment_tree}"
                )
                expected_common = [
                    original_tree,
                    resolved["resolved_lock_sha256"],
                    closure_runtime_tree,
                    "rustc 1.88.0 (6b00bc388 2025-06-23)",
                ]
                if (
                    online_fields[:4] != expected_common
                    or online_fields[4] != expected_online_command
                    or offline_fields[:4] != expected_common
                    or offline_fields[4] != expected_offline_command
                ):
                    raise RuntimeError(
                        f"legacy closure checkpoint mismatch: {endpoint_id}"
                    )
                compile_marker = compile_marker_root / f"{safe}.tsv"
                compile_marker_state = "absent_requires_compile"
                compile_marker_command = None
                compile_marker_sha = None
                prior_compile_marker_command = None
                prior_compile_marker_sha = None
                if compile_marker.exists():
                    compile_fields = marker_fields(compile_marker)
                    compile_marker_command = compile_fields[4]
                    compile_marker_sha = sha256(compile_marker)
                    expected_toolchain = (
                        "cargo 1.88.0 (873a06493 2025-05-10)|"
                        "rustc 1.88.0 (6b00bc388 2025-06-23)"
                    )
                    expected_compile_command = (
                        "cargo-test-no-run-ci-workspace-resolved-lock-v6-"
                        f"{LEGACY_COMPILE_FINGERPRINT}-"
                        f"{environment['patch_sha256']}-{environment_tree}"
                    )
                    expected_compile_common = [
                        original_tree,
                        resolved["resolved_lock_sha256"],
                        closure_runtime_tree,
                        expected_toolchain,
                    ]
                    if (
                        endpoint_id
                        in EXPECTED_PRIOR_V5_CHECKPOINT_ENDPOINTS
                    ):
                        prior_row = prior_schema6_rows[endpoint_id]
                        prior_patch = prior_schema6_root / prior_row["patch"]
                        expected_prior_command = (
                            "cargo-test-no-run-ci-workspace-resolved-lock-"
                            "compatibility-v6-"
                            f"{PRIOR_V6_COMPILE_FINGERPRINT}-"
                            f"{prior_row['patch_sha256']}-"
                            f"{prior_row['compile_tree']}-"
                            f"{prior_row['compile_runtime_tree']}"
                        )
                        prior_common = [
                            original_tree,
                            resolved["resolved_lock_sha256"],
                            prior_row["compile_runtime_tree"],
                            expected_toolchain,
                        ]
                        if (
                            not source_repaired
                            or prior_row["reasons"] == ["none"]
                            or not prior_patch.is_file()
                            or sha256(prior_patch)
                            != prior_row["patch_sha256"]
                            or compile_patch_sha
                            != prior_row["patch_sha256"]
                            or compile_tree != prior_row["compile_tree"]
                            or compile_runtime_tree
                            != prior_row["compile_runtime_tree"]
                            or resolved["resolved_lock_sha256"]
                            != prior_row["resolved_lock_sha256"]
                            or original_tree != prior_row["original_tree"]
                            or environment_tree
                            != prior_row["environment_tree"]
                            or compile_fields[:4] != prior_common
                            or compile_fields[4] != expected_prior_command
                        ):
                            raise RuntimeError(
                                "existing repaired checkpoint is not the "
                                f"exact prior-v6 result: {endpoint_id}"
                            )
                        compile_marker_state = (
                            "exact_prior_compatibility_reusable"
                        )
                        prior_compile_marker_command = compile_fields[4]
                        prior_compile_marker_sha = compile_marker_sha
                    elif source_repaired:
                        raise RuntimeError(
                            "new schema6 repair unexpectedly has an existing "
                            f"compile checkpoint: {endpoint_id}"
                        )
                    else:
                        if (
                            compile_fields[:4] != expected_compile_common
                            or compile_fields[4] != expected_compile_command
                        ):
                            raise RuntimeError(
                                "compile checkpoint is not exact-unaffected: "
                                f"{endpoint_id}"
                            )
                        compile_marker_state = "exact_unaffected_reusable"
                elif not source_repaired:
                    raise RuntimeError(
                        "unaffected endpoint lost its reusable compile "
                        f"checkpoint: {endpoint_id}"
                    )
                elif (
                    endpoint_id
                    in EXPECTED_PRIOR_V5_CHECKPOINT_ENDPOINTS
                ):
                    raise RuntimeError(
                        "exact prior-v6 reusable checkpoint is absent: "
                        f"{endpoint_id}"
                    )
                row.update(
                    {
                        "compile_checkpoint": compile_marker_state,
                        "compile_checkpoint_command_id": (
                            compile_marker_command
                        ),
                        "compile_checkpoint_marker_sha256": (
                            compile_marker_sha
                        ),
                        "prior_compile_checkpoint_command_id": (
                            prior_compile_marker_command
                        ),
                        "prior_compile_checkpoint_marker_sha256": (
                            prior_compile_marker_sha
                        ),
                    }
                )
                migration_rows.append(
                    {
                        "index": index,
                        "endpoint_id": endpoint_id,
                        "cargo_manifest_identity": cargo_identity_before,
                        "resolved_lock_sha256": resolved[
                            "resolved_lock_sha256"
                        ],
                        "closure_runtime_tree": closure_runtime_tree,
                        "compile_runtime_tree": compile_runtime_tree,
                        "compatibility_patch_sha256": compile_patch_sha,
                        "compatibility_applied": source_repaired,
                        "online_checkpoint": "exact_reusable",
                        "offline_checkpoint": "exact_reusable",
                        "compile_checkpoint": compile_marker_state,
                        "compile_checkpoint_command_id": (
                            compile_marker_command
                        ),
                        "compile_checkpoint_marker_sha256": (
                            compile_marker_sha
                        ),
                        "prior_compile_checkpoint_command_id": (
                            prior_compile_marker_command
                        ),
                        "prior_compile_checkpoint_marker_sha256": (
                            prior_compile_marker_sha
                        ),
                    }
                )

        repairs_tsv = staging / "repairs.tsv"
        repairs_tsv.write_text(
            "".join(
                "\t".join(
                    [
                        row["endpoint_id"],
                        row["patch"],
                        row["patch_sha256"],
                        row["environment_tree"],
                        row["compile_tree"],
                        row["compile_runtime_tree"],
                        ",".join(row["reasons"]),
                    ]
                )
                + "\n"
                for row in rows
            )
        )
        migration = {
            "schema_version": 4,
            "kind": "nushell_compile_compatibility_closure_reuse_audit",
            "status": "validated",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "endpoint_count": 42,
            "compatibility_repair_endpoint_count": 13,
            "cargo_manifest_identity_unchanged_count": 42,
            "resolved_lock_sha256_unchanged_count": 42,
            "online_checkpoint_exact_reuse_count": 42,
            "offline_checkpoint_exact_reuse_count": 42,
            "existing_compile_checkpoint_count": sum(
                row["compile_checkpoint"]
                != "absent_requires_compile"
                for row in migration_rows
            ),
            "exact_unaffected_compile_checkpoint_reuse_count": sum(
                row["compile_checkpoint"] == "exact_unaffected_reusable"
                for row in migration_rows
            ),
            "exact_prior_compatibility_checkpoint_reuse_count": sum(
                row["compile_checkpoint"]
                == "exact_prior_compatibility_reusable"
                for row in migration_rows
            ),
            "exact_compile_checkpoint_reuse_count": sum(
                row["compile_checkpoint"]
                in {
                    "exact_unaffected_reusable",
                    "exact_prior_compatibility_reusable",
                }
                for row in migration_rows
            ),
            "prior_compatibility_stale_checkpoint_count": 0,
            "repaired_absent_compile_checkpoint_count": sum(
                row["compatibility_applied"]
                and row["compile_checkpoint"]
                == "absent_requires_compile"
                for row in migration_rows
            ),
            "required_compile_endpoint_count": sum(
                row["compile_checkpoint"] == "absent_requires_compile"
                for row in migration_rows
            ),
            "prior_compile_repairs_manifest_sha256": (
                PRIOR_SCHEMA6_MANIFEST_SHA256
            ),
            "prior_v5_compile_fingerprint": PRIOR_V5_COMPILE_FINGERPRINT,
            "prior_v6_compile_fingerprint": PRIOR_V6_COMPILE_FINGERPRINT,
            "legacy_index_fingerprint": LEGACY_INDEX_FINGERPRINT,
            "legacy_compile_fingerprint": LEGACY_COMPILE_FINGERPRINT,
            "environment_repairs_manifest_sha256": sha256(
                environment_root / "manifest.json"
            ),
            "resolved_lock_index_sha256": sha256(
                resolved_root / "resolved_locks.tsv"
            ),
            "rows": migration_rows,
        }
        if (
            migration["existing_compile_checkpoint_count"] != 39
            or migration[
                "exact_unaffected_compile_checkpoint_reuse_count"
            ]
            != 29
            or migration[
                "exact_prior_compatibility_checkpoint_reuse_count"
            ]
            != 10
            or migration["exact_compile_checkpoint_reuse_count"] != 39
            or migration["prior_compatibility_stale_checkpoint_count"] != 0
            or migration["repaired_absent_compile_checkpoint_count"] != 3
            or migration["required_compile_endpoint_count"] != 3
        ):
            raise RuntimeError(
                "schema7 checkpoint partition is not "
                "39 reusable = 29 unaffected + 10 exact prior-v5; "
                "3 repaired endpoints absent and require compile"
            )
        migration_path = staging / "closure_reuse_audit.json"
        migration_path.write_text(
            json.dumps(migration, indent=2, sort_keys=True) + "\n"
        )
        manifest = {
            "schema_version": 7,
            "kind": "nushell_42_endpoint_compile_repairs",
            "status": "validated",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "endpoint_count": 42,
            "source_only": False,
            "dependency_identity_preserving": True,
            "product_source_and_test_compatibility": True,
            "compatibility_fingerprint_label": "nushell-compatibility-v7",
            "contains_reviewed_test_state_corrections": True,
            "repaired_endpoint_count": sum(
                row["reasons"] != ["none"] for row in rows
            ),
            "repaired_endpoint_ids": [
                row["endpoint_id"]
                for row in rows
                if row["reasons"] != ["none"]
            ],
            "custom_completion_aligned_endpoint_count": 3,
            "custom_completion_definition_edit_count": sum(
                row["custom_completion_definition_edit_count"] for row in rows
            ),
            "custom_completion_constructor_edit_count": sum(
                row["custom_completion_constructor_edit_count"] for row in rows
            ),
            "reedline_api_aligned_endpoint_count": sum(
                "align_reedline_api_contract" in row["reasons"] for row in rows
            ),
            "reedline_full_helper_endpoint_count": sum(
                row["reedline_repair_policy"]
                == "docker_full_fix_reedline_api_helper"
                for row in rows
            ),
            "reedline_m09_inline_endpoint_count": sum(
                row["reedline_repair_policy"]
                == "milestone_m09_docker_inline_reedline_block"
                for row in rows
            ),
            "reedline_minimal_traversal_endpoint_count": sum(
                row["reedline_repair_policy"]
                == "resolved_api_minimal_traversal_only"
                for row in rows
            ),
            "reedline_official_helper_equivalent_endpoint_count": sum(
                row["reedline_official_helper_equivalent"] for row in rows
            ),
            "reedline_traversal_direction_import_removal_count": sum(
                row["reedline_traversal_direction_import_removal_count"]
                for row in rows
            ),
            "reedline_traversal_method_removal_count": sum(
                row["reedline_traversal_method_removal_count"] for row in rows
            ),
            "reedline_tab_traversal_block_removal_count": sum(
                row["reedline_tab_traversal_block_removal_count"]
                for row in rows
            ),
            "reedline_immediately_accept_line_removal_count": sum(
                row["reedline_immediately_accept_line_removal_count"]
                for row in rows
            ),
            "reedline_repl_semicolon_addition_count": sum(
                row["reedline_repl_semicolon_addition_count"] for row in rows
            ),
            "reedline_text_object_import_replacement_count": sum(
                row["reedline_text_object_import_replacement_count"]
                for row in rows
            ),
            "reedline_nested_edit_command_replacement_count": sum(
                row["reedline_nested_edit_command_replacement_count"]
                for row in rows
            ),
            "reedline_simple_edit_command_replacement_count": sum(
                row["reedline_simple_edit_command_replacement_count"]
                for row in rows
            ),
            "reedline_parse_text_object_compatibility_rewrite_count": sum(
                row[
                    "reedline_parse_text_object_compatibility_rewrite_count"
                ]
                for row in rows
            ),
            "official_reedline_helper_sha256": official_reedline_helper_sha,
            "official_reedline_helper_evidence": REEDLINE_HELPER_EVIDENCE,
            "m09_docker_evidence": M09_DOCKER_EVIDENCE,
            "m09_docker_evidence_sha256": m09_docker_sha,
            "m03_docker_evidence": M03_DOCKER_EVIDENCE,
            "m03_docker_evidence_sha256": m03_docker_sha,
            "m04_docker_evidence": M04_DOCKER_EVIDENCE,
            "m04_docker_evidence_sha256": m04_docker_sha,
            "m04_test_hoist_corrected_endpoint_count": sum(
                bool(row["m04_test_corrections"][
                    "pipefail_test_block_removal_count"
                ])
                for row in rows
            ),
            "m04_completion_fixture_field_removal_count": sum(
                row["m04_test_corrections"][
                    "completion_fixture_field_removal_count"
                ]
                for row in rows
            ),
            "m04_pipefail_test_block_removal_count": sum(
                row["m04_test_corrections"][
                    "pipefail_test_block_removal_count"
                ]
                for row in rows
            ),
            "m04_pipefail_active_case_removal_count": sum(
                row["m04_test_corrections"][
                    "pipefail_active_case_removal_count"
                ]
                for row in rows
            ),
            "m04_pipefail_rstest_import_removal_count": sum(
                row["m04_test_corrections"][
                    "pipefail_rstest_import_removal_count"
                ]
                for row in rows
            ),
            "m04_pipefail_canonical_owner_milestone": (
                M04_PIPEFAIL_OWNER_MILESTONE
            ),
            "m04_pipefail_canonical_owner_commit": (
                M04_PIPEFAIL_OWNER_COMMIT
            ),
            "m09_compatibility_endpoint_count": sum(
                "align_m09_resolved_dependency_apis" in row["reasons"]
                for row in rows
            ),
            **{
                f"m09_{key}": sum(
                    row["m09_compatibility_edits"][key] for row in rows
                )
                for key in (
                    "sqlite_import_replacement_count",
                    "sqlite_main_db_replacement_count",
                    "completer_invalid_dereference_semantic_replacement_count",
                    "uucore_import_removal_count",
                    "uucore_localization_setup_removal_count",
                    "uucore_error_message_preservation_count",
                    "ucp_error_alias_count",
                    "uutils_update_variant_replacement_count",
                    "uutils_backup_variant_replacement_count",
                    "uutils_obsolete_option_field_removal_count",
                    "umkdir_api_replacement_count",
                    "value_non_exhaustive_rest_addition_count",
                    "multiline_value_rest_without_trailing_comma_count",
                    "invalid_rest_trailing_comma_count",
                    "preexisting_rest_trailing_comma_count",
                    "preserved_preexisting_rest_trailing_comma_count",
                    "rest_trailing_comma_patch_addition_count",
                    "rest_trailing_comma_patch_deletion_count",
                    "parser_error_fixture_replacement_count",
                    "test_api_alignment_record_count",
                    "test_api_alignment_operation_count",
                )
            },
            "m03_duration_compatibility_endpoint_count": sum(
                "transplant_positive_duration_from_value_prerequisite"
                in row["reasons"]
                for row in rows
            ),
            "m03_duration_from_value_prerequisite_transplant_count": sum(
                row["m03_duration_from_value_edits"][
                    "duration_from_value_prerequisite_transplant_count"
                ]
                for row in rows
            ),
            "m03_task_implementation_rewrite_count": sum(
                row["m03_duration_from_value_edits"][
                    "task_implementation_rewrite_count"
                ]
                for row in rows
            ),
            "m03_resolved_api_operation_count": sum(
                row["m03_resolved_api_edits"]["operation_count"]
                for row in rows
            ),
            "m03_resolved_api_path_count": sum(
                11
                if "align_m03_resolved_dependency_apis" in row["reasons"]
                else 0
                for row in rows
            ),
            "m03_completion_fixture_field_removal_count": sum(
                row["m03_test_corrections"][
                    "completion_fixture_field_removal_count"
                ]
                for row in rows
            ),
            "m03_test_api_alignment_record_count": sum(
                row["m03_test_corrections"][
                    "test_api_alignment_record_count"
                ]
                for row in rows
            ),
            "m03_test_api_alignment_operation_count": sum(
                row["m03_test_corrections"][
                    "test_api_alignment_operation_count"
                ]
                for row in rows
            ),
            "m03_pipefail_test_block_removal_count": sum(
                row["m03_test_corrections"][
                    "pipefail_test_block_removal_count"
                ]
                for row in rows
            ),
            "m03_pipefail_active_case_removal_count": sum(
                row["m03_test_corrections"][
                    "pipefail_active_case_removal_count"
                ]
                for row in rows
            ),
            "m03_pipefail_rstest_import_removal_count": sum(
                row["m03_test_corrections"][
                    "pipefail_rstest_import_removal_count"
                ]
                for row in rows
            ),
            "m07_pipeline_refactor_endpoint_count": sum(
                "complete_m07_pipeline_helper_refactor" in row["reasons"]
                for row in rows
            ),
            "m07_pipeline_wrapper_removal_count": sum(
                row["m07_pipeline_refactor_edits"][
                    "metadata_pipeline_wrapper_removal_count"
                ]
                + row["m07_pipeline_refactor_edits"][
                    "http_pipeline_wrapper_removal_count"
                ]
                for row in rows
            ),
            "m07_pipeline_import_restoration_count": sum(
                row["m07_pipeline_refactor_edits"][
                    "pipeline_import_restoration_count"
                ]
                for row in rows
            ),
            "coredev1_stable_feature_gate_endpoint_count": sum(
                "remove_stable_let_chains_feature_gate" in row["reasons"]
                for row in rows
            ),
            "coredev1_stable_feature_gate_removal_count": sum(
                row["coredev1_stable_feature_gate_edits"][
                    "stable_feature_gate_removal_count"
                ]
                for row in rows
            ),
            "coredev4_experimental_metadata_endpoint_count": sum(
                "transplant_g02_experimental_metadata_prerequisite"
                in row["reasons"]
                for row in rows
            ),
            "coredev4_experimental_metadata_path_count": sum(
                len(
                    set(row["changed_paths"])
                    & {
                        path.as_posix()
                        for path in COREDEV4_EXPERIMENTAL_PATHS
                    }
                )
                if "transplant_g02_experimental_metadata_prerequisite"
                in row["reasons"]
                else 0
                for row in rows
            ),
            "coredev4_resolved_api_path_count": sum(
                11
                if "align_coredev4_resolved_dependency_apis"
                in row["reasons"]
                else 0
                for row in rows
            ),
            "coredev4_resolved_api_operation_count": sum(
                row["coredev4_resolved_api_edits"]["operation_count"]
                for row in rows
            ),
            "coredev4_config_test_api_alignment_record_count": sum(
                row["coredev4_experimental_metadata_edits"][
                    "config_test_api_alignment_record_count"
                ]
                for row in rows
            ),
            "coredev4_config_test_api_alignment_operation_count": sum(
                row["coredev4_experimental_metadata_edits"][
                    "config_test_api_alignment_operation_count"
                ]
                for row in rows
            ),
            "schema6_parser_fixture_endpoint_count": sum(
                "align_parser_error_fixture_with_runnable_parser"
                in row["reasons"]
                and row["endpoint_id"] in SCHEMA6_PARSER_FIXTURE_ENDPOINTS
                for row in rows
            ),
            "schema6_parser_fixture_record_count": sum(
                bool(row["schema6_parser_fixture_edits"][
                    "parser_error_fixture_replacement_count"
                ])
                for row in rows
            ),
            "schema6_parser_fixture_operation_count": sum(
                row["schema6_parser_fixture_edits"][
                    "parser_error_fixture_replacement_count"
                ]
                for row in rows
            ),
            "coredev4_option_metadata_const_addition_count": sum(
                row["coredev4_experimental_metadata_edits"][
                    "option_metadata_const_addition_count"
                ]
                for row in rows
            ),
            "coredev4_option_status_change_count": sum(
                row["coredev4_experimental_metadata_edits"][
                    "option_status_change_count"
                ]
                for row in rows
            ),
            "g02_experimental_metadata_positive_evidence": (
                positive_evidence
            ),
            "test_edit_endpoint_count": sum(
                bool(row["test_edits"]) for row in rows
            ),
            "test_repair_endpoint_count": sum(
                bool(row["test_edits"]) for row in rows
            ),
            "test_edit_record_count": sum(
                len(row["test_edits"]) for row in rows
            ),
            "test_repair_path_record_count": sum(
                len(row["test_edits"]) for row in rows
            ),
            "test_repair_unique_path_count": len(
                {
                    edit["path"]
                    for row in rows
                    for edit in row["test_edits"]
                }
            ),
            "test_edit_operation_count": sum(
                edit["edit_count"]
                for row in rows
                for edit in row["test_edits"]
            ),
            "cargo_manifest_identity_unchanged_count": 42,
            "resolved_lock_sha256_unchanged_count": 42,
            "signature_test_identity_unchanged_count": sum(
                row["signature_test_sha256_before"]
                == row["signature_test_sha256_after"]
                for row in rows
            ),
            "signature_test_identity_corrected_count": sum(
                row["signature_test_sha256_before"]
                != row["signature_test_sha256_after"]
                for row in rows
            ),
            "test_file_deletion_count": 0,
            "test_suppression_or_ignore_addition_count": 0,
            "environment_repairs_manifest_sha256": sha256(
                environment_root / "manifest.json"
            ),
            "custom_completion_audit_sha256": sha256(audit_path),
            "resolved_lock_manifest_sha256": sha256(
                resolved_root / "manifest.json"
            ),
            "resolved_lock_index_sha256": sha256(
                resolved_root / "resolved_locks.tsv"
            ),
            "repairs_index": "repairs.tsv",
            "repairs_index_sha256": sha256(repairs_tsv),
            "closure_reuse_audit": "closure_reuse_audit.json",
            "closure_reuse_audit_sha256": sha256(migration_path),
            "prior_schema5_compile_repairs_manifest_sha256": (
                prior_compile_manifest_sha
            ),
            "prior_schema6_compile_repairs_manifest_sha256": (
                PRIOR_SCHEMA6_MANIFEST_SHA256
            ),
            "compile_checkpoint_existing_count": migration[
                "existing_compile_checkpoint_count"
            ],
            "compile_checkpoint_exact_unaffected_reuse_count": migration[
                "exact_unaffected_compile_checkpoint_reuse_count"
            ],
            "compile_checkpoint_exact_prior_compatibility_reuse_count": (
                migration[
                    "exact_prior_compatibility_checkpoint_reuse_count"
                ]
            ),
            "compile_checkpoint_exact_reuse_count": migration[
                "exact_compile_checkpoint_reuse_count"
            ],
            "compile_checkpoint_required_compile_count": migration[
                "required_compile_endpoint_count"
            ],
            "endpoints": rows,
        }
        if (
            manifest["repaired_endpoint_count"] != 13
            or set(manifest["repaired_endpoint_ids"])
            != EXPECTED_SCHEMA4_REPAIR_ENDPOINTS
            or manifest[
                "m09_multiline_value_rest_without_trailing_comma_count"
            ]
            != 26
            or manifest["m09_invalid_rest_trailing_comma_count"] != 0
            or manifest[
                "m09_preexisting_rest_trailing_comma_count"
            ]
            != 26
            or manifest[
                "m09_preserved_preexisting_rest_trailing_comma_count"
            ]
            != 26
            or manifest[
                "m09_rest_trailing_comma_patch_addition_count"
            ]
            != 0
            or manifest[
                "m09_rest_trailing_comma_patch_deletion_count"
            ]
            != 0
            or manifest["m09_test_api_alignment_record_count"] != 6
            or manifest["m09_test_api_alignment_operation_count"] != 6
            or manifest["m03_duration_compatibility_endpoint_count"] != 2
            or manifest[
                "m03_duration_from_value_prerequisite_transplant_count"
            ]
            != 2
            or manifest["m03_task_implementation_rewrite_count"] != 0
            or manifest["m03_resolved_api_operation_count"] != 44
            or manifest["m03_resolved_api_path_count"] != 22
            or manifest["m03_completion_fixture_field_removal_count"] != 10
            or manifest["m03_test_api_alignment_record_count"] != 6
            or manifest["m03_test_api_alignment_operation_count"] != 6
            or manifest["m03_pipefail_test_block_removal_count"] != 2
            or manifest["m03_pipefail_active_case_removal_count"] != 12
            or manifest["m03_pipefail_rstest_import_removal_count"] != 2
            or manifest["m07_pipeline_refactor_endpoint_count"] != 1
            or manifest["m07_pipeline_wrapper_removal_count"] != 4
            or manifest["m07_pipeline_import_restoration_count"] != 0
            or manifest["coredev1_stable_feature_gate_endpoint_count"] != 2
            or manifest["coredev1_stable_feature_gate_removal_count"] != 2
            or manifest[
                "coredev4_experimental_metadata_endpoint_count"
            ]
            != 1
            or manifest[
                "coredev4_experimental_metadata_path_count"
            ]
            != 5
            or manifest[
                "coredev4_option_metadata_const_addition_count"
            ]
            != 6
            or manifest["coredev4_option_status_change_count"] != 0
            or manifest["coredev4_resolved_api_path_count"] != 11
            or manifest["coredev4_resolved_api_operation_count"] != 21
            or manifest[
                "coredev4_config_test_api_alignment_record_count"
            ]
            != 2
            or manifest[
                "coredev4_config_test_api_alignment_operation_count"
            ]
            != 2
            or manifest["schema6_parser_fixture_endpoint_count"] != 3
            or manifest["schema6_parser_fixture_record_count"] != 3
            or manifest["schema6_parser_fixture_operation_count"] != 6
            or manifest["test_edit_endpoint_count"] != 8
            or manifest["test_edit_record_count"] != 29
            or manifest["test_repair_unique_path_count"] != 8
            or manifest["test_edit_operation_count"] != 56
            or manifest["signature_test_identity_unchanged_count"] != 38
            or manifest["signature_test_identity_corrected_count"] != 4
            or manifest["compile_checkpoint_exact_reuse_count"] != 39
            or manifest["compile_checkpoint_required_compile_count"] != 3
        ):
            raise RuntimeError("schema7 compile repair manifest count gate failed")
        if (
            sha256(prior_compile_manifest_path) != prior_compile_manifest_sha
            or sha256(prior_compile_repairs_path)
            != PRIOR_SCHEMA5_REPAIRS_SHA256
            or sha256(prior_compile_closure_path)
            != PRIOR_SCHEMA5_CLOSURE_SHA256
        ):
            raise RuntimeError(
                "formal schema5 compile bundle changed during candidate build"
            )
        if (
            sha256(prior_schema6_root / "manifest.json")
            != PRIOR_SCHEMA6_MANIFEST_SHA256
            or sha256(prior_schema6_root / "repairs.tsv")
            != PRIOR_SCHEMA6_REPAIRS_SHA256
            or sha256(prior_schema6_root / "closure_reuse_audit.json")
            != PRIOR_SCHEMA6_CLOSURE_SHA256
        ):
            raise RuntimeError(
                "formal schema6 compile bundle changed during candidate build"
            )
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
                "repaired_endpoint_count": 13,
                "custom_completion_definition_edit_count": manifest[
                    "custom_completion_definition_edit_count"
                ],
                "custom_completion_constructor_edit_count": manifest[
                    "custom_completion_constructor_edit_count"
                ],
                "reedline_api_aligned_endpoint_count": manifest[
                    "reedline_api_aligned_endpoint_count"
                ],
                "reedline_full_helper_endpoint_count": manifest[
                    "reedline_full_helper_endpoint_count"
                ],
                "reedline_m09_inline_endpoint_count": manifest[
                    "reedline_m09_inline_endpoint_count"
                ],
                "reedline_minimal_traversal_endpoint_count": manifest[
                    "reedline_minimal_traversal_endpoint_count"
                ],
                "online_checkpoint_exact_reuse_count": migration[
                    "online_checkpoint_exact_reuse_count"
                ],
                "offline_checkpoint_exact_reuse_count": migration[
                    "offline_checkpoint_exact_reuse_count"
                ],
                "exact_unaffected_compile_checkpoint_reuse_count": migration[
                    "exact_unaffected_compile_checkpoint_reuse_count"
                ],
                "exact_prior_compatibility_checkpoint_reuse_count": migration[
                    "exact_prior_compatibility_checkpoint_reuse_count"
                ],
                "required_compile_endpoint_count": migration[
                    "required_compile_endpoint_count"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
