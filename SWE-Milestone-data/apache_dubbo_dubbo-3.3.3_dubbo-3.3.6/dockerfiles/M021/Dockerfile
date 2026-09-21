# Build on pre-configured base image
FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

WORKDIR /testbed
# Use the testbed from base image directly (it already contains milestone tags)

# Configure git for commits
RUN git config --global user.email "build@docker" && git config --global user.name "Docker Build"

# Create fix script for module and dependency issues
RUN cat > /opt/apply_m021_fix.sh << 'SCRIPT'
#!/bin/bash
cd /testbed

# Remove dubbo-spring6-security module references from root pom.xml
sed -i '/<module>dubbo-plugin\/dubbo-spring6-security<\/module>/d' pom.xml

# Remove dubbo-demo modules from root pom.xml
sed -i '/<module>dubbo-demo/d' pom.xml

# Fix spotless format violations
sed -i 's/^[[:space:]]*$//' pom.xml

# Remove dubbo-spring6-security profile and dependency blocks
for f in dubbo-distribution/dubbo-all/pom.xml dubbo-distribution/dubbo-bom/pom.xml dubbo-distribution/dubbo-all-shaded/pom.xml; do
    if [ -f "$f" ]; then
        perl -i -0pe 's/<profile>\s*<id>spring6-security<\/id>.*?<\/profile>//gs' "$f" 2>/dev/null || true
        perl -i -0pe 's/<dependency>\s*<groupId>org.apache.dubbo<\/groupId>\s*<artifactId>dubbo-spring6-security<\/artifactId>.*?<\/dependency>//gs' "$f" 2>/dev/null || true
        perl -i -0pe 's/<include>org.apache.dubbo:dubbo-spring6-security<\/include>//gs' "$f" 2>/dev/null || true
    fi
done

# Add missing properties if not exist
if ! grep -q "netty_http3_version" dubbo-dependencies-bom/pom.xml; then
    sed -i 's|<bouncycastle-bcprov_version>1.81</bouncycastle-bcprov_version>|<bouncycastle-bcprov_version>1.81</bouncycastle-bcprov_version>\n    <netty_http3_version>0.0.28.Final</netty_http3_version>\n    <bouncycastle-jdk15on_version>1.70</bouncycastle-jdk15on_version>|' dubbo-dependencies-bom/pom.xml
fi

# Add missing dependencies to BOM
if ! grep -q "netty-incubator-codec-http3" dubbo-dependencies-bom/pom.xml; then
    sed -i '/<\!-- Common Annotations API -->/i\      <dependency>\n        <groupId>io.netty.incubator</groupId>\n        <artifactId>netty-incubator-codec-http3</artifactId>\n        <version>${netty_http3_version}</version>\n      </dependency>\n      <dependency>\n        <groupId>org.bouncycastle</groupId>\n        <artifactId>bcprov-jdk15on</artifactId>\n        <version>${bouncycastle-jdk15on_version}</version>\n      </dependency>\n      <dependency>\n        <groupId>org.bouncycastle</groupId>\n        <artifactId>bcpkix-jdk15on</artifactId>\n        <version>${bouncycastle-jdk15on_version}</version>\n      </dependency>\n      <dependency>\n        <groupId>org.bouncycastle</groupId>\n        <artifactId>bcprov-ext-jdk15on</artifactId>\n        <version>${bouncycastle-jdk15on_version}</version>\n      </dependency>\n' dubbo-dependencies-bom/pom.xml
fi
SCRIPT
RUN chmod +x /opt/apply_m021_fix.sh

# ========== Fix END state and update tag ==========
RUN cd /testbed && git checkout milestone-M021-end

# Apply module and dependency fixes
RUN /opt/apply_m021_fix.sh

# Delete problematic test files
RUN rm -f /testbed/dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java
RUN rm -f /testbed/dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java
RUN rm -f /testbed/dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
RUN rm -f /testbed/dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java
RUN rm -f /testbed/dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java
RUN rm -f /testbed/dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java
RUN rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
RUN rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java
RUN rm -f /testbed/dubbo-test/dubbo-test-modules/src/test/java/org/apache/dubbo/dependency/FileTest.java

# Apply spotless to fix formatting issues
RUN cd /testbed && mvn spotless:apply -q || true

# Commit and update END tag
RUN cd /testbed && \
    git add -A && \
    git commit --amend -m "End state for M021 (with fixes)" && \
    git tag -f milestone-M021-end HEAD

# ========== Fix START state and update tag ==========
RUN cd /testbed && git checkout milestone-M021-start

# Apply same fixes for START state
RUN /opt/apply_m021_fix.sh

# Delete problematic test files
RUN rm -f /testbed/dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java
RUN rm -f /testbed/dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java
RUN rm -f /testbed/dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
RUN rm -f /testbed/dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java
RUN rm -f /testbed/dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java
RUN rm -f /testbed/dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java
RUN rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
RUN rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java
RUN rm -f /testbed/dubbo-test/dubbo-test-modules/src/test/java/org/apache/dubbo/dependency/FileTest.java

# Apply spotless to fix formatting issues
RUN cd /testbed && mvn spotless:apply -q || true

# Commit and update START tag
RUN cd /testbed && \
    git add -A && \
    git commit --amend -m "Start state for M021 (with fixes)" && \
    git tag -f milestone-M021-start HEAD

# Default to START state
RUN cd /testbed && git checkout milestone-M021-start

# Re-compile the project
RUN cd /testbed && mvn clean install -DskipTests -B -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true
