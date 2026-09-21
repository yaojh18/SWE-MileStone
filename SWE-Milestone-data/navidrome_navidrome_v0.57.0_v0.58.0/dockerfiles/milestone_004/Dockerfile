# Build on pre-configured base image
FROM navidrome_navidrome_v0.57.0_v0.58.0/base:latest

# Remove the original /testbed from base image and copy local testbed
# Build context: /home/gangda/workspace/czl/AgentBench/DATA/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004/testbed
RUN rm -rf /testbed
COPY . /testbed/

# END state requires Go 1.24.5 (START state works with 1.24.4 from base)
# Upgrade Go to support both states
RUN cd /tmp && \
    wget -q https://go.dev/dl/go1.24.5.linux-amd64.tar.gz && \
    rm -rf /usr/local/go && \
    tar -C /usr/local -xzf go1.24.5.linux-amd64.tar.gz && \
    rm go1.24.5.linux-amd64.tar.gz

# Install Ginkgo CLI (required for running Ginkgo tests)
RUN go install github.com/onsi/ginkgo/v2/ginkgo@latest

# Configure git for commits
RUN git config --global user.email "testbed@example.com" && \
    git config --global user.name "Testbed"

# Checkout to END state first
RUN cd /testbed && git checkout milestone-milestone_004-end

# [FIX] Strategy: Keep persistence_suite_test.go ENABLED (provides test helpers)
# Comment out only the "Participant Foreign Key Handling" block in album_repository_test.go
# because it uses createArtistWithLibrary from artist_repository_test.go which is ignored

# Patch mock files that have interface mismatches
RUN cd /testbed && \
    for f in tests/mock_album_repo.go tests/mock_artist_repo.go tests/mock_mediafile_repo.go \
             tests/mock_data_store.go tests/mock_library_repo.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Patch core test files that depend on broken mocks
RUN cd /testbed && \
    for f in model/metadata/persistent_ids_test.go \
             core/agents/agents_plugin_test.go core/agents/agents_test.go \
             core/agents/session_keys_test.go core/agents/lastfm/agent_test.go \
             core/agents/listenbrainz/agent_test.go core/artwork/artwork_internal_test.go \
             core/auth/auth_test.go core/common_test.go \
             core/external/provider_albumimage_test.go core/external/provider_artistimage_test.go \
             core/external/provider_artistradio_test.go core/external/provider_topsongs_test.go \
             core/external/provider_updatealbuminfo_test.go core/external/provider_updateartistinfo_test.go \
             core/library_test.go core/media_streamer_Internal_test.go core/media_streamer_test.go \
             core/players_test.go core/playlists_test.go core/scrobbler/buffered_scrobbler_test.go \
             core/scrobbler/play_tracker_test.go core/share_test.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Patch model test files that have issues
RUN cd /testbed && \
    for f in model/user_test.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Patch OTHER persistence test files (NOT album_repository_test.go and NOT persistence_suite_test.go)
# persistence_suite_test.go provides TestPersistence, BeforeSuite, test data, and helper functions
# album_repository_test.go contains the 40 tests we want to collect for milestone_004
RUN cd /testbed && \
    for f in persistence/artist_repository_test.go \
             persistence/genre_repository_test.go persistence/library_repository_test.go \
             persistence/mediafile_repository_test.go \
             persistence/player_repository_test.go persistence/playlist_repository_test.go \
             persistence/playqueue_repository_test.go persistence/property_repository_test.go \
             persistence/radio_repository_test.go \
             persistence/scrobble_buffer_repository_test.go \
             persistence/share_repository_test.go \
             persistence/sql_base_repository_test.go \
             persistence/sql_bookmarks_test.go \
             persistence/tag_library_filtering_test.go \
             persistence/tag_repository_test.go \
             persistence/transcoding_repository_test.go \
             persistence/user_repository_test.go \
             persistence/collation_test.go \
             persistence/export_test.go \
             persistence/helpers_test.go \
             persistence/persistence_test.go \
             persistence/sql_restful_test.go \
             persistence/sql_search_test.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Instead of commenting out Participant Foreign Key Handling tests,
# add the createArtistWithLibrary function from artist_repository_test.go inline
# This avoids creating new files while keeping all tests functional
RUN cd /testbed/persistence && \
    sed -i '$ a\\n// createArtistWithLibrary is copied from artist_repository_test.go to enable Participant FK tests\nfunc createArtistWithLibrary(repo model.ArtistRepository, artist *model.Artist, libraryID int) error {\n\terr := repo.Put(artist)\n\tif err != nil {\n\t\treturn err\n\t}\n\tlr := NewLibraryRepository(request.WithUser(GinkgoT().Context(), adminUser), GetDBXBuilder())\n\treturn lr.AddArtist(libraryID, artist.ID)\n}' album_repository_test.go

# Patch plugins test files
RUN cd /testbed && \
    for f in plugins/adapter_media_agent_test.go plugins/base_capability_test.go \
             plugins/host_scheduler_test.go plugins/host_subsonicapi_test.go \
             plugins/host_websocket_test.go plugins/manager_test.go \
             plugins/manifest_permissions_test.go plugins/plugin_lifecycle_manager_test.go \
             plugins/runtime_test.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Patch scanner and server test files
RUN cd /testbed && \
    for f in scanner/controller_test.go scanner/phase_2_missing_tracks_test.go \
             scanner/phase_3_refresh_albums_test.go scanner/phase_4_playlists_test.go \
             scanner/scanner_benchmark_test.go scanner/scanner_multilibrary_test.go \
             scanner/scanner_test.go \
             server/auth_test.go server/initial_setup_test.go server/middlewares_test.go \
             server/nativeapi/config_test.go server/nativeapi/library_test.go \
             server/nativeapi/native_api_song_test.go server/nativeapi/queue_test.go \
             server/serve_index_test.go server/subsonic/album_lists_test.go \
             server/subsonic/browsing_test.go server/subsonic/helpers_test.go \
             server/subsonic/media_annotation_test.go server/subsonic/media_retrieval_test.go \
             server/subsonic/middlewares_test.go server/subsonic/playlists_test.go \
             server/subsonic/searching_test.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Commit the patches and move the END tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Enable persistence tests with Participant FK Handling commented out" && \
    git tag -f milestone-milestone_004-end HEAD

# Apply same patches for START state
RUN cd /testbed && git checkout milestone-milestone_004-start

# Patch mock files for START state
RUN cd /testbed && \
    for f in tests/mock_album_repo.go tests/mock_artist_repo.go tests/mock_mediafile_repo.go \
             tests/mock_data_store.go tests/mock_library_repo.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Patch core test files for START state
RUN cd /testbed && \
    for f in model/metadata/persistent_ids_test.go \
             core/agents/agents_plugin_test.go core/agents/agents_test.go \
             core/agents/session_keys_test.go core/agents/lastfm/agent_test.go \
             core/agents/listenbrainz/agent_test.go core/artwork/artwork_internal_test.go \
             core/auth/auth_test.go core/common_test.go \
             core/external/provider_albumimage_test.go core/external/provider_artistimage_test.go \
             core/external/provider_artistradio_test.go core/external/provider_topsongs_test.go \
             core/external/provider_updatealbuminfo_test.go core/external/provider_updateartistinfo_test.go \
             core/library_test.go core/media_streamer_Internal_test.go core/media_streamer_test.go \
             core/players_test.go core/playlists_test.go core/scrobbler/buffered_scrobbler_test.go \
             core/scrobbler/play_tracker_test.go core/share_test.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Patch model test files for START state
RUN cd /testbed && \
    for f in model/user_test.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Patch OTHER persistence test files for START state (NOT album_repository_test.go and NOT persistence_suite_test.go)
RUN cd /testbed && \
    for f in persistence/artist_repository_test.go \
             persistence/genre_repository_test.go persistence/library_repository_test.go \
             persistence/mediafile_repository_test.go \
             persistence/player_repository_test.go persistence/playlist_repository_test.go \
             persistence/playqueue_repository_test.go persistence/property_repository_test.go \
             persistence/radio_repository_test.go \
             persistence/scrobble_buffer_repository_test.go \
             persistence/share_repository_test.go \
             persistence/sql_base_repository_test.go \
             persistence/sql_bookmarks_test.go \
             persistence/tag_library_filtering_test.go \
             persistence/tag_repository_test.go \
             persistence/transcoding_repository_test.go \
             persistence/user_repository_test.go \
             persistence/collation_test.go \
             persistence/export_test.go \
             persistence/helpers_test.go \
             persistence/persistence_test.go \
             persistence/sql_restful_test.go \
             persistence/sql_search_test.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Add createArtistWithLibrary function for START state too
RUN cd /testbed/persistence && \
    sed -i '$ a\\n// createArtistWithLibrary is copied from artist_repository_test.go to enable Participant FK tests\nfunc createArtistWithLibrary(repo model.ArtistRepository, artist *model.Artist, libraryID int) error {\n\terr := repo.Put(artist)\n\tif err != nil {\n\t\treturn err\n\t}\n\tlr := NewLibraryRepository(request.WithUser(GinkgoT().Context(), adminUser), GetDBXBuilder())\n\treturn lr.AddArtist(libraryID, artist.ID)\n}' album_repository_test.go

# Patch plugins test files for START state
RUN cd /testbed && \
    for f in plugins/adapter_media_agent_test.go plugins/base_capability_test.go \
             plugins/host_scheduler_test.go plugins/host_subsonicapi_test.go \
             plugins/host_websocket_test.go plugins/manager_test.go \
             plugins/manifest_permissions_test.go plugins/plugin_lifecycle_manager_test.go \
             plugins/runtime_test.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Patch scanner and server test files for START state
RUN cd /testbed && \
    for f in scanner/controller_test.go scanner/phase_2_missing_tracks_test.go \
             scanner/phase_3_refresh_albums_test.go scanner/phase_4_playlists_test.go \
             scanner/scanner_benchmark_test.go scanner/scanner_multilibrary_test.go \
             scanner/scanner_test.go \
             server/auth_test.go server/initial_setup_test.go server/middlewares_test.go \
             server/nativeapi/config_test.go server/nativeapi/library_test.go \
             server/nativeapi/native_api_song_test.go server/nativeapi/queue_test.go \
             server/serve_index_test.go server/subsonic/album_lists_test.go \
             server/subsonic/browsing_test.go server/subsonic/helpers_test.go \
             server/subsonic/media_annotation_test.go server/subsonic/media_retrieval_test.go \
             server/subsonic/middlewares_test.go server/subsonic/playlists_test.go \
             server/subsonic/searching_test.go; do \
        sed -i '1i//go:build ignore' "$f"; \
    done

# Commit the patches and move the START tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Enable persistence tests with Participant FK Handling commented out" && \
    git tag -f milestone-milestone_004-start HEAD


# Pre-install frontend dependencies for Vitest tests
RUN cd /testbed/ui && npm ci --silent && rm -rf build/3rdparty
