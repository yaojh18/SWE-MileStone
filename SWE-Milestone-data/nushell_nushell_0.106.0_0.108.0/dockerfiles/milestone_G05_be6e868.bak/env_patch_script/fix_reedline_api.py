#!/usr/bin/env python3
"""
Fix reedline API compatibility issues by removing TraversalDirection and with_immediately_accept usage.
This script properly handles the multi-line code blocks that sed struggles with.
"""

import re
import sys
from pathlib import Path

def fix_reedline_config(file_path):
    """Remove TraversalDirection import and tab_traversal match expression."""
    with open(file_path, 'r') as f:
        content = f.read()

    # Remove TraversalDirection from imports
    content = re.sub(
        r',\s*TraversalDirection\s*,',
        ',',
        content
    )
    content = re.sub(
        r'TraversalDirection\s*,',
        '',
        content
    )

    # Remove the entire tab_traversal match block (lines 292-306)
    # Pattern: from "columnar_menu = match extract_value("tab_traversal"..."
    # to the matching closing brace with Err(_) => columnar_menu,
    pattern = r'''columnar_menu\s*=\s*match\s+extract_value\("tab_traversal",.*?\{\s*
\s*Ok\(tab_traversal\)\s*=>\s*match\s+tab_traversal\.coerce_str\(\)\?\.as_ref\(\)\s*\{.*?
\s*\},\s*
\s*Err\(_\)\s*=>\s*columnar_menu,\s*
\s*\};'''

    content = re.sub(pattern, '', content, flags=re.DOTALL)

    with open(file_path, 'w') as f:
        f.write(content)

    return True

def fix_repl(file_path):
    """Remove with_immediately_accept method calls."""
    with open(file_path, 'r') as f:
        lines = f.readlines()

    new_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Check if this line contains .with_immediately_accept
        if '.with_immediately_accept(' in line:
            # Check if the previous non-empty line needs a semicolon
            if new_lines:
                # Find the last non-empty, non-comment line
                for j in range(len(new_lines) - 1, -1, -1):
                    prev_line = new_lines[j].rstrip()
                    if prev_line and not prev_line.strip().startswith('//'):
                        # If it ends with ) but not with ; or }, add a semicolon
                        if prev_line.endswith(')') and not prev_line.endswith(';') and not prev_line.endswith('}'):
                            new_lines[j] = prev_line + ';\n'
                        break
            i += 1
            continue

        new_lines.append(line)
        i += 1

    with open(file_path, 'w') as f:
        f.writelines(new_lines)

    return True

if __name__ == '__main__':
    testbed_path = Path('/testbed')

    print("Fixing reedline API compatibility...")

    # Fix reedline_config.rs
    reedline_config = testbed_path / 'crates/nu-cli/src/reedline_config.rs'
    if reedline_config.exists():
        fix_reedline_config(reedline_config)
        print(f"✓ Fixed {reedline_config}")

    # Fix repl.rs
    repl_file = testbed_path / 'crates/nu-cli/src/repl.rs'
    if repl_file.exists():
        fix_repl(repl_file)
        print(f"✓ Fixed {repl_file}")

    print("Done!")
