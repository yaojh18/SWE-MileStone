# Unified Nushell DAG runtime.  Endpoint source and tests are supplied only by
# the reviewed implementation/test patches copied below.
ARG NUSHELL_BASE_IMAGE=nushell_nushell_0.106.0_0.108.0/base:latest
ARG RUST_188_IMAGE=rust:1.88.0
FROM ${RUST_188_IMAGE} AS rust188
FROM ${NUSHELL_BASE_IMAGE}

USER root
COPY --from=rust188 /usr/local/rustup/toolchains/1.88.0-x86_64-unknown-linux-gnu/ \
    /usr/local/rustup/toolchains/1.88.0-x86_64-unknown-linux-gnu/

RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "swe-milestone@local" && \
    git config --global user.name "SWE Milestone" && \
    mkdir -p /testbed /opt/swe-milestone-target \
      /opt/swe-milestone-dag/delivery \
      /opt/swe-milestone-dag/environment_repairs \
      /opt/swe-milestone-dag/compile_repairs \
      /opt/swe-milestone-dag/resolved_locks \
      /opt/swe-milestone-unified

COPY agent-anchor/ /testbed/
COPY delivery/ /opt/swe-milestone-dag/delivery/
COPY environment_repairs/ /opt/swe-milestone-dag/environment_repairs/
COPY compile_repairs/ /opt/swe-milestone-dag/compile_repairs/
COPY resolved_locks/ /opt/swe-milestone-dag/resolved_locks/
COPY endpoint_index.tsv anchor.commit /opt/swe-milestone-dag/
COPY --chmod=0555 runtime/nushell_unified_environment.sh \
    /opt/swe-milestone-unified/nushell_environment.sh
COPY --chmod=0555 runtime/nushell_unified_entrypoint.sh \
    /opt/swe-milestone-unified/entrypoint.sh
COPY --chmod=0555 runtime/nushell_state.sh \
    /opt/swe-milestone-unified/state.sh
COPY --chmod=0555 runtime/nushell_compile_repair.sh \
    /opt/swe-milestone-unified/compile_repair.sh
COPY --chmod=0555 runtime/nushell_rebuild.sh \
    /opt/swe-milestone-unified/rebuild.sh

ENV RUSTUP_TOOLCHAIN=1.88.0-x86_64-unknown-linux-gnu \
    CARGO_NET_OFFLINE=true \
    CARGO_BUILD_JOBS=12 \
    NUSHELL_CARGO_PROFILE=ci \
    NU_LOG_LEVEL=DEBUG \
    RUST_BACKTRACE=1 \
    CARGO_TERM_COLOR=never \
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0=safe.directory \
    GIT_CONFIG_VALUE_0=/testbed
WORKDIR /testbed
ENTRYPOINT ["/opt/swe-milestone-unified/entrypoint.sh"]
CMD ["/bin/bash"]
