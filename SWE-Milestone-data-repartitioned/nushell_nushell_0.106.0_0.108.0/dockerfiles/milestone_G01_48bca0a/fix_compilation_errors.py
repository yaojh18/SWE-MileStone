#!/usr/bin/env python3
"""Fix compilation errors caused by cross-milestone patches (core_dev.2 + core_dev.3).

Fixes:
1. E0638: Add `..` to Value variant pattern matches (non_exhaustive)
2. E0432/E0599: Remove references to missing reedline APIs
3. E0658: Rewrite let-chains to nested if-let (Rust 1.87+ → 1.86.0)
"""
import re
import sys
import os

def fix_file(filepath, fixers):
    """Apply a list of fixer functions to a file."""
    if not os.path.exists(filepath):
        return
    with open(filepath, 'r') as f:
        content = f.read()
    original = content
    for fixer in fixers:
        content = fixer(content)
    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"  Fixed: {filepath}")

def add_non_exhaustive_dots(content):
    """Add `..` to Value variant pattern matches that need it."""
    # Multi-line patterns like:
    #   Value::Range {
    #       ref val,
    #       internal_span,
    #   }
    # → add `..` before closing }
    content = re.sub(
        r'(Value::\w+\s*\{[^}]*?internal_span\s*,)\s*\n(\s*)\}',
        r'\1\n\2    ..\n\2}',
        content
    )
    # Single-line patterns like: Value::Int { val, internal_span }
    content = re.sub(
        r'(Value::\w+\s*\{[^}]*?internal_span)\s*\}',
        r'\1, .. }',
        content
    )
    return content

def fix_reedline_traversal(content):
    """Remove TraversalDirection import and with_traversal_direction calls."""
    # Remove TraversalDirection from import line
    content = re.sub(r',\s*TraversalDirection', '', content)
    content = re.sub(r'TraversalDirection\s*,\s*', '', content)

    # Replace with_traversal_direction calls:
    # "vertical" => columnar_menu.with_traversal_direction(TraversalDirection::Vertical),
    # _ => columnar_menu.with_traversal_direction(TraversalDirection::Horizontal)
    # → just return columnar_menu
    content = re.sub(
        r'"vertical"\s*=>\s*(\w+)\.with_traversal_direction\([^)]*\)',
        r'"vertical" => \1',
        content
    )
    content = re.sub(
        r'(\w+)\.with_traversal_direction\([^)]*\)',
        r'\1',
        content
    )
    return content

def fix_reedline_immediately_accept(content):
    """Remove .with_immediately_accept(...) method calls."""
    # Case 1: chained method call with preceding comment and trailing semicolon
    # e.g.: // Ensure immediately accept is always cleared\n        .with_immediately_accept(false);
    # → replace with just ";" to terminate the previous method chain
    content = re.sub(
        r'\n\s*// Ensure immediately accept is always cleared\n\s*\.with_immediately_accept\([^)]*\);',
        ';',
        content
    )
    # Case 2: standalone assignment like: line_editor = line_editor.with_immediately_accept(true)
    # → remove the entire line
    content = re.sub(
        r'\n\s*\w+\s*=\s*\w+\.with_immediately_accept\([^)]*\)\s*',
        '\n',
        content
    )
    # Case 3: any remaining .with_immediately_accept(...) calls
    content = re.sub(
        r'\.with_immediately_accept\([^)]*\)',
        '',
        content
    )
    return content

def fix_missing_struct_fields(content):
    """Add missing struct fields introduced by patches."""
    # Fix into::binary::Arguments - add little_endian field after compact
    # Use regex to handle any indentation; idempotent via content check
    if 'little_endian: false,' not in content:
        content = re.sub(
            r'(compact:\s*(true|false),)\s*$',
            r'\1 little_endian: false,',
            content,
            flags=re.MULTILINE
        )
    # Fix str_::length::Arguments - add chars field after graphemes
    if 'chars: false,' not in content:
        content = re.sub(
            r'(graphemes:\s*(true|false),)\s*$',
            r'\1 chars: false,',
            content,
            flags=re.MULTILINE
        )
    return content

def fix_md_table_args(content):
    """Fix table() and fragment() calls in md.rs tests to add missing bool args."""
    # table() and fragment() now take 6 args instead of 4
    # Add false, false for escape_md, escape_html before &Config::default()

    # Single-line: &None, &Config::default() → &None, false, false, &Config::default()
    # [^\S\n] matches whitespace except newline (avoids cross-line matches)
    content = re.sub(
        r'(&None,[^\S\n]*)&Config::default\(\)',
        r'\1false, false, &Config::default()',
        content
    )
    content = re.sub(
        r'(&center,[^\S\n]*)&Config::default\(\)',
        r'\1false, false, &Config::default()',
        content
    )

    # Multi-line: &Config::default() on its own line
    # Insert false, false, before it (matching same indentation)
    lines = content.split('\n')
    new_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped in ('&Config::default()', '&Config::default(),'):
            # Only add if previous line doesn't already end with 'false,'
            if i > 0 and not lines[i-1].rstrip().endswith('false,'):
                indent = line[:len(line) - len(line.lstrip())]
                new_lines.append(f"{indent}false,")
                new_lines.append(f"{indent}false,")
        new_lines.append(line)
    content = '\n'.join(new_lines)

    return content

def fix_let_chains(content):
    """Rewrite if let ... && ... && let ... to nested if-let blocks."""
    old = """            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape
                && keyword == b"catch"
                && let [nu_protocol::CompileError::NotInALoop { .. }] =
                    &working_set.compile_errors[compile_error_count..]
            {
                working_set.compile_errors.truncate(compile_error_count);
            }"""
    new = """            if let SyntaxShape::Keyword(ref keyword, ..) = positional.shape {
                if keyword == b"catch" {
                    if let [nu_protocol::CompileError::NotInALoop { .. }] =
                        &working_set.compile_errors[compile_error_count..]
                    {
                        working_set.compile_errors.truncate(compile_error_count);
                    }
                }
            }"""
    return content.replace(old, new)

def fix_get_engine_state_rename(content):
    """Fix renamed get_engine_state -> add_command_context in test code."""
    content = content.replace(
        'get_engine_state()',
        'add_command_context(EngineState::new())'
    )
    return content

def fix_sqlite_feature_gate(content):
    """Remove #[cfg(feature = "sqlite")] from enum variants.

    The core_dev.3 patch adds this cfg gate but the sqlite feature isn't
    propagated to nu-protocol, making the Sqlite variant always absent.
    """
    content = re.sub(
        r'\n\s*#\[cfg\(feature\s*=\s*"sqlite"\)\]',
        '',
        content
    )
    return content

def fix_non_exhaustive_value_creation(content):
    """Replace Value struct expressions with constructor calls.

    Value variants are #[non_exhaustive] after core_dev.3 patch, so
    struct expressions like Value::String { val, internal_span, .. } fail.
    Use Value::string(val, span) and Value::list(vals, span) constructors instead.
    """
    # Replace multi-line Value::String { val: X, internal_span, .. } → Value::string(X, internal_span)
    content = re.sub(
        r'Value::String\s*\{\s*\n\s*val:\s*(\w+),\s*\n\s*internal_span,\s*\n\s*\.\.\s*\n\s*\}',
        r'Value::string(\1, internal_span)',
        content
    )
    # Replace multi-line Value::List { vals: VEC, internal_span, } → Value::list(VEC, internal_span)
    # Use a non-greedy match for the vec expression
    content = re.sub(
        r'Value::List\s*\{[^}]*?vals:\s*(vec!\[[^\]]*\]),\s*\n\s*internal_span,\s*\n\s*\}',
        r'Value::list(\1, internal_span)',
        content
    )
    return content

def main():
    testbed = sys.argv[1] if len(sys.argv) > 1 else '/testbed'
    print("Fixing compilation errors...")

    # Fix non_exhaustive errors across .rs files in crates/ (excluding nu-protocol
    # which defines Value and can construct variants without ..)
    import glob
    for filepath in glob.glob(os.path.join(testbed, 'crates/**/*.rs'), recursive=True):
        # Skip nu-protocol crate (same crate as Value definition, doesn't need ..)
        if '/nu-protocol/' in filepath:
            continue
        fix_file(filepath, [add_non_exhaustive_dots])

    # Fix reedline API errors
    fix_file(os.path.join(testbed, 'crates/nu-cli/src/reedline_config.rs'),
             [fix_reedline_traversal])
    fix_file(os.path.join(testbed, 'crates/nu-cli/src/repl.rs'),
             [fix_reedline_immediately_accept])

    # Fix let-chains in parser.rs
    fix_file(os.path.join(testbed, 'crates/nu-parser/src/parser.rs'),
             [fix_let_chains])

    # Fix missing struct fields in test code
    fix_file(os.path.join(testbed, 'crates/nu-command/src/conversions/into/binary.rs'),
             [fix_missing_struct_fields])
    fix_file(os.path.join(testbed, 'crates/nu-command/src/strings/str_/length.rs'),
             [fix_missing_struct_fields])

    # Fix table()/fragment() calls in md.rs tests (needs 2 extra bool args)
    fix_file(os.path.join(testbed, 'crates/nu-command/src/formats/to/md.rs'),
             [fix_md_table_args])

    # Fix get_engine_state -> add_command_context rename in command_context.rs tests
    fix_file(os.path.join(testbed, 'src/command_context.rs'),
             [fix_get_engine_state_rename])

    # Fix sqlite feature propagation: add nu-protocol/sqlite to root sqlite feature
    # (done via sed in Dockerfile instead, since it's a Cargo.toml change)

    # Fix non-exhaustive Value struct creation in test helpers
    fix_file(os.path.join(testbed, 'crates/nu-cli/tests/completions/support/completions_helpers.rs'),
             [fix_non_exhaustive_value_creation])

    print("Done fixing compilation errors.")

if __name__ == '__main__':
    main()
