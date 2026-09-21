# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Upgrade Rust toolchain to meet rust-version requirement (1.88)
RUN rustup install 1.88.0 && rustup default 1.88.0

# Configure git for commits
RUN git config --global user.email "env-patch@test.local" && \
    git config --global user.name "ENV-PATCH"

# Fix test structure: Move test_colors into the existing mod tests block
# The symbol extractor expects flags::defs::tests::test_colors, but the test is
# currently at flags::defs::test_colors (not in a mod tests block)
# Solution: Move the test into the existing mod tests block

# Create a Perl script to move the test
RUN cat > /tmp/move_test.pl << 'PERL_SCRIPT'
#!/usr/bin/perl
use strict;
use warnings;

my $file_path = $ARGV[0];

# Read the file
open(my $fh, '<', $file_path) or die "Cannot open $file_path: $!";
my $content = do { local $/; <$fh> };
close($fh);

# Find and extract the test_colors function with its attributes
# Pattern: #[cfg(test)] followed by #[test] followed by fn test_colors() { ... }
if ($content =~ m/(#\[cfg\(test\)\]\s*#\[test\]\s*fn test_colors\(\) \{.*?\n\})\s*\n/s) {
    my $test_func = $1;

    # Remove #[cfg(test)] since it will be inside mod tests (which already has #[cfg(test)])
    $test_func =~ s/#\[cfg\(test\)\]\s*//;

    # Remove the function from its current location
    $content =~ s/#\[cfg\(test\)\]\s*#\[test\]\s*fn test_colors\(\) \{.*?\n\}\s*\n//s;

    # Find the mod tests block and insert after "use super::*;"
    # Indent the test function (4 spaces for module content)
    my @lines = split(/\n/, $test_func);
    my $indented_test = join("\n", map { $_ ? "    $_" : $_ } @lines);

    # Insert after "use super::*;"
    $content =~ s/(mod tests \{\s*use super::\*;)/$1\n\n$indented_test/;

    # Write back
    open(my $out_fh, '>', $file_path) or die "Cannot write $file_path: $!";
    print $out_fh $content;
    close($out_fh);

    print "Successfully moved test_colors\n";
    exit 0;
} else {
    print STDERR "test_colors function not found\n";
    exit 1;
}
PERL_SCRIPT

# Make the script executable
RUN chmod +x /tmp/move_test.pl

# Checkout to END state and apply fix
RUN cd /testbed && git checkout milestone-milestone_seed_99fe884_1-end && \
    perl /tmp/move_test.pl crates/core/flags/defs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Move test_colors into mod tests block to match expected test ID" && \
    git tag -f milestone-milestone_seed_99fe884_1-end HEAD

# Checkout to START state and apply the same fix
RUN cd /testbed && git checkout milestone-milestone_seed_99fe884_1-start && \
    if grep -q "fn test_colors" crates/core/flags/defs.rs 2>/dev/null; then \
        perl /tmp/move_test.pl crates/core/flags/defs.rs && \
        git add -A && \
        git commit -m "[ENV-PATCH] Move test_colors into mod tests block to match expected test ID" && \
        git tag -f milestone-milestone_seed_99fe884_1-start HEAD; \
    fi

# Clean up
RUN rm -f /tmp/move_test.pl

# Set default git state to START
RUN cd /testbed && git checkout milestone-milestone_seed_99fe884_1-start
