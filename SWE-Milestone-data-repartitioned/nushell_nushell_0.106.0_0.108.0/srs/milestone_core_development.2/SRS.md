# Software Requirements Specification: Nushell Core Reliability and Observable Shell State

## Overview

Deliver a core-reliability release that makes language feedback earlier, interactive editing and completion more useful, filesystem behavior portable, command options more explicit, and live shell state easier to inspect. Invalid control flow, row conditions, malformed ranges, and non-UTF-8 units must fail safely before execution; string, search, and path commands gain precise operations; and overlays expose active state and scoped registration consistently. Existing valid behavior must remain stable, internal pipeline construction must be consistent, and platform-specific execution must behave predictably even when environment paths are absent.

### Requirements Summary

1. **FR1**: New String Comparison Operators
2. **FR2**: Parse-Time Validation of Control Flow and Row Conditions
3. **FR3**: Local Variable Completion Support
4. **FR4**: Immediate Command Execution via Commandline Edit
5. **FR5**: Add `--chars` Flag to `str length` Command
6. **FR6**: Case-Insensitive Filesystem Support for `path relative-to`
7. **FR7**: Complete and Deterministic Overlay State Listing
8. **FR8**: Fix Scoped Module Registration in `overlay use`
9. **NFR1**: Backward Compatibility
10. **NFR2**: Performance
11. **NFR3**: `PipelineData` Constructor Refactoring (Code Quality)
12. **FR9**: Safe Stepped-Range and Non-UTF-8 Parsing
13. **FR10**: Explicit `find` Case and Multiline Modes
14. **FR11**: Consistent Optional Lookup and External Resolution

### Affected Modules

- nu-parser, IR compilation, value operators, and type validation
- nu-engine and nu-cli completion, REPL, and PipelineData construction
- nu-command string, find, lookup, path, filesystem, commandline, and overlay behavior
- nu-path Windows device-path helpers and exports
- nu-protocol values, engine state, and overlay representation

## Functional Requirements

### Functional Area: Language semantics and early feedback

Operators and condition or control-flow validation must communicate intent precisely before an invalid pipeline reaches runtime.

### FR1: New String Comparison Operators
**Problem**: Users cannot easily filter strings that do NOT start or end with a specific substring using operators, requiring verbose negation logic.

**Requirements**:
- Add `not-starts-with` operator that returns true if a string does not start with another string
- Add `not-ends-with` operator that returns true if a string does not end with another string
- Both operators should work on string values only
- Operators should be available in completions when working with string types
- Operators should be documented in `help operators` output

**API Contracts**:
- Add `Comparison::NotStartsWith` and `Comparison::NotEndsWith` variants to the `Comparison` enum in `nu-protocol/src/ast/operator.rs`
- The `Comparison::as_str()` method must return `"not-starts-with"` and `"not-ends-with"` respectively
- Add `Value::not_starts_with()` and `Value::not_ends_with()` methods to `nu-protocol/src/value/mod.rs`
- Update operator completions to include both new operators for string-typed values

**Acceptance**:
- `not-starts-with` returns `true` when the string does NOT begin with the given prefix, `false` otherwise
- `not-ends-with` returns `true` when the string does NOT end with the given suffix, `false` otherwise
- Both operators work correctly with `where` for filtering lists of strings
- When typing a string followed by a space, the new operators appear in completion suggestions


### FR2: Parse-Time Validation of Control Flow and Row Conditions
Invalid loop control and incompatible row conditions are both structural language errors and must be rejected before execution begins.

**Problem**:
Using `break` or `continue` outside of a loop context results in a runtime error, making it difficult to catch mistakes early during development.

Using non-boolean expressions in row conditions (such as `where` clauses) results in confusing runtime behavior instead of a clear error message.

**User Report**:
```
Running `break` outside a loop produces an unclear runtime error.
It would be better to detect this at parse/compile time.
```

**Requirements**:
- Using `break` outside of a loop (such as `loop`, `while`, or `for`) should produce a compile-time error
- Using `continue` outside of a loop should produce a compile-time error
- The error message should clearly indicate that the statement can only be used inside a loop
- This validation should occur during IR compilation, not at runtime
- Using `break`/`continue` inside a `do` block that is not inside a loop should still produce an error

- Row condition expressions in commands like `where` must evaluate to a boolean type
- If the expression type is not compatible with boolean, a parse-time type mismatch error should be raised
- The error message should indicate that a boolean type was expected

**API Contracts**:
- The `compile_break()` and `compile_continue()` functions in `nu-engine/src/compile/keyword.rs` must return `CompileError::NotInALoop` when not inside a loop
- The error type is `CompileError::NotInALoop` with diagnostic code `nu::compile::not_in_a_loop`
- The error message must contain `"not_in_a_loop"` (the diagnostic code) in stderr output

- The `parse_row_condition()` function in `nu-parser/src/parser.rs` must use `type_compatible(&Type::Bool, &expression.ty)` to check expression type
- When type check fails, emit `ParseError::TypeMismatch(Type::Bool, expression.ty, expression.span)`
- The error message must contain `"expected bool"` string for test validation

**Acceptance**:
- Using `break` or `continue` outside any loop construct produces a compile-time error
- Using `break` or `continue` inside a closure/block that is not within a loop also produces a compile-time error
- Using `break` or `continue` inside valid loop constructs (`loop`, `while`, `for`) works correctly

- Row conditions with non-boolean expressions (e.g., literal integers, strings) produce a parse-time type mismatch error
- Row conditions with valid boolean expressions execute successfully

### Functional Area: Interactive editing and completion

The active editing scope must supply relevant completions and allow deliberate command-buffer execution and character counting.

### FR3: Local Variable Completion Support
**Problem**: Variable completions fail to suggest local variables **while the user is still typing inside an unclosed block**. The completion system behaves as if the scope has already ended, even though the user is actively typing within it. This affects function parameters, loop iteration variables, let bindings, and match pattern variables.

**User Report**:
```
When typing inside an unclosed block, tab completion does not suggest local variables:

  Broken (cursor before closing brace):
    def test [foo] { $foo<TAB>     → No completion for $foo
    for foo in [1] { $foo<TAB>    → No completion for $foo
    if true { let x = 1; $x<TAB>  → No completion for $x

  Expected: Variables should complete while typing inside the block.
```

**Requirements**:
- Variable completion should include function parameters (regular, optional, rest, and flag parameters)
- Variable completion should include loop iteration variables in `for` loops
- Variable completion should include variables declared with `let` in the current scope
- Variable completion should include pattern-matched variables in `match` expressions
- Variable completion should NOT include variables from sibling or parent scopes that have already ended
- Built-in variables (`$nu`, `$in`, `$env`) should retain their type information in completions

**API Contracts**:
- Variables must remain visible for completion while the user is typing inside an unclosed block, even if the block is syntactically incomplete
- Variable completion behavior must be consistent across both CLI (nu-cli) and LSP (nu-lsp) contexts

**Acceptance**:
- Variables in the current scope are included in completions
- Variables from ended scopes are NOT included in completions
- All variable types (parameters, let bindings, loop variables, match bindings) are included in completions


### FR4: Immediate Command Execution via Commandline Edit
**Problem**: Users cannot programmatically execute a command immediately after editing the commandline buffer. Currently, editing the buffer requires the user to manually press Enter.

**Requirements**:
- Add an `--accept` (or `-A`) flag to the `commandline edit` command
- When `--accept` is specified, the command should be executed immediately after the buffer is updated
- The flag should work with `--replace`, `--insert`, and `--append` modes
- The REPL state should properly track the accept flag and reset it after use

**API Contracts**:
- Add `accept: bool` field to the `ReplState` struct in `nu-protocol/src/engine/engine_state.rs`
- The `commandline edit` command in `nu-cli/src/commands/commandline/edit.rs` must add a switch `"accept"` with short flag `'A'`
- When `--accept` flag is present, set `repl.accept = true` before returning

**Acceptance**:
- The `--accept` flag causes the REPL to immediately execute the command after updating the buffer
- The `--accept` flag is documented in `commandline edit --help`


### FR5: Add `--chars` Flag to `str length` Command
**Problem**: The `str length` command supports counting by UTF-8 bytes (default) or grapheme clusters (`-g`), but has no option to count Unicode scalar values — the intuitive "character count" for most users.

**Requirements**:
- Add a `--chars` (`-c`) switch to the `str length` command that counts length using Unicode scalar values (`val.chars().count()`)
- The three counting modes are mutually exclusive: default (UTF-8 bytes), `--grapheme-clusters` (`-g`), and `--chars` (`-c`)
- When `--chars` is combined with `--grapheme-clusters`, produce an `IncompatibleParametersSingle` error
- Update existing switch descriptions from "count length using ..." to "count length in ..."

**API Contracts**:
- Add `chars: bool` field to the `Arguments` struct in `crates/nu-command/src/strings/str_/length.rs`
- Add a `"chars"` switch with short flag `Some('c')` to the command signature
- In the `run()` and `run_const()` functions, read the flag via `call.has_flag(engine_state, stack, "chars")?` / `call.has_flag_const(working_set, "chars")?` and pass it to `Arguments`
- In the `action()` function, add an `else if arg.chars { val.chars().count() }` branch between the `graphemes` and default (bytes) branches
- In `crates/nu-command/src/strings/mod.rs` `grapheme_flags()`, add a check that `--grapheme-clusters` and `--chars` are not used together, returning `ShellError::IncompatibleParametersSingle` if both are set

**Acceptance**:
- `'hällo' | str length --chars` returns `5`
- `'hello' | str length --chars` returns `5`
- `'hello' | str length` (default, bytes) still returns `5`
- `'hällo' | str length` (default, bytes) returns `6`
- Combining `--grapheme-clusters` and `--chars` produces an error


### Functional Area: Portable filesystem behavior

Path operations must respect host filesystem semantics without changing case-sensitive behavior on platforms that rely on it.

### FR6: Case-Insensitive Filesystem Support for `path relative-to`
**Problem**: The `path relative-to` command uses strict prefix matching via `Path::strip_prefix`, which fails on case-insensitive filesystems (Windows, macOS) when paths differ only in casing (e.g., `/etc` vs `/Etc`).

**Requirements**:
- When `path relative-to` fails to strip prefix with exact matching, on case-insensitive filesystems (Windows, macOS), fall back to case-insensitive component-by-component comparison
- Add a helper function `is_case_insensitive_filesystem()` that returns `true` on Windows and macOS using `cfg!(any(target_os = "windows", target_os = "macos"))`
- Add a helper function `try_case_insensitive_strip_prefix(lhs: &Path, rhs: &Path) -> Option<PathBuf>` that compares path components case-insensitively and returns the remaining relative path if the prefix matches
- `Component::Normal` parts are compared via `to_string_lossy().to_lowercase()`; non-Normal components (root, prefix, etc.) must match exactly
- On case-sensitive filesystems (Linux, FreeBSD), behavior remains unchanged — mismatched casing still produces an error

**API Contracts**:
- `is_case_insensitive_filesystem()` defined as a private function in `crates/nu-command/src/path/relative_to.rs`
- `try_case_insensitive_strip_prefix(lhs: &Path, rhs: &Path) -> Option<std::path::PathBuf>` defined in the same file
- The `relative_to()` function must call `is_case_insensitive_filesystem()` in the `Err` branch of `lhs.strip_prefix(&rhs)`, and if true, attempt `try_case_insensitive_strip_prefix` before returning an error

**Acceptance**:
- On case-insensitive filesystems (Windows, macOS), `path relative-to` succeeds when paths differ only in casing, returning the correct relative path
- On case-sensitive filesystems (Linux, FreeBSD), paths with different casing are treated as different paths and produce an error
- Paths that are truly different (not just casing differences) produce an error on all platforms


### Functional Area: Observable overlay state

Session overlays must expose stable ordering and activity information while resolving modules from the scope in which they are declared.

### FR7: Complete and Deterministic Overlay State Listing
The overlay table's schema, active status, hidden entries, and ordering together define one stable introspection result.

**Problem**:
The `overlay list` command currently returns only a list of strings containing active overlay names. Users cannot:
1. Determine the active/hidden state of overlays without additional commands
2. Track which overlays exist in the current session versus which are currently active
3. See overlays that have been hidden via `overlay hide`

Users need predictable ordering to retrieve the topmost active overlay from the list.

**Requirements**:
- The `overlay list` command shall return a table instead of a plain list of strings
- Each row in the table shall contain:
  - A `name` column (string type) with the overlay name
  - An `active` column (boolean type) indicating whether the overlay is currently active
- The table shall include all overlays known to the engine state, not just active overlays
- Active overlays shall have `active` set to `true`
- Hidden overlays (those hidden via `overlay hide`) shall have `active` set to `false`
- The command's input/output type signature shall be updated accordingly
- The command description shall be updated to reflect the new behavior

- Hidden (inactive) overlays shall be listed first in the output
- Active overlays shall follow hidden overlays, listed in the order they were activated
- The last entry in the list shall correspond to the most recently activated (topmost) overlay

**Acceptance**:
- The output of `overlay list` is a table with columns named `name` (String) and `active` (Bool)
- Active overlays appear with `active` equal to `true`
- Hidden overlays (after `overlay hide`) appear with `active` equal to `false`
- The table can be filtered by `active` field to separate active and hidden overlays
- Individual overlay records can be accessed by index (e.g., `overlay list | get 1`)

- In default state, `overlay list | last` returns the default overlay
- After activating an overlay, `overlay list | last` returns the most recently activated overlay
- Hidden overlays appear before active overlays in the list
- With multiple overlays, the activation order is preserved among active overlays

### FR8: Fix Scoped Module Registration in `overlay use`
**Problem**: The `overlay use` command fails to correctly register modules that are defined within scoped contexts (e.g., inside `do { }` blocks). When a module is defined and used within the same scope, the overlay lookup cannot find the module.

**Requirements**:
- The `overlay use` command shall correctly find and register modules defined in the current scope
- Quote trimming on module names shall not interfere with module lookup in scoped contexts
- The fix shall preserve correct behavior for modules defined in global contexts

**Acceptance**:
- When defining a module inside a `do` block and immediately using it with `overlay use`, the overlay is correctly registered
- When running `overlay list | last | get name` inside a `do` block after using a scoped module, the correct overlay name is returned
- When using quoted module names (single or double quotes) with `overlay use`, the overlay is correctly registered and can be hidden


## Non-Functional Requirements

### NFR1: Backward Compatibility
- Existing code using `starts-with` and `ends-with` operators must continue to work unchanged
- Existing completion behavior for global variables must remain unchanged
- Existing loop and control flow behavior must remain unchanged for valid use cases

### NFR2: Performance
- Variable completion should not introduce noticeable latency, even for scopes with many variables
- Compile-time loop validation should not significantly impact parse performance

### NFR3: `PipelineData` Constructor Refactoring (Code Quality)
Replace direct `PipelineData` enum variant construction with associated constructor functions throughout the codebase to improve consistency and reduce verbosity.

**Required Changes**:
- Add four associated constructor functions to the `PipelineData` impl block in `crates/nu-protocol/src/pipeline/pipeline_data.rs`:
  - `PipelineData::empty()` — a `const fn` that returns `PipelineData::Empty`
  - `PipelineData::value(val: Value, metadata)` — wraps `PipelineData::Value` variant
  - `PipelineData::list_stream(stream: ListStream, metadata)` — wraps `PipelineData::ListStream` variant
  - `PipelineData::byte_stream(stream: ByteStream, metadata)` — wraps `PipelineData::ByteStream` variant
- The `metadata` parameter for `value()`, `list_stream()`, and `byte_stream()` must accept `impl Into<Option<PipelineMetadata>>`, so callers can pass either `None` or a `PipelineMetadata` value directly
- Replace direct enum variant construction in expression positions across the codebase:
  - `PipelineData::Empty` → `PipelineData::empty()`
  - `PipelineData::Value(val, None)` → `PipelineData::value(val, None)`
  - `PipelineData::Value(val, Some(meta))` → `PipelineData::value(val, meta)`
  - `PipelineData::ListStream(stream, meta)` → `PipelineData::list_stream(stream, meta)`
  - `PipelineData::ByteStream(stream, meta)` → `PipelineData::byte_stream(stream, meta)`
  - Do NOT replace occurrences inside `match`, `if let`, or `matches!` patterns — those are destructuring, not construction, and must keep the enum variant names

**Scope**: This is a cross-cutting refactoring spanning most crates (nu-command, nu-engine, nu-cli, nu-plugin-core, nu-plugin-engine, nu-plugin-test-support, etc.).


## Accompanying Changes

### Windows Device Path Handling (`is_windows_device_path`)

Add a helper function `is_windows_device_path` to `crates/nu-path/src/helpers.rs` that detects Windows special device paths (e.g., `CON`, `NUL`, `COM1`, `\\.\CON`). On non-Windows platforms, this function always returns `false`.

The function must be publicly exported via `pub use helpers::{..., is_windows_device_path, ...}` in `crates/nu-path/src/lib.rs`.

Several file I/O commands must use this function to correctly handle Windows device paths:

- **`open.rs`**: Skip glob expansion for device paths (return the path directly instead of globbing)
- **`save.rs`**: Treat device paths as "existing" when deciding whether to open in append mode (`path.exists() || is_windows_device_path(path)`)
- **`source.rs`**: Skip path canonicalization for device paths (return the path as-is)
- **`parse_keywords.rs`**: In `find_in_dirs`, return device paths directly without searching directories

**Note**: This is a Windows-only behavioral change. On Linux, all code paths guarded by `is_windows_device_path` are unreachable.


## Glossary

- **Row Condition**: An expression used in filtering commands like `where` that is evaluated for each row/element
- **IR Compilation**: The intermediate representation compilation phase that converts parsed code to executable bytecode
- **REPL**: Read-Eval-Print Loop, the interactive shell interface

### Functional Area: Parser and command robustness

Malformed input and option selection must produce deliberate shell behavior instead of panics or implicit mode changes.

### FR9: Safe Stepped-Range and Non-UTF-8 Parsing
**Requirements**:
- Detect the `..=` range operator from its actual operator offset so stepped inclusive ranges cannot panic when earlier bytes contain similar characters.
- Reject non-UTF-8 unit tokens through the normal parse-failure path instead of lossy conversion.

**Acceptance**:
- Valid stepped inclusive ranges parse and execute without a bounds panic.
- A non-UTF-8 unit value fails safely and does not fabricate replacement characters.

### FR10: Explicit `find` Case and Multiline Modes
**Requirements**:
- Make ordinary `find` searches case-sensitive unless `--ignore-case` is supplied, applying that flag consistently to literal and regex searches.
- Make `--multiline` preserve a multiline string as one value rather than splitting it into lines; reject that mode for byte-stream input with actionable guidance.
- Restrict `--dotall` to regex search.

**Acceptance**:
- Case and multiline results change only when their corresponding flags are present, and incompatible byte-stream or non-regex flag combinations return clear errors.

### FR11: Consistent Optional Lookup and External Resolution
**Requirements**:
- Honor `get --optional` during constant evaluation as well as runtime evaluation.
- Do not fail external-command resolution merely because `$env.PATH` is absent; continue through the platform resolver so Windows command built-ins remain available.

**Acceptance**:
- Missing const cell paths return the documented optional result, and a missing PATH produces the command's real platform-level outcome rather than a preliminary PATH lookup error.

## Verification Strategy

Exercise operator results, parse-time failures, active-scope completion, REPL buffer acceptance, Unicode counting, stepped ranges, non-UTF-8 units, and case-sensitive or insensitive search and path cases. Verify const optional lookup, missing-PATH external execution, overlay visibility, ordering, and scoped registration alongside PipelineData call sites and Windows device-path handling, while preserving behavior for unaffected callers and platforms.

# Environment Dependency Changes (relative to Base Env)

No environment dependency changes beyond the repository state at the milestone start.
