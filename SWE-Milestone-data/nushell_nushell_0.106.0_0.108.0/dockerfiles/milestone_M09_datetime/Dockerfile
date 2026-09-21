# Build on pre-configured base image for nushell
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Configure git
RUN git config --global user.email "test@test.com" && \
    git config --global user.name "Test User" && \
    git config --global --add safe.directory /testbed

# Environment variables from CI
ENV NUSHELL_CARGO_PROFILE=ci
ENV NU_LOG_LEVEL=DEBUG
ENV RUST_BACKTRACE=1

# Remove the original /testbed from base image and copy local testbed
RUN rm -rf /testbed
COPY . /testbed/

WORKDIR /testbed

# =============================================================================
# END STATE PATCHES
# =============================================================================
RUN cd /testbed && git checkout milestone-milestone_M09_datetime-end

# [ENV-PATCH] Fix Cargo.toml: remove nu-mcp references
RUN cd /testbed && \
    (sed -i '/"crates\/nu-mcp",/d' Cargo.toml || true) && \
    (sed -i '/^nu-mcp = /d' Cargo.toml || true) && \
    (sed -i '/^mcp = \[$/,/^\]$/d' Cargo.toml || true)

# [ENV-PATCH] Apply all API compatibility patches using inline Python script
RUN cd /testbed && python3 << 'PYTHON_SCRIPT'
import re
import os
import glob

# Fix rusqlite::DatabaseName import
filepath = "/testbed/crates/nu-command/src/database/values/sqlite.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    content = content.replace("Connection, DatabaseName, Error", "Connection, Error")
    # Comment out entire backup_database_to_file function
    content = re.sub(
        r'(pub fn backup_database_to_file\([^}]*\{[^}]*\})',
        r'/* [ENV-PATCH] Commented out - uses DatabaseName::Main\n    \1\n    */',
        content,
        flags=re.DOTALL
    )
    # Comment out entire restore_database_from_file function - has complex nested structure
    content = re.sub(
        r'(pub fn restore_database_from_file\([^{]*\{.*?^\s{4}\})',
        r'/* [ENV-PATCH] Commented out - uses DatabaseName::Main\n    \1\n    */',
        content,
        flags=re.DOTALL | re.MULTILINE
    )
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix reedline_config.rs - remove unsupported imports and replace EditCommand cases
filepath = "/testbed/crates/nu-cli/src/reedline_config.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Remove TextObject-related imports on line 16-17
    content = re.sub(r'MenuBuilder, Reedline, ReedlineEvent, ReedlineMenu, TextObject, TextObjectScope,',
                     'MenuBuilder, Reedline, ReedlineEvent, ReedlineMenu,', content)
    content = re.sub(r'TextObjectType, TraversalDirection,', '', content)

    # Replace with_traversal_direction calls - just remove the method call
    content = re.sub(r'\.with_traversal_direction\(TraversalDirection::\w+\)', '', content)

    # Replace specific EditCommand case branches - match complete case including closing brace
    error_replacement = 'return Err(ShellError::GenericError { error: "Not supported".into(), msg: "".into(), span: None, help: None, inner: vec![] })'

    # For commands with nested braces (cutinsidepair, copyinsidepair, cutaroundpair, copyaroundpair)
    for cmd in ['cutinsidepair', 'copyinsidepair', 'cutaroundpair', 'copyaroundpair']:
        pattern = rf'"{cmd}"\s*=>\s*\{{[^{{}}]*\{{[^{{}}]*\}}\s*\}}'
        content = re.sub(pattern, f'"{cmd}" => {error_replacement}, // [ENV-PATCH]', content, flags=re.DOTALL)

    # For simpler commands (copytextobject, cuttextobject)
    for cmd in ['copytextobject', 'cuttextobject']:
        pattern = rf'"{cmd}"\s*=>\s*EditCommand::\w+\s*\{{[^}}]*\}},'
        content = re.sub(pattern, f'"{cmd}" => {error_replacement}, // [ENV-PATCH]', content, flags=re.DOTALL)

    # Comment out the parse_text_object function entirely since it uses unsupported types
    # Match from "fn parse_text_object" to the closing brace of the function
    content = re.sub(
        r'(fn parse_text_object\(.*?\n\) -> Result<TextObject, ShellError> \{.*?\n\})',
        r'/* [ENV-PATCH] Commented out - uses unsupported TextObject types\n\1\n*/',
        content,
        flags=re.DOTALL
    )

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix completer.rs - comment out bool dereference
filepath = "/testbed/crates/nu-cli/src/completions/completer.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    if '*need_fallback = false;' in content:
        content = content.replace('*need_fallback = false;', '// [ENV-PATCH] *need_fallback = false;')
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Fixed {filepath}")

# Fix uucore imports and usage
uucore_files = [
    "/testbed/crates/nu-command/src/filesystem/ucp.rs",
    "/testbed/crates/nu-command/src/filesystem/umkdir.rs",
    "/testbed/crates/nu-command/src/filesystem/umv.rs",
    "/testbed/crates/nu-command/src/filesystem/utouch.rs",
    "/testbed/crates/nu-command/src/platform/whoami.rs",
    "/testbed/crates/nu-command/src/system/uname.rs",
    "/testbed/crates/nu-command/src/filesystem/mktemp.rs",
]
for filepath in uucore_files:
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            content = f.read()
        modified = False
        # Comment out import
        if 'use uucore::{localized_help_template, translate};' in content:
            content = content.replace(
                'use uucore::{localized_help_template, translate};',
                '// [ENV-PATCH] use uucore::{localized_help_template, translate};'
            )
            modified = True
        # Replace localized_help_template() calls with simple strings
        if 'localized_help_template(' in content:
            content = re.sub(r'localized_help_template\([^)]*\)', '""', content)
            modified = True
        # Replace translate!() calls with simple strings - handle nested parens
        if 'translate!(&error.to_string())' in content:
            content = content.replace('msg: translate!(&error.to_string()),', 'msg: "".to_string(), // [ENV-PATCH]')
            modified = True
        if 'translate!' in content:
            # Fallback for other patterns without nested parens
            content = re.sub(r'translate!\([^()]*\)', '"".to_string()', content)
            modified = True
        if modified:
            with open(filepath, 'w') as f:
                f.write(content)
            print(f"Fixed {filepath}")

# Fix ucp.rs - UpdateMode, BackupMode, CpError, context fields
filepath = "/testbed/crates/nu-command/src/filesystem/ucp.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    # Remove CpError from import line
    content = content.replace('use uu_cp::{BackupMode, CopyMode, CpError, UpdateMode};',
                              'use uu_cp::{BackupMode, CopyMode, UpdateMode}; // [ENV-PATCH] removed CpError')
    # Remove context and set_selinux_context fields
    content = re.sub(r'^\s*context:\s*.*,?\s*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*set_selinux_context:\s*.*,?\s*$', '', content, flags=re.MULTILINE)
    # Replace the entire match error block since CpError is not available
    # Original: match error { CpError::NotAllFilesCopied => {} _ => {...} };
    # Replace with just the error return
    old_match = r'''match error \{
                // code should still be EXIT_ERR as does GNU cp
                CpError::NotAllFilesCopied => \{\}
                _ => \{
                    eprintln!\("here"\);
                    return Err\(ShellError::GenericError \{
                        error: format!\("\{error\}"\),
                        msg: translate!\(&error\.to_string\(\)\),
                        span: None,
                        help: None,
                        inner: vec!\[\],
                    \}\);
                \}
            \};'''
    new_code = '''// [ENV-PATCH] Simplified error handling since CpError is not available
            return Err(ShellError::GenericError {
                error: format!("{error}"),
                msg: "".to_string(), // [ENV-PATCH]
                span: None,
                help: None,
                inner: vec![],
            });'''
    content = re.sub(old_match, new_code, content, flags=re.DOTALL)
    # Add context field - simple replacement before closing braces of Attributes structs
    content = content.replace('        };\n        Ok(attributes)', '            context: None, // [ENV-PATCH]\n        };\n        Ok(attributes)')
    content = content.replace('            xattr: ATTR_UNSET,\n        };', '            xattr: ATTR_UNSET,\n            context: None, // [ENV-PATCH]\n        };')
    content = content.replace('            links: ATTR_UNSET,\n        })', '            links: ATTR_UNSET,\n            context: None, // [ENV-PATCH]\n        })')
    # Also remove any CpError usage in match patterns
    content = re.sub(r'CpError::', '// [ENV-PATCH] CpError:: ', content)
    # Fix UpdateMode and BackupMode enums - be careful not to replace inside error messages
    content = content.replace('(UpdateMode::IfOlder', '(UpdateMode::ReplaceAll /* [ENV-PATCH] was: IfOlder */')
    content = content.replace('(UpdateMode::All', '(UpdateMode::ReplaceAll /* [ENV-PATCH] was: All */')
    content = content.replace('BackupMode::None', 'BackupMode::NoBackup /* [ENV-PATCH] was: None */')
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix umv.rs - UpdateMode, BackupMode, context field
filepath = "/testbed/crates/nu-command/src/filesystem/umv.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    # Remove context field
    content = re.sub(r'^\s*context:\s*.*,?\s*$', '', content, flags=re.MULTILINE)
    # Fix UpdateMode and BackupMode enums
    content = content.replace('UpdateMode::IfOlder', 'UpdateMode::ReplaceAll /* [ENV-PATCH] was: IfOlder */')
    content = content.replace('UpdateMode::All', 'UpdateMode::ReplaceAll /* [ENV-PATCH] was: All */')
    content = content.replace('BackupMode::None', 'BackupMode::NoBackup /* [ENV-PATCH] was: None */')
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix umkdir.rs - replace uu_mkdir call with std::fs
filepath = "/testbed/crates/nu-command/src/filesystem/umkdir.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    # Comment out Config construction and replace mkdir call
    content = re.sub(
        r'let config = uu_mkdir::Config \{[^}]*\};',
        '// [ENV-PATCH] let config = ...',
        content,
        flags=re.DOTALL
    )
    content = re.sub(
        r'mkdir\(&dir,\s*&config\)',
        'std::fs::create_dir_all(&dir) /* [ENV-PATCH] was: mkdir(&dir, &config) */',
        content
    )
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix Value::Record patterns - add .. for non-exhaustive enum
for rs_file in glob.glob('/testbed/crates/**/*.rs', recursive=True):
    try:
        with open(rs_file, 'r') as f:
            content = f.read()
        modified = False
        # Pattern: Value::Record with only val and internal_span
        patterns_to_fix = [
            (r'Value::Record\s*\{\s*ref\s+mut\s+val,\s*internal_span,\s*\}',
             'Value::Record { ref mut val, internal_span, .. }'),
            (r'Value::Record\s*\{\s*val,\s*internal_span,\s*\}',
             'Value::Record { val, internal_span, .. }'),
            (r'Value::Range\s*\{\s*ref\s+val,\s*internal_span,\s*\}',
             'Value::Range { ref val, internal_span, .. }'),
            (r'Value::Range\s*\{\s*val,\s*internal_span,\s*\}',
             'Value::Range { val, internal_span, .. }'),
        ]
        for pattern, replacement in patterns_to_fix:
            new_content = re.sub(pattern, replacement, content)
            if new_content != content:
                content = new_content
                modified = True
        if modified:
            with open(rs_file, 'w') as f:
                f.write(content)
    except Exception:
        pass

# Fix update_cells.rs specifically
filepath = "/testbed/crates/nu-cmd-extra/src/extra/filters/update_cells.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    old_pattern = """Value::Record {
                    ref mut val,
                    internal_span,
                },"""
    new_pattern = """Value::Record {
                    ref mut val,
                    internal_span,
                    ..
                },"""
    if old_pattern in content:
        content = content.replace(old_pattern, new_pattern)
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Fixed {filepath}")

# Fix save.rs - add .. to Value::Custom pattern
filepath = "/testbed/crates/nu-command/src/filesystem/save.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    content = content.replace('Value::Custom { val, internal_span }', 'Value::Custom { val, internal_span, .. }')
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix seq_date.rs - add .. to Value patterns
filepath = "/testbed/crates/nu-command/src/generators/seq_date.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    content = content.replace('Value::Int { val, internal_span }', 'Value::Int { val, internal_span, .. }')
    content = content.replace('Value::Duration { val, internal_span }', 'Value::Duration { val, internal_span, .. }')
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix stor/import.rs - comment out the entire run function since restore_database_from_file is not available
filepath = "/testbed/crates/nu-command/src/stor/import.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    # Comment out the entire run function
    content = re.sub(
        r'(    fn run\([^)]*\) -> Result<PipelineData, ShellError> \{.*?^    \})',
        r'    /* [ENV-PATCH] Commented out - uses restore_database_from_file which is not available\n\1\n    */\n    fn run(\n        &self,\n        _engine_state: &EngineState,\n        _stack: &mut Stack,\n        call: &Call,\n        _input: PipelineData,\n    ) -> Result<PipelineData, ShellError> {\n        Err(ShellError::GenericError {\n            error: "stor import not available".into(),\n            msg: "restore_database_from_file is not available in this version".into(),\n            span: Some(call.head),\n            help: None,\n            inner: vec![],\n        })\n    }',
        content,
        flags=re.MULTILINE | re.DOTALL
    )
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix ParseError::InvalidBinaryString test cases - comment them out
filepath = "/testbed/crates/nu-parser/tests/test_parser.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    # Comment out test functions that use InvalidBinaryString
    content = re.sub(
        r'(#\[test\]\s*fn test_invalid_binary_string\([^{]*\{[^}]*ParseError::InvalidBinaryString[^}]*\})',
        r'/* [ENV-PATCH] Commented out - InvalidBinaryString not available\n\1\n*/',
        content,
        flags=re.DOTALL
    )
    # Also comment out specific assertions
    content = content.replace('Some(ParseError::InvalidBinaryString(_, _))', '_ /* [ENV-PATCH] Some(ParseError::InvalidBinaryString(_, _)) */')
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

print("All patches applied successfully")
PYTHON_SCRIPT

# [ENV-PATCH] Force fix uucore imports in mktemp.rs and whoami.rs using sed
RUN cd /testbed && \
    sed -i 's/^use uucore::{localized_help_template, translate};$/\/\/ [ENV-PATCH] use uucore::{localized_help_template, translate};/' \
        crates/nu-command/src/filesystem/mktemp.rs \
        crates/nu-command/src/platform/whoami.rs || true

# Commit END state patches
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix API compatibility issues for END state" && \
    git tag -f milestone-milestone_M09_datetime-end HEAD

# =============================================================================
# START STATE PATCHES
# =============================================================================
RUN cd /testbed && git checkout milestone-milestone_M09_datetime-start

# [ENV-PATCH] Fix Cargo.toml: remove nu-mcp references
RUN cd /testbed && \
    (sed -i '/"crates\/nu-mcp",/d' Cargo.toml || true) && \
    (sed -i '/^nu-mcp = /d' Cargo.toml || true) && \
    (sed -i '/^mcp = \[$/,/^\]$/d' Cargo.toml || true)

# [ENV-PATCH] Apply same patches to START state
RUN cd /testbed && python3 << 'PYTHON_SCRIPT'
import re
import os
import glob

# Fix rusqlite::DatabaseName import
filepath = "/testbed/crates/nu-command/src/database/values/sqlite.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    content = content.replace("Connection, DatabaseName, Error", "Connection, Error")
    # Comment out entire backup_database_to_file function
    content = re.sub(
        r'(pub fn backup_database_to_file\([^}]*\{[^}]*\})',
        r'/* [ENV-PATCH] Commented out - uses DatabaseName::Main\n    \1\n    */',
        content,
        flags=re.DOTALL
    )
    # Comment out entire restore_database_from_file function - has complex nested structure
    content = re.sub(
        r'(pub fn restore_database_from_file\([^{]*\{.*?^\s{4}\})',
        r'/* [ENV-PATCH] Commented out - uses DatabaseName::Main\n    \1\n    */',
        content,
        flags=re.DOTALL | re.MULTILINE
    )
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix reedline_config.rs - remove unsupported imports and replace EditCommand cases
filepath = "/testbed/crates/nu-cli/src/reedline_config.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Remove TextObject-related imports on line 16-17
    content = re.sub(r'MenuBuilder, Reedline, ReedlineEvent, ReedlineMenu, TextObject, TextObjectScope,',
                     'MenuBuilder, Reedline, ReedlineEvent, ReedlineMenu,', content)
    content = re.sub(r'TextObjectType, TraversalDirection,', '', content)

    # Replace with_traversal_direction calls - just remove the method call
    content = re.sub(r'\.with_traversal_direction\(TraversalDirection::\w+\)', '', content)

    # Replace specific EditCommand case branches - match complete case including closing brace
    error_replacement = 'return Err(ShellError::GenericError { error: "Not supported".into(), msg: "".into(), span: None, help: None, inner: vec![] })'

    # For commands with nested braces (cutinsidepair, copyinsidepair, cutaroundpair, copyaroundpair)
    for cmd in ['cutinsidepair', 'copyinsidepair', 'cutaroundpair', 'copyaroundpair']:
        pattern = rf'"{cmd}"\s*=>\s*\{{[^{{}}]*\{{[^{{}}]*\}}\s*\}}'
        content = re.sub(pattern, f'"{cmd}" => {error_replacement}, // [ENV-PATCH]', content, flags=re.DOTALL)

    # For simpler commands (copytextobject, cuttextobject)
    for cmd in ['copytextobject', 'cuttextobject']:
        pattern = rf'"{cmd}"\s*=>\s*EditCommand::\w+\s*\{{[^}}]*\}},'
        content = re.sub(pattern, f'"{cmd}" => {error_replacement}, // [ENV-PATCH]', content, flags=re.DOTALL)

    # Comment out the parse_text_object function entirely since it uses unsupported types
    # Match from "fn parse_text_object" to the closing brace of the function
    content = re.sub(
        r'(fn parse_text_object\(.*?\n\) -> Result<TextObject, ShellError> \{.*?\n\})',
        r'/* [ENV-PATCH] Commented out - uses unsupported TextObject types\n\1\n*/',
        content,
        flags=re.DOTALL
    )

    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix completer.rs - comment out bool dereference
filepath = "/testbed/crates/nu-cli/src/completions/completer.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    if '*need_fallback = false;' in content:
        content = content.replace('*need_fallback = false;', '// [ENV-PATCH] *need_fallback = false;')
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Fixed {filepath}")

# Fix uucore imports and usage
uucore_files = [
    "/testbed/crates/nu-command/src/filesystem/ucp.rs",
    "/testbed/crates/nu-command/src/filesystem/umkdir.rs",
    "/testbed/crates/nu-command/src/filesystem/umv.rs",
    "/testbed/crates/nu-command/src/filesystem/utouch.rs",
    "/testbed/crates/nu-command/src/filesystem/whoami.rs",
    "/testbed/crates/nu-command/src/system/uname.rs",
]
for filepath in uucore_files:
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            content = f.read()
        modified = False
        # Comment out import
        if 'use uucore::{localized_help_template, translate};' in content:
            content = content.replace(
                'use uucore::{localized_help_template, translate};',
                '// [ENV-PATCH] use uucore::{localized_help_template, translate};'
            )
            modified = True
        # Replace localized_help_template() calls with simple strings
        if 'localized_help_template(' in content:
            content = re.sub(r'localized_help_template\([^)]*\)', '""', content)
            modified = True
        # Replace translate!() calls with simple strings - handle nested parens
        if 'translate!(&error.to_string())' in content:
            content = content.replace('msg: translate!(&error.to_string()),', 'msg: "".to_string(), // [ENV-PATCH]')
            modified = True
        if 'translate!' in content:
            # Fallback for other patterns without nested parens
            content = re.sub(r'translate!\([^()]*\)', '"".to_string()', content)
            modified = True
        if modified:
            with open(filepath, 'w') as f:
                f.write(content)
            print(f"Fixed {filepath}")

# Fix ucp.rs - UpdateMode, BackupMode, CpError, context fields
filepath = "/testbed/crates/nu-command/src/filesystem/ucp.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    # Remove CpError from import line
    content = content.replace('use uu_cp::{BackupMode, CopyMode, CpError, UpdateMode};',
                              'use uu_cp::{BackupMode, CopyMode, UpdateMode}; // [ENV-PATCH] removed CpError')
    # Remove context and set_selinux_context fields
    content = re.sub(r'^\s*context:\s*.*,?\s*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\s*set_selinux_context:\s*.*,?\s*$', '', content, flags=re.MULTILINE)
    # Fix UpdateMode and BackupMode enums
    content = content.replace('(UpdateMode::IfOlder', '(UpdateMode::ReplaceAll /* [ENV-PATCH] was: IfOlder */')
    content = content.replace('(UpdateMode::All', '(UpdateMode::ReplaceAll /* [ENV-PATCH] was: All */')
    content = content.replace('BackupMode::None', 'BackupMode::NoBackup /* [ENV-PATCH] was: None */')
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix umv.rs - UpdateMode, BackupMode, context field
filepath = "/testbed/crates/nu-command/src/filesystem/umv.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    # Remove context field
    content = re.sub(r'^\s*context:\s*.*,?\s*$', '', content, flags=re.MULTILINE)
    # Fix UpdateMode and BackupMode enums
    content = content.replace('UpdateMode::IfOlder', 'UpdateMode::ReplaceAll /* [ENV-PATCH] was: IfOlder */')
    content = content.replace('UpdateMode::All', 'UpdateMode::ReplaceAll /* [ENV-PATCH] was: All */')
    content = content.replace('BackupMode::None', 'BackupMode::NoBackup /* [ENV-PATCH] was: None */')
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix umkdir.rs - replace uu_mkdir call with std::fs
filepath = "/testbed/crates/nu-command/src/filesystem/umkdir.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    # Comment out Config construction and replace mkdir call
    content = re.sub(
        r'let config = uu_mkdir::Config \{[^}]*\};',
        '// [ENV-PATCH] let config = ...',
        content,
        flags=re.DOTALL
    )
    content = re.sub(
        r'mkdir\(&dir,\s*&config\)',
        'std::fs::create_dir_all(&dir) /* [ENV-PATCH] was: mkdir(&dir, &config) */',
        content
    )
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix Value::Record patterns - add .. for non-exhaustive enum
for rs_file in glob.glob('/testbed/crates/**/*.rs', recursive=True):
    try:
        with open(rs_file, 'r') as f:
            content = f.read()
        modified = False
        # Pattern: Value::Record with only val and internal_span
        patterns_to_fix = [
            (r'Value::Record\s*\{\s*ref\s+mut\s+val,\s*internal_span,\s*\}',
             'Value::Record { ref mut val, internal_span, .. }'),
            (r'Value::Record\s*\{\s*val,\s*internal_span,\s*\}',
             'Value::Record { val, internal_span, .. }'),
            (r'Value::Range\s*\{\s*ref\s+val,\s*internal_span,\s*\}',
             'Value::Range { ref val, internal_span, .. }'),
            (r'Value::Range\s*\{\s*val,\s*internal_span,\s*\}',
             'Value::Range { val, internal_span, .. }'),
        ]
        for pattern, replacement in patterns_to_fix:
            new_content = re.sub(pattern, replacement, content)
            if new_content != content:
                content = new_content
                modified = True
        if modified:
            with open(rs_file, 'w') as f:
                f.write(content)
    except Exception:
        pass

# Fix update_cells.rs specifically
filepath = "/testbed/crates/nu-cmd-extra/src/extra/filters/update_cells.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    old_pattern = """Value::Record {
                    ref mut val,
                    internal_span,
                },"""
    new_pattern = """Value::Record {
                    ref mut val,
                    internal_span,
                    ..
                },"""
    if old_pattern in content:
        content = content.replace(old_pattern, new_pattern)
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Fixed {filepath}")

# Fix save.rs - add .. to Value::Custom pattern
filepath = "/testbed/crates/nu-command/src/filesystem/save.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    content = content.replace('Value::Custom { val, internal_span }', 'Value::Custom { val, internal_span, .. }')
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix seq_date.rs - add .. to Value patterns
filepath = "/testbed/crates/nu-command/src/generators/seq_date.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    content = content.replace('Value::Int { val, internal_span }', 'Value::Int { val, internal_span, .. }')
    content = content.replace('Value::Duration { val, internal_span }', 'Value::Duration { val, internal_span, .. }')
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix stor/import.rs - comment out the entire run function since restore_database_from_file is not available
filepath = "/testbed/crates/nu-command/src/stor/import.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    # Comment out the entire run function
    content = re.sub(
        r'(    fn run\([^)]*\) -> Result<PipelineData, ShellError> \{.*?^    \})',
        r'    /* [ENV-PATCH] Commented out - uses restore_database_from_file which is not available\n\1\n    */\n    fn run(\n        &self,\n        _engine_state: &EngineState,\n        _stack: &mut Stack,\n        call: &Call,\n        _input: PipelineData,\n    ) -> Result<PipelineData, ShellError> {\n        Err(ShellError::GenericError {\n            error: "stor import not available".into(),\n            msg: "restore_database_from_file is not available in this version".into(),\n            span: Some(call.head),\n            help: None,\n            inner: vec![],\n        })\n    }',
        content,
        flags=re.MULTILINE | re.DOTALL
    )
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

# Fix ParseError::InvalidBinaryString test cases - comment them out
filepath = "/testbed/crates/nu-parser/tests/test_parser.rs"
if os.path.exists(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    # Comment out test functions that use InvalidBinaryString
    content = re.sub(
        r'(#\[test\]\s*fn test_invalid_binary_string\([^{]*\{[^}]*ParseError::InvalidBinaryString[^}]*\})',
        r'/* [ENV-PATCH] Commented out - InvalidBinaryString not available\n\1\n*/',
        content,
        flags=re.DOTALL
    )
    # Also comment out specific assertions
    content = content.replace('Some(ParseError::InvalidBinaryString(_, _))', '_ /* [ENV-PATCH] Some(ParseError::InvalidBinaryString(_, _)) */')
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Fixed {filepath}")

print("All patches applied successfully")
PYTHON_SCRIPT

# [ENV-PATCH] Force fix uucore imports in mktemp.rs and whoami.rs using sed
RUN cd /testbed && \
    sed -i 's/^use uucore::{localized_help_template, translate};$/\/\/ [ENV-PATCH] use uucore::{localized_help_template, translate};/' \
        crates/nu-command/src/filesystem/mktemp.rs \
        crates/nu-command/src/platform/whoami.rs || true

# Commit START state patches
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix API compatibility issues for START state" && \
    git tag -f milestone-milestone_M09_datetime-start HEAD

# Set default to START state
RUN cd /testbed && git checkout milestone-milestone_M09_datetime-start
