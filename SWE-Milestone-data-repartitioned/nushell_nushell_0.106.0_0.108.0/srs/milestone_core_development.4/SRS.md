# Software Requirements Specification: Nushell Core Semantics and Execution Safety

## Overview

Deliver a core-semantics release in which value access, stream execution, completion, presentation, and type annotations behave predictably across static and dynamic inputs. Custom values must participate in ordinary save and cell-path contracts, streaming commands must preserve flow and error state, file watching must work as either a callback or an event stream, and opt-in runtime annotation and pipe-failure checks must reject incompatible values or failed external pipelines without changing default behavior. The implementation must also keep SQLite paths, networking features, reedline, rusqlite, signatures, and test APIs coherent.

### Requirements Summary

1. **FR1**: Case-Insensitive and Optional Cell-Path Access
2. **FR2**: Save Custom Values to Disk
3. **FR3**: Context-Specific Glob and String Compatibility
4. **FR4**: Robust `each` Streaming, Null, and Error Semantics
5. **FR5**: Reset Content Type for Partial Input Commands
6. **FR6**: Error Handler Cleanup on Loop Control in Try Blocks
7. **FR7**: Range Iteration Type Inference
8. **FR8**: Static List Completions for Command Parameters
9. **FR9**: Default Terminal Color in Theme
10. **FR10**: Optional Runtime Type Enforcement and Conversion Errors
11. **NFR1**: Reedline Library Upgrade Adaptation (reedline 0.41.0 → 0.42.0)
12. **NFR2**: Rusqlite Library Upgrade Adaptation (rusqlite 0.31 → 0.37)
13. **NFR3**: Signature Struct Field Changes
14. **NFR4**: Test Support for Experimental Options
15. **FR11**: Closure-Free `watch` Event Streams
16. **FR12**: Opt-In External Pipeline Failure Propagation
17. **FR13**: Shell-Relative SQLite Output Paths
18. **NFR5**: Network and TLS Feature Compatibility

### Affected Modules

- nu-protocol value, type, signature, and custom-value contracts
- nu-engine, nu-parser, pipeline evaluation, and experimental options
- nu-command watch, stream, cell-path, save, completion, SQLite, and presentation behavior
- nu-cli/reedline, process exit tracking, network/TLS features, and test support

## Functional Requirements

### Functional Area: Predictable value access and custom-value contracts

Built-in and custom values must honor the same access, casing, optional-path, serialization, and compatible-type expectations.

### FR1: Case-Insensitive and Optional Cell-Path Access
Cell-path flags and custom-value callbacks are one access contract: optional and case-insensitive semantics must reach every built-in and plugin-backed value consistently.

**Problem**:
Users must append `!` to each cell-path member to enable case-insensitive access, which is verbose when accessing multiple columns.

Custom value implementations cannot honor the optional (`?`) and case-insensitive (`!`) cell-path modifiers. When users access custom values with these modifiers, the behavior is incorrect.

**Requirements**:
- Add `--ignore-case` flag to the `get` command that makes all cell-path members case-insensitive
- Add `--ignore-case` flag to the `select` command with the same behavior
- Add `--ignore-case` flag to the `reject` command with the same behavior
- The flag should apply to all cell-path members in the command, equivalent to appending `!` to each one
- The flag must work in combination with the `--optional` flag

- Add `optional: bool` parameter to `CustomValue::follow_path_int` method signature: `fn follow_path_int(&self, self_span: Span, index: usize, path_span: Span, optional: bool) -> Result<Value, ShellError>`
- Add `optional: bool` and `casing: Casing` parameters to `CustomValue::follow_path_string` method signature: `fn follow_path_string(&self, self_span: Span, column_name: String, path_span: Span, optional: bool, casing: Casing) -> Result<Value, ShellError>`
- The `Casing` enum from `nu_protocol::casing` has variants `Casing::Sensitive` and `Casing::Insensitive`
- When `optional` is true and the path member does not exist, return `Value::nothing(span)` instead of an error
- When `casing` is `Casing::Insensitive`, perform case-insensitive matching for string keys
- Update all custom value implementations to handle these new parameters

**Acceptance**:
- Basic case: `get --ignore-case <cell-path>` on a table with differently-cased column names succeeds when the column exists with different casing
- Nested path case: `get --ignore-case <nested.cell.path>` applies case-insensitive matching to each path member in the chain
- Select case: `select --ignore-case <column>` selects the column regardless of case differences between the specified name and actual column name
- Reject case: `reject --ignore-case <key>` removes the key from a record regardless of case differences
- Combined flags: `--ignore-case` works correctly in combination with `--optional` flag

- Accessing an existing key on a custom value returns the value correctly
- Accessing a non-existent key on a custom value raises an appropriate error
- Using optional access syntax on a non-existent key returns null instead of an error
- Using case-insensitive access syntax matches keys regardless of case differences

### FR2: Save Custom Values to Disk
**Problem**: The `save` command cannot write custom values to files. Plugin-defined custom values have no way to implement custom serialization for file output.

**Requirements**:
- Add a `save` method to the `CustomValue` trait that allows custom values to define how they are written to files
- The `save` method signature: `fn save(&self, path: Spanned<&Path>, value_span: Span, save_span: Span) -> Result<(), ShellError>`
- The `save` command should check if the input is a custom value and call its `save` method
- The default implementation should return an error indicating the custom value does not implement saving
- The error message must contain the string `"Custom value does not implement \`save\`"` and suggest checking the plugin's documentation
- Plugin authors can implement the `save` method to support file output

**Acceptance**:
- When attempting to save a custom value that does not support saving, an appropriate error should be raised indicating the operation is not supported
- When saving a custom value that implements the save functionality, the file should be written using the custom serialization


### FR3: Context-Specific Glob and String Compatibility
**Problem**: Glob and string values need runtime subtype compatibility without erasing the parser's stricter distinction at definition sites.

**Requirements**:
- In `Type::is_subtype_of`, treat `Type::Glob` and `Type::String` as mutually compatible for runtime subtype checks
- Add match arms: `(Type::Glob, Type::String) => true` and `(Type::String, Type::Glob) => true`
- Note: The parse-time `type_compatible` function in `nu-parser` already has asymmetric handling and should NOT be modified

**Acceptance**:
- When a glob-typed variable is passed to a function expecting a string parameter, an error "expected string, found glob" is produced (the parser still distinguishes them for type checking at definition sites)
- When a string-typed variable is passed to a function expecting a glob parameter, the call succeeds and the value is used correctly


### Functional Area: Streaming and evaluation correctness

Pipeline operations must stream when requested, handle empty values consistently, clear invalidated metadata, and retain complete error state.

### FR4: Robust `each` Streaming, Null, and Error Semantics
The `each` command must treat flattening, null input, and nested failures as one coherent stream contract rather than losing values or errors at pipeline boundaries.

**Problem**:
When the closure passed to `each` returns a stream, the stream is fully collected before being returned as a single item in the output. This blocks processing of subsequent items and prevents true streaming behavior.

When `each` receives a single `null` value, it currently processes it through the closure. This is inconsistent with the intended behavior where `each` should iterate over elements.

When errors occur in nested `each` closures, the full error chain showing the call stack is not properly preserved, making debugging difficult.

**Requirements**:
- Add `--flatten` flag to the `each` command
- When `--flatten` is used, items from closure streams should be yielded immediately as they are received rather than waiting for the entire stream to be collected
- The output should be a flat stream rather than a list of collected values
- This effectively flattens output that would otherwise be `list<list<T>>` into `list<T>`
- The default behavior (without `--flatten`) should remain unchanged: streams are collected into values

- When `each` receives a single `null` value as input, it should return `null` without executing the closure
- When `each` receives an empty pipeline, it should return empty
- This enables graceful handling of missing or optional values in pipelines

- Errors in nested `each` closures should propagate with the full call chain information
- Each level of nesting should be represented in the error output
- The error message should show `eval_block_with_input` for each level of the `each` call stack

**Acceptance**:
- The `each` command must accept a `--flatten` flag (short form `-f`)
- When `each --flatten` is used with a closure that returns a range, items are streamed immediately to downstream commands without waiting for all iterations
- When processing `[0 3] | each --flatten {|e| $e..<($e + 3) | round } | square`, items from the first iteration are passed to `square` before the second iteration completes (output order demonstrates interleaving: first iteration items alternate with second iteration items as they complete)
- Without `--flatten`, the same pipeline collects all items from each iteration before passing to the next command

- When `each` receives a single `null` value as input, it returns `null` without executing the closure

- When an error occurs in nested `each` closures, the error output should show the call stack for each nesting level
- The error chain should contain `eval_block_with_input` entries corresponding to each nesting level

### FR5: Reset Content Type for Partial Input Commands
**Problem**: Commands like `first` and `str substring` that return a subset of the input incorrectly preserve the original content type metadata. A substring of JSON text is not necessarily valid JSON.

**Requirements**:
- The `first` command should clear the `content_type` metadata when extracting bytes from a stream
- The `str substring` command should clear the `content_type` metadata
- The `skip` command should clear the `content_type` metadata when skipping bytes
- The `take` command should clear the `content_type` metadata when taking bytes
- The `bytes at` command should clear the `content_type` metadata when extracting byte ranges

**Acceptance**:
- When partial content is extracted from input that has content type metadata, the result's metadata should not contain the original content type
- This can be verified by checking `metadata | get content_type?` on the output, which should return null


### FR6: Error Handler Cleanup on Loop Control in Try Blocks
**Problem**: When using `break` or `continue` inside a `try` block within a loop, error handlers are not properly cleaned up, causing subsequent errors to be incorrectly caught or the error propagation to fail.

**User Report**:
```
When breaking out of a try block inside a loop, subsequent error make
commands are incorrectly caught or the error doesn't propagate correctly.
```

**Requirements**:
- When `break` is used inside a `try` block within a loop, error handling state must be properly cleaned up
- When `continue` is used inside a `try` block within a loop, error handling state must be properly cleaned up
- Nested `try` blocks should also properly clean up their error handling state when a loop control statement is encountered
- Errors raised after the loop should propagate correctly and not be caught by handlers from within the loop

**Acceptance**:
- When executing a loop with `try { break } catch { ... }` followed by `error make`, the error is NOT caught by the catch block and propagates correctly
- When executing a loop with nested `try` blocks containing `break`, both inner and outer catch blocks are bypassed correctly
- When executing a loop with `try { continue } catch { ... }`, errors after the loop propagate correctly


### FR7: Range Iteration Type Inference
**Problem**: When iterating over a `Range` in a `for` loop, the loop variable's type is not correctly inferred, potentially causing type mismatches with runtime enforcement enabled.

**Requirements**:
- When parsing a `for` loop that iterates over a `Range` expression, infer the loop variable's type as `Type::Number` (since range elements can be either int or float)
- This ensures the loop variable has a compatible type regardless of whether the range yields integers or floats

**Acceptance**:
- When iterating over a range with `for i in 1..10 { ... }`, the variable `i` has type `Number`
- No type errors occur when using range iteration with runtime type enforcement enabled


### Functional Area: Completion and terminal presentation

Interactive guidance and default rendering must remain useful across command signatures and terminal color schemes.

### FR8: Static List Completions for Command Parameters
**Problem**: Currently, command parameters can only specify completions via a custom completion command. There is no simple syntax for providing a static list of valid values for tab completion.

**Requirements**:
- Support specifying a static list of valid values for parameter completions inline in the parameter declaration
- Support referencing a constant list for parameter completions
- Support completion records that can provide additional metadata for each completion value
- Both `def` and `extern` commands should support static list completions
- Built-in commands should be able to specify static completion lists in their signatures
- When a record is provided instead of a list, produce a `ParseError::OperatorUnsupportedType` error

**Acceptance**:
- Commands defined with inline list completions should show those items as tab completion options
- Commands defined with constant list completions should show those items as tab completion options
- Records with a `value` field in the completion list should extract the value correctly
- When a record (instead of a list) is provided for completions, a parse error should be raised
- Built-in commands with static completion lists should show their completion options


### FR9: Default Terminal Color in Theme
**Problem**: The default color theme uses explicit white color (`Color::White`) for many element types. On terminals with light backgrounds, white text is nearly invisible.

**Requirements**:
- Change the default theme to use `Color::Default` instead of `Color::White` for element types that should inherit the terminal's default foreground color
- This affects: separator, int, duration, range, float, string, nothing, binary, cell-path, record, list, block, and search_result colors
- Preserve explicit colors for elements that should have distinct coloring (header, empty, bool, filesize, datetime, row_index, hints)
- `Color::Default` must render as ANSI SGR code 39 (escape sequence `\u{1b}[39m`) for default foreground color

**Acceptance**:
- When `{a: 1, b: 2}` is displayed with ANSI coloring enabled, integer values and table borders use the default terminal color ANSI escape code `\u{1b}[39m` (not white `\u{1b}[37m`)
- When `find` command highlights matches, the highlight style uses `\u{1b}[41;39m` (red background code 41 combined with default foreground code 39)
- When displaying an empty list `[]` with ANSI coloring, the table borders use default color code `\u{1b}[39m`
- The `search_result` theme element uses `Color::Default.normal().on(Color::Red)` (default foreground on red background)


### Functional Area: Optional runtime annotation enforcement

When explicitly enabled, declared variable types become runtime contracts and mismatches produce the normal conversion diagnostics.

### FR10: Optional Runtime Type Enforcement and Conversion Errors
Runtime annotation checking is an opt-in contract whose observable result is a normal conversion error whenever a dynamic value violates its declared type.

**Problem**:
Type annotations on variables are ignored at runtime, allowing dynamically-typed values to be assigned to annotated variables without validation.

When a variable with a type annotation (such as `record<b: int>`) is assigned a value with an incompatible type at runtime (such as `record<a: int>`), no error is raised.

**Requirements**:
- Introduce an experimental opt-in option named `enforce-runtime-annotations`
- Create a new module `crates/nu-experimental/src/options/enforce_runtime_annotations.rs` following the pattern of existing options (e.g., `pipefail.rs`)
- Define a static `ENFORCE_RUNTIME_ANNOTATIONS: ExperimentalOption` that can be queried via `.get()` method
- The option identifier must be `"enforce-runtime-annotations"` (used with `--experimental-options=[enforce-runtime-annotations]`)
- Register the option in `crates/nu-experimental/src/options/mod.rs` by adding it to the `ALL` array and re-exporting it
- When enabled, variable assignments must validate the assigned value's type against the declared type annotation
- The option must be opt-in by default (disabled unless explicitly enabled)

- When runtime type enforcement is enabled and a value with incompatible type is assigned to an annotated variable, produce a conversion error
- The type check should occur when executing the variable storage instruction, comparing the value's type against the variable's declared type (obtainable from `engine_state.get_var(var_id).ty`)
- The error message must contain "can't convert" along with the source type and target type information
- The error identifier must be "nu::shell::cant_convert"
- Compound assignment operators (e.g., `+=`, `-=`, `*=`, `/=`) that change the result type should also trigger type validation errors

**Acceptance**:
- When the experimental option is enabled and a variable is assigned a value incompatible with its type annotation, a conversion error is produced
- When the experimental option is disabled, variable assignments behave as before (no runtime type checking)
- The option can be enabled via command line: `nu --experimental-options=[enforce-runtime-annotations]`

- When the option is enabled and executing `let x: record<b: int> = ({a: 1} | to nuon | from nuon)`, an error containing "can't convert record<a: int> to record<b: int>" is produced
- When the option is enabled and executing `mut a: record<b: int> = {b:1}; $a.b /= 4`, an error containing "nu::shell::cant_convert" is produced because division produces a float incompatible with the `int` type annotation

## Non-Functional Requirements

- All changes must maintain backward compatibility with existing scripts
- Performance of `each` should not regress for non-streaming use cases
- The completion system should remain responsive with static list completions

### NFR1: Reedline Library Upgrade Adaptation (reedline 0.41.0 → 0.42.0)
**Scope**: Adapt `crates/nu-cli/src/reedline_config.rs` to the reedline 0.42.0 API. The reedline library introduced a new text object system that replaces the previous `CutInside`/`YankInside` edit commands.

**API Changes**:
- `EditCommand::CutInside { left, right }` → `EditCommand::CutInsidePair { left: char, right: char }` (renamed)
- `EditCommand::YankInside { left, right }` → `EditCommand::CopyInsidePair { left: char, right: char }` (renamed, Yank→Copy)

**Acceptance**: The keybinding configuration parsing in `reedline_config.rs` compiles and works correctly with reedline 0.42.0.

### NFR2: Rusqlite Library Upgrade Adaptation (rusqlite 0.31 → 0.37)
**API Changes**:
- `DatabaseName` enum (variants `Main`, `Temp`, `Attached(&str)`) → replaced by the `Name` trait; methods now accept generic `impl Name` parameters
- `DatabaseName::Main` → use `rusqlite::MAIN_DB` constant (type `&CStr`, value `c"main"`) or a `&str` literal `"main"`
- `DatabaseName::Temp` → use `rusqlite::TEMP_DB` constant or `"temp"`
- `DatabaseName::Attached(name)` → pass the `name: &str` directly (since `&str` implements `Name`)

**Acceptance**: The database commands in `sqlite.rs` compile and work correctly with rusqlite 0.37.

### NFR3: Signature Struct Field Changes
**API Changes**:
- `PositionalArg` and `Flag` (in `crates/nu-protocol/src/signature.rs`): replace the existing `custom_completion: Option<DeclId>` field with `completion: Option<Completion>`, where `Completion` is a new enum supporting both custom completion commands (`DeclId`) and static value lists

**Acceptance**: The structs compile with the updated field layout, and all existing tests pass.

### NFR4: Test Support for Experimental Options
**Scope**: Add experimental options support to the test helper infrastructure (`crates/nu-test-support/src/macros.rs`), enabling integration tests to pass `--experimental-options` to the `nu` binary.

**API Changes**:
- `NuOpts` struct: add field `pub experimental: Option<Vec<String>>`
- `nu_run_test()` function: when `experimental` is `Some(opts)`, append `--experimental-options=[opt1,opt2,...]` to the `nu` command invocation

**Acceptance**: Integration tests can use `nu!(experimental: vec!["option_name".to_string()], "command")` to run commands with experimental options enabled.

### Functional Area: Observable filesystem and process streams

Long-running filesystem and external-process pipelines must expose their events and terminal state as first-class pipeline data.

### FR11: Closure-Free `watch` Event Streams
**Requirements**:
- Keep the existing closure callback form of `watch`, but make the closure optional.
- Without a closure, return a cancellable stream of records with `operation`, `path`, and optional `new_path` fields for create, remove, write, and rename events.
- Apply recursive, glob, verbose, and debounce filtering consistently in both modes; retain `--debounce-ms` compatibility while accepting typed durations through `--debounce`.

**Acceptance**:
- A closure-free invocation can be piped through ordinary Nushell filters and stops cleanly on an interrupt.
- Rename records preserve both the original and destination paths, while non-rename records leave `new_path` empty.

### FR12: Opt-In External Pipeline Failure Propagation
**Requirements**:
- Register an opt-in experimental option named `pipefail`.
- Track exit-status futures across pipeline construction, evaluation, closures, and command printing without changing behavior when the option is disabled.
- When enabled, report the rightmost failed external command and update `$env.LAST_EXIT_CODE` accordingly.

**Acceptance**:
- Pipelines with an earlier failing external command fail under `pipefail` even when a later command succeeds.
- The same pipelines retain the prior last-command behavior when `pipefail` is not enabled.

### Functional Area: Path and build compatibility

Command paths and optional build features must resolve consistently inside the shell's own execution context.

### FR13: Shell-Relative SQLite Output Paths
**Requirements**:
- Resolve relative `into sqlite` database paths against Nushell's current working directory while preserving the special in-memory database target.

**Acceptance**:
- Running `into sqlite relative.db` after changing Nushell's working directory writes to that directory, not to the host process's unrelated working directory.

### NFR5: Network and TLS Feature Compatibility
The default build must include the network feature with rustls, and both rustls and native-TLS configurations must compile through the shared HTTP client and TLS configuration interface.

## Verification Strategy

Exercise value access, custom-value saving, stream flow, metadata, nested errors, completion, and terminal rendering under their normal settings. Run the annotation cases with and without `enforce-runtime-annotations`, including glob/string and range behavior; run external-pipeline cases with and without `pipefail`; and exercise `watch` in callback and closure-free streaming modes. Also verify shell-relative SQLite paths and compile the retained dependency, network/TLS, signature, and test-helper APIs.

# Environment Dependency Changes (relative to Base Env)

- Use the retained entry image's Rust 1.86-compatible checkout. The image applies symmetric START/END compatibility commits rather than requiring a Rust 1.88 toolchain.
- The source transition updates `reedline` from 0.41.0 to 0.42.0 and `rusqlite` from 0.31 to 0.37; the retained image includes the compatibility adaptations needed to compile those APIs.
