#!/usr/bin/env python3
"""Reproducibly audit the six manually unified milestone statements.

The audit establishes a transitive coverage proof:

1. every source FR/NFR, acceptance body, compatibility/additional/accompanying
   section, test-acceptance section, and glossary is fully contained in one of
   the declared source fragments;
2. every meaningful line from every source obligation remains in the unified
   output after declared boundary-wording replacements; heading renumbering and
   coalesced subsection labels are ignored, so human-selected FR fusions remain
   auditable without forcing pasted duplicate ``Problem``/``Acceptance`` labels;
3. every non-empty environment constraint has declared output evidence or an
   explicit patch-semantic exclusion;
4. the checked-in output is byte-for-byte equal to a fresh render and contains
   neither source IDs nor document-boundary language;
5. every merged statement is pinned to its materialized patch, change units,
   merge provenance, and logical F2P/P2P contract before human conclusions are
   published into the same canonical audit artifact.

The JSON result is written into the repartitioned dataset alongside the
generated SRS files, then both existing root-manifest aliases receive the same
updated coverage status, hash, and summary bytes.
Any failed assertion prevents a misleading PASS manifest from being written.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable

from build_merge_srs import (
    DEFAULT_OUTPUT_ROOT,
    SPECS,
    Fragment,
    ObligationExclusion,
    UnifiedSRS,
    extract_fragment,
    output_srs_path,
    render,
    requirements_summary,
    source_srs,
)


SPECIAL_OBLIGATION_HEADINGS = {
    "compatibility notes",
    "notes",
    "additional requirements",
    "accompanying changes",
    "test acceptance criteria",
    "glossary",
}

BOUNDARY_PHRASES = (
    "source milestone",
    "downstream milestone",
    "upstream milestone",
    "absorbed milestone",
    "retained milestone",
    "first task",
    "second task",
    "merged task",
    "combined task",
)

# These phrases came from earlier drafts that were structurally unified but
# still advertised the join or invented a dependency between unrelated source
# areas.  Keep them as regression guards; semantic cohesion is also manually
# reviewed after the reproducible coverage audit.
ARTIFICIAL_COHESION_PHRASES = (
    "two complementary compatibility areas",
    "remain independently testable within one client release",
    "complete the multi-library user experience and harden the scanner",
    "then make type annotations optionally enforceable at runtime",
    "the same release must",
    "overlay introspection must build on those foundations",
)

TEST_ROLES = ("fail_to_pass", "none_to_pass", "pass_to_pass")


# Every non-empty source environment bullet must be represented here.  Keeping
# the source bullet as the key makes additions to source SRS files fail closed.
# Evidence may intentionally consolidate or supersede sequential versions.
ENVIRONMENT_EVIDENCE: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {
    ("BurntSushi_ripgrep_14.1.1_15.0.0", "milestone_seed_5f5da48_1_sub-01"): {
        "- Rust upgraded to 1.88.0": ("Rust toolchain upgraded to 1.88.0",),
    },
    ("navidrome_navidrome_v0.57.0_v0.58.0", "milestone_003_sub-04"): {
        "- github.com/onsi/ginkgo/v2/ginkgo v2.23.4 added": (
            "`github.com/onsi/ginkgo/v2/ginkgo` v2.23.4",
            "retained entry image",
        ),
    },
    ("nushell_nushell_0.106.0_0.108.0", "milestone_core_development.4"): {
        "- `reedline`: `0.41.0` → `0.42.0`": ("`reedline` from 0.41.0 to 0.42.0",),
        "- `rusqlite`: `0.31` → `0.37`": ("`rusqlite` from 0.31 to 0.37",),
    },
}


ELEMENT_ENVIRONMENT_BULLETS = (
    "- serve@14.2.5 added",
    "- Playwright browser dependencies added (via `npx playwright install chromium --with-deps`):",
    "  - xvfb added",
    "  - libnss3 added",
    "  - libnspr4 added",
    "  - libasound2 added",
    "  - libatk1.0-0 added",
    "  - libatk-bridge2.0-0 added",
    "  - libatspi2.0-0 added",
    "  - libcups2 added",
    "  - libdbus-1-3 added",
    "  - libdrm2 added",
    "  - libgbm1 added",
    "  - libxcomposite1 added",
    "  - libxdamage1 added",
    "  - libxfixes3 added",
    "  - libxkbcommon0 added",
    "  - libxrandr2 added",
    "  - fonts-liberation added",
    "  - fonts-noto-color-emoji added",
    "  - fonts-freefont-ttf added",
    "  - fonts-ipafont-gothic added",
    "  - fonts-tlwg-loma-otf added",
    "  - fonts-unifont added",
    "  - fonts-wqy-zenhei added",
    "- Base image: element-hq_element-web_v1.11.95_v1.11.97/base:latest (derived from node:22-bookworm)",
)


# Environment constraints are audited independently from FR/NFR blocks.  A
# constraint may be removed only when the materialized outer semantic patch
# proves it is snapshot/container drift rather than work the task asks the
# solver to perform.  Exact source bullets remain the keys so source edits fail
# closed, just like ENVIRONMENT_EVIDENCE.
ENVIRONMENT_EXCLUSIONS: dict[
    tuple[str, str], dict[str, dict[str, Any]]
] = {
    ("element-hq_element-web_v1.11.95_v1.11.97", "feature_enhancements"): {
        bullet: {
            "reason": (
                "The materialized Element outer semantic patch contains only the "
                "declared CSS/TypeScript feature paths; package, browser-install, base-image, "
                "and system dependency changes are not part of that transition."
            ),
            "patch_evidence": (
                "patches/feature_enhancements/patch_manifest.json selects 13 CSS/TypeScript paths",
                "no package manifest, Dockerfile, or browser-install path is selected",
            ),
        }
        for bullet in ELEMENT_ENVIRONMENT_BULLETS
    },
    ("nushell_nushell_0.106.0_0.108.0", "milestone_core_development.4"): {
        "- Rust toolchain upgraded to 1.88.0 (via rust-toolchain.toml)": {
            "reason": (
                "The retained entry image is Rust 1.86-based and applies symmetric START/END "
                "compatibility commits; the raw 1.88 toolchain bump is not solver work in the "
                "reviewed semantic patch."
            ),
            "patch_evidence": (
                "dockerfiles/milestone_core_development.4/Dockerfile downgrades both states to Rust 1.86",
                "rust-toolchain.toml is outside the selected semantic projection",
            ),
        },
    },
    ("nushell_nushell_0.106.0_0.108.0", "milestone_G02_a647707"): {
        bullet: {
            "reason": (
                "The retained entry image supplies Rust 1.86 and normalizes both canonical "
                "states with audited environment compatibility commits; upgrading the compiler "
                "or Cargo is not part of the solver-facing semantic task."
            ),
            "patch_evidence": (
                "dockerfiles/milestone_core_development.4/Dockerfile pins base compatibility to Rust 1.86",
                "canonical state resolution strips ENV-PATCH commits before semantic diffing",
            ),
        }
        for bullet in (
            "- rustc upgraded to 1.88.0 (from 1.86.0)",
            "- cargo upgraded to 1.88.0 (from 1.86.0)",
        )
    },
    ("navidrome_navidrome_v0.57.0_v0.58.0", "milestone_004"): {
        "- go upgraded to 1.24.5": {
            "reason": (
                "The Go toolchain change is snapshot drift outside the materialized semantic patch."
            ),
            "patch_evidence": (
                "Dockerfile, Makefile, go.mod, and go.sum are excluded snapshot paths",
                "gold.patch contains only the 30 declared scanner/UI source paths",
            ),
        },
        "- github.com/onsi/ginkgo/v2/ginkgo v2.27.4 added (Ginkgo CLI)": {
            "reason": (
                "The Ginkgo CLI dependency is snapshot drift outside the materialized semantic patch."
            ),
            "patch_evidence": (
                "go.mod and go.sum are excluded snapshot paths",
                "gold.patch contains no environment or dependency file",
            ),
        },
    },
}

# Human conclusions live in code so regenerating the canonical audit does not
# erase them.  The artifact facts and hashes are always derived afresh by
# audit_materialized_patch_and_tests(); this catalog contains judgments only.
# Keep exactly one key for every UnifiedSRS in SPECS.
HUMAN_PATCH_TEST_REVIEWS: dict[tuple[str, str], dict[str, Any]] = {
    (
        "BurntSushi_ripgrep_14.1.1_15.0.0",
        "milestone_seed_5f5da48_1_sub-02",
    ): {
        "review_status": "COMPLETE",
        "curation_disposition": "REVISE",
        "requirement_decisions": [
            {
                "heading": "Overview, Requirements Summary, and Affected Modules",
                "decision": "REWRITE",
                "rationale": (
                    "The two parser changes form one glob-language compatibility task and are "
                    "presented without milestone-boundary language."
                ),
                "patch_evidence": [
                    "crates/globset/src/glob.rs",
                    "crates/ignore/src/gitignore.rs",
                    "crates/ignore/src/overrides.rs",
                ],
            },
            {
                "heading": "FR1: End-to-End Nested Alternation Semantics",
                "decision": "KEEP",
                "rationale": (
                    "Parser state, regex translation, and path-matching changes all support "
                    "nested brace alternations in the final patch."
                ),
                "patch_evidence": ["crates/globset/src/glob.rs"],
            },
            {
                "heading": "FR2: Maintain Proper Error Handling for Malformed Alternation Patterns",
                "decision": "KEEP",
                "rationale": (
                    "The parser continues to reject unmatched closing braces and unclosed "
                    "alternation groups while accepting structurally valid nesting."
                ),
                "patch_evidence": ["crates/globset/src/glob.rs"],
            },
            {
                "heading": "FR3: Configurable Recovery for Unclosed Character Classes",
                "decision": "REWRITE",
                "rationale": (
                    "Recovery literalizes only the unmatched opening bracket and then resumes "
                    "ordinary glob parsing; the source wording incorrectly literalized the full suffix."
                ),
                "patch_evidence": ["crates/globset/src/glob.rs"],
            },
            {
                "heading": "FR4: Caller-Specific Defaults for Gitignore and Override Globs",
                "decision": "KEEP",
                "rationale": (
                    "Gitignore enables recovery by default while override globs retain strict "
                    "behavior, and both builders expose the forwarding option."
                ),
                "patch_evidence": [
                    "crates/ignore/src/gitignore.rs",
                    "crates/ignore/src/overrides.rs",
                ],
            },
            {
                "heading": "Compatibility Notes",
                "decision": "REWRITE",
                "rationale": (
                    "NestedAlternates remains in the public error enum and is documented as "
                    "obsolete; the patch does not add a Rust deprecation attribute."
                ),
                "patch_evidence": ["crates/globset/src/glob.rs"],
            },
        ],
        "patch_assessment": {
            "semantic_alignment": "REVISE_STATEMENT",
            "notes": [
                "The direct entry START to exit END patch contains 257 changed source LOC and 43 change units.",
                "The source segment tree chain mismatches, so the reviewed direct outer transition is the patch authority.",
                "Every retained requirement is supported after the two precise wording corrections above.",
            ],
        },
        "test_assessment": {
            "f2p_relevance": "ALL_PUBLISHED_F2P_PATCH_RELEVANT",
            "p2p_role": "BROAD_REGRESSION_BASELINE_NOT_DIFFICULTY_SIGNAL",
            "missing_target_coverage": [
                "regression::r3127_gitignore_allow_unclosed_class lacks entry-START evidence and requires an endpoint rerun.",
                "The strict OverrideBuilder default is represented only by a non-common P2P candidate and needs focused endpoint evidence.",
            ],
            "excluded_or_irrelevant_tests": [
                "Nine new allow_unclosed_class tests are source N2P and remain excluded by the F2P/P2P-only merge policy."
            ],
            "recommend_endpoint_rerun": True,
        },
    },
    (
        "element-hq_element-web_v1.11.95_v1.11.97",
        "feature_enhancements",
    ): {
        "review_status": "COMPLETE",
        "curation_disposition": "REVISE",
        "requirement_decisions": [
            {
                "heading": "Overview, Requirements Summary, and Affected Modules",
                "decision": "REWRITE",
                "rationale": (
                    "Identity state, notification state, reply targeting, and room reporting "
                    "are expressed as one intent-preserving client task without source boundaries."
                ),
                "patch_evidence": [
                    "src/components/views/settings/encryption/ResetIdentityPanel.tsx",
                    "src/stores/notifications/RoomNotificationState.ts",
                    "src/components/views/rooms/SendMessageComposer.tsx",
                    "src/components/views/dialogs/ReportRoomDialog.tsx",
                ],
            },
            {
                "heading": "FR1: Guard Identity Reset and Forced Verification Transitions",
                "decision": "KEEP",
                "rationale": (
                    "The reset flow exposes pending state and the forced-verification flow "
                    "blocks escape paths until the required transition completes."
                ),
                "patch_evidence": [
                    "src/components/views/settings/encryption/ResetIdentityPanel.tsx",
                    "src/components/structures/MatrixChat.tsx",
                ],
            },
            {
                "heading": "FR2: Improve Room Notification State API",
                "decision": "REWRITE",
                "rationale": (
                    "Activity makes hasAnyNotificationOrActivity true only when feature_hidebold "
                    "is disabled; knocks and Notification-or-higher levels remain independently true."
                ),
                "patch_evidence": ["src/stores/notifications/RoomNotificationState.ts"],
            },
            {
                "heading": "FR3: Pinned Identity Change Dismissal Button Text",
                "decision": "KEEP",
                "rationale": "The warning action is relabelled to describe dismissal rather than verification.",
                "patch_evidence": ["src/components/views/settings/UserIdentityWarning.tsx"],
            },
            {
                "heading": "FR4: Remove Unintentional Mentions in Replies",
                "decision": "REWRITE",
                "rationale": (
                    "A reply starts with the original sender and mentions explicitly added in "
                    "the new reply; it does not inherit user IDs from the replied-to event, while "
                    "room-mention behavior is otherwise unchanged by this patch."
                ),
                "patch_evidence": ["src/components/views/rooms/SendMessageComposer.tsx"],
            },
            {
                "heading": "FR5: Add Room Reporting Dialog",
                "decision": "KEEP",
                "rationale": (
                    "The room summary entry point, dialog form, reason validation, optional "
                    "administrative message, and reporting request are all present."
                ),
                "patch_evidence": [
                    "src/components/views/rooms/RoomSummaryCard.tsx",
                    "src/components/views/dialogs/ReportRoomDialog.tsx",
                ],
            },
            {
                "heading": "KeyStoragePanel stylesheet import",
                "decision": "EXCLUDE",
                "rationale": (
                    "The single import is unrelated snapshot drift: no matching component or "
                    "stylesheet change belongs to the reviewed identity/room transition."
                ),
                "patch_evidence": [
                    "res/css/_components.pcss reviewed hunk e7ed8ed04787469ec673b1485626f50fc31f6e7c4e6ada8c056ab0534cf13c6e"
                ],
            },
            {
                "heading": "Environment Dependency Changes appendix",
                "decision": "EXCLUDE",
                "rationale": (
                    "serve, Playwright browser packages, system fonts, and the claimed base-image "
                    "change are absent from the semantic patch and retained entry image contract."
                ),
                "patch_evidence": [
                    "No package manifest, Dockerfile, or browser-install path is selected by the materialized patch."
                ],
            },
        ],
        "patch_assessment": {
            "semantic_alignment": "REVISE_PATCH_AND_STATEMENT",
            "notes": [
                "The source segment tree chain mismatches, so the reviewed direct outer transition is authoritative.",
                "One uniquely selected KeyStoragePanel CSS import is reverse-applied from canonical END and recorded as an audited hunk exclusion.",
                "The remaining patch is within the intended 100-1000 LOC interval.",
            ],
        },
        "test_assessment": {
            "f2p_relevance": "TWO_OUTER_TRANSITION_F2P_TESTS_REQUIRE_EXPLICIT_PROMOTION",
            "p2p_role": "BROAD_REGRESSION_BASELINE_NOT_DIFFICULTY_SIGNAL",
            "missing_target_coverage": [
                "Six RoomNotificationState candidates lack entry-START evidence and require endpoint rerun.",
                "Three ReportRoom tests are source N2P and require endpoint recovery rather than implicit promotion.",
                "The UserIdentityWarning label change has no focused direct F2P test."
            ],
            "excluded_or_irrelevant_tests": [
                "The two ResetIdentityPanel tests promoted in merge_plan.json are proven fail at entry START and pass at exit END.",
                "Broad outer-endpoint reclassification is rejected because it would add 33 unrelated snapshot F2P tests."
            ],
            "recommend_endpoint_rerun": True,
        },
    },
    (
        "navidrome_navidrome_v0.57.0_v0.58.0",
        "milestone_003_sub-04",
    ): {
        "review_status": "COMPLETE",
        "curation_disposition": "REVISE",
        "requirement_decisions": [
            {
                "heading": "FR1: Cover Art Extraction for Additional Audio Formats",
                "decision": "KEEP",
                "rationale": "The patch adds WMA, DSF, and WavPack embedded artwork detection.",
                "patch_evidence": ["adapters/taglib/taglib_wrapper.cpp"],
            },
            {
                "heading": "FR2: WMA Multi-Value Tag Parsing",
                "decision": "KEEP",
                "rationale": "The ASF attribute loop forwards every value instead of only the first.",
                "patch_evidence": ["adapters/taglib/taglib_wrapper.cpp"],
            },
            {
                "heading": "FR3: Misleading Custom Tag Split Configuration Warning",
                "decision": "KEEP",
                "rationale": "The warning is gated on a non-empty split list and regexp failures are warnings.",
                "patch_evidence": ["model/tag_mappings.go"],
            },
            {
                "heading": "FR4: Album Participant Foreign Key Constraint Errors",
                "decision": "KEEP",
                "rationale": "The SQL projection joins against artist IDs and silently omits invalid references.",
                "patch_evidence": ["persistence/sql_participations.go"],
            },
            {
                "heading": "FR5: Persistent Global Library Filtering",
                "decision": "KEEP",
                "rationale": "Selector, Redux persistence, hooks, and data-provider filtering all occur in the net patch.",
                "patch_evidence": [
                    "ui/src/common/LibrarySelector.jsx",
                    "ui/src/common/useLibrarySelection.js",
                    "ui/src/dataProvider/wrapperDataProvider.js",
                    "ui/src/reducers/libraryReducer.js",
                ],
            },
            {
                "heading": "FR6: Library Assignment Inputs and User Validation",
                "decision": "REWRITE",
                "rationale": (
                    "Validation is enforced by UserEdit; UserCreate intentionally uses optional/default "
                    "library assignment and a no-op form validator."
                ),
                "patch_evidence": [
                    "ui/src/user/UserCreate.jsx",
                    "ui/src/user/UserEdit.jsx",
                    "ui/src/user/userValidation.js",
                ],
            },
            {
                "heading": "FR7: Library Administration and Statistics Presentation",
                "decision": "KEEP",
                "rationale": "CRUD pages, protected primary-library controls, and formatting helpers are present.",
                "patch_evidence": [
                    "ui/src/library/LibraryCreate.jsx",
                    "ui/src/library/LibraryEdit.jsx",
                    "ui/src/library/LibraryList.jsx",
                    "ui/src/utils/formatters.js",
                ],
            },
            {
                "heading": "Internationalization subsection",
                "decision": "EXCLUDE",
                "rationale": (
                    "ui/src/i18n/en.json is unchanged across the canonical outer transition; "
                    "adding translations is not work represented by gold.patch."
                ),
                "patch_evidence": [
                    "provenance warning: ui/src/i18n/en.json same_other_content_present_at_both_states"
                ],
            },
            {
                "heading": "Environment dependency appendix",
                "decision": "KEEP_WITH_CLARIFICATION",
                "rationale": (
                    "The retained entry Docker installs Ginkgo 2.23.4 and that runner contract "
                    "must remain visible. The downstream Go 1.24.5 and Ginkgo 2.27.4 claims are "
                    "snapshot drift outside the reviewed semantic patch."
                ),
                "patch_evidence": [
                    "dockerfiles/milestone_003_sub-04/Dockerfile installs Ginkgo 2.23.4",
                    "Dockerfile, Makefile, go.mod, and go.sum are outside gold.patch",
                ],
            },
        ],
        "patch_assessment": {
            "semantic_alignment": "REVISE_STATEMENT",
            "notes": [
                "The outer patch is authoritative because the source segment tree chain mismatches.",
                "The 1649 LOC net patch exceeds the intended 100-1000 LOC difficulty interval.",
            ],
        },
        "test_assessment": {
            "f2p_relevance": "ALL_PUBLISHED_F2P_PATCH_RELEVANT",
            "p2p_role": "BROAD_REGRESSION_BASELINE_NOT_DIFFICULTY_SIGNAL",
            "missing_target_coverage": [
                "DSF/WavPack/WMA cover detection",
                "empty/invalid/valid custom-tag split warnings",
                "library selector, assignment, administration, SSE, and display behavior",
            ],
            "excluded_or_irrelevant_tests": [
                "81 source UI none-to-pass tests are excluded by the merged F2P/P2P-only policy"
            ],
            "recommend_endpoint_rerun": True,
        },
    },
    (
        "nushell_nushell_0.106.0_0.108.0",
        "milestone_core_development.4",
    ): {
        "review_status": "COMPLETE",
        "curation_disposition": "REVISE",
        "requirement_decisions": [
            {
                "heading": "Overview, Requirements Summary, and Affected Modules",
                "decision": "REWRITE",
                "rationale": (
                    "The current framing covers the retained FRs but omits two large user-visible "
                    "parts of the authoritative net patch: closure-free watch event streams and "
                    "experimental pipefail propagation. It also omits the SQLite path and transport "
                    "compatibility work needed to describe the affected modules honestly."
                ),
                "patch_evidence": [
                    "crates/nu-command/src/filesystem/watch.rs",
                    "crates/nu-experimental/src/options/pipefail.rs",
                    "crates/nu-command/src/database/commands/into_sqlite.rs",
                    "crates/nu-command/src/network/tls",
                ],
            },
            {
                "heading": "FR1: Case-Insensitive and Optional Cell-Path Access",
                "decision": "KEEP",
                "rationale": (
                    "The get/select/reject flags and the optional/casing-aware custom-value "
                    "callbacks are all represented in gold.patch."
                ),
                "patch_evidence": [
                    "crates/nu-command/src/filters/get.rs",
                    "crates/nu-command/src/filters/select.rs",
                    "crates/nu-command/src/filters/reject.rs",
                    "crates/nu-protocol/src/value/custom_value.rs",
                ],
            },
            {
                "heading": "FR2: Save Custom Values to Disk",
                "decision": "KEEP",
                "rationale": "The CustomValue save contract and save-command dispatch occur in the net patch.",
                "patch_evidence": [
                    "crates/nu-protocol/src/value/custom_value.rs",
                    "crates/nu-command/src/filesystem/save.rs",
                ],
            },
            {
                "heading": "FR3: Bidirectional Glob and String Subtyping",
                "decision": "REWRITE",
                "rationale": (
                    "Type::is_subtype_of gains both Glob/String directions, but parser "
                    "type_compatible intentionally remains asymmetric. The requirement and "
                    "acceptance text must distinguish runtime assignment/call compatibility from "
                    "definition-site parser checks instead of promising unrestricted bidirectional "
                    "implicit conversion."
                ),
                "patch_evidence": [
                    "crates/nu-protocol/src/ty.rs",
                    "crates/nu-parser/src/type_check.rs",
                ],
            },
            {
                "heading": "FR4: Robust each Streaming, Null, and Error Semantics",
                "decision": "KEEP",
                "rationale": "Flattened streaming, single-null no-op behavior, and nested error propagation are present.",
                "patch_evidence": [
                    "crates/nu-command/src/filters/each.rs",
                    "crates/nu-engine/src/closure_eval.rs",
                ],
            },
            {
                "heading": "FR5: Reset Content Type for Partial Input Commands",
                "decision": "KEEP",
                "rationale": "The bytes/first/skip/take/substring paths clear invalidated content metadata.",
                "patch_evidence": [
                    "crates/nu-command/src/bytes/at.rs",
                    "crates/nu-command/src/filters/first.rs",
                    "crates/nu-command/src/filters/skip/skip_.rs",
                    "crates/nu-command/src/filters/take/take_.rs",
                    "crates/nu-command/src/strings/str_/substring.rs",
                ],
            },
            {
                "heading": "FR6: Error Handler Cleanup on Loop Control in Try Blocks",
                "decision": "KEEP",
                "rationale": "IR compilation emits error-handler cleanup before break and continue exits.",
                "patch_evidence": ["crates/nu-engine/src/compile/keyword.rs"],
            },
            {
                "heading": "FR7: Range Iteration Type Inference",
                "decision": "KEEP",
                "rationale": "Range iteration receives Number-compatible inference in the parser/compiler patch.",
                "patch_evidence": [
                    "crates/nu-parser/src/parse_keywords.rs",
                    "crates/nu-engine/src/compile/keyword.rs",
                ],
            },
            {
                "heading": "FR8: Static List Completions for Command Parameters",
                "decision": "KEEP",
                "rationale": "Static completion values, parser support, and completer consumption are all changed.",
                "patch_evidence": [
                    "crates/nu-cli/src/completions/static_completions.rs",
                    "crates/nu-parser/src/parse_shape_specs.rs",
                    "crates/nu-protocol/src/signature.rs",
                ],
            },
            {
                "heading": "FR9: Default Terminal Color in Theme",
                "decision": "KEEP",
                "rationale": (
                    "The default theme switches relevant values to terminal-default foreground; "
                    "the published find and table F2P cases are therefore relevant theme regressions."
                ),
                "patch_evidence": [
                    "crates/nu-color-config/src/style_computer.rs",
                    "crates/nu-command/src/viewers/table.rs",
                    "crates/nu-command/src/filters/find.rs",
                ],
            },
            {
                "heading": "FR10: Optional Runtime Type Enforcement and Conversion Errors",
                "decision": "KEEP",
                "rationale": "The option registration and assignment-time type checks are present in the exit patch.",
                "patch_evidence": [
                    "crates/nu-experimental/src/options/enforce_runtime_annotations.rs",
                    "crates/nu-engine/src/eval_ir.rs",
                    "crates/nu-protocol/src/ty.rs",
                ],
            },
            {
                "heading": "New FR: Closure-Free watch Event Streams and Debounce Compatibility",
                "decision": "ADD",
                "rationale": (
                    "gold.patch makes the watch closure optional, emits operation/path/new_path "
                    "event-table rows when it is absent, and preserves -d as a millisecond debounce "
                    "compatibility form. This is substantial user behavior missing from the SRS."
                ),
                "patch_evidence": ["crates/nu-command/src/filesystem/watch.rs"],
            },
            {
                "heading": "New FR: Experimental pipefail Exit Propagation",
                "decision": "ADD",
                "rationale": (
                    "The opt-in pipefail option propagates external-process exit futures through "
                    "pipeline, closure, and evaluator paths so the rightmost failed external command "
                    "determines LAST_EXIT_CODE. This large implementation is absent from the SRS."
                ),
                "patch_evidence": [
                    "crates/nu-experimental/src/options/pipefail.rs",
                    "crates/nu-engine/src/eval.rs",
                    "crates/nu-engine/src/eval_ir.rs",
                    "crates/nu-protocol/src/pipeline/pipeline_data.rs",
                    "crates/nu-protocol/src/process/child.rs",
                ],
            },
            {
                "heading": "New Supporting Requirement: SQLite, Duration, and Network Compatibility",
                "decision": "ADD",
                "rationale": (
                    "The net patch resolves relative SQLite output paths against the shell working "
                    "directory, supplies Duration conversion used by watch, and carries network/native-"
                    "TLS feature compatibility. These can be fused into one supporting compatibility "
                    "section rather than presented as unrelated top-level tasks."
                ),
                "patch_evidence": [
                    "crates/nu-command/src/database/commands/into_sqlite.rs",
                    "crates/nu-protocol/src/value/from_value.rs",
                    "crates/nu-command/src/network/http/client.rs",
                    "crates/nu-command/src/network/tls/impl_native_tls.rs",
                ],
            },
            {
                "heading": "NFR1-NFR4: Reedline, Rusqlite, Signature, and Test-Helper Compatibility",
                "decision": "KEEP",
                "rationale": "The compatibility adaptations are present across source and Cargo manifests.",
                "patch_evidence": [
                    "crates/nu-cli/src/reedline_config.rs",
                    "crates/nu-command/src/database/values/sqlite.rs",
                    "crates/nu-protocol/src/signature.rs",
                    "crates/nu-test-support/src/macros.rs",
                ],
            },
            {
                "heading": "Environment Dependency Changes appendix",
                "decision": "REWRITE",
                "rationale": (
                    "Reedline and Rusqlite compatibility remain supporting source work, but the "
                    "rustc/cargo 1.88 toolchain file is outside gold.patch. The SIF START and END refs "
                    "carry Docker-only commits that downgrade Rust to 1.86 for base-image compatibility; "
                    "the canonical resolver strips those commits, so the solver must not reproduce them."
                ),
                "patch_evidence": [
                    "rust-toolchain.toml is not a selected semantic gold-patch path",
                    "[ENV-PATCH] Downgrade Rust version from 1.88.0 to 1.86.0 for base image compatibility",
                    "crates/nu-cli/Cargo.toml",
                    "crates/nu-command/Cargo.toml",
                ],
            },
        ],
        "patch_assessment": {
            "semantic_alignment": "REVISE_STATEMENT",
            "notes": [
                "The direct canonical entry START to exit END patch is exact on the declared semantic paths; the core.4 END to G02 START tree chain mismatches, so segment concatenation is provenance only.",
                "The net patch contains 637 change units and 3907 source LOC, far above the intended 100-1000 LOC difficulty interval and requiring later partitioning after merge.",
                "Of 166 declared semantic paths, 164 change; the two plugin example helper scripts are unchanged at both canonical endpoints and must not become solver obligations.",
                "The entry Docker source is docker://hyd2apse/nushell:milestone_core_development.4-v0.9 and the local SIF is the milestone_core_development.4 image selected by merge policy.",
                "The SIF START tag points to 13 test/environment compatibility commits after canonical START, and the source END tag has seven such commits; canonical resolution strips them while preserving exact source transition trees.",
                "Every requested original current/adjacent commit inspected in the SIF exists; G02 START and END require no compatibility stripping, so the observed drift is tag/environment construction rather than a wrong image.",
            ],
        },
        "test_assessment": {
            "f2p_relevance": "PATCH_RELEVANT_WITH_SUPPORTING_REGRESSION_CASES_AND_COVERAGE_GAPS",
            "p2p_role": "BROAD_REGRESSION_BASELINE_NOT_DIFFICULTY_SIGNAL",
            "missing_target_coverage": [
                "The six pipefail behavior cases are source N2P and excluded from the merged contract, leaving the new major pipefail FR without direct F2P coverage.",
                "command_watch_with_filecompletion does not exercise closure-free event-table streaming or -d debounce behavior.",
                "into_sqlite::test_auto_conversion does not establish shell-cwd-relative path resolution.",
                "The glob/string case was converted from source F2P to merged P2P and does not directly cover both runtime subtyping and asymmetric parser contexts.",
                "The two runtime-enforcement F2P cases have unresolved outer evidence because the merged entry endpoint was not observed.",
            ],
            "excluded_or_irrelevant_tests": [
                "shell::pipeline::commands::external::pipefail_feature::case_1 through case_6 are patch-relevant but excluded N2P and require repaired endpoint evidence.",
                "repl::test_custom_commands::dont_allow_implicit_casting_between_glob_and_string was converted from F2P to P2P and cannot be the only FR3 acceptance evidence.",
                "plugins::nu_plugin_nu_example::call, help-completer, and SQLite auto-conversion are supporting regressions rather than direct coverage of every stated behavior.",
                "The find and table F2P cases are not irrelevant: their ANSI 37-to-39 expectations directly exercise FR9 default-theme behavior.",
            ],
            "recommend_endpoint_rerun": True,
        },
    },
    (
        "nushell_nushell_0.106.0_0.108.0",
        "milestone_core_development.2",
    ): {
        "review_status": "COMPLETE",
        "curation_disposition": "REVISE",
        "requirement_decisions": [
            {
                "heading": "Overview, Requirements Summary, and Affected Modules",
                "decision": "REWRITE",
                "rationale": (
                    "The existing framing accurately covers the named FRs but omits parser safety, "
                    "find/get behavior, shell diagnostics, and several user-visible compatibility "
                    "changes that are present in the authoritative net patch."
                ),
                "patch_evidence": [
                    "crates/nu-parser/src/parser.rs",
                    "crates/nu-command/src/filters/find.rs",
                    "crates/nu-command/src/filters/get.rs",
                    "crates/nu-command/src/system/run_external.rs",
                ],
            },
            {
                "heading": "FR1: New String Comparison Operators",
                "decision": "KEEP",
                "rationale": (
                    "Operator AST/value/help/completion support is in gold.patch, although the four "
                    "operator-completion endpoint tests remain red and must not be claimed as passing."
                ),
                "patch_evidence": [
                    "crates/nu-protocol/src/ast/operator.rs",
                    "crates/nu-protocol/src/value/mod.rs",
                    "crates/nu-cli/src/completions/operator_completions.rs",
                    "crates/nu-command/src/help/help_operators.rs",
                ],
            },
            {
                "heading": "FR2: Parse-Time Validation of Control Flow and Row Conditions",
                "decision": "KEEP",
                "rationale": "Loop-control compilation checks and boolean row-condition validation are present.",
                "patch_evidence": [
                    "crates/nu-engine/src/compile/keyword.rs",
                    "crates/nu-parser/src/parser.rs",
                ],
            },
            {
                "heading": "FR3: Local Variable Completion Support",
                "decision": "KEEP",
                "rationale": "CLI/LSP scope recovery and typed variable completion changes occur in the net patch.",
                "patch_evidence": [
                    "crates/nu-cli/src/completions/variable_completions.rs",
                    "crates/nu-lsp/src/completion.rs",
                ],
            },
            {
                "heading": "FR4: Immediate Command Execution via Commandline Edit",
                "decision": "KEEP",
                "rationale": "The accept flag and REPL-state handling are directly changed.",
                "patch_evidence": [
                    "crates/nu-cli/src/commands/commandline/edit.rs",
                    "crates/nu-cli/src/repl.rs",
                    "crates/nu-protocol/src/engine/engine_state.rs",
                ],
            },
            {
                "heading": "FR5: Add --chars Flag to str length Command",
                "decision": "KEEP",
                "rationale": "Unicode-scalar counting and incompatible flag handling are present in gold.patch.",
                "patch_evidence": [
                    "crates/nu-command/src/strings/str_/length.rs",
                    "crates/nu-command/src/strings/mod.rs",
                ],
            },
            {
                "heading": "FR6: Case-Insensitive Filesystem Support for path relative-to",
                "decision": "KEEP",
                "rationale": "The platform gate and case-insensitive component fallback are directly implemented.",
                "patch_evidence": ["crates/nu-command/src/path/relative_to.rs"],
            },
            {
                "heading": "FR7: Complete and Deterministic Overlay State Listing",
                "decision": "KEEP",
                "rationale": "The table schema, active flag, hidden entries, and ordering are present.",
                "patch_evidence": [
                    "crates/nu-cmd-lang/src/core_commands/overlay/list.rs",
                    "crates/nu-cmd-lang/src/core_commands/hide.rs",
                ],
            },
            {
                "heading": "FR8: Fix Scoped Module Registration in overlay use",
                "decision": "KEEP",
                "rationale": "Scoped module lookup and quote handling are represented in the exit patch.",
                "patch_evidence": ["crates/nu-cmd-lang/src/core_commands/overlay/use_.rs"],
            },
            {
                "heading": "NFR3: PipelineData Constructor Refactoring",
                "decision": "KEEP",
                "rationale": "Constructor helpers and their cross-crate call-site migration dominate the patch and are exact.",
                "patch_evidence": [
                    "crates/nu-protocol/src/pipeline/pipeline_data.rs",
                    "cross-crate PipelineData constructor call sites in gold.patch",
                ],
            },
            {
                "heading": "Accompanying Change: Windows Device Path Handling",
                "decision": "KEEP",
                "rationale": "The helper/export and open/save/source/parser call sites all occur in gold.patch.",
                "patch_evidence": [
                    "crates/nu-path/src/helpers.rs",
                    "crates/nu-path/src/lib.rs",
                    "crates/nu-command/src/filesystem/open.rs",
                    "crates/nu-command/src/filesystem/save.rs",
                    "crates/nu-parser/src/parse_keywords.rs",
                ],
            },
            {
                "heading": "New FR: Parser Robustness for Ranges and Non-UTF8 Input",
                "decision": "ADD",
                "rationale": (
                    "The net patch prevents malformed stepped inclusive ranges from panicking and "
                    "makes non-UTF8 unit parsing fail safely. The published range and non-UTF8 F2P "
                    "cases directly require this behavior, but the SRS currently omits it."
                ),
                "patch_evidence": [
                    "crates/nu-parser/src/parser.rs",
                    "crates/nu-parser/src/type_check.rs",
                ],
            },
            {
                "heading": "New FR: find and Constant get Query Semantics",
                "decision": "ADD",
                "rationale": (
                    "gold.patch changes find --ignore-case/--multiline behavior and makes const get "
                    "honor --optional; these user-visible query semantics need one natural command-"
                    "behavior section."
                ),
                "patch_evidence": [
                    "crates/nu-command/src/filters/find.rs",
                    "crates/nu-command/src/filters/get.rs",
                ],
            },
            {
                "heading": "New FR: External Command Discovery Diagnostics",
                "decision": "ADD",
                "rationale": (
                    "The patch improves missing-PATH diagnostics and Windows CMD built-in handling, "
                    "which is observable shell execution behavior not covered by the current statement."
                ),
                "patch_evidence": ["crates/nu-command/src/system/run_external.rs"],
            },
            {
                "heading": "New Supporting Requirement: Output, Menu, Help, and Conversion Robustness",
                "decision": "ADD",
                "rationale": (
                    "Table border/index coloring, columnar-menu traversal, UTF-8 exploration, help "
                    "trimming, Duration FromValue, signature flag parsing, and gstat commit-ID "
                    "formatting are meaningful final-patch behavior. They should be fused into a "
                    "supporting compatibility section rather than expanded into unrelated FRs."
                ),
                "patch_evidence": [
                    "crates/nu-command/src/viewers/table.rs",
                    "crates/nu-cli/src/reedline_config.rs",
                    "crates/nu-explore/src/explore.rs",
                    "crates/nu-command/src/help/help_.rs",
                    "crates/nu-protocol/src/value/mod.rs",
                    "crates/nu_plugin_gstat/src/gstat.rs",
                ],
            },
            {
                "heading": "Environment Dependency Changes appendix",
                "decision": "KEEP_WITH_CLARIFICATION",
                "rationale": (
                    "Docker compatibility and injected-test commits after the milestone tags are "
                    "container construction, not solver work. The canonical net patch strips them, "
                    "so the statement should retain the no-extra-environment-obligation meaning "
                    "without implying that the SIF tag is byte-identical to canonical START."
                ),
                "patch_evidence": [
                    "[ENV-PATCH] Fix version mismatch and API compatibility for core_dev.2 START state",
                    "[ENV-PATCH] Fix version mismatch and API compatibility for core_dev.2 END state",
                    "canonical core.2 START and END state-resolution audit",
                ],
            },
        ],
        "patch_assessment": {
            "semantic_alignment": "REVISE_STATEMENT",
            "notes": [
                "The direct canonical entry START to exit END patch is exact on all 211 declared semantic paths; the core.2 END to G04 START tree chain mismatches, so intermediate segment diffs are diagnostics rather than the gold definition.",
                "The net patch contains 577 change units and 2312 source LOC, above the intended 100-1000 LOC interval and requiring later partitioning after merge.",
                "The existing named FRs are patch-backed, but parser robustness and several command behaviors present in the final patch are missing from the statement.",
                "The entry Docker source is docker://hyd2apse/nushell:milestone_core_development.2-v0.9 and the local SIF is the milestone_core_development.2 image selected by merge policy.",
                "The SIF core.2 START tag contains one environment compatibility commit plus one injected-test commit, and its END tag contains one environment compatibility commit; canonical resolution strips them and both source segment transitions then validate exactly.",
                "Every requested original current/adjacent commit inspected in the SIF exists; G04 START and END require no compatibility stripping, so the mismatch is snapshot continuity drift rather than use of the wrong image.",
            ],
        },
        "test_assessment": {
            "f2p_relevance": "THREE_PUBLISHED_F2P_UNSUPPORTED_BY_CORE2_PATCH",
            "p2p_role": "BROAD_REGRESSION_BASELINE_NOT_DIFFICULTY_SIGNAL",
            "missing_target_coverage": [
                "Four operator-completion candidates still fail at the merged exit despite FR1 source changes: assignment_operator_completions, cell_path_operator_completions, cellpath_assignment_operator_completions, and operator_completions.",
                "The three case-insensitive path relative-to tests are excluded source N2P, leaving FR6 without merged direct F2P evidence.",
                "str length --chars has no focused merged F2P acceptance test.",
                "Newly documented find/get, external-command diagnostic, and supporting output/menu/conversion behaviors need focused direct or explicit P2P evidence.",
                "Four valid F2P targets have unresolved merged-end evidence and require rerun: break_outside_loop, local_variable_completion, not_starts_with_operator_succeeds, and not_ends_with_operator_succeeds.",
            ],
            "excluded_or_irrelevant_tests": [
                "commands::try_::loop_nested_try_break_should_pop_error_handlers is unrelated to the core.2 outer patch and belongs with core.4 FR6.",
                "commands::try_::loop_try_break_should_pop_error_handlers is unrelated to the core.2 outer patch and belongs with core.4 FR6.",
                "commands::try_::loop_try_continue_should_pop_error_handlers is unrelated to the core.2 outer patch and belongs with core.4 FR6.",
                "The range case_1/case_4/case_5 and parse_non_utf8_fails tests are patch-direct and must be retained while their missing requirement text is added.",
                "The path relative-to N2P tests must be recovered/rerun rather than silently promoted into the merged F2P set.",
            ],
            "recommend_endpoint_rerun": True,
        },
    },
    (
        "apache_dubbo_dubbo-3.3.3_dubbo-3.3.6",
        "M003.3",
    ): {
        "review_status": "COMPLETE",
        "curation_disposition": "REVISE",
        "requirement_decisions": [
            {
                "heading": "FR1: Mutiny Publisher Contract Across Client and Server Streams",
                "decision": "KEEP",
                "rationale": "The abstract and client/server publisher adapters are all new in gold.patch.",
                "patch_evidence": [
                    "dubbo-plugin/dubbo-mutiny/src/main/java/org/apache/dubbo/mutiny/AbstractTripleMutinyPublisher.java",
                    "dubbo-plugin/dubbo-mutiny/src/main/java/org/apache/dubbo/mutiny/ClientTripleMutinyPublisher.java",
                    "dubbo-plugin/dubbo-mutiny/src/main/java/org/apache/dubbo/mutiny/ServerTripleMutinyPublisher.java",
                ],
            },
            {
                "heading": "FR2: Mutiny Subscriber Contract Across Client and Server Streams",
                "decision": "KEEP",
                "rationale": "The abstract and client/server subscriber adapters are all new in gold.patch.",
                "patch_evidence": [
                    "dubbo-plugin/dubbo-mutiny/src/main/java/org/apache/dubbo/mutiny/AbstractTripleMutinySubscriber.java",
                    "dubbo-plugin/dubbo-mutiny/src/main/java/org/apache/dubbo/mutiny/ClientTripleMutinySubscriber.java",
                    "dubbo-plugin/dubbo-mutiny/src/main/java/org/apache/dubbo/mutiny/ServerTripleMutinySubscriber.java",
                ],
            },
            {
                "heading": "FR3: Mutiny Client Call Utilities",
                "decision": "KEEP",
                "rationale": "MutinyClientCalls implements all four RPC shapes.",
                "patch_evidence": [
                    "dubbo-plugin/dubbo-mutiny/src/main/java/org/apache/dubbo/mutiny/calls/MutinyClientCalls.java"
                ],
            },
            {
                "heading": "FR4: Mutiny Server Call Utilities",
                "decision": "REWRITE",
                "rationale": (
                    "Only unary and server-streaming helpers normalize failures through TriRpcStatus; "
                    "client-streaming and bidi paths forward observer errors directly."
                ),
                "patch_evidence": [
                    "dubbo-plugin/dubbo-mutiny/src/main/java/org/apache/dubbo/mutiny/calls/MutinyServerCalls.java"
                ],
            },
            {
                "heading": "FR5: Mutiny Method Handlers",
                "decision": "KEEP",
                "rationale": "All four StubMethodHandler implementations are new in the net patch.",
                "patch_evidence": ["dubbo-plugin/dubbo-mutiny/src/main/java/org/apache/dubbo/mutiny/handler"],
            },
            {
                "heading": "FR6: Mutiny Code Generator",
                "decision": "REWRITE",
                "rationale": (
                    "The patch adds the generator entry point, while its two Mutiny templates already "
                    "exist unchanged at START and END and must be selected rather than created."
                ),
                "patch_evidence": [
                    "dubbo-plugin/dubbo-compiler/src/main/java/org/apache/dubbo/gen/tri/mutiny/MutinyDubbo3TripleGenerator.java",
                    "provenance warning: Mutiny templates already present at both states",
                ],
            },
            {
                "heading": "Proto File Package Name Handling",
                "decision": "EXCLUDE",
                "rationale": "message.proto and MessageServiceTest.java are absent from both canonical states.",
                "patch_evidence": [
                    "dubbo-demo/dubbo-demo-spring-boot-idl/dubbo-demo-spring-boot-idl-provider/src/main/proto/message.proto",
                    "dubbo-demo/dubbo-demo-spring-boot-idl/dubbo-demo-spring-boot-idl-provider/src/test/java/org/apache/dubbo/springboot/idl/demo/MessageServiceTest.java",
                ],
            },
            {
                "heading": "REST parameter-binding acceptance text",
                "decision": "EXCLUDE",
                "rationale": "REST snapshot tests do not exercise the Mutiny semantic patch.",
                "patch_evidence": ["M003.2 Test Acceptance Criteria semantic exclusion"],
            },
        ],
        "patch_assessment": {
            "semantic_alignment": "REVISE_STATEMENT",
            "notes": [
                "The source segment tree chain is exact.",
                "The 998 LOC net patch remains within the intended 100-1000 LOC interval.",
            ],
        },
        "test_assessment": {
            "f2p_relevance": "ALL_PUBLISHED_F2P_PATCH_RELEVANT",
            "p2p_role": "BROAD_REGRESSION_BASELINE_NOT_DIFFICULTY_SIGNAL",
            "missing_target_coverage": [
                "publisher/subscriber lifecycle and backpressure adapters",
                "ordinary packaged-proto Mutiny generator output",
                "client bidirectional call utility",
            ],
            "excluded_or_irrelevant_tests": [
                "package-less proto MessageServiceTest is correctly excluded",
                "12 patch-relevant publisher/subscriber tests were excluded as non-common P2P and require endpoint rerun",
            ],
            "recommend_endpoint_rerun": True,
        },
    },
}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return payload


def json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise AssertionError(f"artifact escapes output root: {path}") from exc


def validate_review_catalog(specs: Iterable[UnifiedSRS] = SPECS) -> None:
    expected = {(spec.workspace, spec.retained_id) for spec in specs}
    actual = set(HUMAN_PATCH_TEST_REVIEWS)
    if actual != expected:
        raise AssertionError(
            "human patch/test review catalog must cover exactly SPECS; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    for key, review in HUMAN_PATCH_TEST_REVIEWS.items():
        required = {
            "review_status",
            "curation_disposition",
            "requirement_decisions",
            "patch_assessment",
            "test_assessment",
        }
        missing = required - set(review)
        if missing:
            raise AssertionError(f"human review {key} lacks fields: {sorted(missing)}")
        if review["review_status"] != "COMPLETE":
            raise AssertionError(f"human review {key} is not complete")
        if review["curation_disposition"] not in {"KEEP", "REVISE", "EXCLUDE"}:
            raise AssertionError(f"human review {key} has invalid curation disposition")
        if "TODO" in json.dumps(review, ensure_ascii=False).upper():
            raise AssertionError(f"human review {key} still contains TODO text")
        decisions = review["requirement_decisions"]
        if not isinstance(decisions, list) or not decisions:
            raise AssertionError(
                f"human review {key} requirement_decisions must be a non-empty list"
            )
        for decision in decisions:
            if not isinstance(decision, dict):
                raise AssertionError(f"human review {key} has a malformed decision")
            required_decision_fields = {"heading", "decision", "rationale", "patch_evidence"}
            if required_decision_fields - set(decision):
                raise AssertionError(f"human review {key} has an incomplete decision")
            if decision["decision"] not in {
                "KEEP",
                "REWRITE",
                "ADD",
                "EXCLUDE",
                "KEEP_WITH_CLARIFICATION",
            }:
                raise AssertionError(f"human review {key} has an invalid decision action")
            if not all(
                isinstance(decision[field], str) and decision[field].strip()
                for field in ("heading", "rationale")
            ):
                raise AssertionError(f"human review {key} has an empty decision field")
            evidence = decision["patch_evidence"]
            if not isinstance(evidence, list) or not evidence or not all(
                isinstance(item, str) and item.strip() for item in evidence
            ):
                raise AssertionError(f"human review {key} decision lacks patch evidence")
        if not isinstance(review["patch_assessment"], dict) or not isinstance(
            review["test_assessment"], dict
        ):
            raise AssertionError(f"human review {key} assessments must be objects")
        patch_assessment = review["patch_assessment"]
        if not isinstance(patch_assessment.get("semantic_alignment"), str) or not isinstance(
            patch_assessment.get("notes"), list
        ) or not patch_assessment["notes"]:
            raise AssertionError(f"human review {key} patch assessment is incomplete")
        test_assessment = review["test_assessment"]
        for field in ("f2p_relevance", "p2p_role"):
            if not isinstance(test_assessment.get(field), str) or not test_assessment[field]:
                raise AssertionError(f"human review {key} test assessment lacks {field}")
        for field in ("missing_target_coverage", "excluded_or_irrelevant_tests"):
            values = test_assessment.get(field)
            if not isinstance(values, list) or not all(
                isinstance(item, str) and item.strip() for item in values
            ):
                raise AssertionError(f"human review {key} test assessment has invalid {field}")
        if not isinstance(test_assessment.get("recommend_endpoint_rerun"), bool):
            raise AssertionError(f"human review {key} lacks endpoint-rerun decision")


def audit_publishable_projection(
    semantic: dict[str, Any],
    *,
    artifact: Path,
) -> dict[str, Any]:
    """Verify exact or explicitly reviewed START-to-END semantic projection proof."""

    proof = semantic.get("combined_transition_validation")
    net_patch = semantic.get("net_patch")
    if not isinstance(proof, dict) or not isinstance(net_patch, dict):
        raise AssertionError(f"patch lacks projection proof: {artifact}")
    status = proof.get("status")
    exact_status = "exact_on_declared_semantic_paths"
    reviewed_status = "exact_on_declared_semantic_paths_after_reviewed_hunk_exclusions"
    proof_exclusions = proof.get("reviewed_hunk_exclusions") or []
    net_exclusions = net_patch.get("reviewed_hunk_exclusions") or []
    if status == exact_status:
        if proof_exclusions or net_exclusions:
            raise AssertionError(f"exact projection hides reviewed exclusions: {artifact}")
        return {"status": status, "reviewed_hunk_exclusions": []}
    if status != reviewed_status:
        raise AssertionError(f"patch has an unpublishable projection status: {artifact}")
    if not isinstance(proof_exclusions, list) or not proof_exclusions:
        raise AssertionError(f"reviewed projection has no hunk audit: {artifact}")
    if proof_exclusions != net_exclusions:
        raise AssertionError(f"reviewed hunk audits disagree: {artifact}")
    for item in proof_exclusions:
        if not isinstance(item, dict):
            raise AssertionError(f"reviewed hunk audit is malformed: {artifact}")
        for field in ("review_id", "reason", "path", "changed_line", "raw_hunk_sha256"):
            if not isinstance(item.get(field), str) or not item[field]:
                raise AssertionError(f"reviewed hunk audit lacks {field}: {artifact}")
        if re.fullmatch(r"[0-9a-f]{64}", item["raw_hunk_sha256"]) is None:
            raise AssertionError(f"reviewed hunk audit has an invalid SHA-256: {artifact}")
    for field in (
        "raw_semantic_patch_sha256",
        "reviewed_end_tree",
        "reviewed_semantic_projection_tree",
    ):
        if not proof.get(field) or proof.get(field) != net_patch.get(field):
            raise AssertionError(f"reviewed projection disagrees on {field}: {artifact}")
    return {"status": status, "reviewed_hunk_exclusions": proof_exclusions}


def audit_materialized_patch_and_tests(
    output_root: Path,
    spec: UnifiedSRS,
    review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one published merge and combine fresh facts with human judgment."""

    key = (spec.workspace, spec.retained_id)
    if review is None:
        review = HUMAN_PATCH_TEST_REVIEWS[key]
    review = copy.deepcopy(review)

    repo_dir = output_root / spec.workspace
    srs_path = output_srs_path(output_root, spec)
    patch_dir = repo_dir / "patches" / spec.retained_id
    patch_manifest_path = patch_dir / "patch_manifest.json"
    provenance_path = repo_dir / "merge_provenance" / f"{spec.retained_id}.json"
    for required_path in (srs_path, patch_manifest_path, provenance_path):
        if not required_path.is_file():
            raise FileNotFoundError(f"missing reviewed artifact: {required_path}")

    patch_manifest = read_json(patch_manifest_path)
    if patch_manifest.get("workspace") != spec.workspace:
        raise AssertionError(f"patch workspace mismatch: {patch_manifest_path}")
    if patch_manifest.get("retained_id") != spec.retained_id:
        raise AssertionError(f"patch retained ID mismatch: {patch_manifest_path}")
    if patch_manifest.get("materialization_status") != "materialized":
        raise AssertionError(f"patch is not materialized: {patch_manifest_path}")

    gold_name = patch_manifest.get("gold_patch_file")
    units_name = patch_manifest.get("change_units_file")
    if not isinstance(gold_name, str) or not gold_name:
        raise AssertionError(f"materialized patch lacks gold_patch_file: {patch_manifest_path}")
    if not isinstance(units_name, str) or not units_name:
        raise AssertionError(f"materialized patch lacks change_units_file: {patch_manifest_path}")
    gold_path = patch_dir / gold_name
    units_path = patch_dir / units_name
    if not gold_path.is_file() or not units_path.is_file():
        raise FileNotFoundError(f"materialized patch payload is incomplete: {patch_dir}")
    gold_sha256 = sha256_file(gold_path)
    units_sha256 = sha256_file(units_path)
    if patch_manifest.get("gold_patch_sha256") != gold_sha256:
        raise AssertionError(f"gold patch hash mismatch: {gold_path}")
    if patch_manifest.get("change_units_sha256") != units_sha256:
        raise AssertionError(f"change-unit hash mismatch: {units_path}")

    patch_manifest_sha256 = sha256_file(patch_manifest_path)
    provenance = read_json(provenance_path)
    if provenance.get("workspace") != spec.workspace:
        raise AssertionError(f"merge provenance workspace mismatch: {provenance_path}")
    if provenance.get("retained_id") != spec.retained_id:
        raise AssertionError(f"merge provenance retained ID mismatch: {provenance_path}")
    published_patch = provenance.get("patch_materialization")
    if not isinstance(published_patch, dict) or published_patch.get("status") != "materialized":
        raise AssertionError(f"merge provenance does not publish a materialized patch: {provenance_path}")
    if published_patch.get("manifest_sha256") != patch_manifest_sha256:
        raise AssertionError(f"merge provenance patch-manifest hash is stale: {provenance_path}")
    if published_patch.get("gold_patch_sha256") != gold_sha256:
        raise AssertionError(f"merge provenance gold-patch hash is stale: {provenance_path}")

    semantic = patch_manifest.get("semantic_materialization")
    if not isinstance(semantic, dict):
        raise AssertionError(f"patch lacks semantic materialization proof: {patch_manifest_path}")
    total_changed_loc = semantic.get("total_changed_loc")
    if not isinstance(total_changed_loc, int) or total_changed_loc < 0:
        raise AssertionError(f"invalid semantic LOC: {patch_manifest_path}")
    net_patch = semantic.get("net_patch")
    if not isinstance(net_patch, dict):
        raise AssertionError(f"patch lacks semantic net patch: {patch_manifest_path}")
    semantic_scope = net_patch.get("semantic_scope")
    if not isinstance(semantic_scope, dict):
        raise AssertionError(f"patch lacks semantic scope: {patch_manifest_path}")
    chain = semantic.get("segment_chain_validation")
    warnings = semantic.get("provenance_warnings")
    if not isinstance(chain, list) or not isinstance(warnings, list):
        raise AssertionError(f"patch semantic chain/warnings are malformed: {patch_manifest_path}")
    projection_proof = audit_publishable_projection(
        semantic,
        artifact=patch_manifest_path,
    )
    chain_records: list[dict[str, Any]] = []
    for item in chain:
        if not isinstance(item, dict) or not isinstance(item.get("status"), str):
            raise AssertionError(f"malformed segment-chain record: {patch_manifest_path}")
        chain_records.append(
            {
                "source_milestone_id": item.get("source_milestone_id"),
                "target_milestone_id": item.get("target_milestone_id"),
                "status": item["status"],
            }
        )

    test_contract = provenance.get("test_contract")
    if not isinstance(test_contract, dict):
        raise AssertionError(f"merge provenance lacks test contract: {provenance_path}")
    effective_counts = test_contract.get("effective_counts")
    effective_tests = test_contract.get("effective_tests")
    if not isinstance(effective_counts, dict) or set(effective_counts) != set(TEST_ROLES):
        raise AssertionError(f"test contract counts are malformed: {provenance_path}")
    if not isinstance(effective_tests, dict):
        raise AssertionError(f"test contract identities are malformed: {provenance_path}")
    for role in TEST_ROLES:
        tests = effective_tests.get(role)
        if not isinstance(tests, list) or effective_counts[role] != len(tests):
            raise AssertionError(f"test contract {role} count mismatch: {provenance_path}")
    logical = test_contract.get("logical_composition")
    if not isinstance(logical, dict):
        raise AssertionError(f"test contract lacks logical composition: {provenance_path}")
    unresolved = logical.get("unresolved_outer_evidence", [])
    excluded_n2p = logical.get("excluded_n2p", [])
    excluded_p2p = logical.get("excluded_non_common_p2p", [])
    if not all(isinstance(value, list) for value in (unresolved, excluded_n2p, excluded_p2p)):
        raise AssertionError(f"test logical-composition lists are malformed: {provenance_path}")

    human_test = review["test_assessment"]
    recommend_rerun = human_test.get("recommend_endpoint_rerun")
    if not isinstance(recommend_rerun, bool):
        raise AssertionError(f"human review {key} lacks boolean recommend_endpoint_rerun")

    return {
        "review_schema_version": 1,
        "status": review["review_status"],
        "curation_disposition": review["curation_disposition"],
        "reviewed_artifacts": {
            "srs": {
                "path": relative_path(srs_path, output_root),
                "sha256": sha256_file(srs_path),
            },
            "patch_manifest": {
                "path": relative_path(patch_manifest_path, output_root),
                "sha256": patch_manifest_sha256,
            },
            "gold_patch": {
                "path": relative_path(gold_path, output_root),
                "sha256": gold_sha256,
            },
            "change_units": {
                "path": relative_path(units_path, output_root),
                "sha256": units_sha256,
            },
            "merge_provenance": {
                "path": relative_path(provenance_path, output_root),
                "sha256": sha256_file(provenance_path),
            },
        },
        "requirement_decisions": review["requirement_decisions"],
        "patch_assessment": {
            "evidence_validation": "PASS",
            "materialization_status": "materialized",
            "total_changed_loc": total_changed_loc,
            "segment_chain_validation": chain_records,
            "provenance_warning_count": len(warnings),
            "semantic_scope": {
                "policy": semantic_scope.get("policy"),
                "declared_path_count": len(semantic_scope.get("declared_paths", [])),
                "selected_path_count": len(semantic_scope.get("selected_paths", [])),
                "excluded_snapshot_path_count": len(
                    semantic_scope.get("excluded_snapshot_paths", [])
                ),
            },
            "projection_proof": projection_proof,
            "human_conclusion": review["patch_assessment"],
        },
        "test_assessment": {
            "evidence_validation": "PASS",
            "logical_policy": logical.get("policy"),
            "effective_counts": {role: effective_counts[role] for role in TEST_ROLES},
            "unresolved_outer_evidence_count": len(unresolved),
            "excluded_n2p_count": len(excluded_n2p),
            "excluded_non_common_p2p_count": len(excluded_p2p),
            "endpoint_rerun_required": bool(unresolved) or recommend_rerun,
            "human_conclusion": human_test,
        },
    }


def heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None
    return len(match.group(1)), match.group(2)


def clean_heading(text: str) -> str:
    text = re.sub(r"^\d+\.\s+", "", text.strip())
    return text


def canonicalize(markdown: str) -> str:
    """Normalize only formatting changes made by normalize_requirements()."""

    result: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line or re.fullmatch(r"\s*---+\s*", line):
            continue
        parsed = heading(line)
        if parsed:
            text = clean_heading(parsed[1])
            if re.fullmatch(r"(?:Functional )?Requirements", text, re.I):
                continue
            text = re.sub(r"^(FR|NFR)\d+(?=\s*:)", r"\1", text, flags=re.I)
            result.append(text)
        else:
            result.append(line)
    return "\n".join(result)


def obligation_content_lines(markdown: str) -> list[str]:
    """Normalize requirement content while ignoring merge-only presentation.

    The first heading identifies the source FR/NFR/special block and may be
    intentionally replaced by a fused title. Nested headings remain evidence.
    Bold subsection labels are presentation-only; any content on the same line
    remains mandatory.
    """

    lines: list[str] = []
    skipped_first_heading = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"---+", line):
            continue
        parsed = heading(line)
        if parsed:
            if not skipped_first_heading:
                skipped_first_heading = True
                continue
            text = clean_heading(parsed[1])
            text = re.sub(r"^(?:FR|NFR)\d+\s*:\s*", "", text, flags=re.I)
            lines.append(re.sub(r"\s+", " ", text).strip())
            continue
        label = re.match(r"^\*\*[^*]+?\*\*\s*:?[ \t]*(.*)$", line)
        if label:
            remainder = label.group(1).strip()
            if remainder:
                lines.append(re.sub(r"\s+", " ", remainder))
            continue
        lines.append(re.sub(r"\s+", " ", line))
    return lines


def fragment_bounds(markdown: str, fragment: Fragment) -> tuple[int, int]:
    start = markdown.find(fragment.start_heading)
    if start < 0:
        raise AssertionError(f"missing fragment start {fragment.start_heading!r}")
    end = markdown.find(fragment.end_heading, start + len(fragment.start_heading))
    if end < 0:
        raise AssertionError(f"missing fragment end {fragment.end_heading!r}")
    return start, end


def block_end(markdown: str, start: int, level: int) -> int:
    line_end = markdown.find("\n", start)
    if line_end < 0:
        return len(markdown)
    cursor = line_end + 1
    for match in re.finditer(r"^#{1,6}\s+.+$", markdown[cursor:], flags=re.M):
        absolute = cursor + match.start()
        parsed = heading(match.group(0))
        assert parsed is not None
        if parsed[0] <= level:
            return absolute
    return len(markdown)


def required_blocks(markdown: str) -> list[dict[str, Any]]:
    """Find every source obligation independently of configured fragments."""

    blocks: list[dict[str, Any]] = []
    for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", markdown, flags=re.M):
        parsed = heading(match.group(0))
        assert parsed is not None
        level, text = parsed
        clean = clean_heading(text)
        is_numbered = re.match(r"^(?:FR|NFR)\d+\s*:", clean, flags=re.I)
        is_special = clean.lower() in SPECIAL_OBLIGATION_HEADINGS
        if not (is_numbered or is_special):
            continue
        end = block_end(markdown, match.start(), level)
        body = markdown[match.start():end].strip()
        blocks.append(
            {
                "heading": clean,
                "start_offset": match.start(),
                "end_offset": end,
                "source_sha256": sha256(body),
                "acceptance_markers": body.count("**Acceptance**"),
            }
        )
    return blocks


def range_is_covered(start: int, end: int, ranges: Iterable[tuple[int, int]]) -> bool:
    cursor = start
    for range_start, range_end in sorted(ranges):
        if range_end <= cursor:
            continue
        if range_start > cursor:
            return False
        cursor = max(cursor, range_end)
        if cursor >= end:
            return True
    return cursor >= end


def environment_body(markdown: str) -> str:
    marker = "# Environment Dependency Changes (relative to Base Env)"
    start = markdown.find(marker)
    if start < 0:
        raise AssertionError("source SRS lacks environment section")
    return markdown[start + len(marker):].strip()


def environment_bullets(markdown: str) -> list[str]:
    body = environment_body(markdown)
    if body == "No changes detected.":
        return []
    return [line.rstrip() for line in body.splitlines() if re.match(r"^\s*-\s+", line)]


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def audit_spec(dataset: Path, output_root: Path, spec: UnifiedSRS) -> dict[str, Any]:
    output_path = output_srs_path(output_root, spec)
    actual = output_path.read_text(encoding="utf-8")
    expected = render(spec, dataset)
    if actual != expected:
        raise AssertionError(f"stale generated output: {output_path}")

    # One SRS plus the benchmark-standard environment appendix, without the
    # visual seams or vocabulary of a pasted pair of task descriptions.
    h1s = re.findall(r"^#(?!#)\s+(.+)$", actual, flags=re.M)
    expected_h1s = [
        f"Software Requirements Specification: {spec.title}",
        "Environment Dependency Changes (relative to Base Env)",
    ]
    if h1s != expected_h1s:
        raise AssertionError(f"{output_path}: unexpected H1 sequence {h1s}")
    for required_heading in (
        "## Overview",
        "### Requirements Summary",
        "### Affected Modules",
        "## Functional Requirements",
        "## Verification Strategy",
        "# Environment Dependency Changes (relative to Base Env)",
    ):
        if actual.count(required_heading) != 1:
            raise AssertionError(f"{output_path}: expected one {required_heading!r}")
    if re.search(r"^\s*---+\s*$", actual, flags=re.M):
        raise AssertionError(f"{output_path}: retained a source-document thematic break")
    lowered = actual.lower()
    for phrase in BOUNDARY_PHRASES:
        if phrase in lowered:
            raise AssertionError(f"{output_path}: contains boundary phrase {phrase!r}")
    for phrase in ARTIFICIAL_COHESION_PHRASES:
        if phrase in lowered:
            raise AssertionError(
                f"{output_path}: contains artificial cohesion phrase {phrase!r}"
            )

    authored_groups = [
        fragment.section_heading for fragment in spec.fragments if fragment.section_heading
    ] + [section.heading for section in spec.patch_derived_sections]
    if len(authored_groups) < 2:
        raise AssertionError(
            f"{output_path}: expected at least two concern-based requirement groups"
        )
    if len(authored_groups) != len(set(authored_groups)):
        raise AssertionError(f"{output_path}: duplicate concern-based requirement group")
    for group in authored_groups:
        heading_text = f"### Functional Area: {group}"
        if actual.count(heading_text) != 1:
            raise AssertionError(
                f"{output_path}: expected one functional-area heading {heading_text!r}"
            )

    source_ids = sorted(
        {fragment.milestone_id for fragment in spec.fragments}
        | {exclusion.milestone_id for exclusion in spec.exclusions}
    )
    for source_id in source_ids:
        if source_id in actual:
            raise AssertionError(f"{output_path}: exposes source milestone ID {source_id!r}")

    fr_numbers = [int(value) for value in re.findall(r"^### FR(\d+):", actual, flags=re.M)]
    if fr_numbers != list(range(1, len(fr_numbers) + 1)):
        raise AssertionError(f"{output_path}: non-contiguous FR numbering {fr_numbers}")
    nfr_numbers = [int(value) for value in re.findall(r"^### NFR(\d+):", actual, flags=re.M)]
    if nfr_numbers != list(range(1, len(nfr_numbers) + 1)):
        raise AssertionError(f"{output_path}: non-contiguous NFR numbering {nfr_numbers}")

    summary_start = actual.index("### Requirements Summary") + len(
        "### Requirements Summary"
    )
    summary_end = actual.index("### Affected Modules", summary_start)
    actual_summary = actual[summary_start:summary_end].strip()
    expected_summary = requirements_summary(actual)
    if actual_summary != expected_summary:
        raise AssertionError(
            f"{output_path}: Requirements Summary does not match final FR/NFR sequence"
        )

    final_requirement_titles = set(
        re.findall(r"^### (?:FR|NFR)\d+:\s*(.+)$", actual, flags=re.M)
    )
    fusion_records: list[dict[str, Any]] = []
    for fusion in spec.fusions:
        if fusion.title not in final_requirement_titles:
            raise AssertionError(
                f"{output_path}: missing fused requirement title {fusion.title!r}"
            )
        stale = sorted(set(fusion.source_titles) & final_requirement_titles)
        if stale:
            raise AssertionError(
                f"{output_path}: source requirements were not fused: {stale}"
            )
        fusion_records.append(
            {
                "output_title": fusion.title,
                "source_titles": list(fusion.source_titles),
                "intro": fusion.intro,
                "status": "PASS",
            }
        )

    output_content = set(obligation_content_lines(actual))
    fragment_records: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    exclusion_map = {
        (item.milestone_id, item.heading): item for item in spec.exclusions
    }
    if len(exclusion_map) != len(spec.exclusions):
        raise AssertionError(f"{output_path}: duplicate semantic exclusion")
    observed_exclusions: set[tuple[str, str]] = set()
    for source_id in source_ids:
        markdown = source_srs(dataset, spec, source_id)
        source_fragments = [fragment for fragment in spec.fragments if fragment.milestone_id == source_id]
        ranges = [fragment_bounds(markdown, fragment) for fragment in source_fragments]
        blocks = required_blocks(markdown)
        for block in blocks:
            exclusion = exclusion_map.get((source_id, block["heading"]))
            if exclusion is not None:
                block["covered"] = False
                block["source_line"] = line_number(markdown, block["start_offset"])
                block["content_line_count"] = len(
                    obligation_content_lines(
                        markdown[block["start_offset"] : block["end_offset"]]
                    )
                )
                block["content_line_coverage"] = False
                block["semantic_exclusion"] = {
                    "reason": exclusion.reason,
                    "patch_evidence": list(exclusion.patch_evidence),
                }
                observed_exclusions.add((source_id, block["heading"]))
                continue
            covering_fragments = [
                fragment
                for fragment in source_fragments
                if range_is_covered(
                    block["start_offset"],
                    block["end_offset"],
                    [fragment_bounds(markdown, fragment)],
                )
            ]
            if not covering_fragments:
                raise AssertionError(
                    f"{spec.workspace}/{source_id}: uncovered source obligation {block['heading']!r}"
                )
            transformed_block = markdown[
                block["start_offset"] : block["end_offset"]
            ].strip()
            for fragment in covering_fragments:
                for old, new in fragment.replacements:
                    transformed_block = transformed_block.replace(old, new)
            required_lines = obligation_content_lines(transformed_block)
            missing_lines = sorted(set(required_lines) - output_content)
            if missing_lines:
                raise AssertionError(
                    f"{output_path}: source obligation {source_id}/{block['heading']} "
                    f"lost content lines {missing_lines[:8]}"
                )
            block["covered"] = True
            block["source_line"] = line_number(markdown, block["start_offset"])
            block["content_line_count"] = len(required_lines)
            block["content_line_coverage"] = True

        stale_exclusions = sorted(
            key for key in exclusion_map if key[0] == source_id and key not in observed_exclusions
        )
        if stale_exclusions:
            raise AssertionError(
                f"{spec.workspace}/{source_id}: stale semantic exclusions {stale_exclusions}"
            )

        bullets = environment_bullets(markdown)
        evidence_map = ENVIRONMENT_EVIDENCE.get((spec.workspace, source_id), {})
        environment_exclusions = ENVIRONMENT_EXCLUSIONS.get(
            (spec.workspace, source_id), {}
        )
        overlap = sorted(set(evidence_map) & set(environment_exclusions))
        mapped_bullets = set(evidence_map) | set(environment_exclusions)
        missing_map = sorted(set(bullets) - mapped_bullets)
        stale_map = sorted(mapped_bullets - set(bullets))
        if overlap or missing_map or stale_map:
            raise AssertionError(
                f"{spec.workspace}/{source_id}: environment evidence map mismatch; "
                f"overlap={overlap}, unmapped={missing_map}, stale={stale_map}"
            )
        env_records: list[dict[str, Any]] = []
        output_environment = environment_body(actual)
        for bullet in bullets:
            exclusion = environment_exclusions.get(bullet)
            if exclusion is not None:
                reason = exclusion.get("reason")
                patch_evidence = exclusion.get("patch_evidence")
                if not isinstance(reason, str) or not reason.strip():
                    raise AssertionError(
                        f"{spec.workspace}/{source_id}: environment exclusion lacks reason: {bullet}"
                    )
                if not isinstance(patch_evidence, (list, tuple)) or not all(
                    isinstance(item, str) and item.strip() for item in patch_evidence
                ):
                    raise AssertionError(
                        f"{spec.workspace}/{source_id}: environment exclusion lacks evidence: {bullet}"
                    )
                env_records.append(
                    {
                        "source_constraint": bullet,
                        "output_evidence": [],
                        "covered": False,
                        "status": "EXCLUDED_BY_PATCH_SEMANTICS",
                        "semantic_exclusion": {
                            "reason": reason,
                            "patch_evidence": list(patch_evidence),
                        },
                    }
                )
                continue
            evidence = evidence_map[bullet]
            absent = [token for token in evidence if token not in output_environment]
            if absent:
                raise AssertionError(
                    f"{output_path}: environment constraint {bullet!r} lacks evidence {absent}"
                )
            env_records.append(
                {
                    "source_constraint": bullet,
                    "output_evidence": list(evidence),
                    "covered": True,
                    "status": "COVERED",
                }
            )

        source_records.append(
            {
                "source_id": source_id,
                "source_path": str(dataset / spec.workspace / "srs" / source_id / "SRS.md"),
                "source_sha256": sha256(markdown),
                "required_blocks": blocks,
                "environment": {
                    "no_changes": environment_body(markdown) == "No changes detected.",
                    "constraints": env_records,
                },
            }
        )

        for fragment in source_fragments:
            raw_start, raw_end = fragment_bounds(markdown, fragment)
            raw = markdown[raw_start:raw_end].strip()
            transformed = extract_fragment(markdown, fragment)
            fragment_records.append(
                {
                    "source_id": source_id,
                    "start_heading": fragment.start_heading,
                    "end_heading": fragment.end_heading,
                    "source_lines": [
                        line_number(markdown, raw_start),
                        line_number(markdown, raw_end - 1),
                    ],
                    "source_sha256": sha256(raw),
                    "transformed_sha256": sha256(transformed),
                    "declared_replacements": [list(pair) for pair in fragment.replacements],
                    "covered_source_obligations": [
                        block["heading"]
                        for block in required_blocks(markdown)
                        if range_is_covered(
                            block["start_offset"],
                            block["end_offset"],
                            [(raw_start, raw_end)],
                        )
                    ],
                    "coverage_mode": "declared-range; obligation content checked independently",
                }
            )

    return {
        "workspace": spec.workspace,
        "retained_id": spec.retained_id,
        "output_path": str(output_path),
        "output_sha256": sha256(actual),
        "natural_unified_document": {
            "benchmark_srs_h1_and_environment_appendix_h1": True,
            "single_document_sections": True,
            "contiguous_requirement_numbering": True,
            "requirements_summary_matches_final_numbering": True,
            "no_thematic_source_seams": True,
            "no_source_ids": True,
            "no_boundary_language": True,
            "concern_based_requirement_groups": authored_groups,
            "no_artificial_cohesion_language": True,
        },
        "fr_count": len(fr_numbers),
        "nfr_count": len(nfr_numbers),
        "requirement_fusions": fusion_records,
        "semantic_exclusions": [
            {
                "source_id": item.milestone_id,
                "heading": item.heading,
                "reason": item.reason,
                "patch_evidence": list(item.patch_evidence),
                "status": "EXCLUDED_BY_PATCH_SEMANTICS",
            }
            for item in spec.exclusions
        ],
        "source_documents": source_records,
        "fragment_proofs": fragment_records,
        "human_patch_test_review": audit_materialized_patch_and_tests(
            output_root, spec
        ),
        "status": "PASS",
    }


def prepare_root_manifest_sync(
    output_root: Path,
    audit_payload: dict[str, Any],
    audit_bytes: bytes,
) -> tuple[tuple[Path, Path], bytes]:
    """Build one root-manifest payload without writing or touching patch artifacts."""

    repartition_path = output_root / "REPARTITION_MANIFEST.json"
    alias_path = output_root / "merge_manifest.json"
    for path in (repartition_path, alias_path):
        if not path.is_file():
            raise FileNotFoundError(
                f"refusing to create a new root manifest; canonical file is missing: {path}"
            )
    if repartition_path.read_bytes() != alias_path.read_bytes():
        raise AssertionError("root manifests differ before coverage synchronization")

    root = read_json(repartition_path)
    if root.get("schema_version") != 1:
        raise AssertionError("root manifest schema_version must remain 1")
    if root.get("dataset_contract") != "swe_milestone_repartitioned_merge_v1":
        raise AssertionError("unexpected root dataset contract")
    operations = root.get("operations")
    if not isinstance(operations, list) or len(operations) != len(HUMAN_PATCH_TEST_REVIEWS):
        raise AssertionError("root manifest must contain the six reviewed operations")
    operation_keys = {
        (item.get("workspace"), item.get("retained_id"))
        for item in operations
        if isinstance(item, dict)
    }
    if operation_keys != set(HUMAN_PATCH_TEST_REVIEWS):
        raise AssertionError("root operations differ from the human-review catalog")
    materialization = root.get("patch_materialization")
    if not isinstance(materialization, dict) or materialization.get("status") != "complete":
        raise AssertionError("root manifest patch materialization is not complete")
    if materialization.get("materialized") != len(operations) or materialization.get(
        "total"
    ) != len(operations):
        raise AssertionError("root manifest materialized patch counts are stale")

    # Verify every operation's immutable patch references before reserializing
    # the root object.  This function never opens any patch artifact for write.
    for operation in operations:
        if operation.get("patch_materialization_status") != "materialized":
            raise AssertionError(f"root operation is not materialized: {operation}")
        patch_manifest_rel = operation.get("patch_manifest_file")
        gold_rel = operation.get("gold_patch_file")
        if not isinstance(patch_manifest_rel, str) or not isinstance(gold_rel, str):
            raise AssertionError("root operation lacks materialized patch paths")
        patch_manifest_path = output_root / patch_manifest_rel
        gold_path = output_root / gold_rel
        if not patch_manifest_path.is_file() or not gold_path.is_file():
            raise FileNotFoundError("root operation references missing patch artifacts")
        patch_manifest = read_json(patch_manifest_path)
        if patch_manifest.get("materialization_status") != "materialized":
            raise AssertionError(f"root operation patch manifest is pending: {patch_manifest_path}")
        gold_sha256 = sha256_file(gold_path)
        if patch_manifest.get("gold_patch_sha256") != gold_sha256:
            raise AssertionError(f"patch manifest gold hash mismatch: {patch_manifest_path}")
        if operation.get("gold_patch_sha256") != gold_sha256:
            raise AssertionError(f"root operation gold hash mismatch: {gold_path}")

    coverage = root.get("problem_statement_coverage")
    if not isinstance(coverage, dict):
        raise AssertionError("root manifest lacks problem_statement_coverage")
    if coverage.get("file") != "problem_statement_coverage_audit.json":
        raise AssertionError("root manifest points at a non-canonical coverage artifact")
    summary = audit_payload.get("summary")
    if not isinstance(summary, dict):
        raise AssertionError("audit payload lacks summary")
    root["problem_statement_coverage"] = {
        **coverage,
        "status": audit_payload.get("status"),
        "sha256": hashlib.sha256(audit_bytes).hexdigest(),
        "summary": copy.deepcopy(summary),
    }
    return (repartition_path, alias_path), json_bytes(root)


def atomic_replace_existing(path: Path, payload: bytes) -> None:
    """Atomically replace one canonical file without publishing another artifact."""

    if not path.is_file():
        raise FileNotFoundError(f"refusing to create missing canonical file: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        owned_descriptor = descriptor
        descriptor = -1
        with os.fdopen(owned_descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        try:
            directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        except (AttributeError, OSError):
            directory_descriptor = -1
        if directory_descriptor >= 0:
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path.exists():
            temporary_path.unlink()


def write_audit_and_sync_root_manifests(
    *,
    output_root: Path,
    audit_path: Path,
    audit_payload: dict[str, Any],
) -> None:
    """Publish the canonical audit and identical coverage pointers in both roots."""

    canonical_audit = output_root / "problem_statement_coverage_audit.json"
    if audit_path.resolve() != canonical_audit.resolve():
        raise AssertionError(
            f"refusing a second audit result path; expected {canonical_audit}, got {audit_path}"
        )
    if not audit_path.is_file():
        raise FileNotFoundError(
            f"refusing to create a second audit artifact; canonical manifest is missing: {audit_path}"
        )
    audit_serialized = json_bytes(audit_payload)
    root_paths, root_serialized = prepare_root_manifest_sync(
        output_root, audit_payload, audit_serialized
    )

    # Every individual publication is atomic.  Both root aliases receive the
    # exact same byte string, and all validation is complete before any replace.
    atomic_replace_existing(audit_path, audit_serialized)
    for root_path in root_paths:
        atomic_replace_existing(root_path, root_serialized)
    if root_paths[0].read_bytes() != root_paths[1].read_bytes():
        raise AssertionError("root manifests differ after coverage synchronization")
    if read_json(root_paths[0])["problem_statement_coverage"]["sha256"] != sha256_file(
        audit_path
    ):
        raise AssertionError("root manifest coverage hash is stale after synchronization")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("SWE-Milestone-data"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "problem_statement_coverage_audit.json",
    )
    args = parser.parse_args()

    validate_review_catalog()
    results = [audit_spec(args.dataset, args.output_root, spec) for spec in SPECS]
    required_blocks = sum(
        len(source["required_blocks"])
        for result in results
        for source in result["source_documents"]
    )
    acceptance_markers = sum(
        block["acceptance_markers"]
        for result in results
        for source in result["source_documents"]
        for block in source["required_blocks"]
    )
    environment_constraints = sum(
        len(source["environment"]["constraints"])
        for result in results
        for source in result["source_documents"]
    )
    environment_exclusions = sum(
        constraint.get("status") == "EXCLUDED_BY_PATCH_SEMANTICS"
        for result in results
        for source in result["source_documents"]
        for constraint in source["environment"]["constraints"]
    )
    completed_reviews = sum(
        result["human_patch_test_review"]["status"] == "COMPLETE"
        for result in results
    )
    endpoint_reruns_required = sum(
        bool(
            result["human_patch_test_review"]["test_assessment"][
                "endpoint_rerun_required"
            ]
        )
        for result in results
    )
    review_dispositions: dict[str, int] = {}
    for result in results:
        disposition = result["human_patch_test_review"]["curation_disposition"]
        review_dispositions[disposition] = review_dispositions.get(disposition, 0) + 1
    manifest = {
        "schema_version": 1,
        "status": "PASS",
        "audit_contract": {
            "requirement_coverage": "every source obligation is either covered by a declared fragment or explicitly excluded with patch-semantic evidence",
            "content_fidelity": "all non-excluded meaningful source obligation lines remain after declared wording, heading renumbering, and coalesced subsection-label transformations",
            "format": "every document uses Overview with Requirements Summary and Affected Modules followed by contiguous FR/NFR sections and the standard environment appendix",
            "human_fusion": "every declared overlapping source-title group maps to exactly one reviewed output requirement title and retains all source content lines",
            "environment_coverage": "every source environment bullet has checked output evidence or an explicit patch-semantic exclusion",
            "naturalness_guards": "one cohesive document with concern-based requirement groups and no source IDs, boundary language, artificial dependency claims, or thematic seams",
            "patch_test_human_review": "each merged statement is pinned to its materialized patch, change units, merge provenance, and logical F2P/P2P contract; human conclusions are code-backed and independently statused",
        },
        "summary": {
            "merged_statements": len(results),
            "source_documents": sum(len(result["source_documents"]) for result in results),
            "required_blocks": required_blocks,
            "semantic_exclusions": sum(
                len(result["semantic_exclusions"]) for result in results
            ),
            "acceptance_markers": acceptance_markers,
            "environment_constraints": environment_constraints,
            "environment_exclusions": environment_exclusions,
            "human_patch_test_reviews": len(results),
            "human_patch_test_reviews_complete": completed_reviews,
            "human_patch_test_reviews_pending": len(results) - completed_reviews,
            "materialized_patches_reviewed": len(results),
            "endpoint_reruns_required": endpoint_reruns_required,
            "review_dispositions": dict(sorted(review_dispositions.items())),
            "review_failures": 0,
            "failures": 0,
        },
        "results": results,
    }
    write_audit_and_sync_root_manifests(
        output_root=args.output_root,
        audit_path=args.manifest,
        audit_payload=manifest,
    )
    print(
        f"PASS: {len(results)} statements, {required_blocks} source obligation blocks, "
        f"{acceptance_markers} acceptance bodies, {environment_constraints} environment constraints"
    )
    print(args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
