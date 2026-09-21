#!/usr/bin/env python3
"""Remove dubbo-spring6-security references from pom.xml files."""

import re
import sys

def remove_spring6_security_profile(content):
    """Remove the spring6-security profile block."""
    # Pattern to match the entire spring6-security profile block
    pattern = r'\s*<profile>\s*<id>spring6-security</id>.*?</profile>'
    return re.sub(pattern, '', content, flags=re.DOTALL)

def remove_spring6_security_dependency(content):
    """Remove dubbo-spring6-security dependency block."""
    # Pattern to match dependency block with dubbo-spring6-security
    pattern = r'\s*<dependency>\s*<groupId>org\.apache\.dubbo</groupId>\s*<artifactId>dubbo-spring6-security</artifactId>.*?</dependency>'
    return re.sub(pattern, '', content, flags=re.DOTALL)

def remove_spring6_security_include(content):
    """Remove dubbo-spring6-security include line."""
    pattern = r'\s*<include>org\.apache\.dubbo:dubbo-spring6-security</include>'
    return re.sub(pattern, '', content)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: fix_pom.py <pom_file>")
        sys.exit(1)

    pom_file = sys.argv[1]
    with open(pom_file, 'r') as f:
        content = f.read()

    content = remove_spring6_security_profile(content)
    content = remove_spring6_security_dependency(content)
    content = remove_spring6_security_include(content)

    with open(pom_file, 'w') as f:
        f.write(content)

    print(f"Fixed {pom_file}")
