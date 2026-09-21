#!/usr/bin/env python3
"""Overwrite the six merged SRS files in the repartitioned dataset.

The source SRS documents are first merged mechanically: declared requirement
fragments retain their full Problem/Requirements/Acceptance content and receive
one contiguous FR/NFR sequence.  The checked-in ``RequirementFusion`` records
then encode the human review step for genuinely overlapping FRs.  A fusion
coalesces repeated subsection labels without dropping either source body.

Every output follows the benchmark's common SRS layout: ``## Overview`` with
``### Requirements Summary`` and ``### Affected Modules``, followed by numbered
functional requirements and the original environment-dependency heading.  The
repartitioned dataset is the only publication location: this script refuses to
create missing SRS files, so a typo cannot silently create a second output tree.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Fragment:
    milestone_id: str
    start_heading: str
    end_heading: str
    # Optional authored structure placed before this source fragment.  These
    # headings group requirements by product concern instead of by source-file
    # boundary, which lets a unified statement preserve every source obligation
    # without reading like two documents pasted together.
    section_heading: str | None = None
    section_intro: str | None = None
    # Small, declared rewrites are allowed only when source-document wording
    # would expose an obsolete milestone boundary in the unified task.  The
    # coverage audit applies the same replacements before byte-normalized
    # comparison, so these transformations remain explicit and reproducible.
    replacements: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RequirementFusion:
    """Human-reviewed fusion of overlapping normalized requirement blocks.

    ``source_titles`` are exact post-extraction titles, without their FR number.
    They remain auditable even though the final output exposes only ``title``.
    ``intro`` explains the common behavioral contract without referring to the
    merge process or to source milestones.
    """

    source_titles: tuple[str, ...]
    title: str
    intro: str


@dataclass(frozen=True)
class ObligationExclusion:
    """A source obligation intentionally removed after patch-semantic review."""

    milestone_id: str
    heading: str
    reason: str
    patch_evidence: tuple[str, ...]


@dataclass(frozen=True)
class PatchDerivedRequirement:
    """Requirement added by reviewing the canonical merged START->END patch.

    Source SRS coverage remains independently auditable through ``Fragment``;
    these records cover important observable behavior that the source SRS
    omitted even though it is present in the reviewed gold patch.
    """

    kind: str
    title: str
    body: str


@dataclass(frozen=True)
class PatchDerivedSection:
    heading: str
    intro: str
    requirements: tuple[PatchDerivedRequirement, ...]


@dataclass(frozen=True)
class UnifiedSRS:
    workspace: str
    retained_id: str
    title: str
    overview: str
    affected_modules: tuple[str, ...]
    fragments: tuple[Fragment, ...]
    fusions: tuple[RequirementFusion, ...]
    verification: str
    environment: str
    exclusions: tuple[ObligationExclusion, ...] = ()
    patch_derived_sections: tuple[PatchDerivedSection, ...] = ()


SPECS = (
    UnifiedSRS(
        workspace="BurntSushi_ripgrep_14.1.1_15.0.0",
        retained_id="milestone_seed_5f5da48_1_sub-02",
        title="Structured Glob Parsing and Compatibility Policy",
        overview=(
            "Make ripgrep's glob syntax structurally expressive while keeping malformed-"
            "pattern handling explicit and compatible with each caller. The parser must "
            "track nested brace-alternation scope, preserve useful unmatched-brace errors, "
            "and generate regular expressions with equivalent matching behavior. When a "
            "character class has no closing bracket, a builder policy decides whether `[` "
            "begins literal text or remains an error: gitignore parsing uses Git-compatible "
            "recovery, while command-line override globs stay strict by default. Existing "
            "well-formed patterns and the public API compatibility surface remain stable."
        ),
        affected_modules=(
            "crates/globset (parser, GlobBuilder, regex generation, and tests)",
            "crates/ignore (GitignoreBuilder defaults and forwarding API)",
            "crates/ignore/src/overrides.rs (OverrideBuilder defaults and forwarding API)",
        ),
        fragments=(
            Fragment(
                "milestone_seed_5f5da48_1_sub-01",
                "## Functional Requirements",
                "## Compatibility Notes",
                section_heading="Nested syntax handling",
                section_intro=(
                    "Brace groups must retain their structure from parsing through regex "
                    "generation and path matching."
                ),
            ),
            Fragment(
                "milestone_seed_5f5da48_1_sub-02",
                "## Functional Requirements",
                "## Notes",
                section_heading="Context-aware malformed-class recovery",
                section_intro=(
                    "Unclosed character classes follow an explicit builder policy so each "
                    "caller can choose compatibility recovery or strict diagnostics."
                ),
                replacements=(
                    (
                        "- When unclosed class mode is enabled and an unclosed character class is detected, the opening `[` and all following characters should be treated as literal text",
                        "- When unclosed-class recovery is enabled and no closing `]` exists, treat the unmatched opening `[` as a literal character, then resume parsing subsequent characters with the normal glob syntax",
                    ),
                ),
            ),
            Fragment(
                "milestone_seed_5f5da48_1_sub-01",
                "## Compatibility Notes",
                "# Environment Dependency Changes",
                replacements=(
                    (
                        "- The `NestedAlternates` error kind shall be deprecated but retained in the public API for backward compatibility",
                        "- Retain the `NestedAlternates` error kind in the public API for backward compatibility and document it as obsolete now that valid nested groups no longer emit it",
                    ),
                ),
            ),
            Fragment("milestone_seed_5f5da48_1_sub-02", "## Notes", "# Environment Dependency Changes"),
        ),
        fusions=(
            RequirementFusion(
                source_titles=(
                    "Support Parsing of Nested Alternation Groups",
                    "Generate Correct Regular Expressions for Nested Alternations",
                    "Ensure Nested Alternation Patterns Correctly Match File Paths",
                ),
                title="End-to-End Nested Alternation Semantics",
                intro=(
                    "Nested alternations form one end-to-end language feature: the parser "
                    "must retain their structure, regex translation must preserve it, and "
                    "the resulting matcher must accept and reject the intended paths."
                ),
            ),
            RequirementFusion(
                source_titles=(
                    "Add Unclosed Class Toggle to Glob Builder API",
                    "Implement Literal Fallback Parsing for Unclosed Character Classes",
                ),
                title="Configurable Recovery for Unclosed Character Classes",
                intro=(
                    "The builder option and literal fallback are one parser contract: the "
                    "selected policy must determine whether an unmatched `[` is recovered as "
                    "literal text or reported as an error."
                ),
            ),
            RequirementFusion(
                source_titles=(
                    "Enable Unclosed Class Support by Default in Gitignore Parsing",
                    "Disable Unclosed Class Support by Default in Override Globs",
                ),
                title="Caller-Specific Defaults for Gitignore and Override Globs",
                intro=(
                    "Gitignore and override builders expose the same policy but deliberately "
                    "choose different defaults to preserve their respective compatibility "
                    "contracts."
                ),
            ),
        ),
        verification=(
            "Exercise nested parsing, regex generation, and positive/negative path "
            "matches together with the exact `allow_unclosed_class(bool) -> &mut Self` "
            "API contracts and the different GitignoreBuilder/OverrideBuilder defaults."
        ),
        environment="- Rust toolchain upgraded to 1.88.0.",
    ),
    UnifiedSRS(
        workspace="element-hq_element-web_v1.11.95_v1.11.97",
        retained_id="feature_enhancements",
        title="Trustworthy Identity and Room Interactions",
        overview=(
            "Make Element Web preserve user intent across identity state transitions and "
            "room interactions. Security-sensitive actions must expose their true pending "
            "or verification state and prevent an accidental or premature transition. "
            "Room actions must address only the intended people or room, use unambiguous "
            "copy, and expose notification state through precise contracts. The resulting "
            "flows must follow existing Matrix, React, Compound, and i18n conventions so "
            "the interface and its underlying state remain consistent."
        ),
        affected_modules=(
            "ResetIdentityPanel, MatrixChat forced-verification flow, and UserIdentityWarning",
            "NotificationState, RoomNotificationState, and RoomNotifs",
            "SendMessageComposer reply-mention targeting",
            "RoomSummaryCard, ReportRoomDialog, and their related styles",
        ),
        fragments=(
            Fragment(
                "milestone_seed_56c7fc1_1",
                "### FR1: Identity Reset Loading State Protection",
                "### FR2: Force-Verify Mode Bypass Prevention",
                section_heading="Accurate client state and guarded transitions",
                section_intro=(
                    "The client must represent pending identity work, guarded navigation, "
                    "and room-notification categories according to their underlying state."
                ),
            ),
            Fragment(
                "milestone_seed_56c7fc1_1",
                "### FR2: Force-Verify Mode Bypass Prevention",
                "### FR3: Pinned Identity Change Dismissal Button Text",
            ),
            Fragment(
                "feature_enhancements",
                "## FR3: Improve Room Notification State API",
                "# Environment Dependency Changes",
                replacements=(
                    (
                        "- `RoomNotificationState.hasAnyNotificationOrActivity` getter returns true for any notification, activity, or knock",
                        "- `RoomNotificationState.hasAnyNotificationOrActivity` returns true for knocks and notification levels at or above `Notification`, and for `Activity` only when `feature_hidebold` is disabled",
                    ),
                ),
            ),
            Fragment(
                "milestone_seed_56c7fc1_1",
                "### FR3: Pinned Identity Change Dismissal Button Text",
                "# Environment Dependency Changes",
                section_heading="Intent-preserving feedback and room actions",
                section_intro=(
                    "Labels, mention targeting, and reporting controls must communicate "
                    "and execute exactly the action the user selected."
                ),
            ),
            Fragment(
                "feature_enhancements",
                "## FR1: Remove Unintentional Mentions in Replies",
                "## FR2: Add Room Reporting Dialog",
                replacements=(
                    (
                        "- When composing a reply, the `m.mentions` field must only include the sender of the message being replied to",
                        "- When composing a reply, initialize `m.mentions.user_ids` from the replied-to sender and from mentions explicitly added in the new reply",
                    ),
                    (
                        "- When replying to a message sent by @bob that mentions @charlie, the reply's `m.mentions.user_ids` contains only `[\"@bob\"]`",
                        "- When replying to a message sent by @bob that mentions @charlie, include @bob but exclude @charlie unless the new reply explicitly mentions @charlie",
                    ),
                    (
                        "- Room mentions (`@room`) from the original message are not propagated to replies",
                        "- Do not inherit user IDs from the replied-to event; room-mention behavior otherwise remains unchanged",
                    ),
                ),
            ),
            Fragment(
                "feature_enhancements",
                "## FR2: Add Room Reporting Dialog",
                "## FR3: Improve Room Notification State API",
            ),
        ),
        fusions=(
            RequirementFusion(
                source_titles=(
                    "Identity Reset Loading State Protection",
                    "Force-Verify Mode Bypass Prevention",
                ),
                title="Guard Identity Reset and Forced Verification Transitions",
                intro=(
                    "Security-sensitive identity transitions must expose their pending state "
                    "and prevent every navigation or repeated-action path that would bypass "
                    "the required operation."
                ),
            ),
        ),
        verification=(
            "Trace each visible action to the state transition or room target it controls: "
            "cover pending reset and forced-verification gates, precise notification "
            "getters, pinned-identity dismissal copy, reply recipients, and report-dialog "
            "validation and submission. Existing identity and room behavior must remain "
            "unchanged when the new states or controls do not apply."
        ),
        environment=(
            "No additional milestone-specific packages are required beyond the retained "
            "entry image and its existing Element Web base environment."
        ),
    ),
    UnifiedSRS(
        workspace="navidrome_navidrome_v0.57.0_v0.58.0",
        retained_id="milestone_003_sub-04",
        title="Reliable Multi-Library Data from Scan to Selection",
        overview=(
            "Provide a dependable library lifecycle from media ingestion to the content "
            "a user selects and sees. Scanning must preserve embedded artwork and complete "
            "metadata, avoid misleading configuration diagnostics, and keep invalid "
            "participant references from aborting otherwise valid album persistence. "
            "That library data must then be manageable by administrators, assignable to "
            "users, filterable through persistent selection state, and represented "
            "consistently in forms, events, formatting, and edit-time validation."
        ),
        affected_modules=(
            "TagLib wrapper, tag-mapping configuration, and scanner persistence",
            "ui/src/library, ui/src/user, ui/src/layout, ui/src/common, and UI utilities",
            "UI data provider, Redux actions/reducers, persistence, and SSE refresh",
        ),
        fragments=(
            Fragment(
                "milestone_004",
                "## FR1: Cover Art Extraction for Additional Audio Formats",
                "# Environment Dependency Changes",
                section_heading="Reliable library ingestion",
                section_intro=(
                    "The scanner must retain valid media information and isolate malformed "
                    "configuration or references without corrupting the library update."
                ),
            ),
            Fragment(
                "milestone_003_sub-04",
                "## Functional Requirements",
                "## Additional Requirements",
                section_heading="Library administration and selection",
                section_intro=(
                    "Once ingested, libraries need a complete administrative and user-facing "
                    "control plane for assignment, filtering, and persistent selection."
                ),
                replacements=(
                    (
                        "**Problem**: Non-admin users can be saved without any library assignments, leaving them with no access to content.",
                        "**Problem**: Existing non-admin users can be edited into a state with no library access; new users may instead rely on default-library assignment.",
                    ),
                    (
                        "- When saving a non-admin user with no libraries selected, a validation error displays",
                        "- When editing a non-admin user with no libraries selected, a validation error displays",
                    ),
                    (
                        "- When saving a non-admin user with libraries selected, no validation error displays",
                        "- When editing a non-admin user with libraries selected, no validation error displays",
                    ),
                ),
            ),
            Fragment(
                "milestone_003_sub-04",
                "## Additional Requirements",
                "# Environment Dependency Changes",
                replacements=(
                    ("## Additional Requirements", "## Library-aware UI integration"),
                    (
                        "### Internationalization\n\n"
                        "**Requirements**:\n"
                        "- Add translation keys for all new UI elements including:\n"
                        "  - Library selector labels (\"All Libraries\", \"None\", \"Select Libraries\")\n"
                        "  - Library resource field labels and section headers\n"
                        "  - User library selection labels and helper text\n"
                        "  - Validation error messages\n"
                        "  - Library management notifications (created, updated, deleted)",
                        "",
                    ),
                ),
            ),
        ),
        fusions=(
            RequirementFusion(
                source_titles=(
                    "Global Library Selector Component",
                    "Library Filtering in Data Provider",
                    "Library State Management",
                ),
                title="Persistent Global Library Filtering",
                intro=(
                    "Library selection is one cross-layer contract spanning the global UI, "
                    "persisted Redux state, and the filters attached to content queries."
                ),
            ),
            RequirementFusion(
                source_titles=(
                    "Library Selection Input Component",
                    "User-Library Assignment in User Forms",
                    "User Form Validation",
                ),
                title="Library Assignment Inputs and User Validation",
                intro=(
                    "The reusable selection input, its user-form integration, and assignment "
                    "validation together define how administrators grant library access."
                ),
            ),
            RequirementFusion(
                source_titles=(
                    "Library Management Pages",
                    "Duration and Number Formatting Utilities",
                    "Library Edit Form Input Fix",
                ),
                title="Library Administration and Statistics Presentation",
                intro=(
                    "Library administration must provide complete CRUD pages while presenting "
                    "read-only statistics with consistent formatting and appropriate controls."
                ),
            ),
        ),
        verification=(
            "Follow representative library data through ingestion and presentation. Cover "
            "DSF, WavPack, and WMA artwork, multi-value WMA tags, empty or invalid split "
            "lists, and mixed valid or invalid album participants; then validate the same "
            "library model at selector, form, data-provider, Redux, event-refresh, and "
            "administration boundaries."
        ),
        environment=(
            "- Use `github.com/onsi/ginkgo/v2/ginkgo` v2.23.4, as installed by the "
            "retained entry image. No Go toolchain upgrade is required."
        ),
    ),
    UnifiedSRS(
        workspace="nushell_nushell_0.106.0_0.108.0",
        retained_id="milestone_core_development.4",
        title="Nushell Core Semantics and Execution Safety",
        overview=(
            "Deliver a core-semantics release in which value access, stream execution, "
            "completion, presentation, and type annotations behave predictably across "
            "static and dynamic inputs. Custom values must participate in ordinary save "
            "and cell-path contracts, streaming commands must preserve flow and error "
            "state, file watching must work as either a callback or an event stream, and "
            "opt-in runtime annotation and pipe-failure checks must reject incompatible "
            "values or failed external pipelines without changing default behavior. The "
            "implementation must also keep SQLite paths, networking features, reedline, "
            "rusqlite, signatures, and test APIs coherent."
        ),
        affected_modules=(
            "nu-protocol value, type, signature, and custom-value contracts",
            "nu-engine, nu-parser, pipeline evaluation, and experimental options",
            "nu-command watch, stream, cell-path, save, completion, SQLite, and presentation behavior",
            "nu-cli/reedline, process exit tracking, network/TLS features, and test support",
        ),
        fragments=(
            Fragment(
                "milestone_core_development.4",
                "### FR1: Case-Insensitive Cell-Path Access Flag",
                "### FR2: Flatten Streams from `each` Closure",
                section_heading="Predictable value access and custom-value contracts",
                section_intro=(
                    "Built-in and custom values must honor the same access, casing, "
                    "optional-path, serialization, and compatible-type expectations."
                ),
            ),
            Fragment(
                "milestone_core_development.4",
                "### FR5: Save Custom Values to Disk",
                "### FR6: Optional and Casing in Custom Value Cell-Path Methods",
            ),
            Fragment(
                "milestone_core_development.4",
                "### FR6: Optional and Casing in Custom Value Cell-Path Methods",
                "### FR7: Default Terminal Color in Theme",
            ),
            Fragment(
                "milestone_G02_a647707",
                "### FR3: Bidirectional Glob and String Subtyping",
                "### FR4: Range Iteration Type Inference",
                replacements=(
                    (
                        "### FR3: Bidirectional Glob and String Subtyping",
                        "### FR3: Context-Specific Glob and String Compatibility",
                    ),
                    (
                        "**Problem**: Passing a string-typed variable to a function expecting a glob parameter fails with a conversion error, even though string and glob are semantically compatible for many use cases.",
                        "**Problem**: Glob and string values need runtime subtype compatibility without erasing the parser's stricter distinction at definition sites.",
                    ),
                    (
                        "- Update the `Type::is_subtype_of` method in `nu-protocol/src/ty.rs` to add bidirectional subtyping between `Type::Glob` and `Type::String`",
                        "- In `Type::is_subtype_of`, treat `Type::Glob` and `Type::String` as mutually compatible for runtime subtype checks",
                    ),
                ),
            ),
            Fragment(
                "milestone_core_development.4",
                "### FR2: Flatten Streams from `each` Closure",
                "### FR3: `each` No-Op on Single Null Input",
                section_heading="Streaming and evaluation correctness",
                section_intro=(
                    "Pipeline operations must stream when requested, handle empty values "
                    "consistently, clear invalidated metadata, and retain complete error state."
                ),
            ),
            Fragment(
                "milestone_core_development.4",
                "### FR3: `each` No-Op on Single Null Input",
                "### FR4: Static List Completions for Command Parameters",
            ),
            Fragment(
                "milestone_core_development.4",
                "### FR8: Reset Content Type for Partial Input Commands",
                "### FR9: Propagate Errors Through Nested `each` Chain",
            ),
            Fragment(
                "milestone_core_development.4",
                "### FR9: Propagate Errors Through Nested `each` Chain",
                "### FR10: Error Handler Cleanup on Loop Control in Try Blocks",
            ),
            Fragment(
                "milestone_core_development.4",
                "### FR10: Error Handler Cleanup on Loop Control in Try Blocks",
                "## 3. Non-Functional Requirements",
            ),
            Fragment(
                "milestone_G02_a647707",
                "### FR4: Range Iteration Type Inference",
                "# Environment Dependency Changes",
            ),
            Fragment(
                "milestone_core_development.4",
                "### FR4: Static List Completions for Command Parameters",
                "### FR5: Save Custom Values to Disk",
                section_heading="Completion and terminal presentation",
                section_intro=(
                    "Interactive guidance and default rendering must remain useful across "
                    "command signatures and terminal color schemes."
                ),
            ),
            Fragment(
                "milestone_core_development.4",
                "### FR7: Default Terminal Color in Theme",
                "### FR8: Reset Content Type for Partial Input Commands",
            ),
            Fragment(
                "milestone_G02_a647707",
                "### FR1: Experimental Option for Runtime Type Enforcement",
                "### FR2: Runtime Type Mismatch Produces Conversion Error",
                section_heading="Optional runtime annotation enforcement",
                section_intro=(
                    "When explicitly enabled, declared variable types become runtime "
                    "contracts and mismatches produce the normal conversion diagnostics."
                ),
            ),
            Fragment(
                "milestone_G02_a647707",
                "### FR2: Runtime Type Mismatch Produces Conversion Error",
                "### FR3: Bidirectional Glob and String Subtyping",
            ),
            Fragment(
                "milestone_core_development.4",
                "## 3. Non-Functional Requirements",
                "# Environment Dependency Changes",
            ),
        ),
        fusions=(
            RequirementFusion(
                source_titles=(
                    "Case-Insensitive Cell-Path Access Flag",
                    "Optional and Casing in Custom Value Cell-Path Methods",
                ),
                title="Case-Insensitive and Optional Cell-Path Access",
                intro=(
                    "Cell-path flags and custom-value callbacks are one access contract: "
                    "optional and case-insensitive semantics must reach every built-in and "
                    "plugin-backed value consistently."
                ),
            ),
            RequirementFusion(
                source_titles=(
                    "Flatten Streams from `each` Closure",
                    "`each` No-Op on Single Null Input",
                    "Propagate Errors Through Nested `each` Chain",
                ),
                title="Robust `each` Streaming, Null, and Error Semantics",
                intro=(
                    "The `each` command must treat flattening, null input, and nested failures "
                    "as one coherent stream contract rather than losing values or errors at "
                    "pipeline boundaries."
                ),
            ),
            RequirementFusion(
                source_titles=(
                    "Experimental Option for Runtime Type Enforcement",
                    "Runtime Type Mismatch Produces Conversion Error",
                ),
                title="Optional Runtime Type Enforcement and Conversion Errors",
                intro=(
                    "Runtime annotation checking is an opt-in contract whose observable result "
                    "is a normal conversion error whenever a dynamic value violates its "
                    "declared type."
                ),
            ),
        ),
        verification=(
            "Exercise value access, custom-value saving, stream flow, metadata, nested "
            "errors, completion, and terminal rendering under their normal settings. Run "
            "the annotation cases with and without `enforce-runtime-annotations`, including "
            "glob/string and range behavior; run external-pipeline cases with and without "
            "`pipefail`; and exercise `watch` in callback and closure-free streaming modes. "
            "Also verify shell-relative SQLite paths and compile the retained dependency, "
            "network/TLS, signature, and test-helper APIs."
        ),
        environment=(
            "- Use the retained entry image's Rust 1.86-compatible checkout. The image "
            "applies symmetric START/END compatibility commits rather than requiring a "
            "Rust 1.88 toolchain.\n"
            "- The source transition updates `reedline` from 0.41.0 to 0.42.0 and "
            "`rusqlite` from 0.31 to 0.37; the retained image includes the compatibility "
            "adaptations needed to compile those APIs."
        ),
        patch_derived_sections=(
            PatchDerivedSection(
                heading="Observable filesystem and process streams",
                intro=(
                    "Long-running filesystem and external-process pipelines must expose "
                    "their events and terminal state as first-class pipeline data."
                ),
                requirements=(
                    PatchDerivedRequirement(
                        kind="FR",
                        title="Closure-Free `watch` Event Streams",
                        body=(
                            "**Requirements**:\n"
                            "- Keep the existing closure callback form of `watch`, but make the closure optional.\n"
                            "- Without a closure, return a cancellable stream of records with `operation`, `path`, and optional `new_path` fields for create, remove, write, and rename events.\n"
                            "- Apply recursive, glob, verbose, and debounce filtering consistently in both modes; retain `--debounce-ms` compatibility while accepting typed durations through `--debounce`.\n\n"
                            "**Acceptance**:\n"
                            "- A closure-free invocation can be piped through ordinary Nushell filters and stops cleanly on an interrupt.\n"
                            "- Rename records preserve both the original and destination paths, while non-rename records leave `new_path` empty."
                        ),
                    ),
                    PatchDerivedRequirement(
                        kind="FR",
                        title="Opt-In External Pipeline Failure Propagation",
                        body=(
                            "**Requirements**:\n"
                            "- Register an opt-in experimental option named `pipefail`.\n"
                            "- Track exit-status futures across pipeline construction, evaluation, closures, and command printing without changing behavior when the option is disabled.\n"
                            "- When enabled, report the rightmost failed external command and update `$env.LAST_EXIT_CODE` accordingly.\n\n"
                            "**Acceptance**:\n"
                            "- Pipelines with an earlier failing external command fail under `pipefail` even when a later command succeeds.\n"
                            "- The same pipelines retain the prior last-command behavior when `pipefail` is not enabled."
                        ),
                    ),
                ),
            ),
            PatchDerivedSection(
                heading="Path and build compatibility",
                intro=(
                    "Command paths and optional build features must resolve consistently "
                    "inside the shell's own execution context."
                ),
                requirements=(
                    PatchDerivedRequirement(
                        kind="FR",
                        title="Shell-Relative SQLite Output Paths",
                        body=(
                            "**Requirements**:\n"
                            "- Resolve relative `into sqlite` database paths against Nushell's current working directory while preserving the special in-memory database target.\n\n"
                            "**Acceptance**:\n"
                            "- Running `into sqlite relative.db` after changing Nushell's working directory writes to that directory, not to the host process's unrelated working directory."
                        ),
                    ),
                    PatchDerivedRequirement(
                        kind="NFR",
                        title="Network and TLS Feature Compatibility",
                        body=(
                            "The default build must include the network feature with rustls, "
                            "and both rustls and native-TLS configurations must compile through "
                            "the shared HTTP client and TLS configuration interface."
                        ),
                    ),
                ),
            ),
        ),
    ),
    UnifiedSRS(
        workspace="nushell_nushell_0.106.0_0.108.0",
        retained_id="milestone_core_development.2",
        title="Nushell Core Reliability and Observable Shell State",
        overview=(
            "Deliver a core-reliability release that makes language feedback earlier, "
            "interactive editing and completion more useful, filesystem behavior portable, "
            "command options more explicit, and live shell state easier to inspect. Invalid "
            "control flow, row conditions, malformed ranges, and non-UTF-8 units must fail "
            "safely before execution; string, search, and path commands gain precise "
            "operations; and overlays expose active state and scoped registration "
            "consistently. Existing valid behavior must remain stable, internal pipeline "
            "construction must be consistent, and platform-specific execution must behave "
            "predictably even when environment paths are absent."
        ),
        affected_modules=(
            "nu-parser, IR compilation, value operators, and type validation",
            "nu-engine and nu-cli completion, REPL, and PipelineData construction",
            "nu-command string, find, lookup, path, filesystem, commandline, and overlay behavior",
            "nu-path Windows device-path helpers and exports",
            "nu-protocol values, engine state, and overlay representation",
        ),
        fragments=(
            Fragment(
                "milestone_core_development.2",
                "### FR1: New String Comparison Operators",
                "### FR2: Local Variable Completion Support",
                section_heading="Language semantics and early feedback",
                section_intro=(
                    "Operators and condition or control-flow validation must communicate "
                    "intent precisely before an invalid pipeline reaches runtime."
                ),
            ),
            Fragment(
                "milestone_core_development.2",
                "### FR3: Compile-Time Loop Control Statement Validation",
                "### FR4: Row Condition Type Checking at Parse-Time",
            ),
            Fragment(
                "milestone_core_development.2",
                "### FR4: Row Condition Type Checking at Parse-Time",
                "### FR5: Immediate Command Execution via Commandline Edit",
            ),
            Fragment(
                "milestone_core_development.2",
                "### FR2: Local Variable Completion Support",
                "### FR3: Compile-Time Loop Control Statement Validation",
                section_heading="Interactive editing and completion",
                section_intro=(
                    "The active editing scope must supply relevant completions and allow "
                    "deliberate command-buffer execution and character counting."
                ),
            ),
            Fragment(
                "milestone_core_development.2",
                "### FR5: Immediate Command Execution via Commandline Edit",
                "### FR6: Case-Insensitive Filesystem Support for `path relative-to`",
            ),
            Fragment(
                "milestone_core_development.2",
                "### FR7: Add `--chars` Flag to `str length` Command",
                "## 3. Non-Functional Requirements",
            ),
            Fragment(
                "milestone_core_development.2",
                "### FR6: Case-Insensitive Filesystem Support for `path relative-to`",
                "### FR7: Add `--chars` Flag to `str length` Command",
                section_heading="Portable filesystem behavior",
                section_intro=(
                    "Path operations must respect host filesystem semantics without "
                    "changing case-sensitive behavior on platforms that rely on it."
                ),
            ),
            Fragment(
                "milestone_G04_ca0e961",
                "## Requirements",
                "# Environment Dependency Changes",
                section_heading="Observable overlay state",
                section_intro=(
                    "Session overlays must expose stable ordering and activity information "
                    "while resolving modules from the scope in which they are declared."
                ),
            ),
            Fragment(
                "milestone_core_development.2",
                "## 3. Non-Functional Requirements",
                "# Environment Dependency Changes",
            ),
        ),
        fusions=(
            RequirementFusion(
                source_titles=(
                    "Compile-Time Loop Control Statement Validation",
                    "Row Condition Type Checking at Parse-Time",
                ),
                title="Parse-Time Validation of Control Flow and Row Conditions",
                intro=(
                    "Invalid loop control and incompatible row conditions are both structural "
                    "language errors and must be rejected before execution begins."
                ),
            ),
            RequirementFusion(
                source_titles=(
                    "Return Table Format with Name and Active Status",
                    "Consistent Ordering of Overlays",
                ),
                title="Complete and Deterministic Overlay State Listing",
                intro=(
                    "The overlay table's schema, active status, hidden entries, and ordering "
                    "together define one stable introspection result."
                ),
            ),
        ),
        verification=(
            "Exercise operator results, parse-time failures, active-scope completion, REPL "
            "buffer acceptance, Unicode counting, stepped ranges, non-UTF-8 units, and "
            "case-sensitive or insensitive search and path cases. Verify const optional "
            "lookup, missing-PATH external execution, overlay visibility, ordering, and "
            "scoped registration alongside PipelineData call sites and Windows device-path "
            "handling, while preserving behavior for unaffected callers and platforms."
        ),
        environment="No environment dependency changes beyond the repository state at the milestone start.",
        patch_derived_sections=(
            PatchDerivedSection(
                heading="Parser and command robustness",
                intro=(
                    "Malformed input and option selection must produce deliberate shell "
                    "behavior instead of panics or implicit mode changes."
                ),
                requirements=(
                    PatchDerivedRequirement(
                        kind="FR",
                        title="Safe Stepped-Range and Non-UTF-8 Parsing",
                        body=(
                            "**Requirements**:\n"
                            "- Detect the `..=` range operator from its actual operator offset so stepped inclusive ranges cannot panic when earlier bytes contain similar characters.\n"
                            "- Reject non-UTF-8 unit tokens through the normal parse-failure path instead of lossy conversion.\n\n"
                            "**Acceptance**:\n"
                            "- Valid stepped inclusive ranges parse and execute without a bounds panic.\n"
                            "- A non-UTF-8 unit value fails safely and does not fabricate replacement characters."
                        ),
                    ),
                    PatchDerivedRequirement(
                        kind="FR",
                        title="Explicit `find` Case and Multiline Modes",
                        body=(
                            "**Requirements**:\n"
                            "- Make ordinary `find` searches case-sensitive unless `--ignore-case` is supplied, applying that flag consistently to literal and regex searches.\n"
                            "- Make `--multiline` preserve a multiline string as one value rather than splitting it into lines; reject that mode for byte-stream input with actionable guidance.\n"
                            "- Restrict `--dotall` to regex search.\n\n"
                            "**Acceptance**:\n"
                            "- Case and multiline results change only when their corresponding flags are present, and incompatible byte-stream or non-regex flag combinations return clear errors."
                        ),
                    ),
                    PatchDerivedRequirement(
                        kind="FR",
                        title="Consistent Optional Lookup and External Resolution",
                        body=(
                            "**Requirements**:\n"
                            "- Honor `get --optional` during constant evaluation as well as runtime evaluation.\n"
                            "- Do not fail external-command resolution merely because `$env.PATH` is absent; continue through the platform resolver so Windows command built-ins remain available.\n\n"
                            "**Acceptance**:\n"
                            "- Missing const cell paths return the documented optional result, and a missing PATH produces the command's real platform-level outcome rather than a preliminary PATH lookup error."
                        ),
                    ),
                ),
            ),
        ),
    ),
    UnifiedSRS(
        workspace="apache_dubbo_dubbo-3.3.3_dubbo-3.3.6",
        retained_id="M003.3",
        title="Complete Mutiny Reactive Streaming Support",
        overview=(
            "Provide an end-to-end Mutiny integration for Dubbo Triple streaming. A "
            "backpressure-aware publisher/subscriber layer must bridge Java Flow and "
            "Dubbo stream observers on both client and server. Call utilities then expose "
            "all four RPC shapes through Uni and Multi, method handlers connect generated "
            "stubs to service implementations, and the compiler provides the Mutiny "
            "generator entry point for those stubs. Cancellation, terminal signals, "
            "error conversion, lifecycle hooks, and generator selection must remain "
            "consistent across the complete stack."
        ),
        affected_modules=(
            "dubbo-plugin/dubbo-mutiny publisher/subscriber abstractions",
            "dubbo-plugin/dubbo-mutiny client/server call utilities and method handlers",
            "dubbo-plugin/dubbo-compiler Mutiny generator entry point",
        ),
        fragments=(
            Fragment(
                "M003.2",
                "## Functional Requirements",
                "## Test Acceptance Criteria",
                section_heading="Reactive stream foundation",
                section_intro=(
                    "Publisher and subscriber adapters provide the lifecycle, signal, and "
                    "backpressure contract shared by every RPC shape."
                ),
            ),
            Fragment(
                "M003.3",
                "## FR1: Mutiny Client Call Utilities",
                "## FR5: Proto File Package Name Handling",
                section_heading="RPC integration and generated APIs",
                section_intro=(
                    "Client and server utilities, method handlers, and generated stubs must "
                    "carry that reactive contract across the complete Triple service API."
                ),
                replacements=(
                    (
                        "- Properly convert exceptions to RPC status exceptions",
                        "- Convert failures on unary and server-streaming response paths through `TriRpcStatus`; client-streaming and bidirectional paths propagate the original throwable to the response observer while respecting cancellation",
                    ),
                    (
                        "- When the service function throws an exception, the response observer receives onError with a proper RPC status exception (convert throwable to status using the existing `TriRpcStatus` utility)",
                        "- Unary and server-streaming failures reach `onError` as `TriRpcStatus` exceptions; client-streaming and bidirectional failures reach `onError` as the original throwable, and cancelled observers receive no further signals",
                    ),
                    (
                        "- Provide Mustache template files following the naming pattern of existing templates (interface template and stub template)",
                        "- Select the existing `MutinyDubbo3TripleInterfaceStub.mustache` and `MutinyDubbo3TripleStub.mustache` templates; this task does not add or modify template files",
                    ),
                ),
            ),
        ),
        fusions=(
            RequirementFusion(
                source_titles=(
                    "Abstract Publisher Base Class for Mutiny Integration",
                    "Client-Side Publisher for Streaming Responses",
                    "Server-Side Publisher for Streaming Requests",
                ),
                title="Mutiny Publisher Contract Across Client and Server Streams",
                intro=(
                    "The shared publisher lifecycle and its client/server adapters form one "
                    "backpressure-aware bridge from Dubbo observers to downstream Mutiny "
                    "consumers."
                ),
            ),
            RequirementFusion(
                source_titles=(
                    "Abstract Subscriber Base Class for Mutiny Integration",
                    "Client-Side Subscriber for Streaming Requests",
                    "Server-Side Subscriber for Streaming Responses",
                ),
                title="Mutiny Subscriber Contract Across Client and Server Streams",
                intro=(
                    "The shared subscriber lifecycle and its client/server adapters form one "
                    "bridge from Mutiny producers to Dubbo request and response observers."
                ),
            ),
        ),
        verification=(
            "Use the Mutiny publisher, subscriber, client-call, server-call, and handler "
            "tests as one functional suite covering unary, server-streaming, client-"
            "streaming, and bidirectional calls. Verify that the compiler discovers the "
            "Mutiny generator, selects its interface and implementation templates, and "
            "emits separate Mutiny stub types."
        ),
        environment="No environment dependency changes beyond the repository state at the milestone start.",
        exclusions=(
            ObligationExclusion(
                milestone_id="M003.3",
                heading="FR5: Proto File Package Name Handling",
                reason=(
                    "The declared 5c0bd7f package-less-proto work is absent from the "
                    "canonical M003.2 START to M003.3 END semantic source patch."
                ),
                patch_evidence=(
                    "message.proto and MessageServiceTest.java are absent at both endpoints",
                    "the six template blobs are already identical at START and END",
                ),
            ),
            ObligationExclusion(
                milestone_id="M003.2",
                heading="Test Acceptance Criteria",
                reason=(
                    "The source acceptance block lists unrelated REST snapshot tests; the "
                    "merged contract instead uses logically composed Mutiny F2P/P2P tests."
                ),
                patch_evidence=(
                    "REST parameter-binding tests do not touch the Mutiny semantic patch",
                ),
            ),
        ),
    ),
)


DEFAULT_OUTPUT_ROOT = Path("SWE-Milestone-data-repartitioned")


def output_srs_path(output_root: Path, spec: UnifiedSRS) -> Path:
    """Return the existing canonical SRS path in the derived dataset."""
    return output_root / spec.workspace / "srs" / spec.retained_id / "SRS.md"


def source_srs(dataset: Path, spec: UnifiedSRS, milestone_id: str) -> str:
    path = dataset / spec.workspace / "srs" / milestone_id / "SRS.md"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def extract_fragment(markdown: str, fragment: Fragment) -> str:
    start = markdown.find(fragment.start_heading)
    if start < 0:
        raise ValueError(f"missing start heading {fragment.start_heading!r} in {fragment.milestone_id}")
    end = markdown.find(fragment.end_heading, start + len(fragment.start_heading))
    if end < 0:
        raise ValueError(f"missing end heading {fragment.end_heading!r} in {fragment.milestone_id}")
    rendered = markdown[start:end].strip()
    for old, new in fragment.replacements:
        if old not in rendered:
            raise ValueError(
                f"missing declared replacement source in {fragment.milestone_id}: {old!r}"
            )
        rendered = rendered.replace(old, new)
    return rendered


def normalize_requirements(fragments: list[tuple[Fragment, str]]) -> str:
    fr_counter = 0
    nfr_counter = 0
    rendered: list[str] = []
    for fragment_spec, fragment in fragments:
        current_requirement = False
        if fragment_spec.section_heading:
            rendered.append(f"### Functional Area: {fragment_spec.section_heading}")
            rendered.append("")
        if fragment_spec.section_intro:
            rendered.append(fragment_spec.section_intro)
            rendered.append("")
        lines = fragment.splitlines()
        first_heading = re.match(r"^#{1,6}\s+(.+)$", lines[0]) if lines else None
        if first_heading and re.fullmatch(r"(?:\d+\.\s+)?(?:Functional )?Requirements", first_heading.group(1), re.I):
            lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
        for line in lines:
            # Source documents use thematic breaks at their own boundaries.
            # Keeping those breaks between the copied fragments makes the
            # unified statement look pasted together, so omit them here.
            if re.fullmatch(r"\s*---+\s*", line):
                continue
            heading = re.match(r"^(#{1,6})\s+(.+)$", line)
            if not heading:
                rendered.append(line)
                continue
            text = heading.group(2)
            fr = re.match(r"FR\d+\s*:\s*(.+)$", text, re.I)
            nfr = re.match(r"NFR\d+\s*:\s*(.+)$", text, re.I)
            if fr:
                fr_counter += 1
                current_requirement = True
                rendered.append(f"### FR{fr_counter}: {fr.group(1)}")
            elif nfr:
                nfr_counter += 1
                current_requirement = True
                rendered.append(f"### NFR{nfr_counter}: {nfr.group(1)}")
            elif re.match(r"(?:\d+\.\s+)?(?:Non-Functional Requirements|Additional Requirements|Accompanying Changes|Compatibility Notes|Notes|Test Acceptance Criteria|Glossary)$", text, re.I):
                current_requirement = False
                clean = re.sub(r"^\d+\.\s+", "", text)
                rendered.append(f"## {clean}")
            else:
                rendered.append(("#### " if current_requirement else "### ") + text)
        rendered.append("")
    return "\n".join(rendered).strip()


REQUIREMENT_HEADING = re.compile(r"^###\s+(FR|NFR)\d+\s*:\s*(.+?)\s*$", re.M)
SECTION_HEADING = re.compile(r"^#{2,3}\s+.+$", re.M)
LABELED_SUBSECTION = re.compile(r"^\*\*([^*]+?)\*\*\s*:?[ \t]*(.*)$")


def requirement_blocks(markdown: str) -> list[dict[str, object]]:
    """Return numbered requirement spans from normalized Markdown."""

    matches = list(REQUIREMENT_HEADING.finditer(markdown))
    blocks: list[dict[str, object]] = []
    for index, match in enumerate(matches):
        candidates = [len(markdown)]
        if index + 1 < len(matches):
            candidates.append(matches[index + 1].start())
        next_section = SECTION_HEADING.search(markdown, match.end())
        if next_section:
            candidates.append(next_section.start())
        end = min(candidates)
        block_text = markdown[match.start():end].rstrip()
        body_start = block_text.find("\n")
        body = "" if body_start < 0 else block_text[body_start + 1 :].strip()
        blocks.append(
            {
                "kind": match.group(1).upper(),
                "title": match.group(2).strip(),
                "start": match.start(),
                "end": end,
                "body": body,
            }
        )
    return blocks


def split_labeled_subsections(body: str) -> list[tuple[str | None, str, list[str]]]:
    """Split a requirement body while retaining every non-label source line."""

    sections: list[tuple[str | None, str, list[str]]] = []
    key: str | None = None
    display = ""
    lines: list[str] = []

    def flush() -> None:
        nonlocal lines
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            sections.append((key, display, lines))
        lines = []

    for line in body.splitlines():
        match = LABELED_SUBSECTION.match(line.strip())
        if match:
            flush()
            display = match.group(1).strip().rstrip(":")
            key = display.casefold()
            remainder = match.group(2).strip()
            if remainder:
                lines.append(remainder)
        else:
            lines.append(line.rstrip())
    flush()
    return sections


def fuse_requirement_bodies(fusion: RequirementFusion, bodies: list[str]) -> str:
    """Coalesce subsection labels for a human-selected overlapping FR group."""

    order: list[str | None] = []
    display_by_key: dict[str | None, str] = {}
    content_by_key: dict[str | None, list[list[str]]] = {}
    for body in bodies:
        for key, display, lines in split_labeled_subsections(body):
            if key not in content_by_key:
                order.append(key)
                display_by_key[key] = display
                content_by_key[key] = []
            content_by_key[key].append(lines)

    rendered = [fusion.intro]
    for key in order:
        rendered.append("")
        if key is not None:
            rendered.append(f"**{display_by_key[key]}**:")
        chunks = content_by_key[key]
        for index, lines in enumerate(chunks):
            if index:
                rendered.append("")
            rendered.extend(lines)
    return "\n".join(rendered).strip()


def renumber_requirements(markdown: str) -> str:
    counters = {"FR": 0, "NFR": 0}

    def replace(match: re.Match[str]) -> str:
        kind = match.group(1).upper()
        counters[kind] += 1
        return f"### {kind}{counters[kind]}: {match.group(2).strip()}"

    return REQUIREMENT_HEADING.sub(replace, markdown)


def apply_requirement_fusions(
    markdown: str, fusions: tuple[RequirementFusion, ...]
) -> str:
    """Apply reviewed fusions to the direct normalized requirement merge."""

    for fusion in fusions:
        blocks = requirement_blocks(markdown)
        by_title = {str(block["title"]): block for block in blocks}
        if len(by_title) != len(blocks):
            raise ValueError("requirement titles must be unique before fusion")
        missing = [title for title in fusion.source_titles if title not in by_title]
        if missing:
            raise ValueError(f"missing fusion source titles for {fusion.title!r}: {missing}")
        selected = [by_title[title] for title in fusion.source_titles]
        kinds = {str(block["kind"]) for block in selected}
        if len(kinds) != 1:
            raise ValueError(f"fusion cannot mix FR and NFR blocks: {fusion.title!r}")
        first = min(selected, key=lambda block: int(block["start"]))
        body = fuse_requirement_bodies(
            fusion,
            [str(block["body"]) for block in selected],
        )
        replacement = f"### {next(iter(kinds))}0: {fusion.title}\n\n{body}\n\n"
        replacements: list[tuple[int, int, str]] = []
        for block in selected:
            text = replacement if block is first else ""
            replacements.append((int(block["start"]), int(block["end"]), text))
        for start, end, text in sorted(replacements, reverse=True):
            markdown = markdown[:start] + text + markdown[end:]
        markdown = re.sub(r"\n{4,}", "\n\n\n", markdown).strip()
    return renumber_requirements(markdown)


def requirements_summary(requirements: str) -> str:
    lines = []
    for index, match in enumerate(REQUIREMENT_HEADING.finditer(requirements), start=1):
        kind = match.group(1).upper()
        number = re.search(rf"{kind}(\d+)", match.group(0), flags=re.I)
        assert number is not None
        lines.append(
            f"{index}. **{kind}{number.group(1)}**: {match.group(2).strip()}"
        )
    if not lines:
        raise ValueError("unified SRS has no numbered requirements")
    return "\n".join(lines)


def render_patch_derived_sections(
    sections: tuple[PatchDerivedSection, ...],
) -> str:
    rendered: list[str] = []
    for section in sections:
        if not section.heading.strip() or not section.intro.strip():
            raise ValueError("patch-derived sections require a heading and intro")
        rendered.extend(
            [
                f"### Functional Area: {section.heading}",
                "",
                section.intro.strip(),
                "",
            ]
        )
        for requirement in section.requirements:
            kind = requirement.kind.upper()
            if kind not in {"FR", "NFR"}:
                raise ValueError(
                    f"unsupported patch-derived requirement kind: {requirement.kind!r}"
                )
            rendered.extend(
                [
                    f"### {kind}0: {requirement.title.strip()}",
                    "",
                    requirement.body.strip(),
                    "",
                ]
            )
    return "\n".join(rendered).strip()


def render(spec: UnifiedSRS, dataset: Path) -> str:
    fragments = [
        (
            fragment,
            extract_fragment(source_srs(dataset, spec, fragment.milestone_id), fragment),
        )
        for fragment in spec.fragments
    ]
    direct_requirements = normalize_requirements(fragments)
    patch_derived = render_patch_derived_sections(spec.patch_derived_sections)
    if patch_derived:
        direct_requirements = f"{direct_requirements}\n\n{patch_derived}"
    requirements = apply_requirement_fusions(direct_requirements, spec.fusions)
    summary = requirements_summary(requirements)
    modules = "\n".join(f"- {item}" for item in spec.affected_modules)
    environment = re.sub(r"^###\s+", "## ", spec.environment, flags=re.M)
    return (
        f"# Software Requirements Specification: {spec.title}\n\n"
        f"## Overview\n\n{spec.overview}\n\n"
        f"### Requirements Summary\n\n{summary}\n\n"
        f"### Affected Modules\n\n{modules}\n\n"
        f"## Functional Requirements\n\n{requirements}\n\n"
        f"## Verification Strategy\n\n{spec.verification}\n\n"
        f"# Environment Dependency Changes (relative to Base Env)\n\n{environment}\n"
    )


def overview_projection(markdown: str) -> str:
    marker = "## Overview"
    start = markdown.find(marker)
    if start < 0:
        raise ValueError("generated SRS has no Overview")
    rest = markdown[start + len(marker) :].lstrip()
    end = rest.find("\n## ")
    return (rest if end < 0 else rest[:end]).strip().replace("\n", " ")


def refresh_srs_projection(output_root: Path, spec: UnifiedSRS, markdown: str) -> None:
    """Refresh existing SRS-derived metadata without rebuilding the dataset."""

    repo_dir = output_root / spec.workspace
    csv_path = repo_dir / "milestones.csv"
    provenance_path = repo_dir / "merge_provenance" / f"{spec.retained_id}.json"
    if not csv_path.is_file() or not provenance_path.is_file():
        raise FileNotFoundError(
            f"missing canonical SRS projection artifacts for {spec.workspace}/{spec.retained_id}"
        )

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    retained_rows = [row for row in rows if row.get("id") == spec.retained_id]
    if len(retained_rows) != 1:
        raise ValueError(
            f"expected one milestones.csv row for {spec.workspace}/{spec.retained_id}"
        )
    if "mini_srs" in fieldnames:
        retained_rows[0]["mini_srs"] = overview_projection(markdown)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    problem = provenance.get("problem_statement")
    if not isinstance(problem, dict):
        raise ValueError(f"missing problem_statement provenance: {provenance_path}")
    expected_path = f"srs/{spec.retained_id}/SRS.md"
    if problem.get("path") != expected_path:
        raise ValueError(
            f"unexpected problem statement path in {provenance_path}: {problem.get('path')!r}"
        )
    problem["sha256"] = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    problem["review"] = (
        "manually unified and checked against the canonical merged START-to-END patch"
    )
    provenance_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("SWE-Milestone-data"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    for spec in SPECS:
        destination = output_srs_path(args.output_root, spec)
        if not destination.is_file():
            raise FileNotFoundError(
                f"refusing to create a second merge artifact; canonical SRS is missing: {destination}"
            )
        rendered = render(spec, args.dataset)
        destination.write_text(rendered, encoding="utf-8")
        refresh_srs_projection(args.output_root, spec, rendered)
        print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
