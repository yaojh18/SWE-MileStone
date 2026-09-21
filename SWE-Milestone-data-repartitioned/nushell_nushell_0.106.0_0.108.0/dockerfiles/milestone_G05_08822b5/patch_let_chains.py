#!/usr/bin/env python3
"""
[ENV-PATCH] Patch deansi.rs to avoid let-chains syntax (Rust 1.88+ feature)
This rewrites functions using 'if cond && let Ok(x) = expr' to nested if statements
"""

import re

with open('crates/nu-utils/src/deansi.rs', 'r') as f:
    content = f.read()

# Patch strip_ansi_unlikely function - avoid let-chains
old_fn1 = '''pub fn strip_ansi_unlikely(string: &str) -> Cow<str> {
    // Check if any ascii control character except LF(0x0A = 10) is present,
    // which will be stripped. Includes the primary start of ANSI sequences ESC
    // (0x1B = decimal 27)
    if string.bytes().any(|x| matches!(x, 0..=9 | 11..=31))
        && let Ok(stripped) = String::from_utf8(strip_ansi_escapes::strip(string))
    {
        return Cow::Owned(stripped);
    }
    // Else case includes failures to parse!
    Cow::Borrowed(string)
}'''

new_fn1 = '''pub fn strip_ansi_unlikely(string: &str) -> Cow<str> {
    // Check if any ascii control character except LF(0x0A = 10) is present,
    // which will be stripped. Includes the primary start of ANSI sequences ESC
    // (0x1B = decimal 27)
    // [ENV-PATCH] Rewritten to avoid let-chains (Rust 1.88+ feature)
    if string.bytes().any(|x| matches!(x, 0..=9 | 11..=31)) {
        if let Ok(stripped) = String::from_utf8(strip_ansi_escapes::strip(string)) {
            return Cow::Owned(stripped);
        }
    }
    // Else case includes failures to parse!
    Cow::Borrowed(string)
}'''

content = content.replace(old_fn1, new_fn1)

# Patch strip_ansi_string_unlikely function
old_fn2 = '''pub fn strip_ansi_string_unlikely(string: String) -> String {
    // Check if any ascii control character except LF(0x0A = 10) is present,
    // which will be stripped. Includes the primary start of ANSI sequences ESC
    // (0x1B = decimal 27)
    if string
        .as_str()
        .bytes()
        .any(|x| matches!(x, 0..=8 | 11..=31))
        && let Ok(stripped) = String::from_utf8(strip_ansi_escapes::strip(&string))
    {
        return stripped;
    }
    // Else case includes failures to parse!
    string
}'''

new_fn2 = '''pub fn strip_ansi_string_unlikely(string: String) -> String {
    // Check if any ascii control character except LF(0x0A = 10) is present,
    // which will be stripped. Includes the primary start of ANSI sequences ESC
    // (0x1B = decimal 27)
    // [ENV-PATCH] Rewritten to avoid let-chains (Rust 1.88+ feature)
    if string.as_str().bytes().any(|x| matches!(x, 0..=8 | 11..=31)) {
        if let Ok(stripped) = String::from_utf8(strip_ansi_escapes::strip(&string)) {
            return stripped;
        }
    }
    // Else case includes failures to parse!
    string
}'''

content = content.replace(old_fn2, new_fn2)

with open('crates/nu-utils/src/deansi.rs', 'w') as f:
    f.write(content)

print('Patched deansi.rs successfully')
