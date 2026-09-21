# Reproducible description of the common, task-neutral runtime layer.
# The final SIF is solidified from the validated base-offline SIF so every
# dependency below is already local; no network operation is permitted.
FROM hyd2apse/navidrome:base-offline-v0.9

ENV CGO_ENABLED=1 \
    GOTOOLCHAIN=auto \
    GOPROXY=off \
    GOSUMDB=off \
    GOCACHE=/tmp/swe-milestone-navidrome-go-build \
    PKG_CONFIG_PATH=/opt/swe-milestone-cache/taglib/lib/pkgconfig \
    NPM_CONFIG_OFFLINE=true \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_CACHE=/opt/swe-milestone-cache/npm

COPY agent-anchor/ /testbed/
COPY delivery/ /opt/swe-milestone-dag/delivery/
COPY navidrome_unified_environment.sh /opt/swe-milestone-unified/navidrome_environment.sh
COPY navidrome_unified_entrypoint.sh /opt/swe-milestone-unified/entrypoint.sh
COPY navidrome_state.sh /usr/local/bin/navidrome-state
COPY navidrome_rebuild.sh /usr/local/bin/navidrome-rebuild

WORKDIR /testbed
ENTRYPOINT ["/opt/swe-milestone-unified/entrypoint.sh"]
