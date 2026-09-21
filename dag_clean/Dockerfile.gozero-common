FROM zeromicro_go-zero_v1.6.0_v1.9.3/m028:reviewed-runtime-source

# Logical common Docker contract.  The final SIF is solidified offline from
# the already-downloaded m028 SIF after merging the immutable Go module caches
# captured in every evaluator/base SIF.  No endpoint source or test mutation
# belongs here.
ENV PATH="/usr/local/go/bin:${PATH}" \
    GOPATH=/go \
    GOMODCACHE=/go/pkg/mod \
    GOCACHE=/tmp/swe-milestone-go-build \
    GOPROXY=file:///go/pkg/mod/cache/download,off \
    GOSUMDB=off \
    GOTOOLCHAIN=local \
    GO111MODULE=on \
    GOFLAGS=-buildvcs=false
WORKDIR /testbed
ENTRYPOINT ["/opt/swe-milestone-unified/entrypoint.sh"]
CMD ["/bin/bash"]
