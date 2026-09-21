#!/usr/bin/env python3
"""Apply API compatibility patches for milestone_M09_datetime"""

import re
import os

def fix_reedline_config():
    """Fix reedline_config.rs - remove unsupported imports and EditCommand variants"""
    filepath = "/testbed/crates/nu-cli/src/reedline_config.rs"
    if not os.path.exists(filepath):
        return

    with open(filepath, 'r') as f:
        content = f.read()

    # Remove TextObject imports
    content = content.replace(', TextObject, TextObjectScope,', ',')
    content = content.replace(', TextObjectType', '')

    # Replace unsupported EditCommand match arms with error returns
    # Pattern: "commandname" => { ... } or "commandname" => EditCommand::...
    error_return = 'return Err(ShellError::GenericError { error: "Command not supported".to_string(), msg: "".to_string(), span: None, help: None, inner: vec![] })'

    # For commands that have code blocks
    for cmd in ['cutinsidepair', 'copyinsidepair', 'cutaroundpair', 'copyaroundpair']:
        # Match the entire case block including nested braces
        pattern = rf'"{cmd}" => \{{[^}}]*\{{[^}}]*\}}[^}}]*\}}'
        content = re.sub(pattern, f'"{cmd}" => {error_return}, // [ENV-PATCH]', content, flags=re.DOTALL)

    # For commands that reference EditCommand directly
    for cmd in ['copytextobject', 'cuttextobject']:
        pattern = rf'"{cmd}" => EditCommand::[^,]*,'
        content = re.sub(pattern, f'"{cmd}" => {error_return}, // [ENV-PATCH]', content)

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

def fix_completer():
    """Fix completer.rs - comment out bool dereference"""
    filepath = "/testbed/crates/nu-cli/src/completions/completer.rs"
    if not os.path.exists(filepath):
        return

    with open(filepath, 'r') as f:
        content = f.read()

    if '*need_fallback = false;' in content:
        content = content.replace('*need_fallback = false;', '// [ENV-PATCH] *need_fallback = false;')
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Fixed {filepath}")

def fix_uucore_imports():
    """Fix uucore imports - comment out use statements"""
    files = [
        "/testbed/crates/nu-command/src/filesystem/ucp.rs",
        "/testbed/crates/nu-command/src/filesystem/umkdir.rs",
        "/testbed/crates/nu-command/src/filesystem/umv.rs",
        "/testbed/crates/nu-command/src/filesystem/utouch.rs",
        "/testbed/crates/nu-command/src/platform/whoami.rs",
        "/testbed/crates/nu-command/src/system/uname.rs",
    ]

    for filepath in files:
        if not os.path.exists(filepath):
            continue

        with open(filepath, 'r') as f:
            content = f.read()

        if 'use uucore::{localized_help_template, translate};' in content:
            content = content.replace('use uucore::{localized_help_template, translate};',
                                    '// [ENV-PATCH] use uucore::{localized_help_template, translate};')
            with open(filepath, 'w') as f:
                f.write(content)
            print(f"Fixed {filepath}")

def fix_ucp():
    """Fix ucp.rs - remove CpError import, unsupported fields, and fix enums"""
    filepath = "/testbed/crates/nu-command/src/filesystem/ucp.rs"
    if not os.path.exists(filepath):
        return

    with open(filepath, 'r') as f:
        content = f.read()

    # Remove CpError import
    content = content.replace('use uu_cp::CpError;', '// [ENV-PATCH] use uu_cp::CpError;')

    # Remove context and set_selinux_context fields
    content = re.sub(r'^\s*context:.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*set_selinux_context:.*$', '', content, flags=re.MULTILINE)

    # Fix UpdateMode and BackupMode - replace with valid values
    content = re.sub(r'\(UpdateMode::IfOlder', '(UpdateMode::ReplaceAll /* [ENV-PATCH] was: IfOlder */', content)
    content = re.sub(r'\(UpdateMode::All', '(UpdateMode::ReplaceAll /* [ENV-PATCH] was: All */', content)
    content = content.replace('BackupMode::None', 'BackupMode::NoBackup /* [ENV-PATCH] was: None */')

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

def fix_umv():
    """Fix umv.rs - remove unsupported fields and fix enums"""
    filepath = "/testbed/crates/nu-command/src/filesystem/umv.rs"
    if not os.path.exists(filepath):
        return

    with open(filepath, 'r') as f:
        content = f.read()

    # Remove context field
    content = re.sub(r'^\s*context:.*$', '', content, flags=re.MULTILINE)

    # Fix UpdateMode and BackupMode
    content = content.replace('UpdateMode::All', 'UpdateMode::ReplaceAll /* [ENV-PATCH] was: All */')
    content = content.replace('BackupMode::None', 'BackupMode::NoBackup /* [ENV-PATCH] was: None */')

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

def fix_umkdir():
    """Fix umkdir.rs - replace uu_mkdir call with std::fs"""
    filepath = "/testbed/crates/nu-command/src/filesystem/umkdir.rs"
    if not os.path.exists(filepath):
        return

    with open(filepath, 'r') as f:
        content = f.read()

    # Comment out Config struct construction
    content = re.sub(
        r'let config = uu_mkdir::Config \{[^}]*\};',
        '// [ENV-PATCH] Commented out uu_mkdir::Config construction',
        content,
        flags=re.DOTALL
    )

    # Replace uu_mkdir call with std::fs
    content = re.sub(
        r'uu_mkdir::uu_mkdir\(&config\)',
        'std::fs::create_dir_all(path) /* [ENV-PATCH] was: uu_mkdir::uu_mkdir(&config) */',
        content
    )

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

def fix_update_cells():
    """Fix update_cells.rs - add .. to Value::Record pattern"""
    filepath = "/testbed/crates/nu-cmd-extra/src/extra/filters/update_cells.rs"
    if not os.path.exists(filepath):
        return

    with open(filepath, 'r') as f:
        content = f.read()

    # Add .. to Value::Record pattern
    pattern = r'(Value::Record \{\s*ref mut val,\s*internal_span,\s*\})'
    replacement = r'Value::Record {\n                    ref mut val,\n                    internal_span,\n                    ..\n                }'
    content = re.sub(pattern, replacement, content)

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

if __name__ == "__main__":
    fix_reedline_config()
    fix_completer()
    fix_uucore_imports()
    fix_ucp()
    fix_umv()
    fix_umkdir()
    fix_update_cells()
    print("All patches applied successfully")
