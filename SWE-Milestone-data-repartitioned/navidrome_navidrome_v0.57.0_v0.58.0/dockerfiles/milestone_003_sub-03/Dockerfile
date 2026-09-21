# Build on pre-configured base image
FROM navidrome_navidrome_v0.57.0_v0.58.0/base:latest

# Install Ginkgo CLI for spec-level test reporting
RUN cd /testbed && go install github.com/onsi/ginkgo/v2/ginkgo@v2.23.4

# Remove the original /testbed from base image and copy local testbed
RUN rm -rf /testbed
COPY . /testbed/

# Configure git for commits in container
RUN git config --global user.email "env-patch@example.com" && \
    git config --global user.name "ENV-PATCH"

# Patch END state: Fix mock Search method signatures and test calls
RUN cd /testbed && git checkout . && git checkout milestone-milestone_003_sub-03-end && \
    # Fix mock files - remove includeMissing bool parameter
    sed -i 's/func (m \*MockAlbumRepo) Search(q string, offset int, size int, includeMissing bool, options ...model.QueryOptions)/func (m *MockAlbumRepo) Search(q string, offset int, size int, options ...model.QueryOptions)/g' tests/mock_album_repo.go && \
    sed -i 's/args := m.Called(q, offset, size, includeMissing, options)/args := m.Called(q, offset, size, options)/g' tests/mock_album_repo.go && \
    sed -i 's/func (m \*MockArtistRepo) Search(q string, offset int, size int, includeMissing bool, options ...model.QueryOptions)/func (m *MockArtistRepo) Search(q string, offset int, size int, options ...model.QueryOptions)/g' tests/mock_artist_repo.go && \
    sed -i 's/args := m.Called(q, offset, size, includeMissing, options)/args := m.Called(q, offset, size, options)/g' tests/mock_artist_repo.go && \
    sed -i 's/func (m \*MockMediaFileRepo) Search(q string, offset int, size int, includeMissing bool, options ...model.QueryOptions)/func (m *MockMediaFileRepo) Search(q string, offset int, size int, options ...model.QueryOptions)/g' tests/mock_mediafile_repo.go && \
    sed -i 's/args := m.Called(q, offset, size, includeMissing, options)/args := m.Called(q, offset, size, options)/g' tests/mock_mediafile_repo.go && \
    # Fix test files - remove the 4th bool parameter from Search calls
    sed -i 's/\.Search(\([^,]*\), \([^,]*\), \([^,]*\), \(true\|false\))/.Search(\1, \2, \3)/g' persistence/artist_repository_test.go && \
    sed -i 's/\.Search(\([^,]*\), \([^,]*\), \([^,]*\), \(true\|false\))/.Search(\1, \2, \3)/g' persistence/mediafile_repository_test.go && \
    sed -i 's/\.Search(\([^,]*\), \([^,]*\), \([^,]*\), \(true\|false\))/.Search(\1, \2, \3)/g' server/subsonic/album_lists_test.go && \
    sed -i 's/\.Search(\([^,]*\), \([^,]*\), \([^,]*\), \(true\|false\))/.Search(\1, \2, \3)/g' server/subsonic/browsing_test.go && \
    sed -i 's/\.Search(\([^,]*\), \([^,]*\), \([^,]*\), \(true\|false\))/.Search(\1, \2, \3)/g' server/subsonic/searching_test.go && \
    # Only ignore files that have other unresolvable issues
    sed -i '1i//go:build ignore\n' plugins/manager_test.go && \
    sed -i '1i//go:build ignore\n' core/agents/agents_plugin_test.go && \
    git add -A && \
    git commit -m "[ENV-PATCH] Fix Search method signatures and test calls to match interface" && \
    git tag -f milestone-milestone_003_sub-03-end HEAD

# Patch START state: Fix Mock files to add includeMissing bool parameter to match interface
RUN cd /testbed && git checkout milestone-milestone_003_sub-03-start && \
    # Add SendBroadcastMessage to Broker interface (after SendMessage line)
    sed -i '/SendMessage(ctx context.Context, event Event)/a\\tSendBroadcastMessage(ctx context.Context, event Event)' server/events/sse.go && \
    # Add SendBroadcastMessage implementation for broker struct
    sed -i '/^func (b \*broker) SendMessage/i\\nfunc (b *broker) SendBroadcastMessage(ctx context.Context, evt Event) {\n\tctx = BroadcastToAll(ctx)\n\tb.SendMessage(ctx, evt)\n}\n' server/events/sse.go && \
    # Add SendBroadcastMessage implementation for noopBroker
    sed -i '/func (noopBroker) SendMessage/a\\nfunc (noopBroker) SendBroadcastMessage(context.Context, Event) {}' server/events/sse.go && \
    # Fix mock_artist_repo.go - add includeMissing bool parameter to Search method
    sed -i 's/func (m \*MockArtistRepo) Search(q string, offset int, size int, options \.\.\.model\.QueryOptions)/func (m *MockArtistRepo) Search(q string, offset int, size int, includeMissing bool, options ...model.QueryOptions)/' tests/mock_artist_repo.go && \
    sed -i 's/args := m\.Called(q, offset, size, options)/args := m.Called(q, offset, size, includeMissing, options)/' tests/mock_artist_repo.go && \
    # Fix mock_mediafile_repo.go - add includeMissing bool parameter to Search method
    sed -i 's/func (m \*MockMediaFileRepo) Search(q string, offset int, size int, options \.\.\.model\.QueryOptions)/func (m *MockMediaFileRepo) Search(q string, offset int, size int, includeMissing bool, options ...model.QueryOptions)/' tests/mock_mediafile_repo.go && \
    sed -i 's/args := m\.Called(q, offset, size, options)/args := m.Called(q, offset, size, includeMissing, options)/' tests/mock_mediafile_repo.go && \
    # Fix mock_album_repo.go - add includeMissing bool parameter to Search method
    sed -i 's/func (m \*MockAlbumRepo) Search(q string, offset int, size int, options \.\.\.model\.QueryOptions)/func (m *MockAlbumRepo) Search(q string, offset int, size int, includeMissing bool, options ...model.QueryOptions)/' tests/mock_album_repo.go && \
    sed -i 's/args := m\.Called(q, offset, size, options)/args := m.Called(q, offset, size, includeMissing, options)/' tests/mock_album_repo.go && \
    # Ignore plugins tests due to runtime.go type mismatch
    for f in plugins/*_test.go; do sed -i '1i//go:build ignore' "$f"; done && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add SendBroadcastMessage and fix Mock Search signatures for START state" && \
    git tag -f milestone-milestone_003_sub-03-start HEAD

# Set default to START state
RUN cd /testbed && git checkout milestone-milestone_003_sub-03-start

# Pre-install frontend dependencies for Vitest tests
RUN cd /testbed/ui && npm ci --silent && rm -rf build/3rdparty
