# Build on pre-configured base image for nushell
FROM nushell_nushell_0.106.0_0.108.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed with milestone tags
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Configure git identity for commits
RUN git config --global user.email "env-patch@test.local" && \
    git config --global user.name "ENV Patch"

# ==========================================
# PHASE 1: Apply patches to END state
# ==========================================
RUN cd /testbed && git checkout -- . && git checkout milestone-milestone_M07_refactor-end

# [ENV-PATCH] Downgrade Rust version requirements to match base image (1.86.0)
# The milestone testbed has been cherry-picked with commits that bumped rust-version to 1.88.0
# We need to downgrade to be compatible with the base image's Rust 1.86.0
RUN cd /testbed && \
    sed -i 's/rust-version = "1.88.0"/rust-version = "1.86.0"/g' Cargo.toml && \
    sed -i 's/edition = "2024"/edition = "2021"/g' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Downgrade Rust version from 1.88.0 to 1.86.0 for base image compatibility"

# [ENV-PATCH] Comment out nu-mcp from workspace and dependencies - crate directory doesn't exist
RUN cd /testbed && \
    sed -i 's|"crates/nu-mcp",|# [ENV-PATCH] "crates/nu-mcp", # crate not included in milestone|' Cargo.toml && \
    sed -i 's|nu-mcp = { path = "./crates/nu-mcp"|# [ENV-PATCH] nu-mcp = { path = "./crates/nu-mcp"|' Cargo.toml && \
    sed -i 's|"dep:nu-mcp",|# [ENV-PATCH] "dep:nu-mcp", # disabled - crate not included|' Cargo.toml && \
    sed -i 's|"nu-mcp/mcp",|# [ENV-PATCH] "nu-mcp/mcp", # disabled - crate not included|' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out nu-mcp from workspace - crate not included in milestone"

# [ENV-PATCH] Add missing NuCow type definition - required by cherry-picked commits
RUN cd /testbed && \
    cat >> crates/nu-utils/src/lib.rs << 'NUCOW_DEF'

// [ENV-PATCH] NuCow type - missing from cherry-picked commits
/// A copy-on-write type with separate borrowed and owned type parameters.
/// Used for compile-time static data that can be converted to owned at runtime.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NuCow<B, O> {
    Borrowed(B),
    Owned(O),
}

impl<B, O> serde::Serialize for NuCow<B, O>
where
    B: serde::Serialize,
    O: serde::Serialize,
{
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        match self {
            NuCow::Borrowed(b) => b.serialize(serializer),
            NuCow::Owned(o) => o.serialize(serializer),
        }
    }
}

impl<'de, B, O> serde::Deserialize<'de> for NuCow<B, O>
where
    O: serde::Deserialize<'de>,
{
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        O::deserialize(deserializer).map(NuCow::Owned)
    }
}
NUCOW_DEF
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing NuCow type definition"

# [ENV-PATCH] Add missing pipeline function - removed by f21fd3b testing refactor but still used by some tests
RUN cd /testbed && \
    cat >> crates/nu-test-support/src/lib.rs << 'PIPELINE_DEF'

// [ENV-PATCH] pipeline function - was removed by testing refactor but some tests still use it
/// Reformat a multiline pipeline into a single line for use with `nu -c`
///
/// Warning: Will not correctly handle statements that are not `;` separated!
pub fn pipeline(commands: &str) -> String {
    commands
        .trim()
        .lines()
        .map(|line| line.trim())
        .collect::<Vec<&str>>()
        .join(" ")
        .trim_end()
        .to_string()
}
PIPELINE_DEF
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing pipeline function - needed by tests not updated in refactor"

# [ENV-PATCH] Add pipeline import to test files that need it
RUN cd /testbed && \
    sed -i 's/use nu_test_support::nu;/use nu_test_support::{nu, pipeline};/' crates/nu-command/tests/commands/network/http/get.rs && \
    sed -i 's/use nu_test_support::nu;/use nu_test_support::{nu, pipeline};/' crates/nu-command/tests/commands/debug/metadata_set.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add pipeline import to test files that need it"

# [ENV-PATCH] Fix Value non-exhaustive pattern matches - need to add '..' to struct patterns
RUN cd /testbed && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/abs.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/ceil.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/floor.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/log.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/round.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/sqrt.rs && \
    sed -i '/Value::Int {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/generators/seq_date.rs && \
    sed -i '/Value::Duration {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/generators/seq_date.rs && \
    sed -i '/Value::Custom {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/filesystem/save.rs && \
    sed -i '/Value::Record {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-cmd-extra/src/extra/filters/update_cells.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/command/datetime/as_datetime.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/command/datetime/replace_time_zone.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/values/nu_dtype/mod.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix Value non-exhaustive pattern matches"

# [ENV-PATCH] Fix rusqlite::DatabaseName - use string literal "main" instead of enum
RUN cd /testbed && \
    sed -i 's/Connection, DatabaseName, Error/Connection, Error/' crates/nu-command/src/database/values/sqlite.rs && \
    sed -i 's/DatabaseName::Main/"main"/' crates/nu-command/src/database/values/sqlite.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Replace DatabaseName::Main with string literal"

# [ENV-PATCH] Fix immutable argument assignment in completer.rs
RUN cd /testbed && \
    sed -i 's/need_fallback: bool/mut need_fallback: bool/' crates/nu-cli/src/completions/completer.rs && \
    sed -i 's/\*need_fallback = false;/need_fallback = false;/' crates/nu-cli/src/completions/completer.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix immutable argument in completer.rs"

# [ENV-PATCH] Comment out tests using ConfigFileKind which doesn't exist in this milestone
RUN cd /testbed && \
    sed -i 's/^use nu_test_support/\/\/ [ENV-PATCH] use nu_test_support/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^#\[test\]/\/\/ [ENV-PATCH] #[test]/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^fn print_config_env_default/\/\/ [ENV-PATCH] fn print_config_env_default/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    let actual/\/\/ [ENV-PATCH]     let actual/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    assert/\/\/ [ENV-PATCH]     assert/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^        actual/\/\/ [ENV-PATCH]         actual/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^        nu_utils/\/\/ [ENV-PATCH]         nu_utils/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^            \./\/\/ [ENV-PATCH]             ./' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    );/\/\/ [ENV-PATCH]     );/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^}/\/\/ [ENV-PATCH] }/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^use nu_test_support/\/\/ [ENV-PATCH] use nu_test_support/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^#\[test\]/\/\/ [ENV-PATCH] #[test]/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^fn print_config_nu_default/\/\/ [ENV-PATCH] fn print_config_nu_default/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    let actual/\/\/ [ENV-PATCH]     let actual/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    assert/\/\/ [ENV-PATCH]     assert/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^        actual/\/\/ [ENV-PATCH]         actual/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^        nu_utils/\/\/ [ENV-PATCH]         nu_utils/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^            \./\/\/ [ENV-PATCH]             ./' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    );/\/\/ [ENV-PATCH]     );/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^}/\/\/ [ENV-PATCH] }/' crates/nu-command/tests/commands/config_nu_default.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests using ConfigFileKind (not in milestone)"

# [ENV-PATCH] Comment out tests using ParseError::InvalidBinaryString which doesn't exist
RUN cd /testbed && \
    sed -i '497,537 s/^/\/\/ [ENV-PATCH] /' crates/nu-parser/tests/test_parser.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests using ParseError::InvalidBinaryString" && \
    git tag -f milestone-milestone_M07_refactor-end HEAD

# ==========================================
# PHASE 2: Apply patches to START state
# ==========================================
RUN cd /testbed && git checkout milestone-milestone_M07_refactor-start

# [ENV-PATCH] Downgrade Rust version requirements to match base image (1.86.0)
# Same as END state - need to match base image's Rust 1.86.0
RUN cd /testbed && \
    sed -i 's/rust-version = "1.88.0"/rust-version = "1.86.0"/g' Cargo.toml && \
    sed -i 's/edition = "2024"/edition = "2021"/g' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Downgrade Rust version from 1.88.0 to 1.86.0 for base image compatibility"

# [ENV-PATCH] Comment out nu-mcp from workspace and dependencies - crate directory doesn't exist
RUN cd /testbed && \
    sed -i 's|"crates/nu-mcp",|# [ENV-PATCH] "crates/nu-mcp", # crate not included in milestone|' Cargo.toml && \
    sed -i 's|nu-mcp = { path = "./crates/nu-mcp"|# [ENV-PATCH] nu-mcp = { path = "./crates/nu-mcp"|' Cargo.toml && \
    sed -i 's|"dep:nu-mcp",|# [ENV-PATCH] "dep:nu-mcp", # disabled - crate not included|' Cargo.toml && \
    sed -i 's|"nu-mcp/mcp",|# [ENV-PATCH] "nu-mcp/mcp", # disabled - crate not included|' Cargo.toml && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out nu-mcp from workspace - crate not included in milestone"

# [ENV-PATCH] Add missing NuCow type definition - same as END state
RUN cd /testbed && \
    cat >> crates/nu-utils/src/lib.rs << 'NUCOW_DEF'

// [ENV-PATCH] NuCow type - missing from cherry-picked commits
/// A copy-on-write type with separate borrowed and owned type parameters.
/// Used for compile-time static data that can be converted to owned at runtime.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NuCow<B, O> {
    Borrowed(B),
    Owned(O),
}

impl<B, O> serde::Serialize for NuCow<B, O>
where
    B: serde::Serialize,
    O: serde::Serialize,
{
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        match self {
            NuCow::Borrowed(b) => b.serialize(serializer),
            NuCow::Owned(o) => o.serialize(serializer),
        }
    }
}

impl<'de, B, O> serde::Deserialize<'de> for NuCow<B, O>
where
    O: serde::Deserialize<'de>,
{
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        O::deserialize(deserializer).map(NuCow::Owned)
    }
}
NUCOW_DEF
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing NuCow type definition"

# NOTE: START state already has `pipeline` function - DO NOT add it here
# But test files need to import it explicitly (original bug in milestone state)
# [ENV-PATCH] Add pipeline import to test files that need it - same as END state
RUN cd /testbed && \
    sed -i 's/use nu_test_support::nu;/use nu_test_support::{nu, pipeline};/' crates/nu-command/tests/commands/network/http/get.rs && \
    sed -i 's/use nu_test_support::nu;/use nu_test_support::{nu, pipeline};/' crates/nu-command/tests/commands/debug/metadata_set.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add pipeline import to test files"

# [ENV-PATCH] Fix Value non-exhaustive pattern matches - same as END state
RUN cd /testbed && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/abs.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/ceil.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/floor.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/log.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/round.rs && \
    sed -i '/Value::Range {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-command/src/math/sqrt.rs && \
    sed -i '/Value::Int {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/generators/seq_date.rs && \
    sed -i '/Value::Duration {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/generators/seq_date.rs && \
    sed -i '/Value::Custom {/,/}/ s/internal_span }/internal_span, .. }/' crates/nu-command/src/filesystem/save.rs && \
    sed -i '/Value::Record {/,/}/ s/internal_span,$/internal_span, ../' crates/nu-cmd-extra/src/extra/filters/update_cells.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/command/datetime/as_datetime.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/command/datetime/replace_time_zone.rs && \
    sed -i 's/Value::String { val, internal_span }/Value::String { val, internal_span, .. }/g' crates/nu_plugin_polars/src/dataframe/values/nu_dtype/mod.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix Value non-exhaustive pattern matches"

# [ENV-PATCH] Fix rusqlite::DatabaseName - use string literal "main" instead of enum
RUN cd /testbed && \
    sed -i 's/Connection, DatabaseName, Error/Connection, Error/' crates/nu-command/src/database/values/sqlite.rs && \
    sed -i 's/DatabaseName::Main/"main"/' crates/nu-command/src/database/values/sqlite.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Replace DatabaseName::Main with string literal"

# [ENV-PATCH] Fix immutable argument assignment in completer.rs
RUN cd /testbed && \
    sed -i 's/need_fallback: bool/mut need_fallback: bool/' crates/nu-cli/src/completions/completer.rs && \
    sed -i 's/\*need_fallback = false;/need_fallback = false;/' crates/nu-cli/src/completions/completer.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix immutable argument in completer.rs"

# [ENV-PATCH] Comment out tests using ConfigFileKind - same as END state
RUN cd /testbed && \
    sed -i 's/^use nu_test_support/\/\/ [ENV-PATCH] use nu_test_support/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^#\[test\]/\/\/ [ENV-PATCH] #[test]/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^fn print_config_env_default/\/\/ [ENV-PATCH] fn print_config_env_default/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    let actual/\/\/ [ENV-PATCH]     let actual/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    assert/\/\/ [ENV-PATCH]     assert/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^        actual/\/\/ [ENV-PATCH]         actual/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^        nu_utils/\/\/ [ENV-PATCH]         nu_utils/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^            \./\/\/ [ENV-PATCH]             ./' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^    );/\/\/ [ENV-PATCH]     );/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^}/\/\/ [ENV-PATCH] }/' crates/nu-command/tests/commands/config_env_default.rs && \
    sed -i 's/^use nu_test_support/\/\/ [ENV-PATCH] use nu_test_support/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^#\[test\]/\/\/ [ENV-PATCH] #[test]/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^fn print_config_nu_default/\/\/ [ENV-PATCH] fn print_config_nu_default/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    let actual/\/\/ [ENV-PATCH]     let actual/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    assert/\/\/ [ENV-PATCH]     assert/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^        actual/\/\/ [ENV-PATCH]         actual/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^        nu_utils/\/\/ [ENV-PATCH]         nu_utils/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^            \./\/\/ [ENV-PATCH]             ./' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^    );/\/\/ [ENV-PATCH]     );/' crates/nu-command/tests/commands/config_nu_default.rs && \
    sed -i 's/^}/\/\/ [ENV-PATCH] }/' crates/nu-command/tests/commands/config_nu_default.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests using ConfigFileKind"

# [ENV-PATCH] Comment out tests using ParseError::InvalidBinaryString which doesn't exist
RUN cd /testbed && \
    sed -i '497,537 s/^/\/\/ [ENV-PATCH] /' crates/nu-parser/tests/test_parser.rs && \
    git add -A && \
    git commit -m "[ENV-PATCH] Comment out tests using ParseError::InvalidBinaryString" && \
    git tag -f milestone-milestone_M07_refactor-start HEAD

# ==========================================
# PHASE 3: Verify compilation
# ==========================================
# Verify END state compiles
RUN cd /testbed && git checkout milestone-milestone_M07_refactor-end && \
    cargo test --no-run --profile ci --workspace --exclude nu_plugin_* -j 4

# Default state is START
RUN cd /testbed && git checkout milestone-milestone_M07_refactor-start
