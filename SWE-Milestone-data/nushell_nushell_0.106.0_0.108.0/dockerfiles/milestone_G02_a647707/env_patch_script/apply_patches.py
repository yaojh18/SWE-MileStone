#!/usr/bin/env python3
"""Apply compilation patches for nushell milestone testing."""

import re
import os

def patch_update_cells():
    """Fix Value::Record pattern to add .. for non-exhaustive enum."""
    filepath = "/testbed/crates/nu-cmd-extra/src/extra/filters/update_cells.rs"
    if not os.path.exists(filepath):
        return

    with open(filepath, "r") as f:
        content = f.read()

    # Fix Value::Record pattern to add ..
    old_pattern = """Value::Record {
                    ref mut val,
                    internal_span,
                },"""
    new_pattern = """Value::Record {
                    ref mut val,
                    internal_span,
                    ..
                },"""

    content = content.replace(old_pattern, new_pattern)

    with open(filepath, "w") as f:
        f.write(content)

def patch_sqlite():
    """Fix DatabaseName import and backup/restore functions."""
    filepath = "/testbed/crates/nu-command/src/database/values/sqlite.rs"
    if not os.path.exists(filepath):
        return

    with open(filepath, "r") as f:
        content = f.read()

    # Remove DatabaseName from import
    content = content.replace("Connection, DatabaseName, Error", "Connection, Error")

    # Replace backup_database_to_file function
    old_backup = """pub fn backup_database_to_file(
        &self,
        conn: &Connection,
        filename: String,
    ) -> Result<(), SqliteError> {
        conn.backup(DatabaseName::Main, Path::new(&filename), None)?;
        Ok(())
    }"""

    new_backup = """pub fn backup_database_to_file(
        &self,
        _conn: &Connection,
        _filename: String,
    ) -> Result<(), SqliteError> {
        // [ENV-PATCH] Function body removed due to rusqlite API change
        Ok(())
    }"""

    content = content.replace(old_backup, new_backup)

    # Replace restore_database_from_file function
    # The original function looks like:
    # pub fn restore_database_from_file(
    #     &self,
    #     conn: &mut Connection,
    #     filename: String,
    # ) -> Result<(), SqliteError> {
    #     conn.restore(
    #         DatabaseName::Main,
    #         Path::new(&filename),
    #         Some(|p: rusqlite::backup::Progress| {
    #             let percent = if p.pagecount == 0 {
    #                 100
    #             } else {
    #                 (p.pagecount - p.remaining) * 100 / p.pagecount
    #             };
    #             if percent % 10 == 0 {
    #                 log::trace!("Restoring: {percent} %");
    #             }
    #         }),
    #     )?;
    #     Ok(())
    # }

    old_restore = """pub fn restore_database_from_file(
        &self,
        conn: &mut Connection,
        filename: String,
    ) -> Result<(), SqliteError> {
        conn.restore(
            DatabaseName::Main,
            Path::new(&filename),
            Some(|p: rusqlite::backup::Progress| {
                let percent = if p.pagecount == 0 {
                    100
                } else {
                    (p.pagecount - p.remaining) * 100 / p.pagecount
                };
                if percent % 10 == 0 {
                    log::trace!("Restoring: {percent} %");
                }
            }),
        )?;
        Ok(())
    }"""

    new_restore = """pub fn restore_database_from_file(
        &self,
        _conn: &mut Connection,
        _filename: String,
    ) -> Result<(), SqliteError> {
        // [ENV-PATCH] Function body removed due to rusqlite API change
        Ok(())
    }"""

    content = content.replace(old_restore, new_restore)

    with open(filepath, "w") as f:
        f.write(content)

def patch_test_parser():
    """Comment out tests that use ParseError::InvalidBinaryString which doesn't exist in START state."""
    filepath = "/testbed/crates/nu-parser/tests/test_parser.rs"
    if not os.path.exists(filepath):
        return

    with open(filepath, "r") as f:
        content = f.read()

    # Check if InvalidBinaryString is used
    if "InvalidBinaryString" not in content:
        return

    # Comment out the tests that use InvalidBinaryString
    tests_to_comment = [
        "parse_binary_with_invalid_octal_format",
        "parse_binary_with_multi_byte_char",
    ]

    for test_name in tests_to_comment:
        # Find the test function and comment it out
        pattern = rf'(#\[test\]\s*pub fn {test_name}\(\).*?)(\n#\[test\]|\nmod\s|\Z)'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            test_body = match.group(1)
            # Comment out each line
            commented_body = '\n'.join([f'// [ENV-PATCH] {line}' if line.strip() and not line.strip().startswith('//') else line
                                        for line in test_body.split('\n')])
            content = content.replace(test_body, commented_body)

    with open(filepath, "w") as f:
        f.write(content)
    print(f"Commented out tests using InvalidBinaryString in {filepath}")


def patch_config_tests():
    """Comment out tests that use ConfigFileKind which doesn't exist in START state."""
    test_files = [
        "/testbed/crates/nu-command/tests/commands/config_env_default.rs",
        "/testbed/crates/nu-command/tests/commands/config_nu_default.rs",
    ]

    for filepath in test_files:
        if not os.path.exists(filepath):
            continue

        with open(filepath, "r") as f:
            content = f.read()

        # Check if ConfigFileKind is used
        if "ConfigFileKind" not in content:
            continue

        # Comment out entire file content
        commented_content = '\n'.join([f'// [ENV-PATCH] {line}' if line.strip() and not line.strip().startswith('//') else line
                                       for line in content.split('\n')])

        with open(filepath, "w") as f:
            f.write(commented_content)
        print(f"Commented out ConfigFileKind tests in {filepath}")


def patch_value_patterns():
    """Fix non-exhaustive Value patterns across all rust files in relevant crates."""
    import glob

    # Find all .rs files in relevant directories
    patterns_to_fix = [
        # Pattern: Value::Range with only two fields
        (r'Value::Range\s*\{\s*ref\s+val,\s*internal_span,?\s*\}',
         'Value::Range { ref val, internal_span, .. }'),
        # Pattern: Value::Int with only two fields
        (r'Value::Int\s*\{\s*val,\s*internal_span,?\s*\}',
         'Value::Int { val, internal_span, .. }'),
        # Pattern: Value::Duration with only two fields
        (r'Value::Duration\s*\{\s*val,\s*internal_span,?\s*\}',
         'Value::Duration { val, internal_span, .. }'),
        # Pattern: Value::Custom with only two fields
        (r'Value::Custom\s*\{\s*val,\s*internal_span,?\s*\}',
         'Value::Custom { val, internal_span, .. }'),
        # Pattern: Value::Record with only two fields (in match arms)
        (r'Value::Record\s*\{\s*ref\s+mut\s+val,\s*internal_span,\s*\}',
         'Value::Record { ref mut val, internal_span, .. }'),
        # Pattern: Value::String with only two fields (various styles)
        (r'Value::String\s*\{\s*val,\s*internal_span,?\s*\}',
         'Value::String { val, internal_span, .. }'),
    ]

    # Search in multiple directories - include all crates
    for dirpath in ['/testbed/crates']:
        for rs_file in glob.glob(f'{dirpath}/**/*.rs', recursive=True):
            try:
                with open(rs_file, 'r') as f:
                    content = f.read()

                modified = False
                for pattern, replacement in patterns_to_fix:
                    new_content = re.sub(pattern, replacement, content)
                    if new_content != content:
                        content = new_content
                        modified = True

                if modified:
                    with open(rs_file, 'w') as f:
                        f.write(content)
                    print(f"Fixed patterns in: {rs_file}")
            except Exception as e:
                print(f"Error processing {rs_file}: {e}")


def patch_completions_helpers():
    """Fix non-exhaustive Value struct expressions in test helpers by using constructor methods."""
    filepath = "/testbed/crates/nu-cli/tests/completions/support/completions_helpers.rs"
    if not os.path.exists(filepath):
        return

    with open(filepath, "r") as f:
        content = f.read()

    # Replace the multiline struct literal with constructor method calls
    old_code = """Value::List {
            vals: vec![Value::String {
                val: dir_str,
                internal_span,
            }],
            internal_span,
        }"""

    new_code = """Value::list(
            vec![Value::string(dir_str, internal_span)],
            internal_span,
        )"""

    if old_code in content:
        content = content.replace(old_code, new_code)
        with open(filepath, "w") as f:
            f.write(content)
        print(f"Fixed non-exhaustive Value struct expression in {filepath}")


if __name__ == "__main__":
    patch_update_cells()
    patch_sqlite()
    patch_value_patterns()
    patch_test_parser()
    patch_config_tests()
    patch_completions_helpers()
    print("Patches applied successfully")
