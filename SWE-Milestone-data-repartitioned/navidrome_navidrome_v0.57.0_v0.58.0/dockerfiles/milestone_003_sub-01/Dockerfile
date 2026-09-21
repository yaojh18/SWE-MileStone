# Build on pre-configured base image
FROM navidrome_navidrome_v0.57.0_v0.58.0/base:latest

# Install Ginkgo CLI for spec-level test reporting
RUN cd /testbed && go install github.com/onsi/ginkgo/v2/ginkgo@v2.23.4

# Remove the original /testbed from base image and copy local testbed
# Build context: /home/gangda/workspace/czl/AgentBench/DATA/harness_workspace/navidrome_navidrome_v0.57.0_v0.58.0/baseline_004_v2/testbed
RUN rm -rf /testbed
COPY . /testbed/

# Set GOFLAGS to always include netgo build tag
# This fixes the "undefined: buildtags.NETGO" build error in main.go
ENV GOFLAGS="-tags=netgo"

# Configure git user for commits
RUN git config --global user.email "env-patch@localhost" && \
    git config --global user.name "ENV-PATCH"

# ============================================================================
# [ENV-PATCH] Fix compilation errors for END state
#
# Problem: persistence/tag_library_filtering_test.go uses SongCount field
# which doesn't exist in model.Tag until a later milestone (milestone_003_sub-03+)
# This causes "rockTag.SongCount undefined" compilation error
#
# Solution: Add //go:build ignore to skip this test file during compilation
# ============================================================================
RUN cd /testbed && git checkout milestone-milestone_003_sub-01-end && \
    sed -i '1i//go:build ignore' persistence/tag_library_filtering_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Ignore tag_library_filtering_test.go - uses SongCount field not available in this milestone" && \
    git tag -f milestone-milestone_003_sub-01-end HEAD

# ============================================================================
# [ENV-PATCH] Fix compilation errors for START state
#
# Problem 1: Mock Search methods have extra 'options ...model.QueryOptions' param
#            but START state interface expects: Search(q, offset, size, includeMissing)
# Problem 2: mock_library_repo.go uses model.ErrValidation and has unused fmt import
# Problem 3: mock_artist_repo.go GetIndex has wrong signature (extra libraryIds param)
# Problem 4: plugins/runtime.go has type mismatch (SchedulerService missing TimeNow)
#
# Solution: Comment out problematic functions, remove unused imports, ignore plugins tests
# ============================================================================
RUN cd /testbed && git checkout milestone-milestone_003_sub-01-start && \
    # Fix 1: mock_album_repo.go - remove options param and its usage
    sed -i 's/func (m \*MockAlbumRepo) Search(q string, offset int, size int, includeMissing bool, options \.\.\.model\.QueryOptions)/func (m *MockAlbumRepo) Search(q string, offset int, size int, includeMissing bool)/' tests/mock_album_repo.go && \
    sed -i '/if len(options) > 0 {/,/}/d' tests/mock_album_repo.go && \
    # Fix 2: mock_artist_repo.go - remove options param and its usage
    sed -i 's/func (m \*MockArtistRepo) Search(q string, offset int, size int, includeMissing bool, options \.\.\.model\.QueryOptions)/func (m *MockArtistRepo) Search(q string, offset int, size int, includeMissing bool)/' tests/mock_artist_repo.go && \
    sed -i '/if len(options) > 0 {/,/}/d' tests/mock_artist_repo.go && \
    # Fix 3: mock_mediafile_repo.go - remove options param and its usage
    sed -i 's/func (m \*MockMediaFileRepo) Search(q string, offset int, size int, includeMissing bool, options \.\.\.model\.QueryOptions)/func (m *MockMediaFileRepo) Search(q string, offset int, size int, includeMissing bool)/' tests/mock_mediafile_repo.go && \
    sed -i '/if len(options) > 0 {/,/}/d' tests/mock_mediafile_repo.go && \
    # Fix 4: mock_library_repo.go - comment out SetUserLibraries function and remove unused fmt import
    sed -i '281,301s/^/\/\/ [ENV-PATCH] /' tests/mock_library_repo.go && \
    sed -i '/"fmt"/d' tests/mock_library_repo.go && \
    # Fix 5: mock_artist_repo.go - comment out GetIndex function (wrong signature: has libraryIds param)
    sed -i '112,143s/^/\/\/ [ENV-PATCH] /' tests/mock_artist_repo.go && \
    # Fix 6: Ignore plugins tests due to runtime.go type mismatch
    for f in plugins/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix mock interface signatures and ignore plugins tests for START state" && \
    git tag -f milestone-milestone_003_sub-01-start HEAD

# Set default git state to START
RUN cd /testbed && git checkout milestone-milestone_003_sub-01-start

# Pre-install frontend dependencies for Vitest tests
RUN cd /testbed/ui && npm ci --silent && rm -rf build/3rdparty
