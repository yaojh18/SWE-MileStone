#!/bin/bash
set -e

FILE="$1"

# Check if test_colors exists
if ! grep -q "fn test_colors" "$FILE"; then
    echo "test_colors not found in $FILE"
    exit 0
fi

# Create a temp file
TEMP=$(mktemp)

# Process the file
awk '
BEGIN { in_test = 0; found_cfg = 0; }

# Found #[cfg(test)] before test_colors
/#\[cfg\(test\)\]/ {
    getline next_line
    if (next_line ~ /^#\[test\]/) {
        getline next_next_line
        if (next_next_line ~ /^fn test_colors/) {
            # This is our target - wrap it in mod tests
            print "#[cfg(test)]"
            print "mod tests {"
            print "    use super::*;"
            print ""
            print "    #[test]"
            print "    fn test_colors() {"
            in_test = 1
            brace_count = 1
            next
        }
    }
    # Not our target, print as-is
    print
    print next_line
    print next_next_line
    next
}

# Inside test_colors function
in_test {
    # Add proper indentation
    line = "    " $0
    print line

    # Count braces
    for (i = 1; i <= length($0); i++) {
        c = substr($0, i, 1)
        if (c == "{") brace_count++
        if (c == "}") brace_count--
    }

    # End of function
    if (brace_count == 0) {
        print "}"
        in_test = 0
    }
    next
}

# Default: print line as-is
{ print }
' "$FILE" > "$TEMP"

# Replace original file
mv "$TEMP" "$FILE"

echo "Fixed test structure in $FILE"
