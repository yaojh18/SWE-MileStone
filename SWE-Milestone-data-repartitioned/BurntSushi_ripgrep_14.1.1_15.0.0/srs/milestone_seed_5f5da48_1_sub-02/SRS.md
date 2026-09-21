# Software Requirements Specification: Structured Glob Parsing and Compatibility Policy

## Overview

Make ripgrep's glob syntax structurally expressive while keeping malformed-pattern handling explicit and compatible with each caller. The parser must track nested brace-alternation scope, preserve useful unmatched-brace errors, and generate regular expressions with equivalent matching behavior. When a character class has no closing bracket, a builder policy decides whether `[` begins literal text or remains an error: gitignore parsing uses Git-compatible recovery, while command-line override globs stay strict by default. Existing well-formed patterns and the public API compatibility surface remain stable.

### Requirements Summary

1. **FR1**: End-to-End Nested Alternation Semantics
2. **FR2**: Maintain Proper Error Handling for Malformed Alternation Patterns
3. **FR3**: Configurable Recovery for Unclosed Character Classes
4. **FR4**: Caller-Specific Defaults for Gitignore and Override Globs

### Affected Modules

- crates/globset (parser, GlobBuilder, regex generation, and tests)
- crates/ignore (GitignoreBuilder defaults and forwarding API)
- crates/ignore/src/overrides.rs (OverrideBuilder defaults and forwarding API)

## Functional Requirements

### Functional Area: Nested syntax handling

Brace groups must retain their structure from parsing through regex generation and path matching.

### FR1: End-to-End Nested Alternation Semantics
Nested alternations form one end-to-end language feature: the parser must retain their structure, regex translation must preserve it, and the resulting matcher must accept and reject the intended paths.

**Problem**:
Glob patterns containing nested alternation groups such as `{a,{b,c}}`, `{{a,b},{c,d}}`, or `{a,b{c,d}}` fail to parse with a `NestedAlternates` error, preventing users from expressing complex file matching patterns.

Nested alternation groups must be compiled to regular expressions that correctly represent the alternation semantics, with each nested group producing a properly scoped non-capturing group in the regex.

Glob patterns with nested alternations must correctly match against file paths, expanding all possible combinations represented by the nested structure.

**User Report**:
```
When I try to use a glob pattern like `**/{node_modules/**/*/{ts,js},crates/**/*.{rs,toml}}`
to match files across different project types, the parser rejects it with a
"nested alternates" error. I need nested alternates to efficiently express
patterns that combine multiple complex path matching rules.
```

**Requirements**:
- The glob parser shall accept patterns containing alternation groups nested at any depth
- Patterns such as `{a,{b,c}}` (one level of nesting) shall be valid
- Patterns such as `{{a,b},{c,d}}` (two alternation groups at the same level, each nested) shall be valid
- Patterns such as `{a,b{c,d}}` (alternation nested within a branch of an outer alternation) shall be valid
- The parser shall correctly track the scope of each alternation group to associate branches with their containing group

- Each alternation group shall compile to a non-capturing regex group with pipe-separated alternatives
- Nested alternation groups shall produce nested non-capturing groups in the generated regex
- The order of alternatives in the generated regex shall preserve the order from the glob pattern
- Simple alternation `{a,b}` shall generate regex pattern `(?:a|b)`
- One-level nested alternation `{a,{b,c}}` shall generate regex pattern `(?:a|(?:b|c))`
- Multi-group nested alternation `{{a,b},{c,d}}` shall generate regex pattern `(?:(?:a|b)|(?:c|d))`

- A pattern with nested alternations shall match any path that satisfies any combination of the alternation branches
- Pattern `{a,b{c,d}}` shall match paths matching `a`, `bc`, or `bd`
- Nested alternations shall work correctly in combination with other glob features (wildcards, character classes, etc.)
- Matching behavior shall be consistent with the existing alternation semantics for flat alternation groups

**Acceptance**:
- When parsing pattern `{a,{b,c}}`, the parser produces a valid glob without error
- When parsing pattern `{{a,b},{c,d}}`, the parser produces a valid glob without error
- When parsing pattern `{a,b{c,d}}`, the parser produces a valid glob without error

- When pattern `{a,b}` is compiled, the resulting regex matches the form `^(?:a|b)$`
- When pattern `{a,{b,c}}` is compiled, the resulting regex matches the form `^(?:a|(?:b|c))$`
- When pattern `{{a,b},{c,d}}` is compiled, the resulting regex matches the form `^(?:(?:a|b)|(?:c|d))$`

- When pattern `{a,b{c,d}}` is matched against path `a`, the match succeeds
- When pattern `{a,b{c,d}}` is matched against path `bc`, the match succeeds
- When pattern `{a,b{c,d}}` is matched against path `bd`, the match succeeds
- When pattern `{a,b{c,d}}` is matched against path `b`, the match fails (incomplete match)
- When pattern `{a,b{c,d}}` is matched against path `be`, the match fails (incorrect alternative)

### FR2: Maintain Proper Error Handling for Malformed Alternation Patterns
**Problem**: While enabling nested alternations, the parser must continue to detect and report errors for syntactically invalid alternation patterns such as unmatched braces.

**Requirements**:
- The parser shall return an `UnclosedAlternates` error when a `{` is found without a matching `}`
- The parser shall return an `UnopenedAlternates` error when a `}` is found without a preceding unmatched `{`
- Error detection shall work correctly for nested patterns where only some braces are unmatched
- Commas outside of alternation groups shall be treated as literal characters, not branch separators

**Acceptance**:
- When parsing pattern `{a,{b,c}` (missing closing brace for outer group), an `UnclosedAlternates` error is returned
- When parsing pattern `a,b}` (closing brace without opening), an `UnopenedAlternates` error is returned
- When parsing pattern `{a,b}}` (extra closing brace), an `UnopenedAlternates` error is returned


### Functional Area: Context-aware malformed-class recovery

Unclosed character classes follow an explicit builder policy so each caller can choose compatibility recovery or strict diagnostics.

### FR3: Configurable Recovery for Unclosed Character Classes
The builder option and literal fallback are one parser contract: the selected policy must determine whether an unmatched `[` is recovered as literal text or reported as an error.

**Problem**:
The glob builder API lacks a configuration option to control how unclosed character classes are handled during pattern parsing.

When the unclosed class option is enabled, the parser must treat the opening bracket `[` as a literal character when no matching closing bracket `]` exists in the pattern.

**Requirements**:
- Provide a configuration method on the glob builder that accepts a boolean parameter to enable/disable unclosed class handling
- When enabled, unclosed character classes should be parsed without error
- When disabled, unclosed character classes should result in a parse error (existing behavior)
- The default value should be disabled to preserve strict parsing behavior for general glob usage
- The configuration method should support method chaining with other builder methods

- When unclosed-class recovery is enabled and no closing `]` exists, treat the unmatched opening `[` as a literal character, then resume parsing subsequent characters with the normal glob syntax
- The parser must correctly handle edge cases including:
  - Single opening bracket: `[`
  - Opening bracket followed by characters: `[abc`
  - Empty bracket sequences: `[]`
  - Multiple unclosed brackets: `[][`
  - Negated patterns without closing bracket: `[!`
  - Negated empty patterns: `[!]`
- When unclosed character classes appear within alternation groups (brace expressions), each branch should be evaluated independently for character class closure
- The parser should handle patterns with many opening brackets efficiently without performance degradation

**Acceptance**:
*Observable Behavior:*
- When unclosed class mode is enabled on a glob builder, patterns with unclosed character classes parse successfully
- When unclosed class mode is disabled (or default), patterns with unclosed character classes return an error
- The configuration can be combined with other glob options (case insensitivity, literal separator, etc.)

*API Contract (for test compatibility):*
- Unit tests expect method `allow_unclosed_class(bool)` returning `&mut Self` on `GlobBuilder`

*Observable Behavior:*
- When unclosed class mode is enabled:
  - Pattern `[` matches the literal string `[`
  - Pattern `[abc` matches the literal string `[abc`
  - Pattern `[]` matches the literal string `[]`
  - Pattern `[][` matches the literal string `[][`
  - Pattern `[!` matches the literal string `[!`
  - Pattern `[!]` matches the literal string `[!]`
  - Pattern `{[abc,xyz}` matches either literal `[abc` or literal `xyz`
  - Pattern `{[abc,[xyz}` matches either literal `[abc` or literal `[xyz`
  - Pattern `{[abc],[xyz}` matches either character class `[abc]` (valid closed class) or literal `[xyz` (unclosed treated as literal)

### FR4: Caller-Specific Defaults for Gitignore and Override Globs
Gitignore and override builders expose the same policy but deliberately choose different defaults to preserve their respective compatibility contracts.

**Problem**:
Git's gitignore implementation treats unclosed character classes as literal patterns. Users with gitignore files containing patterns like `[abc` (without closing bracket) find that ripgrep fails to parse these patterns, causing unexpected behavior where files that should be ignored are not ignored.

Override globs (used for command-line include/exclude patterns) should prioritize clear error messages over permissive parsing, since they are typically provided directly by users who can immediately correct mistakes.

**User Report**:
```
I have a .gitignore file with a pattern [abc which git treats as a literal
filename match. When I run ripgrep with --files, it fails to parse my
gitignore and shows files that should be hidden.
```

**Requirements**:
- The gitignore builder should enable unclosed class mode by default to match git's behavior
- Gitignore files containing patterns with unclosed character classes should parse without error
- The gitignore builder should expose a method to toggle this behavior for users who want stricter parsing

- Override globs should disable unclosed class mode by default
- Users providing malformed glob patterns via command-line flags should receive parse errors rather than silent misinterpretation
- The override builder should expose a method to enable unclosed class support for users who need it

**Acceptance**:
*Observable Behavior:*
- When a .gitignore file contains the pattern `[abc`, a file literally named `[abc` is correctly ignored
- When listing files with `--files` in a git repository containing a .gitignore with unclosed bracket patterns, the ignore rules are applied correctly
- Test `regression::r3127_gitignore_allow_unclosed_class` passes

*API Contract (for test compatibility):*
- Unit tests expect `GitignoreBuilder` to have `allow_unclosed_class(bool)` method returning `&mut Self`
- Default value for unclosed class mode in `GitignoreBuilder` should be `true`

*Observable Behavior:*
- When providing a glob pattern with unclosed character class via the `-g` or `--glob` flag, the command returns an error
- When a pattern like `[abc` is used as a command-line glob filter, the user receives a clear error message about the unclosed character class

*API Contract (for test compatibility):*
- Unit tests expect `OverrideBuilder` to have `allow_unclosed_class(bool)` method returning `&mut Self`
- `OverrideBuilder` should initialize with unclosed class mode disabled

## Compatibility Notes

- Retain the `NestedAlternates` error kind in the public API for backward compatibility and document it as obsolete now that valid nested groups no longer emit it
- Existing patterns without nested alternations shall continue to work identically
- The semantic meaning of commas, braces, and other alternation-related syntax remains unchanged outside of enabling nesting


## Notes

Unit tests in this milestone directly reference the `allow_unclosed_class` method name. The API contract sections above document these expectations to ensure test compatibility. The behavioral acceptance criteria describe what the system should do; the API contract sections describe how tests verify it.

## Verification Strategy

Exercise nested parsing, regex generation, and positive/negative path matches together with the exact `allow_unclosed_class(bool) -> &mut Self` API contracts and the different GitignoreBuilder/OverrideBuilder defaults.

# Environment Dependency Changes (relative to Base Env)

- Rust toolchain upgraded to 1.88.0.
