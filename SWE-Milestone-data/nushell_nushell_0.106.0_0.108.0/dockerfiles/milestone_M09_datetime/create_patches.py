#!/usr/bin/env python3
"""
Create patched versions of M09_datetime milestone files to be compatible with base image API.

Changes:
1. datetime.rs: Remove Zone::OPTIONS and replace .param(Flag::new()) with .named()
2. format_date.rs: Replace PipelineData::value() with PipelineData::Value()
"""

import os
import sys

def patch_datetime_rs(input_path, output_path):
    """Patch datetime.rs to use old API."""
    with open(input_path, 'r') as f:
        lines = f.readlines()

    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip line 47 (const OPTIONS)
        if 'const OPTIONS:' in line:
            i += 1
            continue

        # Replace .param(Flag::new("timezone")...) block with .named()
        # Lines 97-106 in original
        if '.param(' in line and i + 1 < len(lines) and 'Flag::new("timezone")' in lines[i + 1]:
            # Skip the entire .param() block (lines 97-106, which is 10 lines)
            # And insert the .named() replacement
            new_lines.append('            .named(\n')
            new_lines.append('                "timezone",\n')
            new_lines.append('                SyntaxShape::String,\n')
            new_lines.append('                "Specify timezone if the input is a Unix timestamp. Valid options: \'UTC\' (\'u\') or \'LOCAL\' (\'l\')",\n')
            new_lines.append('                Some(\'z\'),\n')
            new_lines.append('            )\n')

            # Skip the original .param() block
            # Find the closing ) that matches
            depth = 0
            found_start = False
            while i < len(lines):
                for ch in lines[i]:
                    if ch == '(':
                        depth += 1
                        found_start = True
                    elif ch == ')':
                        depth -= 1

                i += 1
                if found_start and depth == 0:
                    break
            continue

        new_lines.append(line)
        i += 1

    with open(output_path, 'w') as f:
        f.writelines(new_lines)

    print(f"Patched datetime.rs: {output_path}")

def patch_format_date_rs(input_path, output_path):
    """Patch format_date.rs to use PipelineData::Value instead of PipelineData::value."""
    with open(input_path, 'r') as f:
        content = f.read()

    # Replace PipelineData::value( with PipelineData::Value(
    content = content.replace('PipelineData::value(', 'PipelineData::Value(')

    with open(output_path, 'w') as f:
        f.write(content)

    print(f"Patched format_date.rs: {output_path}")

if __name__ == "__main__":
    # Paths (will be run inside Docker, so use /tmp/patches)
    datetime_in = "/tmp/patches/datetime.rs"
    datetime_out = "/tmp/patches/datetime_patched.rs"

    format_date_in = "/tmp/patches/format_date.rs"
    format_date_out = "/tmp/patches/format_date_patched.rs"

    patch_datetime_rs(datetime_in, datetime_out)
    patch_format_date_rs(format_date_in, format_date_out)
