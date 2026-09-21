# Reviewed common Element Web runtime.
#
# ELEMENT_RUNTIME_BASE must be an OCI tag for the exact reviewed
# maintenance_ui_ux runtime (SIF SHA256
# 305aaaedab285c9fb98e07ac6df733f3a1ddb3f349a38a624400ecf1ba310cec).
# The final SIF pipeline implements this Dockerfile offline by expanding that
# SIF, discarding /testbed, and staging the reviewed payload directories below.
ARG ELEMENT_RUNTIME_BASE
FROM ${ELEMENT_RUNTIME_BASE}

ENV HOME=/tmp/swe-milestone-element-home \
    CI=true \
    FORCE_COLOR=true \
    NODE_OPTIONS=--max-old-space-size=4096 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/swe-milestone-element/playwright-browsers \
    YARN_CACHE_FOLDER=/testbed/.element-yarn-cache \
    YARN_ENABLE_NETWORK=0

RUN rm -rf /testbed \
    /root/.cache/ms-playwright \
    /root/.cache/yarn \
    /usr/local/share/.cache/yarn \
 && mkdir -p /testbed /opt/swe-milestone-element /root/.cache \
    /usr/local/share/.cache

# These directories are produced only after policy review and byte validation.
COPY agent-anchor/ /testbed/
COPY delivery/ /opt/swe-milestone-element/delivery/
COPY node-runtimes/ /opt/swe-milestone-element/node-runtimes/
COPY playwright-browsers/ /opt/swe-milestone-element/playwright-browsers/
COPY element_web_environment.sh /opt/swe-milestone-element/environment.sh
COPY element_web_state.sh /opt/swe-milestone-element/element-state
COPY element_web_entrypoint.sh /opt/swe-milestone-element/entrypoint.sh

RUN chmod 0555 /opt/swe-milestone-element/environment.sh \
      /opt/swe-milestone-element/element-state \
      /opt/swe-milestone-element/entrypoint.sh \
 && git -C /testbed update-ref refs/element/anchor HEAD \
 && git -C /testbed fsck --full --strict

WORKDIR /testbed
ENTRYPOINT ["/opt/swe-milestone-element/entrypoint.sh"]
