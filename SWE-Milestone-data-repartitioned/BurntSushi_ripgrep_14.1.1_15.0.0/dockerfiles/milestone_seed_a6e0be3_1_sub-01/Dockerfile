# Build on pre-configured base image
FROM burntsushi_ripgrep_14.1.1_15.0.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Configure git for commits
RUN git config --global user.email "env-patch@docker.build" && \
    git config --global user.name "ENV-PATCH"

# Upgrade Rust to support edition 2024 (requires Rust 1.85+)
RUN rustup update stable && rustup default stable

# Checkout to END state first (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-milestone_seed_a6e0be3_1_sub-01-end

# Re-fetch dependencies with new Rust version
RUN cd /testbed && cargo fetch

# Fix END state: Remove explicit ref keywords (Rust 1.92 compatibility)
RUN cd /testbed && \
    sed -i '791s/ref t1/t1/' crates/core/flags/hiargs.rs && \
    sed -i '791s/ref t2/t2/' crates/core/flags/hiargs.rs && \
    sed -i '1182s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1185s/ref def/def/' crates/core/flags/hiargs.rs && \
    sed -i '1188s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1191s/ref name/name/' crates/core/flags/hiargs.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Remove explicit ref keywords for Rust 1.92 compatibility" && \
    git tag -f milestone-milestone_seed_a6e0be3_1_sub-01-end HEAD

# Checkout to START state
RUN cd /testbed && git checkout milestone-milestone_seed_a6e0be3_1_sub-01-start

# Apply same ref keyword fixes to START state
RUN cd /testbed && \
    sed -i '791s/ref t1/t1/' crates/core/flags/hiargs.rs && \
    sed -i '791s/ref t2/t2/' crates/core/flags/hiargs.rs && \
    sed -i '1182s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1185s/ref def/def/' crates/core/flags/hiargs.rs && \
    sed -i '1188s/ref name/name/' crates/core/flags/hiargs.rs && \
    sed -i '1191s/ref name/name/' crates/core/flags/hiargs.rs

# Fix START state: Remove tests using max_matches API that doesn't exist
# Use Perl to comment out entire test functions properly
COPY <<'PATCHSCRIPT' /tmp/patch.pl
#!/usr/bin/perl
use strict;
use warnings;

my $file = 'crates/printer/src/standard.rs';
open(my $fh, '<', $file) or die "Cannot open $file: $!";
my @lines = <$fh>;
close($fh);

my @tests_to_remove = ('max_matches', 'max_matches_context', 'max_matches_context_invert', 
                        'max_matches_multi_line1', 'max_matches_multi_line2',
                        'max_matches_multi_line3', 'max_matches_multi_line4',
                        'regression_after_context_with_match');

my $in_test = 0;
my $brace_count = 0;
my $test_start = -1;

for (my $i = 0; $i < @lines; $i++) {
    if ($lines[$i] =~ /^\s*#\[test\]/ && $i + 1 < @lines) {
        # Check next line for function name
        foreach my $test_name (@tests_to_remove) {
            if ($lines[$i+1] =~ /fn\s+$test_name\s*\(\)/) {
                $in_test = 1;
                $test_start = $i;
                $brace_count = 0;
                last;
            }
        }
    }
    
    if ($in_test) {
        $brace_count += ($lines[$i] =~ tr/{//);
        $brace_count -= ($lines[$i] =~ tr/}//);
        
        # Comment out the line
        if ($lines[$i] !~ /^\s*\/\//) {
            my $indent = $lines[$i] =~ /^(\s*)/; 
            $lines[$i] = $1 . '// [ENV-PATCH] ' . substr($lines[$i], length($1));
        }
        
        # End of test function
        if ($brace_count == 0 && $i > $test_start) {
            $in_test = 0;
        }
    }
}

open($fh, '>', $file) or die "Cannot write $file: $!";
print $fh @lines;
close($fh);
PATCHSCRIPT

RUN cd /testbed && \
    perl /tmp/patch.pl && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix Rust 1.92 compat + comment out tests using max_matches API" && \
    git tag -f milestone-milestone_seed_a6e0be3_1_sub-01-start HEAD
