#!/usr/bin/env python3
"""Rebuild Navidrome endpoint test states from the milestone DAG.

The runnable post-hoist trees remain authoritative for every non-test path
except generated protobuf and Wire closures.  Protobuf output is selected from
the endpoint's authoritative ``.proto`` source; Wire output is selected from a
complete non-test product-Go tree signature whose variants were generated
offline for all endpoints.  Globally-hoisted test files are replaced with a
causal test state:

* every root starts from the v0.57.0 test tree;
* a milestone applies only test-file deltas from its declared canonical
  commits;
* a child starts from the three-way union of all parent END test states;
* conflicting parent edits fail closed for human review.

The resulting endpoint trees are then passed through the existing exact state,
transition, and one-commit-anchor builders.  No target image interpreter is
used by this host-side reconstruction.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from build_state_transitions import Transition, build as build_transitions
from endpoint_state_builder import (
    EndpointSpec,
    OwnershipPolicy,
    build_endpoint_states,
)
from materialize_agent_anchor import materialize
from upgrade_navidrome_delivery import execute as upgrade_delivery


EXPECTED_MILESTONES = 10
EXPECTED_ENDPOINTS = 20
EXPECTED_GAPS = 8
ANCHOR_ENDPOINT = "milestone_002:start"
COMPATIBILITY_FAILED_ENDPOINTS = frozenset(
    {
        "milestone_003_sub-01:end",
        "milestone_003_sub-02:start",
        "milestone_003_sub-02:end",
        "milestone_003_sub-03:start",
        "milestone_003_sub-03:end",
        "milestone_003_sub-04:start",
        "milestone_003_sub-04:end",
    }
)
CANONICAL_REPAIRED_ENDPOINTS = (
    COMPATIBILITY_FAILED_ENDPOINTS | {"milestone_005:end"}
)
BASELINE_REF = "refs/tags/v0.57.0"
BASELINE_COMMIT = "4909232e8fc58411c0ce306eb78bb25b61bf1d29"
RUNTIME_FILES = (
    "navidrome_unified_environment.sh",
    "navidrome_unified_entrypoint.sh",
    "navidrome_state.sh",
    "navidrome_rebuild.sh",
    "Dockerfile.navidrome-common",
)

# The post-hoist images contain two concrete cases where an old .proto source
# was paired with generated Go from after milestone_002.  Generated sources are
# a deterministic derivative of their .proto input, not independent product
# semantics.  The two complete closures below were observed in canonical
# pre/post-5b73a4d history and independently in runnable endpoints:
# milestone_007 provides the old closure, while milestone_002:end and its
# dependants provide the new closure.
GENERATED_CLOSURE_CONTRACTS: tuple[dict[str, Any], ...] = (
    {
        "name": "plugin_api",
        "source_path": "plugins/api/api.proto",
        "variants": {
            "c451a82fc32d364cdda666cf5bc1f0303ba88792": {
                "plugins/api/api.pb.go": "47359890411530332db5b1fa1365fa7d5b10c654",
                "plugins/api/api_host.pb.go": "55e648c6c25b2f059191d0dd6abc2f2d182c58d1",
                "plugins/api/api_options.pb.go": "430bf0a5c64b8a8dbe567a53b1c5c335c2d5d9ce",
                "plugins/api/api_plugin.pb.go": "0a022be9bb864c49f28b06403d9936035162baee",
                "plugins/api/api_vtproto.pb.go": "11caa19463c095b5382729d4dd760d346eea3425",
            },
            "7929ff9e63a5cb9a36d950bb48701de60589b56b": {
                "plugins/api/api.pb.go": "b570d5c61ca851d0d0c4b3d396888452caac8280",
                "plugins/api/api_host.pb.go": "55e648c6c25b2f059191d0dd6abc2f2d182c58d1",
                "plugins/api/api_options.pb.go": "430bf0a5c64b8a8dbe567a53b1c5c335c2d5d9ce",
                "plugins/api/api_plugin.pb.go": "0a022be9bb864c49f28b06403d9936035162baee",
                "plugins/api/api_vtproto.pb.go": "11caa19463c095b5382729d4dd760d346eea3425",
            },
        },
    },
    {
        "name": "scheduler_service",
        "source_path": "plugins/host/scheduler/scheduler.proto",
        "variants": {
            "39fd32a5853e33296f3da9e22a816da5b2ccb3cf": {
                "plugins/host/scheduler/scheduler.pb.go": "6d4c292051f8aabc139d27342d4353d2bb535d31",
                "plugins/host/scheduler/scheduler_host.pb.go": "289f3f0bb975a5917b08bacdafe68f97c0fa222f",
                "plugins/host/scheduler/scheduler_plugin.pb.go": "afbed2bf0786b4ba3e33055e5e48662d6e85b7ce",
                "plugins/host/scheduler/scheduler_vtproto.pb.go": "1606ab7f035d78743997f35f56354fdcd1b538b7",
            },
            "d164b4f9060ecb17dadd5bb87e1691fb09049bb5": {
                "plugins/host/scheduler/scheduler.pb.go": "07d250cc55f7ed1d0d9c15f8194e797835ae249f",
                "plugins/host/scheduler/scheduler_host.pb.go": "714603a3b0a7964af9a833f900f5bc6c3a11b7f5",
                "plugins/host/scheduler/scheduler_plugin.pb.go": "ab7f8cd483d15357824b3e4dadc77fa7d25ae99f",
                "plugins/host/scheduler/scheduler_vtproto.pb.go": "ee6421783893303899b07be07f32b272b66f1048",
            },
        },
    },
    {
        "name": "wire_dependency_injection",
        "selector_kind": "product_go_tree_v1",
        "variants": {
            # All variants below were generated in one offline Slurm pass with
            # Go 1.24.5 and the repository's declared Wire tool.  A broad
            # product-Go signature deliberately keys the complete provider API
            # state instead of only wire_injectors.go.
            "14690a6a25832ef6e1f95f14d68f974da25b1de4c910979e1ed7c215d654dced": {
                "cmd/wire_gen.go": "dc558c393352565ed09ca3d5106254c71b545b65",
            },
            "b14fdf61b3e825f1726f46e5d2f5e3fa82086efa158fed2b4fe95733b5d233bb": {
                "cmd/wire_gen.go": "dc558c393352565ed09ca3d5106254c71b545b65",
            },
            "d2139c75206ac1a04e7d24b2617dbc35c9f51142336cc5895d478ca21d74c214": {
                "cmd/wire_gen.go": "59cf91e891ffdfed21a940db5fa8bfe567f8920a",
            },
            "6eb4bcc634bd1d5081f7b12305a30b31ed37c468bcc9214b2051ce1bf22f6c78": {
                "cmd/wire_gen.go": "dc558c393352565ed09ca3d5106254c71b545b65",
            },
            "2690371abeeb27695164f9eee5fd3e07846364dce2d17ead27f85f92e6ddc0f9": {
                "cmd/wire_gen.go": "d876a48a820af28c1f9aa1dcec1f4d0f2cfc34a1",
            },
            "ddfdaf15680c416d0e6389185dfe849adac95595d11bf854b13ab18fb86e7a07": {
                "cmd/wire_gen.go": "dc558c393352565ed09ca3d5106254c71b545b65",
            },
            "a7e1172bb59bfe53e25d6676da7bcf76689f4e01c2a30981ff61c7a480f84abe": {
                "cmd/wire_gen.go": "187ab488d91f03212b33b626b4f6b4a575a726a5",
            },
            "95ae2fe9e85289bcdf15904e5e308b3c115f7114291c093af0d9abf7e350c27c": {
                "cmd/wire_gen.go": "187ab488d91f03212b33b626b4f6b4a575a726a5",
            },
            "67bd3adb0742d25d0f0928a3bc677eb937aef111dc0fe43016fb064a2981da94": {
                "cmd/wire_gen.go": "59cf91e891ffdfed21a940db5fa8bfe567f8920a",
            },
            "06834af6b9047a45792926d436b32068df2f82c3c940a9077ead37995366b861": {
                "cmd/wire_gen.go": "59cf91e891ffdfed21a940db5fa8bfe567f8920a",
            },
            "e3ac49bb486b1fc0ffb77748baa0c01f2608b718d346e217f4cd33e1f6ccb5f4": {
                "cmd/wire_gen.go": "59cf91e891ffdfed21a940db5fa8bfe567f8920a",
            },
        },
    },
)

# Manual review of the 20-endpoint build/test-compile matrix found three
# narrowly-scoped prerequisite gaps in the synthetic multi-library branch.
# Whole-blob rules are allowed only when the canonical file delta is itself the
# compatibility closure (rather than a later feature/test bundle).
CANONICAL_CLOSURE_RULES: tuple[dict[str, Any], ...] = (
    {
        "name": "playlist_api_prerequisite",
        "commit": "2edb969e9778cfca18f2a1f7eb0426f2a1a04f4d",
        "paths": ("core/playlists.go",),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
        ),
    },
    {
        "name": "event_broadcast_prerequisite",
        "commit": "e7e1651a7e89ea3e6fd37ae8f04375cf74d4ebb9",
        "paths": (
            "server/events/events.go",
            "server/events/sse.go",
        ),
        "endpoints": (
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
        ),
    },
    {
        "name": "sub02_matching_test_api_closure",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "paths": ("core/scrobbler/play_tracker_test.go",),
        "endpoints": (
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
            "milestone_003_sub-03:end",
            "milestone_003_sub-04:start",
            "milestone_003_sub-04:end",
        ),
    },
    {
        "name": "sub03_mock_library_prerequisite",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "paths": ("tests/mock_library_repo.go",),
        "endpoints": (
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
            "milestone_003_sub-03:end",
            "milestone_003_sub-04:start",
            "milestone_003_sub-04:end",
        ),
    },
)

# The integration-test commit also rewrote or greatly expanded several existing
# tests.  Copying those complete postimages into earlier endpoints would hoist
# future test semantics.  These rules instead apply only the exact API-consumer
# edits needed to keep the already-existing tests buildable.  Every replacement
# has an exact expected count so source drift fails closed.
ARTIST_ROLE_FILTERS = (
    "artist",
    "albumartist",
    "composer",
    "conductor",
    "lyricist",
    "arranger",
    "producer",
    "director",
    "engineer",
    "mixer",
    "remixer",
    "djmixer",
    "performer",
)
ARTIST_ROLE_FILTER_EXPECTATION_REPLACEMENTS = tuple(
    (
        (
            f'Expect(roleFilter("", "{role}")).To(Equal('
            f'squirrel.NotEq{{"stats ->> \'$.{role}\'": nil}}))'
        ),
        (
            f'Expect(roleFilter("", "{role}")).To(Equal(squirrel.Expr('
            '"EXISTS (SELECT 1 FROM library_artist WHERE '
            "library_artist.artist_id = artist.id AND "
            "JSON_EXTRACT(library_artist.stats, '$."
            f'{role}.m\') IS NOT NULL)")))'
        ),
        1,
    )
    for role in ARTIST_ROLE_FILTERS
)

CANONICAL_TEXT_REPLACEMENT_RULES: tuple[dict[str, Any], ...] = (
    {
        "name": "persistent_id_existing_test_signature",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "path": "model/metadata/persistent_ids_test.go",
        "reason": (
            "adapt the pre-existing getPID tests to the library-aware "
            "getPIDFunc signature without adding the later 5248b9c cases"
        ),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
            "milestone_003_sub-03:end",
            "milestone_003_sub-04:start",
            "milestone_003_sub-04:end",
        ),
        "replacements": (
            (
                "\t\tgetPID func(mf model.MediaFile, md Metadata, spec string) string",
                "\t\tgetPID getPIDFunc",
                1,
            ),
            (
                "getPID(mf, md, spec)",
                "getPID(mf, md, spec, false)",
                12,
            ),
        ),
    },
    {
        "name": "artist_repository_existing_test_api",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "path": "persistence/artist_repository_test.go",
        "reason": (
            "adapt only the pre-existing artist repository tests to explicit "
            "library IDs and the per-library stats representation"
        ),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
        ),
        "replacements": (
            (
                "repo.GetIndex(false)",
                "repo.GetIndex(false, []int{1})",
                5,
            ),
            (
                "repo.GetIndex(true)",
                "repo.GetIndex(true, []int{1})",
                1,
            ),
            (
                "repo.GetIndex(false, model.Role",
                "repo.GetIndex(false, []int{1}, model.Role",
                3,
            ),
            (
                (
                    "\t\t\t\tstats := map[string]map[string]int64{\n"
                    "\t\t\t\t\t\"total\":    {\"s\": 1000, \"m\": 10, \"a\": 2},\n"
                    "\t\t\t\t\t\"composer\": {\"s\": 500, \"m\": 5, \"a\": 1},\n"
                    "\t\t\t\t}"
                ),
                (
                    "\t\t\t\tstats := map[string]map[string]map[string]int64{\n"
                    "\t\t\t\t\t\"1\": {\n"
                    "\t\t\t\t\t\t\"total\":    {\"s\": 1000, \"m\": 10, \"a\": 2},\n"
                    "\t\t\t\t\t\t\"composer\": {\"s\": 500, \"m\": 5, \"a\": 1},\n"
                    "\t\t\t\t\t},\n"
                    "\t\t\t\t}"
                ),
                1,
            ),
            (
                "dba.Stats = string(statsJSON)",
                "dba.LibraryStatsJSON = string(statsJSON)",
                1,
            ),
        ),
    },
    {
        "name": "artist_repository_existing_runtime_fixtures",
        "commit": "00c83af1702438b1940ae7c0d7bf4fd034cfdcfd",
        "path": "persistence/artist_repository_test.go",
        "reason": (
            "adapt only the pre-existing artist tests to per-library stats, "
            "library visibility, and the canonical role filter expression; "
            "do not add the later multi-library test cases"
        ),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
        ),
        "replacements": (
            (
                (
                    "\t\t\tBeforeEach(func() {\n"
                    "\t\t\t\traw = repo.(*artistRepository)\n"
                    "\t\t\t\t// Add stats to artists using direct SQL since "
                    "Put doesn't populate stats\n"
                    '\t\t\t\tcomposerStats := `{"composer": {"s": 1000, '
                    '"m": 5, "a": 2}}`\n'
                    '\t\t\t\tproducerStats := `{"producer": {"s": 500, '
                    '"m": 3, "a": 1}}`\n\n'
                    "\t\t\t\t// Set Beatles as composer\n"
                    "\t\t\t\t_, err := raw.executeSQL(squirrel.Update("
                    'raw.tableName).Set("stats", composerStats).Where('
                    'squirrel.Eq{"id": artistBeatles.ID}))\n'
                    "\t\t\t\tExpect(err).ToNot(HaveOccurred())\n\n"
                    "\t\t\t\t// Set Kraftwerk as producer\n"
                    "\t\t\t\t_, err = raw.executeSQL(squirrel.Update("
                    'raw.tableName).Set("stats", producerStats).Where('
                    'squirrel.Eq{"id": artistKraftwerk.ID}))\n'
                    "\t\t\t\tExpect(err).ToNot(HaveOccurred())\n"
                    "\t\t\t})\n\n"
                    "\t\t\tAfterEach(func() {\n"
                    "\t\t\t\t// Clean up stats\n"
                    "\t\t\t\t_, _ = raw.executeSQL(squirrel.Update("
                    'raw.tableName).Set("stats", "{}").Where('
                    'squirrel.Eq{"id": artistBeatles.ID}))\n'
                    "\t\t\t\t_, _ = raw.executeSQL(squirrel.Update("
                    'raw.tableName).Set("stats", "{}").Where('
                    'squirrel.Eq{"id": artistKraftwerk.ID}))\n'
                    "\t\t\t})\n"
                ),
                (
                    "\t\t\tBeforeEach(func() {\n"
                    "\t\t\t\traw = repo.(*artistRepository)\n"
                    "\t\t\t\t// Add stats to library_artist table since "
                    "stats are now stored per-library\n"
                    '\t\t\t\tcomposerStats := `{"composer": {"s": 1000, '
                    '"m": 5, "a": 2}}`\n'
                    '\t\t\t\tproducerStats := `{"producer": {"s": 500, '
                    '"m": 3, "a": 1}}`\n\n'
                    "\t\t\t\t// Set Beatles as composer in library 1\n"
                    "\t\t\t\t_, err := raw.executeSQL("
                    'squirrel.Insert("library_artist").\n'
                    '\t\t\t\t\tColumns("library_id", "artist_id", "stats").\n'
                    "\t\t\t\t\tValues(1, artistBeatles.ID, composerStats).\n"
                    "\t\t\t\t\tSuffix(\"ON CONFLICT(library_id, artist_id) "
                    'DO UPDATE SET stats = excluded.stats"))\n'
                    "\t\t\t\tExpect(err).ToNot(HaveOccurred())\n\n"
                    "\t\t\t\t// Set Kraftwerk as producer in library 1\n"
                    "\t\t\t\t_, err = raw.executeSQL("
                    'squirrel.Insert("library_artist").\n'
                    '\t\t\t\t\tColumns("library_id", "artist_id", "stats").\n'
                    "\t\t\t\t\tValues(1, artistKraftwerk.ID, producerStats).\n"
                    "\t\t\t\t\tSuffix(\"ON CONFLICT(library_id, artist_id) "
                    'DO UPDATE SET stats = excluded.stats"))\n'
                    "\t\t\t\tExpect(err).ToNot(HaveOccurred())\n"
                    "\t\t\t})\n\n"
                    "\t\t\tAfterEach(func() {\n"
                    "\t\t\t\t// Clean up stats from library_artist table\n"
                    "\t\t\t\t_, _ = raw.executeSQL("
                    'squirrel.Update("library_artist").\n'
                    '\t\t\t\t\tSet("stats", "{}").\n'
                    '\t\t\t\t\tWhere(squirrel.Eq{"artist_id": '
                    "artistBeatles.ID, \"library_id\": 1}))\n"
                    "\t\t\t\t_, _ = raw.executeSQL("
                    'squirrel.Update("library_artist").\n'
                    '\t\t\t\t\tSet("stats", "{}").\n'
                    '\t\t\t\t\tWhere(squirrel.Eq{"artist_id": '
                    "artistKraftwerk.ID, \"library_id\": 1}))\n"
                    "\t\t\t})\n"
                ),
                1,
            ),
            (
                'ctx = request.WithUser(ctx, model.User{ID: "u1"})',
                "ctx = request.WithUser(ctx, regularUser)",
                1,
            ),
            (
                (
                    'ctx = request.WithUser(ctx, model.User{ID: "admin", '
                    "IsAdmin: true})"
                ),
                "ctx = request.WithUser(ctx, adminUser)",
                1,
            ),
            (
                (
                    "\t\t\t\t_, err := raw.executeSQL(squirrel.Update("
                    'raw.tableName).Set("missing", true).Where('
                    'squirrel.Eq{"id": missing.ID}))\n'
                    "\t\t\t\tExpect(err).ToNot(HaveOccurred())\n"
                ),
                (
                    "\t\t\t\t_, err := raw.executeSQL(squirrel.Update("
                    'raw.tableName).Set("missing", true).Where('
                    'squirrel.Eq{"id": missing.ID}))\n'
                    "\t\t\t\tExpect(err).ToNot(HaveOccurred())\n\n"
                    "\t\t\t\t// Associate the existing fixture with library "
                    "1 for repository visibility.\n"
                    "\t\t\t\tlr := NewLibraryRepository(request.WithUser("
                    "log.NewContext(context.TODO()), adminUser), "
                    "GetDBXBuilder())\n"
                    "\t\t\t\terr = lr.AddArtist(1, missing.ID)\n"
                    "\t\t\t\tExpect(err).ToNot(HaveOccurred())\n"
                ),
                1,
            ),
            *ARTIST_ROLE_FILTER_EXPECTATION_REPLACEMENTS,
            (
                (
                    "\t\t\t// Insert the test artist into the database\n"
                    "\t\t\terr := repo.Put(&artistWithMBID)\n"
                    "\t\t\tExpect(err).ToNot(HaveOccurred())\n"
                ),
                (
                    "\t\t\t// Insert the test artist with its canonical "
                    "library association.\n"
                    "\t\t\terr := repo.Put(&artistWithMBID)\n"
                    "\t\t\tExpect(err).ToNot(HaveOccurred())\n"
                    "\t\t\tlr := NewLibraryRepository(request.WithUser("
                    "log.NewContext(context.TODO()), adminUser), "
                    "GetDBXBuilder())\n"
                    "\t\t\terr = lr.AddArtist(1, artistWithMBID.ID)\n"
                    "\t\t\tExpect(err).ToNot(HaveOccurred())\n"
                ),
                1,
            ),
            (
                (
                    "\t\t\terr := repo.Put(&missingArtist)\n"
                    "\t\t\tExpect(err).ToNot(HaveOccurred())\n"
                ),
                (
                    "\t\t\terr := repo.Put(&missingArtist)\n"
                    "\t\t\tExpect(err).ToNot(HaveOccurred())\n"
                    "\t\t\tlr := NewLibraryRepository(request.WithUser("
                    "log.NewContext(context.TODO()), adminUser), "
                    "GetDBXBuilder())\n"
                    "\t\t\terr = lr.AddArtist(1, missingArtist.ID)\n"
                    "\t\t\tExpect(err).ToNot(HaveOccurred())\n"
                ),
                1,
            ),
        ),
    },
    {
        "name": "playlist_repository_existing_test_api",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "path": "persistence/playlist_repository_test.go",
        "reason": (
            "rename two existing playlist fixture calls; do not add tests"
        ),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
            "milestone_003_sub-03:end",
            "milestone_003_sub-04:start",
            "milestone_003_sub-04:end",
        ),
        "replacements": (
            ("newPls.AddTracks(", "newPls.AddMediaFilesByID(", 2),
        ),
    },
    {
        "name": "persistence_suite_existing_playlist_fixtures",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "path": "persistence/persistence_suite_test.go",
        "reason": (
            "adapt the existing persistence suite fixtures to the library-aware "
            "repositories and playlist API without adding later test cases"
        ),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
        ),
        "replacements": (
            (
                '\tmf.LibraryPath = "music" // Default folder\n',
                (
                    '\tmf.LibraryPath = "music" // Default folder\n'
                    '\tmf.LibraryName = "Music Library"\n'
                ),
                1,
            ),
            (
                "\tal.LibraryID = 1\n\tal.Discs = model.Discs{}\n",
                (
                    "\tal.LibraryID = 1\n"
                    '\tal.LibraryPath = "music"\n'
                    '\tal.LibraryName = "Music Library"\n'
                    "\tal.Discs = model.Discs{}\n"
                ),
                1,
            ),
            (
                (
                    "\t//gr := NewGenreRepository(ctx, conn)\n"
                    "\t//for i := range testGenres {\n"
                    "\t//\tg := testGenres[i]\n"
                    "\t//\terr := gr.Put(&g)\n"
                    "\t//\tif err != nil {\n"
                    "\t//\t\tpanic(err)\n"
                    "\t//\t}\n"
                    "\t//}\n"
                ),
                (
                    "\t// Associate users with library 1 "
                    "(default test library)\n"
                    "\tfor i := range testUsers {\n"
                    "\t\terr := ur.SetUserLibraries("
                    "testUsers[i].ID, []int{1})\n"
                    "\t\tif err != nil {\n"
                    "\t\t\tpanic(err)\n"
                    "\t\t}\n"
                    "\t}\n"
                ),
                1,
            ),
            (
                (
                    "\tarr := NewArtistRepository(ctx, conn)\n"
                    "\tfor i := range testArtists {\n"
                    "\t\ta := testArtists[i]\n"
                    "\t\terr := arr.Put(&a)\n"
                    "\t\tif err != nil {\n"
                    "\t\t\tpanic(err)\n"
                    "\t\t}\n"
                    "\t}\n\n"
                    "\tmr := NewMediaFileRepository(ctx, conn)\n"
                ),
                (
                    "\tarr := NewArtistRepository(ctx, conn)\n"
                    "\tfor i := range testArtists {\n"
                    "\t\ta := testArtists[i]\n"
                    "\t\terr := arr.Put(&a)\n"
                    "\t\tif err != nil {\n"
                    "\t\t\tpanic(err)\n"
                    "\t\t}\n"
                    "\t}\n\n"
                    "\t// Associate artists with library 1 "
                    "(default test library)\n"
                    "\tlr := NewLibraryRepository(ctx, conn)\n"
                    "\tfor i := range testArtists {\n"
                    "\t\terr := lr.AddArtist(1, testArtists[i].ID)\n"
                    "\t\tif err != nil {\n"
                    "\t\t\tpanic(err)\n"
                    "\t\t}\n"
                    "\t}\n\n"
                    "\tmr := NewMediaFileRepository(ctx, conn)\n"
                ),
                1,
            ),
            ("plsBest.AddTracks(", "plsBest.AddMediaFilesByID(", 1),
            ("plsCool.AddTracks(", "plsCool.AddMediaFilesByID(", 1),
        ),
    },
    {
        "name": "scanner_existing_suite_fixture",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "path": "scanner/scanner_test.go",
        "reason": (
            "initialize the existing scanner fixture with the canonical music "
            "folder and request user before using the library-aware datastore; "
            "do not add later test cases"
        ),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
            "milestone_003_sub-03:end",
            "milestone_003_sub-04:start",
            "milestone_003_sub-04:end",
        ),
        "replacements": (
            (
                (
                    "\tBeforeEach(func() {\n"
                    "\t\tdb.Init(ctx)\n"
                    "\t\tDeferCleanup(func() {\n"
                    "\t\t\tExpect(tests.ClearDB()).To(Succeed())\n"
                    "\t\t})\n"
                    "\t\tDeferCleanup(configtest.SetupConfig())\n"
                    "\t\tconf.Server.DevExternalScanner = false\n"
                ),
                (
                    "\tBeforeEach(func() {\n"
                    "\t\tDeferCleanup(configtest.SetupConfig())\n"
                    "\t\tconf.Server.MusicFolder = \"fake:///music\" "
                    "// Set to match test library path\n"
                    "\t\tconf.Server.DevExternalScanner = false\n\n"
                    "\t\tdb.Init(ctx)\n"
                    "\t\tDeferCleanup(func() {\n"
                    "\t\t\tExpect(tests.ClearDB()).To(Succeed())\n"
                    "\t\t})\n"
                ),
                1,
            ),
            (
                (
                    "\t\tds.MockedMediaFile = mfRepo\n\n"
                    "\t\ts = scanner.New(ctx, ds, artwork.NoopCacheWarmer(), "
                    "events.NoopBroker(),\n"
                ),
                (
                    "\t\tds.MockedMediaFile = mfRepo\n\n"
                    "\t\t// Create the admin user in the database to match "
                    "the context\n"
                    "\t\tadminUser := model.User{\n"
                    '\t\t\tID:          "123",\n'
                    '\t\t\tUserName:    "admin",\n'
                    '\t\t\tName:        "Admin User",\n'
                    "\t\t\tIsAdmin:     true,\n"
                    '\t\t\tNewPassword: "password",\n'
                    "\t\t}\n"
                    "\t\tExpect(ds.User(ctx).Put(&adminUser)).To(Succeed())\n\n"
                    "\t\ts = scanner.New(ctx, ds, artwork.NoopCacheWarmer(), "
                    "events.NoopBroker(),\n"
                ),
                1,
            ),
        ),
    },
    {
        "name": "scanner_existing_missing_tracks_semantics",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "path": "scanner/phase_2_missing_tracks_test.go",
        "reason": (
            "adapt only the existing missing-track producer tests to the "
            "post-2edb scan-state library source and missing-only groups; "
            "do not add the later cross-library test cases"
        ),
        "endpoints": (
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
            "milestone_003_sub-03:end",
            "milestone_003_sub-04:start",
            "milestone_003_sub-04:end",
        ),
        "replacements": (
            (
                "\t\tstate = &scanState{}\n",
                (
                    "\t\tstate = &scanState{\n"
                    "\t\t\tlibraries: model.Libraries{{ID: 1, "
                    "LastScanStartedAt: time.Date(2021, 1, 1, 0, 0, 0, 0, "
                    "time.UTC)}},\n"
                    "\t\t}\n"
                ),
                1,
            ),
            (
                (
                    "\t\t\t\tExpect(produced).To(HaveLen(1))\n"
                    '\t\t\t\tExpect(produced[0].pid).To(Equal("A"))\n'
                    "\t\t\t\tExpect(produced[0].missing).To(HaveLen(1))\n"
                    "\t\t\t\tExpect(produced[0].matched).To(HaveLen(1))\n"
                ),
                (
                    "\t\t\t\tExpect(produced).To(HaveLen(2))\n"
                    "\t\t\t\t// PID A should have both missing and matched "
                    "tracks\n"
                    "\t\t\t\tvar pidA *missingTracks\n"
                    "\t\t\t\tfor _, p := range produced {\n"
                    '\t\t\t\t\tif p.pid == "A" {\n'
                    "\t\t\t\t\t\tpidA = p\n"
                    "\t\t\t\t\t\tbreak\n"
                    "\t\t\t\t\t}\n"
                    "\t\t\t\t}\n"
                    "\t\t\t\tExpect(pidA).ToNot(BeNil())\n"
                    "\t\t\t\tExpect(pidA.missing).To(HaveLen(1))\n"
                    "\t\t\t\tExpect(pidA.matched).To(HaveLen(1))\n"
                    "\t\t\t\t// PID B should have only missing tracks\n"
                    "\t\t\t\tvar pidB *missingTracks\n"
                    "\t\t\t\tfor _, p := range produced {\n"
                    '\t\t\t\t\tif p.pid == "B" {\n'
                    "\t\t\t\t\t\tpidB = p\n"
                    "\t\t\t\t\t\tbreak\n"
                    "\t\t\t\t\t}\n"
                    "\t\t\t\t}\n"
                    "\t\t\t\tExpect(pidB).ToNot(BeNil())\n"
                    "\t\t\t\tExpect(pidB.missing).To(HaveLen(1))\n"
                    "\t\t\t\tExpect(pidB.matched).To(HaveLen(0))\n"
                ),
                1,
            ),
            (
                (
                    '\t\t\tIt("should not call put if there are no matches '
                    'for any missing tracks", func() {\n'
                ),
                (
                    '\t\t\tIt("should call put for any missing tracks even '
                    'without matches", func() {\n'
                ),
                1,
            ),
            (
                "\t\t\t\tExpect(produced).To(BeZero())\n",
                (
                    "\t\t\t\tExpect(produced).To(HaveLen(2))\n"
                    "\t\t\t\t// Both PID A and PID B should be produced even "
                    "without matches\n"
                    "\t\t\t\tvar pidA, pidB *missingTracks\n"
                    "\t\t\t\tfor _, p := range produced {\n"
                    '\t\t\t\t\tif p.pid == "A" {\n'
                    "\t\t\t\t\t\tpidA = p\n"
                    '\t\t\t\t\t} else if p.pid == "B" {\n'
                    "\t\t\t\t\t\tpidB = p\n"
                    "\t\t\t\t\t}\n"
                    "\t\t\t\t}\n"
                    "\t\t\t\tExpect(pidA).ToNot(BeNil())\n"
                    "\t\t\t\tExpect(pidA.missing).To(HaveLen(1))\n"
                    "\t\t\t\tExpect(pidA.matched).To(HaveLen(0))\n"
                    "\t\t\t\tExpect(pidB).ToNot(BeNil())\n"
                    "\t\t\t\tExpect(pidB.missing).To(HaveLen(1))\n"
                    "\t\t\t\tExpect(pidB.matched).To(HaveLen(0))\n"
                ),
                1,
            ),
        ),
    },
    {
        "name": "scanner_existing_controller_fixture",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "path": "scanner/controller_test.go",
        "reason": (
            "reuse the default library created by db.Init in the existing "
            "controller tests instead of mutating its immutable path"
        ),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
            "milestone_003_sub-03:end",
            "milestone_003_sub-04:start",
            "milestone_003_sub-04:end",
        ),
        "replacements": (
            ('\t"github.com/navidrome/navidrome/model"\n', "", 1),
            (
                (
                    "\t\t\tExpect(ds.Library(ctx).Put(&model.Library{"
                    'ID: 1, Name: "lib", Path: "/tmp"})).To(Succeed())\n'
                ),
                "",
                1,
            ),
        ),
    },
    {
        "name": "scanner_existing_tag_repository_consumer",
        "commit": "2edb969e9778cfca18f2a1f7eb0426f2a1a04f4d",
        "path": "scanner/phase_1_folders.go",
        "reason": (
            "pass the current job library ID to the library-aware TagRepository "
            "API; omit the other scanner behavior from 2edb969e"
        ),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
        ),
        "replacements": (
            (
                "tagRepo.Add(entry.tags...)",
                "tagRepo.Add(entry.job.lib.ID, entry.tags...)",
                1,
            ),
        ),
    },
    {
        "name": "subsonic_existing_playlist_consumer",
        "commit": "e7e1651a7e89ea3e6fd37ae8f04375cf74d4ebb9",
        "path": "server/subsonic/playlists.go",
        "reason": (
            "rename the existing Subsonic playlist consumer to the "
            "library-aware model API without hoisting the remaining server "
            "feature commit"
        ),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
        ),
        "replacements": (
            ("pls.AddTracks(ids)", "pls.AddMediaFilesByID(ids)", 1),
        ),
    },
    {
        "name": "subsonic_existing_artist_index_consumer",
        "commit": "e7e1651a7e89ea3e6fd37ae8f04375cf74d4ebb9",
        "path": "server/subsonic/browsing.go",
        "reason": (
            "preserve the pre-e7 single musicFolderId behavior by passing "
            "that existing libId as a one-element library filter; do not "
            "hoist the later multi-library request refactor"
        ),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
        ),
        "replacements": (
            (
                "GetIndex(false, model.RoleAlbumArtist)",
                "GetIndex(false, []int{libId}, model.RoleAlbumArtist)",
                1,
            ),
        ),
    },
    {
        "name": "subsonic_existing_search_function_type",
        "commit": "e7e1651a7e89ea3e6fd37ae8f04375cf74d4ebb9",
        "path": "server/subsonic/searching.go",
        "reason": (
            "accept the repositories' new optional QueryOptions parameter in "
            "the existing search function type while continuing to call it "
            "with zero options; do not hoist e7 library filtering"
        ),
        "endpoints": (
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
        ),
        "replacements": (
            (
                (
                    "type searchFunc[T any] func(q string, offset int, "
                    "size int, includeMissing bool) (T, error)"
                ),
                (
                    "type searchFunc[T any] func(q string, offset int, "
                    "size int, includeMissing bool, "
                    "options ...model.QueryOptions) (T, error)"
                ),
                1,
            ),
        ),
    },
    {
        "name": "native_api_existing_song_test_constructor",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "path": "server/nativeapi/native_api_song_test.go",
        "reason": (
            "supply the new Library dependency to the existing test router "
            "while retaining all pre-existing mocks and cases"
        ),
        "endpoints": (
            "milestone_003_sub-03:end",
            "milestone_003_sub-04:start",
            "milestone_003_sub-04:end",
        ),
        "replacements": (
            (
                (
                    "New(ds, mockShareImpl, mockPlaylistsImpl, "
                    "mockInsightsImpl)"
                ),
                (
                    "New(ds, mockShareImpl, mockPlaylistsImpl, "
                    "mockInsightsImpl, core.NewMockLibraryService())"
                ),
                1,
            ),
        ),
    },
    {
        "name": "native_api_existing_config_authorization_route",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "path": "server/nativeapi/config_test.go",
        "reason": (
            "retain the existing non-admin authorization regression at the "
            "post-e7 route/JWT boundary where authorization moved; do not "
            "weaken it to a direct-handler 200 assertion or add later tests"
        ),
        "endpoints": (
            "milestone_003_sub-03:end",
            "milestone_003_sub-04:start",
            "milestone_003_sub-04:end",
        ),
        "replacements": (
            (
                (
                    "import (\n"
                    '\t"encoding/json"\n'
                    '\t"net/http"\n'
                    '\t"net/http/httptest"\n\n'
                    '\t"github.com/navidrome/navidrome/conf"\n'
                    '\t"github.com/navidrome/navidrome/conf/configtest"\n'
                    '\t"github.com/navidrome/navidrome/model"\n'
                    '\t"github.com/navidrome/navidrome/model/request"\n'
                ),
                (
                    "import (\n"
                    '\t"context"\n'
                    '\t"encoding/json"\n'
                    '\t"net/http"\n'
                    '\t"net/http/httptest"\n\n'
                    '\t"github.com/navidrome/navidrome/conf"\n'
                    '\t"github.com/navidrome/navidrome/conf/configtest"\n'
                    '\t"github.com/navidrome/navidrome/consts"\n'
                    '\t"github.com/navidrome/navidrome/core"\n'
                    '\t"github.com/navidrome/navidrome/core/auth"\n'
                    '\t"github.com/navidrome/navidrome/model"\n'
                    '\t"github.com/navidrome/navidrome/model/request"\n'
                    '\t"github.com/navidrome/navidrome/server"\n'
                    '\t"github.com/navidrome/navidrome/tests"\n'
                ),
                1,
            ),
            (
                (
                    '\tContext("when user is not admin", func() {\n'
                    '\t\tIt("returns unauthorized", func() {\n'
                    '\t\t\treq := httptest.NewRequest("GET", "/config", nil)\n'
                    "\t\t\tw := httptest.NewRecorder()\n"
                    "\t\t\tctx := request.WithUser(req.Context(), "
                    "model.User{IsAdmin: false})\n\n"
                    "\t\t\tgetConfig(w, req.WithContext(ctx))\n\n"
                    "\t\t\tExpect(w.Code).To(Equal("
                    "http.StatusUnauthorized))\n"
                    "\t\t})\n"
                    "\t})\n"
                ),
                (
                    '\tContext("when user is not admin", func() {\n'
                    '\t\tIt("returns forbidden through the authenticated '
                    'route", func() {\n'
                    "\t\t\tconf.Server.DevUIShowConfig = true\n"
                    "\t\t\tds := &tests.MockDataStore{}\n"
                    "\t\t\tauth.Init(ds)\n"
                    "\t\t\tregularUser := model.User{\n"
                    '\t\t\t\tID:          "user-1",\n'
                    '\t\t\t\tUserName:    "regular",\n'
                    '\t\t\t\tName:        "Regular User",\n'
                    "\t\t\t\tIsAdmin:     false,\n"
                    '\t\t\t\tNewPassword: "userpass",\n'
                    "\t\t\t}\n"
                    "\t\t\tExpect(ds.User(context.TODO()).Put("
                    "&regularUser)).To(Succeed())\n"
                    "\t\t\ttoken, err := auth.CreateToken(&regularUser)\n"
                    "\t\t\tExpect(err).ToNot(HaveOccurred())\n"
                    "\t\t\trouter := server.JWTVerifier(New(ds, nil, nil, "
                    "nil, core.NewMockLibraryService()))\n"
                    "\t\t\treq := httptest.NewRequest(http.MethodGet, "
                    '"/config/config", nil)\n'
                    "\t\t\treq.Header.Set(consts.UIAuthorizationHeader, "
                    '"Bearer "+token)\n'
                    "\t\t\tw := httptest.NewRecorder()\n\n"
                    "\t\t\trouter.ServeHTTP(w, req)\n\n"
                    "\t\t\tExpect(w.Code).To(Equal(http.StatusForbidden))\n"
                    "\t\t})\n"
                    "\t})\n"
                ),
                1,
            ),
        ),
    },
    {
        "name": "subsonic_existing_media_annotation_broker",
        "commit": "5248b9cb6660a5f7292a89d16a796fce0792dd8c",
        "path": "server/subsonic/media_annotation_test.go",
        "reason": (
            "extend the existing fake broker with the newly required "
            "broadcast method; no test cases are added"
        ),
        "endpoints": (
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
            "milestone_003_sub-03:end",
            "milestone_003_sub-04:start",
            "milestone_003_sub-04:end",
        ),
        "replacements": (
            (
                (
                    "func (f *fakeEventBroker) SendMessage(_ context.Context, "
                    "event events.Event) {\n"
                    "\tf.Events = append(f.Events, event)\n"
                    "}\n\n"
                    "var _ events.Broker = (*fakeEventBroker)(nil)"
                ),
                (
                    "func (f *fakeEventBroker) SendMessage(_ context.Context, "
                    "event events.Event) {\n"
                    "\tf.Events = append(f.Events, event)\n"
                    "}\n\n"
                    "func (f *fakeEventBroker) SendBroadcastMessage("
                    "_ context.Context, event events.Event) {\n"
                    "\tf.Events = append(f.Events, event)\n"
                    "}\n\n"
                    "var _ events.Broker = (*fakeEventBroker)(nil)"
                ),
                1,
            ),
        ),
    },
    {
        "name": "taglib_wma_multivalue_product_closure",
        "commit": "d4f869152b7c6d297ca4e4e3a740be95090bb1c0",
        "path": "adapters/taglib/taglib_wrapper.cpp",
        "reason": (
            "restore the exact canonical WMA multi-value extraction product "
            "hunk required by the d4f8691 WMA participant regression test "
            "already present in milestone_005:end; do not hoist unrelated "
            "DSF/WavPack cover-art changes from the same canonical commit"
        ),
        "endpoints": ("milestone_005:end",),
        "replacements": (
            (
                (
                    "    for (const auto item : itemListMap) {\n"
                    "      tags.insert(item.first, "
                    "item.second.front().toString());\n"
                    "    }\n"
                ),
                (
                    "    for (const auto item : itemListMap) {\n"
                    "      char *key = "
                    "const_cast<char*>(item.first.toCString(true));\n\n"
                    "      for (auto j = item.second.begin();\n"
                    "           j != item.second.end(); ++j) {\n\n"
                    "        char *val = "
                    "const_cast<char*>(j->toString().toCString(true));\n"
                    "        goPutStr(id, key, val);\n"
                    "      }\n"
                    "    }\n"
                ),
                1,
            ),
        ),
    },
)


class RebuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class Entry:
    mode: str
    object_type: str
    oid: str

    def as_json(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "object_type": self.object_type,
            "object_oid": self.oid,
        }


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and process.returncode:
        raise RebuildError(
            f"command failed ({' '.join(command)}): "
            + process.stderr.decode("utf-8", errors="replace")[-4000:]
        )
    return process


def git(
    repo: Path,
    *arguments: str,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    index: Path | None = None,
) -> bytes:
    command_env = os.environ.copy()
    if env:
        command_env.update(env)
    command_env["LC_ALL"] = "C"
    if index is not None:
        command_env["GIT_INDEX_FILE"] = str(index)
    return run(
        ["git", "-C", str(repo), *arguments],
        env=command_env,
        input_bytes=input_bytes,
    ).stdout


def git_text(repo: Path, *arguments: str, **kwargs: Any) -> str:
    return git(repo, *arguments, **kwargs).decode(
        "utf-8", errors="surrogateescape"
    ).strip()


def read_catalog(
    dataset: Path,
) -> tuple[list[dict[str, str]], set[tuple[str, str]]]:
    with (dataset / "milestones.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    with (dataset / "dependencies.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        edges = {
            (str(row["source_id"]), str(row["target_id"]))
            for row in csv.DictReader(handle)
        }
    ids = [str(row["id"]) for row in rows]
    if len(ids) != EXPECTED_MILESTONES or len(set(ids)) != EXPECTED_MILESTONES:
        raise RebuildError("Navidrome catalog is not exactly 10 unique milestones")
    if len(edges) != EXPECTED_GAPS:
        raise RebuildError("Navidrome dependency graph is not exactly 8 edges")
    if any(left not in ids or right not in ids for left, right in edges):
        raise RebuildError("dependency graph references an unknown milestone")
    return rows, edges


def topological(
    ids: Sequence[str], edges: set[tuple[str, str]]
) -> list[str]:
    incoming = Counter(right for _, right in edges)
    children: dict[str, list[str]] = defaultdict(list)
    for left, right in edges:
        children[left].append(right)
    ready = deque(sorted(node for node in ids if incoming[node] == 0))
    result: list[str] = []
    while ready:
        node = ready.popleft()
        result.append(node)
        for child in sorted(children[node]):
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
    if len(result) != len(ids):
        raise RebuildError("Navidrome dependency graph is cyclic")
    return result


def tree_entries(repo: Path, treeish: str) -> dict[str, Entry]:
    raw = git(repo, "ls-tree", "-r", "-z", "--full-tree", treeish)
    result: dict[str, Entry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, oid = metadata.decode().split()
        path = os.fsdecode(raw_path)
        result[path] = Entry(mode, object_type, oid)
    return result


def test_entries(
    entries: Mapping[str, Entry], policy: OwnershipPolicy
) -> dict[str, Entry]:
    return {
        path: entry
        for path, entry in entries.items()
        if policy.owner(path) == "test"
    }


def resolve_abbreviation(repo: Path, abbreviation: str) -> str:
    matches = [
        line
        for line in git_text(
            repo, "rev-parse", f"--disambiguate={abbreviation}"
        ).splitlines()
        if line
    ]
    commits = [
        oid
        for oid in matches
        if git_text(repo, "cat-file", "-t", oid) == "commit"
    ]
    if len(commits) != 1:
        raise RebuildError(
            f"canonical commit abbreviation is not unique: "
            f"{abbreviation} -> {commits}"
        )
    return commits[0]


def changed_paths(repo: Path, left: str, right: str) -> list[str]:
    raw = git(
        repo,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        left,
        right,
    )
    return [os.fsdecode(item) for item in raw.split(b"\0") if item]


def apply_commit_test_delta(
    *,
    repo: Path,
    state: dict[str, Entry],
    commit: str,
    policy: OwnershipPolicy,
) -> list[dict[str, Any]]:
    parent = git_text(repo, "rev-parse", f"{commit}^1")
    target_tree = git_text(repo, "rev-parse", f"{commit}^{{tree}}")
    target = tree_entries(repo, target_tree)
    audit: list[dict[str, Any]] = []
    for path in changed_paths(repo, parent, commit):
        if policy.owner(path) != "test":
            continue
        before = state.get(path)
        after = target.get(path)
        if after is None:
            state.pop(path, None)
        else:
            state[path] = after
        audit.append(
            {
                "path": path,
                "change": (
                    "deleted"
                    if after is None
                    else "added"
                    if before is None
                    else "modified"
                ),
                "before": before.as_json() if before else None,
                "after": after.as_json() if after else None,
            }
        )
    return audit


def merge_parent_states(
    *,
    milestone: str,
    parents: Sequence[str],
    parent_states: Mapping[str, Mapping[str, Entry]],
    parent_start_states: Mapping[str, Mapping[str, Entry]],
    baseline: Mapping[str, Entry],
) -> tuple[dict[str, Entry], list[dict[str, Any]]]:
    if not parents:
        return dict(baseline), []
    if len(parents) == 1:
        return dict(parent_states[parents[0]]), []
    merged: dict[str, Entry] = {}
    conflicts: list[dict[str, Any]] = []
    all_paths = set(baseline)
    for parent in parents:
        all_paths.update(parent_states[parent])
        all_paths.update(parent_start_states[parent])
    for path in sorted(all_paths):
        # Use the state from which all parent branches actually diverged.
        # A global release baseline is insufficient when two parents already
        # share earlier DAG ancestors: it would falsely treat one parent's
        # unchanged inherited value as an independent edit.
        start_values = {
            parent_start_states[parent].get(path) for parent in parents
        }
        base = (
            next(iter(start_values))
            if len(start_values) == 1
            else baseline.get(path)
        )
        values = {parent: parent_states[parent].get(path) for parent in parents}
        distinct = set(values.values())
        if len(distinct) == 1:
            selected = next(iter(distinct))
        else:
            changed = {
                parent: value
                for parent, value in values.items()
                if value != base
            }
            changed_values = set(changed.values())
            if len(changed_values) == 1:
                selected = next(iter(changed_values))
            else:
                conflicts.append(
                    {
                        "milestone": milestone,
                        "path": path,
                        "baseline": base.as_json() if base else None,
                        "parent_start_values": {
                            parent: (
                                parent_start_states[parent][path].as_json()
                                if path in parent_start_states[parent]
                                else None
                            )
                            for parent in parents
                        },
                        "parents": {
                            parent: value.as_json() if value else None
                            for parent, value in values.items()
                        },
                    }
                )
                continue
        if selected is not None:
            merged[path] = selected
    return merged, conflicts


def create_tree(
    *,
    repo: Path,
    source_tree: str,
    desired_tests: Mapping[str, Entry],
    policy: OwnershipPolicy,
    index: Path,
) -> str:
    source = tree_entries(repo, source_tree)
    current_test_paths = sorted(
        path for path in source if policy.owner(path) == "test"
    )
    git(repo, "read-tree", source_tree, index=index)
    if current_test_paths:
        git(
            repo,
            "update-index",
            "--force-remove",
            "-z",
            "--stdin",
            index=index,
            input_bytes=b"".join(os.fsencode(path) + b"\0" for path in current_test_paths),
        )
    if desired_tests:
        records = []
        for path, entry in sorted(desired_tests.items()):
            if entry.object_type != "blob":
                raise RebuildError(
                    f"test-owned non-blob entry requires review: {path}"
                )
            records.append(
                f"{entry.mode} {entry.oid}\t".encode()
                + os.fsencode(path)
                + b"\0"
            )
        git(
            repo,
            "update-index",
            "--add",
            "-z",
            "--index-info",
            index=index,
            input_bytes=b"".join(records),
        )
    result = git_text(repo, "write-tree", index=index)
    observed = tree_entries(repo, result)
    observed_tests = test_entries(observed, policy)
    if observed_tests != dict(desired_tests):
        raise RebuildError("constructed test tree does not match desired state")
    for path, entry in source.items():
        if policy.owner(path) != "test" and observed.get(path) != entry:
            raise RebuildError(f"implementation path changed during test rebuild: {path}")
    return result


def product_go_tree_signature(entries: Mapping[str, Entry]) -> str:
    """Fingerprint every non-test product Go blob that can affect Wire."""

    rows = []
    for path, entry in sorted(entries.items()):
        if (
            entry.object_type == "blob"
            and path.endswith(".go")
            and not path.endswith("_test.go")
            and "/testdata/" not in f"/{path}"
            and not path.startswith("plugins/examples/")
            and path != "cmd/wire_gen.go"
        ):
            rows.append(f"{path}\t{entry.mode}\t{entry.oid}")
    return hashlib.sha256(("\n".join(rows) + "\n").encode()).hexdigest()


def apply_canonical_closure(
    *,
    repo: Path,
    source_tree: str,
    overrides: Mapping[str, Entry],
    index: Path,
) -> tuple[str, list[dict[str, Any]]]:
    """Apply a reviewed endpoint-local canonical prerequisite closure."""

    source = tree_entries(repo, source_tree)
    changed = [
        path for path, entry in sorted(overrides.items())
        if source.get(path) != entry
    ]
    audit = [
        {
            "path": path,
            "before": source[path].as_json() if path in source else None,
            "after": overrides[path].as_json(),
        }
        for path in changed
    ]
    if not changed:
        return source_tree, audit
    git(repo, "read-tree", source_tree, index=index)
    records = [
        f"{overrides[path].mode} {overrides[path].oid}\t".encode()
        + os.fsencode(path)
        + b"\0"
        for path in changed
    ]
    git(
        repo,
        "update-index",
        "--add",
        "-z",
        "--index-info",
        index=index,
        input_bytes=b"".join(records),
    )
    result = git_text(repo, "write-tree", index=index)
    observed = tree_entries(repo, result)
    for path, entry in source.items():
        expected = overrides.get(path, entry)
        if observed.get(path) != expected:
            raise RebuildError(
                f"unexpected path change during canonical closure: {path}"
            )
    for path, entry in overrides.items():
        if observed.get(path) != entry:
            raise RebuildError(
                f"canonical closure path did not materialize: {path}"
            )
    return result, audit


def apply_reviewed_text_replacements(
    *,
    repo: Path,
    source_tree: str,
    rules: Sequence[Mapping[str, Any]],
    index: Path,
) -> tuple[str, list[dict[str, Any]]]:
    """Apply exact-count compatibility edits to existing endpoint blobs."""

    result = source_tree
    audit: list[dict[str, Any]] = []
    for rule in rules:
        path = str(rule["path"])
        source = tree_entries(repo, result)
        entry = source.get(path)
        if entry is None or entry.object_type != "blob":
            raise RebuildError(
                f"text replacement source is not a blob: {rule['name']}:{path}"
            )
        content = git(repo, "cat-file", "blob", entry.oid)
        replacement_audit = []
        for before_text, after_text, expected_count in rule["replacements"]:
            before = str(before_text).encode()
            after = str(after_text).encode()
            observed_count = content.count(before)
            if observed_count != int(expected_count):
                raise RebuildError(
                    "reviewed replacement count drifted: "
                    f"{rule['name']}:{path}: expected {expected_count}, "
                    f"observed {observed_count}"
                )
            content = content.replace(before, after)
            replacement_audit.append(
                {
                    "before_sha256": hashlib.sha256(before).hexdigest(),
                    "after_sha256": hashlib.sha256(after).hexdigest(),
                    "replacement_count": observed_count,
                }
            )
        target_oid = git_text(
            repo, "hash-object", "-w", "--stdin", input_bytes=content
        )
        if target_oid == entry.oid:
            raise RebuildError(
                f"reviewed text rule made no change: {rule['name']}:{path}"
            )
        result, changes = apply_canonical_closure(
            repo=repo,
            source_tree=result,
            overrides={
                path: Entry(entry.mode, entry.object_type, target_oid)
            },
            index=index,
        )
        if len(changes) != 1 or changes[0]["path"] != path:
            raise RebuildError(
                f"reviewed text rule changed unexpected paths: {rule['name']}"
            )
        changes[0].update(
            {
                "rule_name": str(rule["name"]),
                "rule_kind": "exact_text_replacements",
                "canonical_commit": str(rule["commit"]),
                "reason": str(rule["reason"]),
                "replacements": replacement_audit,
            }
        )
        audit.extend(changes)
    return result, audit


def normalize_generated_closures(
    *,
    repo: Path,
    source_tree: str,
    index: Path,
) -> tuple[str, list[dict[str, Any]]]:
    """Select generated Go blobs from audited source/provider signatures."""

    source = tree_entries(repo, source_tree)
    effective_source = dict(source)
    expected_entries: dict[str, Entry] = {}
    contract_audit: list[dict[str, Any]] = []
    for contract in GENERATED_CLOSURE_CONTRACTS:
        selector_kind = str(contract.get("selector_kind", "source_blob"))
        if selector_kind == "source_blob":
            source_path = str(contract["source_path"])
            selector_entry = effective_source.get(source_path)
            if (
                selector_entry is None
                or selector_entry.object_type != "blob"
            ):
                raise RebuildError(
                    "generated closure source is missing or not a blob: "
                    f"{source_path}"
                )
            selector = selector_entry.oid
        elif selector_kind == "product_go_tree_v1":
            source_path = None
            selector = product_go_tree_signature(effective_source)
        else:
            raise RebuildError(
                f"unknown generated closure selector: {selector_kind}"
            )
        variants = contract["variants"]
        if selector not in variants:
            raise RebuildError(
                "unknown generated closure selector: "
                f"{contract['name']}={selector}"
            )
        expected = {
            path: Entry("100644", "blob", oid)
            for path, oid in variants[selector].items()
        }
        for path, entry in expected.items():
            git(repo, "cat-file", "-e", f"{entry.oid}^{{blob}}")
            expected_entries[path] = entry
        changed = [
            path
            for path, entry in expected.items()
            if source.get(path) != entry
        ]
        contract_audit.append(
            {
                "name": contract["name"],
                "selector_kind": selector_kind,
                "source_path": source_path,
                "source_blob_oid": (
                    selector if selector_kind == "source_blob" else None
                ),
                "product_go_signature_sha256": (
                    selector
                    if selector_kind == "product_go_tree_v1"
                    else None
                ),
                "status": "normalized" if changed else "already_consistent",
                "changed_paths": changed,
                "expected_generated_blobs": {
                    path: entry.oid for path, entry in sorted(expected.items())
                },
                "observed_generated_blobs": {
                    path: source[path].oid if path in source else None
                    for path in sorted(expected)
                },
            }
        )
        effective_source.update(expected)
    changed_paths = {
        path
        for row in contract_audit
        for path in row["changed_paths"]
    }
    if not changed_paths:
        return source_tree, contract_audit

    git(repo, "read-tree", source_tree, index=index)
    records = [
        f"{entry.mode} {entry.oid}\t".encode()
        + os.fsencode(path)
        + b"\0"
        for path, entry in sorted(expected_entries.items())
    ]
    git(
        repo,
        "update-index",
        "--add",
        "-z",
        "--index-info",
        index=index,
        input_bytes=b"".join(records),
    )
    result = git_text(repo, "write-tree", index=index)
    observed = tree_entries(repo, result)
    for path, entry in source.items():
        expected = expected_entries.get(path, entry)
        if observed.get(path) != expected:
            raise RebuildError(
                f"unexpected path change during generated closure repair: {path}"
            )
    if set(observed) != set(source):
        raise RebuildError("generated closure repair changed the tree path set")
    return result, contract_audit


def transition_rows(
    ids: Sequence[str], edges: set[tuple[str, str]]
) -> list[Transition]:
    result = [
        Transition(
            f"milestone:{milestone}",
            "milestone",
            f"{milestone}:start",
            f"{milestone}:end",
        )
        for milestone in ids
    ]
    result.extend(
        Transition(
            f"gap:{left}:end->{right}:start",
            "gap",
            f"{left}:end",
            f"{right}:start",
        )
        for left, right in sorted(edges)
    )
    return result


def initial_delivery_manifest(delivery: Path) -> dict[str, Any]:
    files = []
    for path in sorted(item for item in delivery.rglob("*") if item.is_file()):
        files.append(
            {
                "path": path.relative_to(delivery).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schema_version": 1,
        "kind": "navidrome_patch_delivery",
        "status": "validated",
        "milestone_count": EXPECTED_MILESTONES,
        "endpoint_count": EXPECTED_ENDPOINTS,
        "gap_count": EXPECTED_GAPS,
        "transition_count": EXPECTED_MILESTONES + EXPECTED_GAPS,
        "files": files,
    }


def publish_replacement(staging: Path, output: Path, replace: bool) -> None:
    if not output.exists():
        os.replace(staging, output)
        return
    if not replace:
        raise RebuildError(f"output exists; pass --replace: {output}")
    current = json.loads((output / "manifest.json").read_text())
    if (
        current.get("kind") != "navidrome_clean_bundle"
        or current.get("milestone_count") != EXPECTED_MILESTONES
        or current.get("endpoint_count") != EXPECTED_ENDPOINTS
    ):
        raise RebuildError("refusing to replace an unrecognized output directory")
    previous = output.parent / f".{output.name}.replaced.{uuid.uuid4().hex}"
    os.replace(output, previous)
    try:
        os.replace(staging, output)
    except BaseException:
        os.replace(previous, output)
        raise
    shutil.rmtree(previous)


def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset = args.dataset.resolve()
    source = args.manual_controller.resolve()
    selected_bundle = args.selected_bundle.resolve()
    ownership = args.ownership_contract.resolve()
    output = args.output.resolve()
    code_root = args.code_root.resolve()
    wire_generation = args.wire_generation.resolve()
    wire_terminal_path = args.wire_terminal.resolve()
    compatibility_path = args.compatibility.resolve()
    compatibility_terminal_path = args.compatibility_terminal.resolve()
    if not (source / ".git" / "objects").is_dir():
        raise RebuildError("manual review controller object store is missing")
    old_manifest = json.loads((selected_bundle / "manifest.json").read_text())
    selections = json.loads(
        (selected_bundle / "endpoint_selection.json").read_text()
    )["endpoints"]
    if (
        old_manifest.get("status") != "validated"
        or len(selections) != EXPECTED_ENDPOINTS
    ):
        raise RebuildError("selected post-hoist bundle is not validated 10/20")
    selection_by_id = {
        str(row["endpoint_id"]): row for row in selections
    }
    if len(selection_by_id) != EXPECTED_ENDPOINTS:
        raise RebuildError("selected endpoint IDs are not unique")
    wire_manifest_path = wire_generation / "manifest.json"
    wire_manifest = json.loads(wire_manifest_path.read_text())
    wire_terminal = json.loads(wire_terminal_path.read_text())
    selected_state_manifest = json.loads(
        (selected_bundle / "delivery/states/manifest.json").read_text()
    )
    selected_controller = selected_bundle / "delivery/controller"
    selected_product_entries = {
        row["endpoint_id"]: tree_entries(
            selected_controller, row["combined_tree"]
        )
        for row in selected_state_manifest.get("endpoints", [])
    }
    selected_product_signatures = {
        endpoint_id: product_go_tree_signature(entries)
        for endpoint_id, entries in selected_product_entries.items()
    }
    selected_wire_blobs = {
        endpoint_id: entries["cmd/wire_gen.go"].oid
        for endpoint_id, entries in selected_product_entries.items()
    }
    wire_endpoint_signatures = {
        row["endpoint_id"]: row["product_go_signature_sha256"]
        for row in wire_manifest.get("endpoints", [])
    }
    wire_endpoint_blobs = {
        row["endpoint_id"]: row["wire_gen_blob_oid"]
        for row in wire_manifest.get("endpoints", [])
    }
    untouched_wire_signatures_preserved = all(
        selected_product_signatures[endpoint_id]
        == wire_endpoint_signatures[endpoint_id]
        for endpoint_id in (
            set(selection_by_id) - COMPATIBILITY_FAILED_ENDPOINTS
        )
    )
    repaired_wire_blobs_preserved = all(
        selected_wire_blobs[endpoint_id]
        == wire_endpoint_blobs[endpoint_id]
        for endpoint_id in COMPATIBILITY_FAILED_ENDPOINTS
    )
    wire_contract = next(
        contract
        for contract in GENERATED_CLOSURE_CONTRACTS
        if contract["name"] == "wire_dependency_injection"
    )
    expected_wire_variants = {
        signature: paths["cmd/wire_gen.go"]
        for signature, paths in wire_contract["variants"].items()
    }
    observed_wire_variants: dict[str, str] = {}
    wire_rows = wire_manifest.get("endpoints", [])
    for row in wire_rows:
        signature = row["product_go_signature_sha256"]
        blob = row["wire_gen_blob_oid"]
        prior = observed_wire_variants.setdefault(signature, blob)
        if prior != blob:
            raise RebuildError(
                "Wire generation signature produced multiple blobs"
            )
        generated = wire_generation / row["path"]
        if (
            sha256_file(generated) != row["wire_gen_sha256"]
            or git_text(source, "hash-object", str(generated)) != blob
        ):
            raise RebuildError(
                f"Wire generation artifact mismatch: {row['endpoint_id']}"
            )
        git(source, "cat-file", "-e", f"{blob}^{{blob}}")
    if (
        wire_manifest.get("status") != "validated"
        or wire_manifest.get("endpoint_count") != EXPECTED_ENDPOINTS
        or len(wire_rows) != EXPECTED_ENDPOINTS
        or wire_manifest.get("signature_variant_count")
        != len(expected_wire_variants)
        or wire_manifest.get("network_policy")
        != {"GOPROXY": "off", "GOSUMDB": "off"}
        or wire_manifest.get("target_python_required") is not False
        or wire_manifest.get("command")
        != "go tool wire gen -tags=netgo ./cmd"
        or len(wire_endpoint_signatures) != EXPECTED_ENDPOINTS
        or len(wire_endpoint_blobs) != EXPECTED_ENDPOINTS
        or not untouched_wire_signatures_preserved
        or not repaired_wire_blobs_preserved
        or observed_wire_variants != expected_wire_variants
        or wire_terminal.get("pipeline")
        != "navidrome-offline-wire-generation"
        or wire_terminal.get("status") != "complete"
        or wire_terminal.get("phase") != "validated"
        or wire_terminal.get("slurm_job_id") != "14278918"
    ):
        raise RebuildError("offline Wire generation provenance mismatch")
    compatibility_manifest_path = compatibility_path / "manifest.json"
    compatibility = json.loads(compatibility_manifest_path.read_text())
    compatibility_terminal = json.loads(
        compatibility_terminal_path.read_text()
    )
    failed_compatibility_endpoints = {
        row["endpoint_id"]
        for row in compatibility.get("endpoints", [])
        if (
            row["product_build_return_code"] != 0
            or row["test_compile_return_code"] != 0
        )
    }
    expected_failed_compatibility_endpoints = (
        COMPATIBILITY_FAILED_ENDPOINTS
    )
    compatibility_rows = {
        row["endpoint_id"]: row
        for row in compatibility.get("endpoints", [])
    }
    selected_state_rows = {
        row["endpoint_id"]: row
        for row in selected_state_manifest.get("endpoints", [])
    }
    untouched_compatibility_endpoints = (
        set(selection_by_id) - CANONICAL_REPAIRED_ENDPOINTS
    )
    untouched_trees_preserved = all(
        selected_state_rows[endpoint_id]["combined_tree"]
        == compatibility_rows[endpoint_id]["combined_tree"]
        for endpoint_id in untouched_compatibility_endpoints
    )
    if (
        compatibility.get("status") != "collected"
        or compatibility.get("endpoint_count") != EXPECTED_ENDPOINTS
        or len(compatibility.get("endpoints", [])) != EXPECTED_ENDPOINTS
        or len(compatibility_rows) != EXPECTED_ENDPOINTS
        or len(selected_state_rows) != EXPECTED_ENDPOINTS
        or compatibility.get("product_build_pass_count") != 13
        or compatibility.get("test_compile_pass_count") != 13
        or compatibility.get("target_python_required") is not False
        or failed_compatibility_endpoints
        != expected_failed_compatibility_endpoints
        or not untouched_trees_preserved
        or old_manifest.get(
            "canonical_closure_repaired_endpoint_count", 7
        ) not in {7, 8}
        or old_manifest.get(
            "canonical_closure_untouched_endpoint_count", 13
        ) not in {12, 13}
        or compatibility_terminal.get("pipeline")
        != "navidrome-compatibility-matrix"
        or compatibility_terminal.get("status") != "complete"
        or compatibility_terminal.get("phase") != "collected"
        or compatibility_terminal.get("slurm_job_id") != "14279147"
    ):
        raise RebuildError("compatibility matrix provenance mismatch")

    rows, edges = read_catalog(dataset)
    ids = [str(row["id"]) for row in rows]
    row_by_id = {str(row["id"]): row for row in rows}
    order = topological(ids, edges)
    parents: dict[str, list[str]] = defaultdict(list)
    for parent, child in edges:
        parents[child].append(parent)
    policy = OwnershipPolicy.from_file(ownership)
    canonical_overrides: dict[str, dict[str, Entry]] = defaultdict(dict)
    canonical_text_rules: dict[str, list[dict[str, Any]]] = defaultdict(list)
    canonical_rule_audit = []
    for rule in CANONICAL_CLOSURE_RULES:
        commit = git_text(
            source, "rev-parse", f"{rule['commit']}^{{commit}}"
        )
        target = tree_entries(source, f"{commit}^{{tree}}")
        path_rows = []
        for path in rule["paths"]:
            entry = target.get(path)
            if entry is None or entry.object_type != "blob":
                raise RebuildError(
                    f"canonical closure path is not a blob: {path}"
                )
            path_rows.append(
                {
                    "path": path,
                    "owner": policy.owner(path),
                    "entry": entry.as_json(),
                }
            )
            for endpoint in rule["endpoints"]:
                prior = canonical_overrides[endpoint].setdefault(path, entry)
                if prior != entry:
                    raise RebuildError(
                        "conflicting canonical endpoint closure: "
                        f"{endpoint}:{path}"
                    )
        canonical_rule_audit.append(
            {
                "name": rule["name"],
                "rule_kind": "exact_canonical_postimage",
                "canonical_commit": commit,
                "canonical_subject": git_text(
                    source, "show", "-s", "--format=%s", commit
                ),
                "endpoints": list(rule["endpoints"]),
                "paths": path_rows,
            }
        )
    for rule in CANONICAL_TEXT_REPLACEMENT_RULES:
        commit = git_text(
            source, "rev-parse", f"{rule['commit']}^{{commit}}"
        )
        parent = git_text(source, "rev-parse", f"{commit}^1")
        canonical_before = tree_entries(source, f"{parent}^{{tree}}").get(
            str(rule["path"])
        )
        canonical_after = tree_entries(source, f"{commit}^{{tree}}").get(
            str(rule["path"])
        )
        if (
            canonical_before is None
            or canonical_before.object_type != "blob"
            or canonical_after is None
            or canonical_after.object_type != "blob"
            or canonical_before == canonical_after
        ):
            raise RebuildError(
                "text replacement canonical evidence is not a changed blob: "
                f"{rule['name']}:{rule['path']}"
            )
        for endpoint in rule["endpoints"]:
            canonical_text_rules[str(endpoint)].append(dict(rule))
        canonical_rule_audit.append(
            {
                "name": rule["name"],
                "rule_kind": "exact_text_replacements",
                "canonical_commit": commit,
                "canonical_parent": parent,
                "canonical_subject": git_text(
                    source, "show", "-s", "--format=%s", commit
                ),
                "reason": str(rule["reason"]),
                "endpoints": list(rule["endpoints"]),
                "paths": [
                    {
                        "path": str(rule["path"]),
                        "owner": policy.owner(str(rule["path"])),
                        "canonical_before": canonical_before.as_json(),
                        "canonical_after": canonical_after.as_json(),
                        "replacement_counts": [
                            int(replacement[2])
                            for replacement in rule["replacements"]
                        ],
                    }
                ],
            }
        )
    expected_closure_endpoints = CANONICAL_REPAIRED_ENDPOINTS
    if (
        set(canonical_overrides) | set(canonical_text_rules)
    ) != expected_closure_endpoints:
        raise RebuildError("canonical closure endpoint set drifted")

    # The review controller intentionally imports bundle objects without
    # publishing the evaluator's ordinary release refs.  Pin the v0.57.0
    # object observed in every validated capture instead of assuming the ref
    # was recreated in this controller.
    baseline_commit = git_text(
        source, "rev-parse", f"{BASELINE_COMMIT}^{{commit}}"
    )
    baseline_tree = git_text(
        source, "rev-parse", f"{BASELINE_COMMIT}^{{tree}}"
    )
    baseline_tests = test_entries(tree_entries(source, baseline_tree), policy)

    start_tests: dict[str, dict[str, Entry]] = {}
    end_tests: dict[str, dict[str, Entry]] = {}
    milestone_audit: list[dict[str, Any]] = []
    merge_conflicts: list[dict[str, Any]] = []
    for milestone in order:
        start, conflicts = merge_parent_states(
            milestone=milestone,
            parents=sorted(parents[milestone]),
            parent_states=end_tests,
            parent_start_states=start_tests,
            baseline=baseline_tests,
        )
        merge_conflicts.extend(conflicts)
        end = dict(start)
        commit_rows = []
        abbreviations = [
            value
            for value in str(row_by_id[milestone].get("commits", "")).split(";")
            if value
        ]
        for abbreviation in abbreviations:
            commit = resolve_abbreviation(source, abbreviation)
            delta = apply_commit_test_delta(
                repo=source,
                state=end,
                commit=commit,
                policy=policy,
            )
            commit_rows.append(
                {
                    "abbreviation": abbreviation,
                    "commit": commit,
                    "subject": git_text(source, "show", "-s", "--format=%s", commit),
                    "test_changes": delta,
                }
            )
        start_tests[milestone] = start
        end_tests[milestone] = end
        milestone_audit.append(
            {
                "milestone_id": milestone,
                "parents": sorted(parents[milestone]),
                "start_test_path_count": len(start),
                "end_test_path_count": len(end),
                "commits": commit_rows,
            }
        )
    if merge_conflicts:
        review = output.parent / "navidrome_test_parent_conflicts.json"
        write_json(
            review,
            {
                "schema_version": 1,
                "kind": "navidrome_test_parent_merge_review",
                "status": "blocked",
                "conflict_count": len(merge_conflicts),
                "conflicts": merge_conflicts,
            },
        )
        raise RebuildError(
            f"parent test-state conflicts require review: {review}"
        )

    staging = output.parent / f".{output.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        controller = staging / "controller"
        run(["git", "init", "-q", "-b", "controller", str(controller)])
        git(controller, "config", "user.name", "Navidrome Causal Test Builder")
        git(controller, "config", "user.email", "navidrome-clean.invalid")
        alternates = controller / ".git" / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_text(
            str((source / ".git" / "objects").resolve()) + "\n",
            encoding="utf-8",
        )
        commit_env = {
            "GIT_AUTHOR_NAME": "Navidrome Causal Test Builder",
            "GIT_AUTHOR_EMAIL": "navidrome-clean.invalid",
            "GIT_COMMITTER_NAME": "Navidrome Causal Test Builder",
            "GIT_COMMITTER_EMAIL": "navidrome-clean.invalid",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        }
        clean_selections = []
        endpoint_audit = []
        generated_closure_audit = []
        canonical_closure_endpoint_audit = []
        index_root = staging / "indexes"
        index_root.mkdir()
        for milestone in ids:
            for side, desired in (
                ("start", start_tests[milestone]),
                ("end", end_tests[milestone]),
            ):
                endpoint_id = f"{milestone}:{side}"
                selected = selection_by_id[endpoint_id]
                source_tree = str(
                    selected.get("original_posthoist_tree", selected["tree"])
                )
                git(source, "cat-file", "-e", f"{source_tree}^{{tree}}")
                clean_tree = create_tree(
                    repo=controller,
                    source_tree=source_tree,
                    desired_tests=desired,
                    policy=policy,
                    index=index_root / endpoint_id.replace(":", "__"),
                )
                clean_tree, generated_audit = normalize_generated_closures(
                    repo=controller,
                    source_tree=clean_tree,
                    index=(
                        index_root
                        / f"{endpoint_id.replace(':', '__')}--generated"
                    ),
                )
                clean_tree, canonical_audit = apply_canonical_closure(
                    repo=controller,
                    source_tree=clean_tree,
                    overrides=canonical_overrides.get(endpoint_id, {}),
                    index=(
                        index_root
                        / f"{endpoint_id.replace(':', '__')}--canonical"
                    ),
                )
                clean_tree, text_replacement_audit = (
                    apply_reviewed_text_replacements(
                        repo=controller,
                        source_tree=clean_tree,
                        rules=canonical_text_rules.get(endpoint_id, ()),
                        index=(
                            index_root
                            / (
                                f"{endpoint_id.replace(':', '__')}"
                                "--canonical-text"
                            )
                        ),
                    )
                )
                canonical_audit.extend(text_replacement_audit)
                commit = git_text(
                    controller,
                    "commit-tree",
                    clean_tree,
                    "-m",
                    f"clean runnable endpoint {endpoint_id}",
                    env=commit_env,
                )
                ref = f"refs/runnable/{milestone}/{side}"
                git(controller, "update-ref", ref, commit)
                clean_selections.append(
                    {
                        **selected,
                        "ref": ref,
                        "commit": commit,
                        "tree": clean_tree,
                        "original_posthoist_commit": selected.get(
                            "original_posthoist_commit", selected["commit"]
                        ),
                        "original_posthoist_tree": source_tree,
                        "test_semantics": "causal_milestone_commit_propagation",
                        "generated_semantics": (
                            "proto_and_wire_keyed_canonical_generated_closure"
                        ),
                    }
                )
                endpoint_audit.append(
                    {
                        "endpoint_id": endpoint_id,
                        "original_posthoist_tree": source_tree,
                        "clean_tree": clean_tree,
                        "test_path_count": len(desired),
                    }
                )
                generated_closure_audit.append(
                    {
                        "endpoint_id": endpoint_id,
                        "contracts": generated_audit,
                    }
                )
                canonical_closure_endpoint_audit.append(
                    {
                        "endpoint_id": endpoint_id,
                        "changed_path_count": len(canonical_audit),
                        "changes": canonical_audit,
                    }
                )
        observed_closure_endpoints = {
            row["endpoint_id"]
            for row in canonical_closure_endpoint_audit
            if row["changed_path_count"]
        }
        if observed_closure_endpoints != expected_closure_endpoints:
            raise RebuildError(
                "canonical closure changed-endpoint set drifted: "
                f"{sorted(observed_closure_endpoints)}"
            )
        canonical_closure_semantics = {
            "schema_version": 1,
            "kind": "navidrome_canonical_prerequisite_closure",
            "status": "validated",
            "created_at": now(),
            "reviewed_endpoint_count": EXPECTED_ENDPOINTS,
            "repaired_endpoint_count": len(observed_closure_endpoints),
            "repaired_endpoints": sorted(observed_closure_endpoints),
            "untouched_endpoint_count": (
                EXPECTED_ENDPOINTS - len(observed_closure_endpoints)
            ),
            "selection_rule": (
                "canonical postimages and reviewed compatibility replacements "
                "required by the 7 failed build/test-compile endpoints, plus "
                "the milestone_005:end WMA product closure paired with its "
                "hoisted canonical regression test; no changes to the other "
                "12 endpoints"
            ),
            "compatibility_matrix": {
                "slurm_job_id": compatibility_terminal["slurm_job_id"],
                "manifest_sha256": sha256_file(
                    compatibility_manifest_path
                ),
                "terminal_sha256": sha256_file(
                    compatibility_terminal_path
                ),
                "endpoint_count": compatibility["endpoint_count"],
                "product_build_pass_count": compatibility[
                    "product_build_pass_count"
                ],
                "test_compile_pass_count": compatibility[
                    "test_compile_pass_count"
                ],
                "target_python_required": False,
            },
            "rules": canonical_rule_audit,
            "endpoints": canonical_closure_endpoint_audit,
        }
        write_json(
            staging / "canonical_prerequisite_closure.json",
            canonical_closure_semantics,
        )
        shutil.rmtree(index_root)
        anchor_ref = f"refs/runnable/{ANCHOR_ENDPOINT.replace(':', '/')}"
        anchor_commit = git_text(controller, "rev-parse", f"{anchor_ref}^{{commit}}")
        git(controller, "update-ref", "refs/dag-clean/anchor", anchor_commit)
        run(
            [
                "git",
                "-C",
                str(controller),
                "checkout",
                "-q",
                "-B",
                "anchor",
                anchor_commit,
            ]
        )
        write_json(
            staging / "endpoint_selection.json",
            {
                "schema_version": 2,
                "kind": "navidrome_reviewed_endpoint_selection",
                "status": "validated",
                "test_semantics": "causal_milestone_commit_propagation",
                "endpoints": clean_selections,
            },
        )
        test_audit = {
            "schema_version": 1,
            "kind": "navidrome_causal_test_semantics",
            "status": "validated",
            "created_at": now(),
            "baseline_ref": BASELINE_REF,
            "baseline_commit": baseline_commit,
            "baseline_tree": baseline_tree,
            "baseline_test_path_count": len(baseline_tests),
            "parent_merge_rule": (
                "common baseline three-way union; conflicting non-baseline "
                "edits fail closed"
            ),
            "parent_conflict_count": 0,
            "milestones": milestone_audit,
            "endpoints": endpoint_audit,
        }
        write_json(staging / "test_semantics.json", test_audit)
        normalized_endpoints = [
            row["endpoint_id"]
            for row in generated_closure_audit
            if any(
                contract["status"] == "normalized"
                for contract in row["contracts"]
            )
        ]
        proto_normalized_endpoints = [
            row["endpoint_id"]
            for row in generated_closure_audit
            if any(
                contract["status"] == "normalized"
                for contract in row["contracts"]
                if contract["name"] in ("plugin_api", "scheduler_service")
            )
        ]
        wire_normalized_endpoints = [
            row["endpoint_id"]
            for row in generated_closure_audit
            if any(
                contract["status"] == "normalized"
                for contract in row["contracts"]
                if contract["name"] == "wire_dependency_injection"
            )
        ]
        expected_proto_normalized = [
            "milestone_002:start",
            "milestone_005:start",
            "milestone_005:end",
            "milestone_006:start",
            "milestone_006:end",
            "milestone_008:start",
            "milestone_008:end",
        ]
        expected_wire_normalized = [
            "milestone_002:start",
            "milestone_002:end",
            "milestone_003_sub-01:start",
            "milestone_003_sub-01:end",
            "milestone_003_sub-02:start",
            "milestone_003_sub-02:end",
            "milestone_003_sub-03:start",
            "milestone_003_sub-03:end",
            "milestone_003_sub-04:start",
            "milestone_003_sub-04:end",
            "milestone_005:start",
            "milestone_005:end",
            "milestone_006:start",
            "milestone_006:end",
            "milestone_007:start",
            "milestone_007:end",
            "milestone_008:start",
            "milestone_008:end",
        ]
        if proto_normalized_endpoints != expected_proto_normalized:
            raise RebuildError(
                "reviewed protobuf-closure endpoint set drifted: "
                f"{proto_normalized_endpoints}"
            )
        if wire_normalized_endpoints != expected_wire_normalized:
            raise RebuildError(
                "reviewed Wire-closure endpoint set drifted: "
                f"{wire_normalized_endpoints}"
            )
        generated_semantics = {
            "schema_version": 1,
            "kind": "navidrome_generated_closure_semantics",
            "status": "validated",
            "created_at": now(),
            "authority": (
                "each endpoint source blob or complete product-Go signature "
                "selects its offline-audited generated Go closure"
            ),
            "canonical_boundary_commit": (
                "5b73a4d5b72ae6ff17b9efd0e29886c93e39f1dc"
            ),
            "old_closure_runnable_witnesses": [
                "milestone_007:start",
                "milestone_007:end",
            ],
            "reviewed_endpoint_count": EXPECTED_ENDPOINTS,
            "normalized_endpoint_count": len(normalized_endpoints),
            "normalized_endpoints": normalized_endpoints,
            "protobuf_normalized_endpoint_count": len(
                proto_normalized_endpoints
            ),
            "protobuf_normalized_endpoints": proto_normalized_endpoints,
            "wire_normalized_endpoint_count": len(wire_normalized_endpoints),
            "wire_normalized_endpoints": wire_normalized_endpoints,
            "wire_generation": {
                "slurm_job_id": wire_terminal["slurm_job_id"],
                "manifest_sha256": sha256_file(wire_manifest_path),
                "terminal_sha256": sha256_file(wire_terminal_path),
                "endpoint_count": wire_manifest["endpoint_count"],
                "signature_variant_count": wire_manifest[
                    "signature_variant_count"
                ],
                "command": wire_manifest["command"],
                "network_policy": wire_manifest["network_policy"],
                "target_python_required": False,
            },
            "unknown_proto_blob_count": 0,
            "contracts": GENERATED_CLOSURE_CONTRACTS,
            "endpoints": generated_closure_audit,
        }
        write_json(
            staging / "generated_closure_semantics.json",
            generated_semantics,
        )

        # Localize every object reachable from the 20 clean endpoint refs
        # before state/transition replay.  Otherwise Git may lazily reuse a
        # canonical subtree through the Lustre alternate, making every patch
        # application pay remote-object latency and leaving synthetic state
        # trees dependent on that alternate.
        git(controller, "repack", "-a", "-d", "--no-write-bitmap-index")
        alternates.unlink()
        git(controller, "fsck", "--connectivity-only", "--no-dangling")
        for row in clean_selections:
            git(controller, "cat-file", "-e", f"{row['commit']}^{{commit}}")
            git(controller, "cat-file", "-e", f"{row['tree']}^{{tree}}")

        endpoint_specs = [
            EndpointSpec(
                f"{milestone}:{side}",
                f"refs/runnable/{milestone}/{side}",
            )
            for milestone in ids
            for side in ("start", "end")
        ]
        states = build_endpoint_states(
            repo=controller,
            anchor_ref="refs/dag-clean/anchor",
            endpoints=endpoint_specs,
            ownership_contract=ownership,
            output=staging / "states",
        )
        transitions = build_transitions(
            state_root=staging / "states",
            source_repo=controller,
            transitions=transition_rows(ids, edges),
            output=staging / "transitions",
        )
        if (
            states.get("endpoint_count") != EXPECTED_ENDPOINTS
            or transitions.get("transition_count")
            != EXPECTED_MILESTONES + EXPECTED_GAPS
            or transitions.get("kind_counts")
            != {"milestone": EXPECTED_MILESTONES, "gap": EXPECTED_GAPS}
        ):
            raise RebuildError("state/transition denominator drift")
        materialize(
            controller,
            staging / "states",
            staging / "agent-anchor",
            staging / "agent_anchor.json",
        )

        git(controller, "fsck", "--full", "--strict")
        for row in clean_selections:
            git(controller, "cat-file", "-e", f"{row['commit']}^{{commit}}")
            git(controller, "cat-file", "-e", f"{row['tree']}^{{tree}}")

        delivery = staging / "delivery"
        delivery.mkdir()
        shutil.copytree(staging / "states", delivery / "states")
        shutil.copytree(staging / "transitions", delivery / "transitions")
        shutil.copy2(staging / "endpoint_selection.json", delivery)
        shutil.copy2(staging / "agent_anchor.json", delivery)
        shutil.copy2(staging / "test_semantics.json", delivery)
        shutil.copy2(staging / "generated_closure_semantics.json", delivery)
        shutil.copy2(
            staging / "canonical_prerequisite_closure.json",
            delivery,
        )
        shutil.copy2(
            wire_manifest_path,
            delivery / "wire_generation_manifest.json",
        )
        shutil.copy2(
            wire_terminal_path,
            delivery / "wire_generation_terminal.json",
        )
        shutil.copy2(
            compatibility_manifest_path,
            delivery / "compatibility_matrix_manifest.json",
        )
        shutil.copy2(
            compatibility_terminal_path,
            delivery / "compatibility_matrix_terminal.json",
        )
        shutil.copy2(ownership, delivery / "navidrome_ownership_contract.json")
        old_decisions = selected_bundle / "delivery" / "manual_decisions.json"
        if old_decisions.is_file():
            shutil.copy2(old_decisions, delivery / "manual_decisions.json")
        runtime = delivery / "runtime"
        runtime.mkdir()
        for name in RUNTIME_FILES:
            source_file = code_root / name
            if not source_file.is_file() or source_file.is_symlink():
                raise RebuildError(f"unsafe runtime input: {source_file}")
            shutil.copy2(source_file, runtime / name)
        shutil.move(str(controller), str(delivery / "controller"))
        write_json(
            delivery / "bundle_manifest.json",
            initial_delivery_manifest(delivery),
        )
        manifest = {
            "schema_version": 2,
            "kind": "navidrome_clean_bundle",
            "status": "validated",
            "phase": "causal_test_replay_complete",
            "created_at": now(),
            "milestone_count": EXPECTED_MILESTONES,
            "endpoint_count": EXPECTED_ENDPOINTS,
            "gap_count": EXPECTED_GAPS,
            "transition_count": EXPECTED_MILESTONES + EXPECTED_GAPS,
            "anchor_endpoint": ANCHOR_ENDPOINT,
            "review_blockers": 0,
            "test_semantics": "causal_milestone_commit_propagation",
            "generated_semantics": "proto_and_wire_keyed_canonical_generated_closure",
            "canonical_closure_repaired_endpoint_count": len(
                observed_closure_endpoints
            ),
            "canonical_closure_untouched_endpoint_count": (
                EXPECTED_ENDPOINTS - len(observed_closure_endpoints)
            ),
            "generated_closure_normalized_endpoint_count": len(
                normalized_endpoints
            ),
            "protobuf_closure_normalized_endpoint_count": len(
                proto_normalized_endpoints
            ),
            "wire_closure_normalized_endpoint_count": len(
                wire_normalized_endpoints
            ),
        }
        write_json(staging / "manifest.json", manifest)
        upgrade_delivery(staging, code_root)
        publish_replacement(staging, output, args.replace)
        staging = None
        return json.loads((output / "manifest.json").read_text())
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--dataset", type=Path, required=True)
    result.add_argument("--manual-controller", type=Path, required=True)
    result.add_argument("--selected-bundle", type=Path, required=True)
    result.add_argument("--ownership-contract", type=Path, required=True)
    result.add_argument("--code-root", type=Path, required=True)
    result.add_argument("--wire-generation", type=Path, required=True)
    result.add_argument("--wire-terminal", type=Path, required=True)
    result.add_argument("--compatibility", type=Path, required=True)
    result.add_argument(
        "--compatibility-terminal", type=Path, required=True
    )
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--replace", action="store_true")
    return result


def main() -> int:
    try:
        result = execute(parser().parse_args())
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (
        RebuildError,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
    ) as exc:
        print(f"rebuild-navidrome-test-semantics: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
