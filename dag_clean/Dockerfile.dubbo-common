# Declarative equivalent of the single Dubbo runtime assembled by
# run_dubbo_final_image.slurm.  /testbed contains one public anchor commit;
# every START/END, milestone, and gap implementation/test patch is immutable
# under /opt/swe-milestone-dag.
FROM hyd2apse/dubbo:base-offline-v0.9

LABEL org.opencontainers.image.title="SWE-Milestone Dubbo unified runtime" \
      swe-milestone.workspace="apache_dubbo_dubbo-3.3.3_dubbo-3.3.6" \
      swe-milestone.base-sif.sha256="e993f0a261981f9930f3e68a0e0617a2e63914b9acf808bf044c32b40efe005a" \
      swe-milestone.maven-closure.sha256="bd325cb9aa3fd6345090e81cf1df746dbe028b95e3a2b9d990eeb4a3008595bd"

RUN rm -rf /testbed /root/.m2/repository \
    && mkdir -p /opt/swe-milestone-unified /opt/swe-milestone-dag

COPY maven-repository/ /opt/swe-milestone-unified/maven-repository/
COPY runtime-assets/ /opt/swe-milestone-unified/runtime-assets/
COPY run_test_services.sh /opt/swe-milestone-unified/run_test_services.sh
COPY unified_dubbo_environment.sh /opt/swe-milestone-unified/unified_dubbo_environment.sh
COPY docker_unified_entrypoint.sh /opt/swe-milestone-unified/docker_entrypoint.sh
COPY bootstrap_current_dubbo_maven_poms.sh /opt/swe-milestone-unified/bootstrap_current_dubbo_maven_poms.sh
COPY dubbo_mvn_wrapper.sh /opt/swe-milestone-unified/bin/mvn
COPY verify_maven_runtime_closure.py /opt/swe-milestone-unified/verify_maven_runtime_closure.py
COPY maven-closure-manifest.json /opt/swe-milestone-unified/maven-closure-manifest.json
COPY runtime_fingerprint.json /opt/swe-milestone-unified/runtime_fingerprint.json
COPY agent-anchor/ /testbed/
COPY dag-delivery/ /opt/swe-milestone-dag/delivery/
COPY verify_embedded_dubbo_delivery.py /opt/swe-milestone-dag/verify_delivery.py
COPY Dockerfile.dubbo-common /opt/swe-milestone-dag/Dockerfile.dubbo-common

RUN chmod 0555 \
      /opt/swe-milestone-unified/run_test_services.sh \
      /opt/swe-milestone-unified/unified_dubbo_environment.sh \
      /opt/swe-milestone-unified/docker_entrypoint.sh \
      /opt/swe-milestone-unified/bootstrap_current_dubbo_maven_poms.sh \
      /opt/swe-milestone-unified/bin/mvn \
      /opt/swe-milestone-unified/verify_maven_runtime_closure.py \
      /opt/swe-milestone-dag/verify_delivery.py \
    && mkdir -p /.singularity.d/env \
    && cp /opt/swe-milestone-unified/unified_dubbo_environment.sh \
      /.singularity.d/env/99-swe-milestone-unified.sh \
    && chmod -R a+rX /opt/swe-milestone-unified \
    && chmod -R a+rX /opt/swe-milestone-dag \
    && chmod -R a+rwX /opt/swe-milestone-unified/maven-repository

ENV MAVEN_OPTS="-Dmaven.repo.local=/opt/swe-milestone-unified/maven-repository -Dmaven.javadoc.skip=true -Dspotless.check.skip=true -Duser.home=/tmp/swe-milestone-home" \
    HOME=/tmp/swe-milestone-home \
    MAVEN_CONFIG=/tmp/swe-milestone-home/.m2 \
    PATH="/opt/swe-milestone-unified/bin:${PATH}" \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /testbed
ENTRYPOINT ["/opt/swe-milestone-unified/docker_entrypoint.sh"]
CMD ["/bin/bash"]
