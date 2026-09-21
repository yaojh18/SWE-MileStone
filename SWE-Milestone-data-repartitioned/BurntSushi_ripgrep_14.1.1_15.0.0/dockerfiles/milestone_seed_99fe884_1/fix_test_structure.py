#!/usr/bin/env python3
"""
Fix test structure to match expected test ID.
The symbol extractor expects flags::defs::tests::test_colors, but the test is
currently at flags::defs::test_colors (not in a mod tests block).
This script wraps the test in a mod tests block.
"""

import sys
import re

def fix_test_structure(file_path):
    with open(file_path, 'r') as f:
        content = f.read()

    # Find the test_colors function with #[cfg(test)] annotation
    # Pattern: #[cfg(test)]\n#[test]\nfn test_colors() { ... }
    pattern = r'(#\[cfg\(test\)\]\s*\n#\[test\]\s*\nfn test_colors\(\)[^}]*\}(?:\s*\n\s*\})*)'

    def wrap_in_mod(match):
        test_code = match.group(1)
        # Remove the #[cfg(test)] from the function (it will be on the mod)
        test_code = test_code.replace('#[cfg(test)]\n', '', 1)
        return f'#[cfg(test)]\nmod tests {{\n    use super::*;\n\n    {test_code}\n}}'

    # Apply the transformation
    new_content = re.sub(pattern, wrap_in_mod, content, flags=re.DOTALL)

    # Write back
    with open(file_path, 'w') as f:
        f.write(new_content)

    print(f"Fixed test structure in {file_path}")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: fix_test_structure.py <file_path>")
        sys.exit(1)

    fix_test_structure(sys.argv[1])
