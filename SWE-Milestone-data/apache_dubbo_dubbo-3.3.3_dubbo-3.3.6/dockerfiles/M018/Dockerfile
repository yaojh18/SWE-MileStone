# Build on pre-configured base image
FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

# Use the testbed from base image directly (it already contains milestone tags)
WORKDIR /testbed

# Checkout to END state (all features available)
RUN cd /testbed && git checkout milestone-M018-end

# Create patched pom.xml files and backup to /testbed-backup
# This is needed because git checkout will restore original files
RUN mkdir -p /testbed-backup

# Remove dubbo-spring6-security module and demo modules from the build
# These have compilation errors due to missing dependencies/classes
# Also remove dubbo-spring-boot-project which has dependency issues
RUN cd /testbed && \
    sed -i '/<module>dubbo-plugin\/dubbo-spring6-security<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-demo\/dubbo-demo-api<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot-idl<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-distribution\/dubbo-all<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-distribution\/dubbo-all-shaded<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-distribution\/dubbo-apache-release<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-spring-boot-project\/.*<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-distribution\/dubbo-dependencies-all<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-dependencies-all<\/module>/d' dubbo-test/pom.xml && \
    sed -i '/<module>dubbo-test-modules<\/module>/d' dubbo-test/pom.xml && \
    cp pom.xml /testbed-backup/pom.xml.end && \
    cp dubbo-test/pom.xml /testbed-backup/dubbo-test-pom.xml

# Fix bouncycastle dependency issue: the bom was changed from jdk15on to jdk18on artifacts
# but dubbo-security/pom.xml still references jdk15on artifacts. Add version directly to dubbo-security pom.xml
RUN cd /testbed && \
    sed -i 's/<artifactId>bcprov-jdk15on<\/artifactId>/<artifactId>bcprov-jdk15on<\/artifactId>\n      <version>1.70<\/version>/g' dubbo-plugin/dubbo-security/pom.xml && \
    sed -i 's/<artifactId>bcpkix-jdk15on<\/artifactId>/<artifactId>bcpkix-jdk15on<\/artifactId>\n      <version>1.70<\/version>/g' dubbo-plugin/dubbo-security/pom.xml && \
    sed -i 's/<artifactId>bcprov-ext-jdk15on<\/artifactId>/<artifactId>bcprov-ext-jdk15on<\/artifactId>\n      <version>1.70<\/version>/g' dubbo-plugin/dubbo-security/pom.xml && \
    cp dubbo-plugin/dubbo-security/pom.xml /testbed-backup/dubbo-security-pom.xml

# Comment out testAllLogMethod in LoggerTest.java (lines 49-85)
# Reason: uses getLogger(String, Class) API not available in this version (only getLogger(Class) and getLogger(String) exist)
RUN cd /testbed && \
    sed -i '49i\/*' dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java && \
    sed -i '87a\*/' dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java && \
    cp dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java /testbed-backup/LoggerTest.java

# Remove MetricsSupportTest.java (rename it to exclude from compilation)
# Reason: type mismatch in fillZero() call - method signature changed and test uses incompatible types
RUN cd /testbed && \
    mv dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java \
       dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java.disabled

# Remove AffinityRouteTest.java (rename it to exclude from compilation)
# Reason: imports non-existent class AffinityServiceStateRouter from package router.affinity.config
RUN cd /testbed && \
    mv dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java \
       dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java.disabled

# Remove MetaCacheManagerTest.java (rename it to exclude from compilation)
# Reason: uses calRevision() method that doesn't exist in MetadataInfo class
RUN cd /testbed && \
    mv dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java \
       dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java.disabled

# Remove NacosNamingServiceWrapperTest.java (rename it to exclude from compilation)
# Reason: uses getAllInstancesWithoutSubscription() method that doesn't exist in NacosNamingServiceWrapper
RUN cd /testbed && \
    mv dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java \
       dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java.disabled

# Remove SimpleRegistryExporter.java and ConfigTest.java (rename to exclude from compilation)
# Reason: SimpleRegistryExporter uses isReuseAddressSupported() method that doesn't exist in NetUtils class
#         ConfigTest depends on SimpleRegistryExporter
RUN cd /testbed && \
    mv dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java \
       dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java.disabled && \
    mv dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java \
       dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java.disabled

# Remove HttpUtilsTest.java (rename it to exclude from compilation)
# Reason: uses parseCharset() method that doesn't exist in HttpUtils class in START state
RUN cd /testbed && \
    mv dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java \
       dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java.disabled

# Configure git identity for commits
RUN git config --global user.email "test@test.com" && git config --global user.name "Test User"

# Commit patches and move END tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M018 on END state" && \
    git tag -f milestone-M018-end

# Build and install all artifacts to local Maven repository (for END state)
# This is needed because some modules depend on other modules' artifacts
RUN cd /testbed && \
    mvn install -DskipTests -B -q -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true 2>&1 | tail -30 || true

# Now prepare the START state pom.xml backup
RUN cd /testbed && git checkout milestone-M018-start && \
    sed -i '/<module>dubbo-plugin\/dubbo-spring6-security<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-demo\/dubbo-demo-api<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot-idl<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-distribution\/dubbo-all<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-distribution\/dubbo-all-shaded<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-distribution\/dubbo-apache-release<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-spring-boot-project\/.*<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-distribution\/dubbo-dependencies-all<\/module>/d' pom.xml && \
    cp pom.xml /testbed-backup/pom.xml.start

# Commit patches and move START tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M018 on START state" && \
    git tag -f milestone-M018-start

# Create a switch_state script to properly switch between START and END states
RUN echo '#!/bin/bash' > /usr/local/bin/switch_state.sh && \
    echo 'STATE=$1' >> /usr/local/bin/switch_state.sh && \
    echo 'cd /testbed' >> /usr/local/bin/switch_state.sh && \
    echo '' >> /usr/local/bin/switch_state.sh && \
    echo 'if [ "$STATE" = "start" ]; then' >> /usr/local/bin/switch_state.sh && \
    echo '    git checkout milestone-M018-start 2>/dev/null || true' >> /usr/local/bin/switch_state.sh && \
    echo '    cp /testbed-backup/pom.xml.start pom.xml' >> /usr/local/bin/switch_state.sh && \
    echo '    cp /testbed-backup/dubbo-security-pom.xml dubbo-plugin/dubbo-security/pom.xml' >> /usr/local/bin/switch_state.sh && \
    echo '    cp /testbed-backup/LoggerTest.java dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java' >> /usr/local/bin/switch_state.sh && \
    echo '    cp /testbed-backup/dubbo-test-pom.xml dubbo-test/pom.xml' >> /usr/local/bin/switch_state.sh && \
    echo 'elif [ "$STATE" = "end" ]; then' >> /usr/local/bin/switch_state.sh && \
    echo '    git checkout milestone-M018-end 2>/dev/null || true' >> /usr/local/bin/switch_state.sh && \
    echo '    cp /testbed-backup/pom.xml.end pom.xml' >> /usr/local/bin/switch_state.sh && \
    echo '    cp /testbed-backup/dubbo-security-pom.xml dubbo-plugin/dubbo-security/pom.xml' >> /usr/local/bin/switch_state.sh && \
    echo '    cp /testbed-backup/LoggerTest.java dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java' >> /usr/local/bin/switch_state.sh && \
    echo '    cp /testbed-backup/dubbo-test-pom.xml dubbo-test/pom.xml' >> /usr/local/bin/switch_state.sh && \
    echo 'else' >> /usr/local/bin/switch_state.sh && \
    echo '    echo "Unknown state: $STATE. Use start or end"' >> /usr/local/bin/switch_state.sh && \
    echo '    exit 1' >> /usr/local/bin/switch_state.sh && \
    echo 'fi' >> /usr/local/bin/switch_state.sh && \
    echo '# Delete .disabled files' >> /usr/local/bin/switch_state.sh && \
    echo 'find /testbed -name "*.disabled" -delete 2>/dev/null || true' >> /usr/local/bin/switch_state.sh && \
    echo '# Restore files from git and then delete problematic test files' >> /usr/local/bin/switch_state.sh && \
    echo 'git checkout -- dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java 2>/dev/null || true' >> /usr/local/bin/switch_state.sh && \
    echo 'git checkout -- dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java 2>/dev/null || true' >> /usr/local/bin/switch_state.sh && \
    echo 'git checkout -- dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java 2>/dev/null || true' >> /usr/local/bin/switch_state.sh && \
    echo 'git checkout -- dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java 2>/dev/null || true' >> /usr/local/bin/switch_state.sh && \
    echo 'git checkout -- dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java 2>/dev/null || true' >> /usr/local/bin/switch_state.sh && \
    echo 'git checkout -- dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java 2>/dev/null || true' >> /usr/local/bin/switch_state.sh && \
    echo 'git checkout -- dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java 2>/dev/null || true' >> /usr/local/bin/switch_state.sh && \
    echo 'rm -f dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java' >> /usr/local/bin/switch_state.sh && \
    echo 'rm -f dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java' >> /usr/local/bin/switch_state.sh && \
    echo 'rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java' >> /usr/local/bin/switch_state.sh && \
    echo 'rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java' >> /usr/local/bin/switch_state.sh && \
    echo 'rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java' >> /usr/local/bin/switch_state.sh && \
    echo 'rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java' >> /usr/local/bin/switch_state.sh && \
    echo 'rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java' >> /usr/local/bin/switch_state.sh && \
    chmod +x /usr/local/bin/switch_state.sh

# Disable post-checkout hook - apply_patches.sh will handle everything
# The hook was causing infinite recursion due to git checkout calls triggering it again
RUN rm -f /testbed/.git/hooks/post-checkout

# Create apply_patches.sh script that will be called by test runner after git checkout
# Use git describe to detect tag name since checkout creates detached HEAD
RUN echo '#!/bin/bash' > /usr/local/bin/apply_patches.sh && \
    echo '# Re-apply patches after git checkout' >> /usr/local/bin/apply_patches.sh && \
    echo 'cd /testbed' >> /usr/local/bin/apply_patches.sh && \
    echo 'TAG=$(git describe --tags --exact-match 2>/dev/null || echo "unknown")' >> /usr/local/bin/apply_patches.sh && \
    echo 'COMMIT=$(git log -1 --format="%H" | head -c 7)' >> /usr/local/bin/apply_patches.sh && \
    echo 'echo "Applying patches for tag=$TAG commit=$COMMIT"' >> /usr/local/bin/apply_patches.sh && \
    echo 'if [[ "$TAG" == *"M018-start"* ]] || [[ "$COMMIT" == "671e18b"* ]]; then' >> /usr/local/bin/apply_patches.sh && \
    echo '    cp /testbed-backup/pom.xml.start /testbed/pom.xml' >> /usr/local/bin/apply_patches.sh && \
    echo 'elif [[ "$TAG" == *"M018-end"* ]] || [[ "$COMMIT" == "b11aab9"* ]]; then' >> /usr/local/bin/apply_patches.sh && \
    echo '    cp /testbed-backup/pom.xml.end /testbed/pom.xml' >> /usr/local/bin/apply_patches.sh && \
    echo 'fi' >> /usr/local/bin/apply_patches.sh && \
    echo 'cp /testbed-backup/dubbo-security-pom.xml /testbed/dubbo-plugin/dubbo-security/pom.xml' >> /usr/local/bin/apply_patches.sh && \
    echo 'cp /testbed-backup/LoggerTest.java /testbed/dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java' >> /usr/local/bin/apply_patches.sh && \
    echo 'cp /testbed-backup/dubbo-test-pom.xml /testbed/dubbo-test/pom.xml' >> /usr/local/bin/apply_patches.sh && \
    echo '# Delete .disabled files' >> /usr/local/bin/apply_patches.sh && \
    echo 'find /testbed -name "*.disabled" -delete 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo '# Restore all tracked files from git (including deleted test files), then delete problematic ones' >> /usr/local/bin/apply_patches.sh && \
    echo 'git checkout -- /testbed/dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo 'git checkout -- /testbed/dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo 'git checkout -- /testbed/dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo 'git checkout -- /testbed/dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo 'git checkout -- /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo 'git checkout -- /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo 'git checkout -- /testbed/dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java 2>/dev/null || true' >> /usr/local/bin/apply_patches.sh && \
    echo '# Now delete the problematic test files' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f /testbed/dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f /testbed/dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f /testbed/dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f /testbed/dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java' >> /usr/local/bin/apply_patches.sh && \
    echo 'rm -f /testbed/dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java' >> /usr/local/bin/apply_patches.sh && \
    chmod +x /usr/local/bin/apply_patches.sh

# Set default state to START for dual-state testing
RUN /usr/local/bin/switch_state.sh start

# Commit the changes made by switch_state.sh and move the tag
RUN cd /testbed && \
    git add -A && \
    git commit --amend --no-edit && \
    git tag -f milestone-M018-start
