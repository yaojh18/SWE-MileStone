# Build on pre-configured base image
FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

WORKDIR /testbed
# Use the testbed from base image directly (it already contains milestone tags)

# Checkout to END state (all features available)
RUN cd /testbed && git checkout milestone-M022-end

# Fix 1: The milestone-M022-end state references dubbo-spring6-security module but is missing
# the pom.xml and main source files. Cherry-pick them from the commit that introduced the module.
RUN cd /testbed && \
    git checkout ea0976b9cb -- dubbo-plugin/dubbo-spring6-security/pom.xml && \
    git checkout ea0976b9cb -- dubbo-plugin/dubbo-spring6-security/src/main/

# Fix 2: The dubbo-dependencies-bom changed bouncycastle from jdk15on to jdk18on (v1.81)
# but dubbo-security still references jdk15on. Cherry-pick the fix from 6939e3c569.
RUN cd /testbed && \
    git checkout 6939e3c569 -- dubbo-plugin/dubbo-security/pom.xml

# Fix 3: The Http3ClientFrameCodec.java in milestone-M022-end is missing import statements
# for ChannelFuture and ChannelFutureListener. Checkout the correct version from ed182c9.
RUN cd /testbed && \
    git checkout ed182c9 -- dubbo-rpc/dubbo-rpc-triple/src/main/java/org/apache/dubbo/rpc/protocol/tri/h3/Http3ClientFrameCodec.java

# Fix 4: Reset all test files to base version except milestone-specific test files.
# This is because the milestone state has test files that depend on source changes not in this milestone.
# The milestone tests are:
# - dubbo-common/src/test/java/org/apache/dubbo/common/utils/NetUtilsTest.java
# - dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
# - dubbo-remoting/dubbo-remoting-netty4/src/test/java/org/apache/dubbo/remoting/transport/netty4/NettyTransporterTest.java
RUN cd /testbed && \
    for f in $(git diff 8e49668a45 HEAD --name-only -- '**/src/test/**/*.java' '**/src/test/**/*.groovy'); do \
        case "$f" in \
            */NetUtilsTest.java|*/SimpleRegistryExporter.java|*/NettyTransporterTest.java) \
                echo "Keeping milestone test: $f" ;; \
            *) \
                git checkout 8e49668a45 -- "$f" 2>/dev/null || rm -f "/testbed/$f" ;; \
        esac; \
    done

# Fix 5: Delete new test files that don't exist in base and aren't milestone tests
# These are test files added in later milestones that test non-existent features
RUN cd /testbed && \
    rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java && \
    rm -f dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java && \
    rm -f dubbo-rpc/dubbo-rpc-triple/src/test/groovy/org/apache/dubbo/rpc/protocol/tri/rest/mapping/RadixTreeTest.groovy && \
    rm -f dubbo-rpc/dubbo-rpc-triple/src/test/groovy/org/apache/dubbo/rpc/protocol/tri/rest/mapping/RegistrationSpec.groovy

# Fix 6: Reset demo module source files to base version (they have changes that depend on non-existent APIs)
# Reset .java files and .proto files, not pom.xml files which have dependency version changes
RUN cd /testbed && \
    git diff 8e49668a45 HEAD --name-only -- 'dubbo-demo/**/*.java' 'dubbo-demo/**/*.proto' | while read f; do git checkout 8e49668a45 -- "$f" 2>/dev/null || rm -f "/testbed/$f"; done

# Rebuild affected modules (skip test compilation for install)
RUN cd /testbed && mvn install -DskipTests -Dmaven.test.skip=true -B -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true -pl dubbo-plugin/dubbo-security,dubbo-plugin/dubbo-spring6-security -am

# Commit all changes so we can switch between states
RUN cd /testbed && \
    git config user.email "test@example.com" && \
    git config user.name "Test" && \
    git add -A && git commit -m "Apply fixes for M022" --allow-empty && \
    git tag -f milestone-M022-end

# Create a unified apply_patches.sh script that detects current branch and applies appropriate fixes
# This script is called by test_runner AFTER it does git checkout
# So we should NOT do git checkout here - just apply the patches
RUN echo '#!/bin/bash' > /usr/local/bin/apply_patches.sh && \
    echo 'set -e' >> /usr/local/bin/apply_patches.sh && \
    echo 'cd /testbed' >> /usr/local/bin/apply_patches.sh && \
    echo '' >> /usr/local/bin/apply_patches.sh && \
    echo '# Detect current state using reflog (last checkout command)' >> /usr/local/bin/apply_patches.sh && \
    echo 'CURRENT_STATE=$(git reflog -1 --format="%gs" 2>/dev/null | grep -oP "checkout: moving from .* to \\K.*" || echo "unknown")' >> /usr/local/bin/apply_patches.sh && \
    echo 'echo ">>> apply_patches.sh: Current state: $CURRENT_STATE"' >> /usr/local/bin/apply_patches.sh && \
    echo '' >> /usr/local/bin/apply_patches.sh && \
    echo '# Common fixes for all states' >> /usr/local/bin/apply_patches.sh && \
    echo 'echo ">>> Applying common fixes..."' >> /usr/local/bin/apply_patches.sh && \
    echo 'git checkout ea0976b9cb -- dubbo-plugin/dubbo-spring6-security/pom.xml 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo 'git checkout ea0976b9cb -- dubbo-plugin/dubbo-spring6-security/src/main/ 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo 'git checkout 6939e3c569 -- dubbo-plugin/dubbo-security/pom.xml 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo '' >> /usr/local/bin/apply_patches.sh && \
    echo '# State-specific fixes - check for end in state name' >> /usr/local/bin/apply_patches.sh && \
    echo 'if [[ "$CURRENT_STATE" == *"end"* ]]; then' >> /usr/local/bin/apply_patches.sh && \
    echo '    echo ">>> Applying END state fixes..."' >> /usr/local/bin/apply_patches.sh && \
    echo '    # Fix Http3ClientFrameCodec.java missing imports' >> /usr/local/bin/apply_patches.sh && \
    echo '    git checkout ed182c9 -- dubbo-rpc/dubbo-rpc-triple/src/main/java/org/apache/dubbo/rpc/protocol/tri/h3/Http3ClientFrameCodec.java 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo '' >> /usr/local/bin/apply_patches.sh && \
    echo '    # Reset tests to base EXCEPT milestone tests' >> /usr/local/bin/apply_patches.sh && \
    echo '    for f in $(git diff 8e49668a45 HEAD --name-only -- "**/src/test/**/*.java" "**/src/test/**/*.groovy"); do' >> /usr/local/bin/apply_patches.sh && \
    echo '        case "$f" in' >> /usr/local/bin/apply_patches.sh && \
    echo '            */NetUtilsTest.java|*/SimpleRegistryExporter.java|*/NettyTransporterTest.java)' >> /usr/local/bin/apply_patches.sh && \
    echo '                echo "Keeping milestone test: $f" ;;' >> /usr/local/bin/apply_patches.sh && \
    echo '            *)' >> /usr/local/bin/apply_patches.sh && \
    echo '                git checkout 8e49668a45 -- "$f" 2>/dev/null || rm -f "/testbed/$f" ;;' >> /usr/local/bin/apply_patches.sh && \
    echo '        esac' >> /usr/local/bin/apply_patches.sh && \
    echo '    done' >> /usr/local/bin/apply_patches.sh && \
    echo 'else' >> /usr/local/bin/apply_patches.sh && \
    echo '    echo ">>> Applying START state fixes..."' >> /usr/local/bin/apply_patches.sh && \
    echo '    # Reset ALL tests to base for START state (milestone tests use END state features)' >> /usr/local/bin/apply_patches.sh && \
    echo '    for f in $(git diff 8e49668a45 HEAD --name-only -- "**/src/test/**/*.java" "**/src/test/**/*.groovy"); do' >> /usr/local/bin/apply_patches.sh && \
    echo '        git checkout 8e49668a45 -- "$f" 2>/dev/null || rm -f "/testbed/$f"' >> /usr/local/bin/apply_patches.sh && \
    echo '    done' >> /usr/local/bin/apply_patches.sh && \
    echo 'fi' >> /usr/local/bin/apply_patches.sh && \
    echo '' >> /usr/local/bin/apply_patches.sh && \
    echo '# Delete new test files that do not exist in base' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f dubbo-rpc/dubbo-rpc-triple/src/test/groovy/org/apache/dubbo/rpc/protocol/tri/rest/mapping/RadixTreeTest.groovy' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f dubbo-rpc/dubbo-rpc-triple/src/test/groovy/org/apache/dubbo/rpc/protocol/tri/rest/mapping/RegistrationSpec.groovy' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f dubbo-plugin/dubbo-spring6-security/src/test/java/org/apache/dubbo/spring/security/oauth2/DeserializationTest.java' >> /usr/local/bin/apply_patches.sh && \
    echo '' >> /usr/local/bin/apply_patches.sh && \
    echo '# Reset demo files' >> /usr/local/bin/apply_patches.sh && \
    echo 'git diff 8e49668a45 HEAD --name-only -- "dubbo-demo/**/*.java" "dubbo-demo/**/*.proto" | while read f; do' >> /usr/local/bin/apply_patches.sh && \
    echo '    git checkout 8e49668a45 -- "$f" 2>/dev/null || rm -f "/testbed/$f"' >> /usr/local/bin/apply_patches.sh && \
    echo 'done' >> /usr/local/bin/apply_patches.sh && \
    echo '' >> /usr/local/bin/apply_patches.sh && \
    echo 'echo ">>> apply_patches.sh: Done"' >> /usr/local/bin/apply_patches.sh && \
    chmod +x /usr/local/bin/apply_patches.sh

# Set default state to START with fixes applied
RUN cd /testbed && git checkout -f milestone-M022-start && /usr/local/bin/apply_patches.sh

# Commit patches and move START tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M022 on START state" && \
    git tag -f milestone-M022-start
