#!/usr/bin/env python3
"""
Wrap test_colors function in a mod tests block to match expected test ID.
The symbol extractor expects flags::defs::tests::test_colors, but the test is
currently at flags::defs::test_colors.
"""

import sys
import re

def wrap_test_in_mod(file_path):
    with open(file_path, 'r') as f:
        lines = f.readlines()

    # Find the test_colors function
    test_start_idx = None
    for i, line in enumerate(lines):
        if line.strip() == 'fn test_colors() {':
            # Check if previous lines are #[cfg(test)] and #[test]
            if i >= 2 and lines[i-1].strip() == '#[test]' and lines[i-2].strip() == '#[cfg(test)]':
                test_start_idx = i - 2  # Start at #[cfg(test)]
                break

    if test_start_idx is None:
        print(f"test_colors not found in {file_path}")
        return

    # Find the end of the test function (closing brace at column 0)
    fn_start_idx = test_start_idx + 2  # Line with fn test_colors()
    brace_count = 0
    test_end_idx = None
    for i in range(fn_start_idx, len(lines)):
        for char in lines[i]:
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    test_end_idx = i
                    break
        if test_end_idx is not None:
            break

    if test_end_idx is None:
        print(f"Could not find end of test_colors in {file_path}")
        return

    # Build the new content
    new_lines = []

    # Everything before the test
    new_lines.extend(lines[:test_start_idx])

    # The mod wrapper
    new_lines.append('#[cfg(test)]\n')
    new_lines.append('mod tests {\n')
    new_lines.append('    use super::*;\n')
    new_lines.append('\n')

    # The test function (skip #[cfg(test)], keep #[test] and fn)
    new_lines.append('    #[test]\n')
    new_lines.append('    fn test_colors() {\n')

    # Function body (add 4 spaces indentation)
    for i in range(fn_start_idx + 3, test_end_idx):
        new_lines.append('    ' + lines[i])

    # Closing braces
    new_lines.append('    }\n')
    new_lines.append('}\n')

    # Everything after the test
    new_lines.extend(lines[test_end_idx + 1:])

    # Write back
    with open(file_path, 'w') as f:
        f.writelines(new_lines)

    print(f"Wrapped test_colors in mod tests block in {file_path}")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: wrap_test.py <file_path>")
        sys.exit(1)

    wrap_test_in_mod(sys.argv[1])
