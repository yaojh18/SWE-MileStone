# Build from the directory produced by `build_ripgrep_clean.py prepare`.
# The offline base contains Rust 1.88, system dependencies, and /opt/vendor.
ARG RIPGREP_BASE_IMAGE=hyd2apse/ripgrep:base-offline-v0.9
FROM ${RIPGREP_BASE_IMAGE}

USER root

RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "swe-milestone@local" && \
    git config --global user.name "SWE Milestone"

RUN mkdir -p /usr/local/cargo && printf '%s\n' \
    '[source.crates-io]' \
    'replace-with = "vendored-sources"' \
    '[source.vendored-sources]' \
    'directory = "/opt/vendor"' \
    > /usr/local/cargo/config.toml

COPY --chmod=0444 ripgrep_vendor_additions.tar \
    /opt/swe-milestone-vendor/ripgrep_vendor_additions.tar
RUN printf '%s  %s\n' \
    'e7d823948e650e3fe2b93ee5d6ba30d0c8120266251fedf15ac85d0d745e23aa' \
    '/opt/swe-milestone-vendor/ripgrep_vendor_additions.tar' \
    | sha256sum -c - && \
    tar -xf /opt/swe-milestone-vendor/ripgrep_vendor_additions.tar \
        -C /opt/vendor && \
    test -s /opt/vendor/arbitrary-1.4.1/.cargo-checksum.json && \
    test -s /opt/vendor/cfg-if-1.0.4/.cargo-checksum.json && \
    test -s /opt/vendor/derive_arbitrary-1.4.1/.cargo-checksum.json && \
    test -s /opt/vendor/getrandom-0.3.4/.cargo-checksum.json && \
    test -s /opt/vendor/regex-1.12.2/.cargo-checksum.json && \
    test -s /opt/vendor/regex-automata-0.4.13/.cargo-checksum.json && \
    test -s /opt/vendor/regex-syntax-0.8.8/.cargo-checksum.json

RUN rm -rf /testbed && mkdir -p \
    /testbed \
    /opt/swe-milestone-dag/delivery \
    /opt/swe-milestone-target \
    /opt/swe-milestone-unified
COPY agent-anchor/ /testbed/
COPY delivery/ /opt/swe-milestone-dag/delivery/
COPY --chmod=0555 runtime/ripgrep_unified_environment.sh \
    /opt/swe-milestone-unified/ripgrep_environment.sh
COPY --chmod=0555 runtime/ripgrep_unified_entrypoint.sh \
    /opt/swe-milestone-unified/entrypoint.sh
COPY --chmod=0555 runtime/ripgrep_rebuild.sh \
    /opt/swe-milestone-unified/rebuild.sh

ENV RUST_BACKTRACE=1 \
    CARGO_NET_OFFLINE=true \
    CARGO_TERM_COLOR=never
WORKDIR /testbed
ENTRYPOINT ["/opt/swe-milestone-unified/entrypoint.sh"]
CMD ["/bin/bash"]
