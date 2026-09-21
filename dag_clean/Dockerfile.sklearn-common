# Declarative equivalent of the scikit-learn unified runtime.  The materialized
# SIF is based on the local M06 evaluator image because M06 contains the common
# base plus the only node-level runtime dependency superset
# (`array_api_compat` and `array_api_strict`).  Its /testbed is removed.
FROM hyd2apse/scikit-learn:m06-v0.9

LABEL org.opencontainers.image.title="SWE-Milestone scikit-learn unified runtime" \
      swe-milestone.workspace="scikit-learn_scikit-learn_1.5.2_1.6.0" \
      swe-milestone.authority="per-evaluator-post-hoist-start-end-tags"

RUN rm -rf /testbed \
    && mkdir -p /testbed /opt/swe-milestone-unified /opt/swe-milestone-dag

COPY agent-anchor/ /testbed/
COPY delivery/ /opt/swe-milestone-dag/delivery/
COPY build_sklearn_clean.py /opt/swe-milestone-dag/verify_delivery.py
COPY sklearn_unified_environment.sh /opt/swe-milestone-unified/sklearn_environment.sh
COPY sklearn_unified_entrypoint.sh /opt/swe-milestone-unified/entrypoint.sh
COPY sklearn_rebuild.sh /opt/swe-milestone-unified/rebuild.sh
COPY Dockerfile.sklearn-common /opt/swe-milestone-dag/Dockerfile.sklearn-common

RUN chmod 0555 \
      /opt/swe-milestone-unified/sklearn_environment.sh \
      /opt/swe-milestone-unified/entrypoint.sh \
      /opt/swe-milestone-unified/rebuild.sh \
      /opt/swe-milestone-dag/verify_delivery.py \
    && chmod -R a+rX /opt/swe-milestone-dag \
    && /opt/swe-milestone-unified/rebuild.sh

ENV HOME=/tmp/swe-milestone-home \
    PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONNOUSERSITE=1 \
    MPLBACKEND=Agg \
    SKLEARN_SKIP_NETWORK_TESTS=1

WORKDIR /testbed
ENTRYPOINT ["/opt/swe-milestone-unified/entrypoint.sh"]
CMD ["/bin/bash"]
