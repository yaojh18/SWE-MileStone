# Build on pre-configured base image
FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

WORKDIR /testbed
# Use the testbed from base image directly (it already contains milestone tags)

# Checkout to END state (all features available)
RUN cd /testbed && git checkout milestone-M019-end

# Fix: Remove dubbo-spring6-security module and dependency references as the module pom.xml doesn't exist in this milestone
# The module directory only contains test files without a pom.xml
# Also remove demo-spring-boot modules that have compilation errors due to missing ServerSentEvent class
RUN cd /testbed && \
    sed -i '/<module>dubbo-plugin\/dubbo-spring6-security<\/module>/d' pom.xml && \
    sed -i '/<module>dubbo-demo-spring-boot-servlet<\/module>/d' dubbo-demo/dubbo-demo-spring-boot/pom.xml

# Remove dubbo-spring6-security dependency blocks from distribution poms
# Using multi-line sed to remove entire dependency blocks and fix empty elements
RUN cd /testbed && \
    sed -i '/<dependency>/{:a;N;/<\/dependency>/!ba;/dubbo-spring6-security/d}' dubbo-distribution/dubbo-all/pom.xml && \
    sed -i '/<dependency>/{:a;N;/<\/dependency>/!ba;/dubbo-spring6-security/d}' dubbo-distribution/dubbo-bom/pom.xml && \
    sed -i '/<dependency>/{:a;N;/<\/dependency>/!ba;/dubbo-spring6-security/d}' dubbo-distribution/dubbo-all-shaded/pom.xml && \
    sed -i '/<include>org.apache.dubbo:dubbo-spring6-security<\/include>/d' dubbo-distribution/dubbo-all/pom.xml && \
    sed -i '/<include>org.apache.dubbo:dubbo-spring6-security<\/include>/d' dubbo-distribution/dubbo-all-shaded/pom.xml

# Fix empty dependencies elements by collapsing multi-line empty elements
RUN cd /testbed && \
    perl -i -0pe 's/<dependencies>\s*<\/dependencies>/<dependencies \/>/g' dubbo-distribution/dubbo-all/pom.xml && \
    perl -i -0pe 's/<dependencies>\s*<\/dependencies>/<dependencies \/>/g' dubbo-distribution/dubbo-all-shaded/pom.xml

# Fix double blank lines in dubbo-bom/pom.xml after dependency removal
RUN cd /testbed && \
    perl -i -0pe 's/\n\n\n/\n\n/g' dubbo-distribution/dubbo-bom/pom.xml

# Fix: Add missing dependency versions to dubbo-dependencies-bom/pom.xml
# The milestone patches removed these from the BOM but the modules still reference them

# Add netty-incubator-codec-http3 version property
RUN cd /testbed && \
    sed -i 's/<bouncycastle-bcprov_version>1.81<\/bouncycastle-bcprov_version>/<bouncycastle-bcprov_version>1.81<\/bouncycastle-bcprov_version>\n    <netty_http3_version>0.0.28.Final<\/netty_http3_version>/' dubbo-dependencies-bom/pom.xml

# Create a patch file for the missing dependencies and apply it inline
RUN cd /testbed && \
    LINE_NUM=$(grep -n '<artifactId>bcpkix-jdk18on</artifactId>' dubbo-dependencies-bom/pom.xml | cut -d: -f1) && \
    INSERT_LINE=$((LINE_NUM + 2)) && \
    echo '      <dependency>' > /tmp/bom_patch.xml && \
    echo '        <groupId>io.netty.incubator</groupId>' >> /tmp/bom_patch.xml && \
    echo '        <artifactId>netty-incubator-codec-http3</artifactId>' >> /tmp/bom_patch.xml && \
    echo '        <version>${netty_http3_version}</version>' >> /tmp/bom_patch.xml && \
    echo '      </dependency>' >> /tmp/bom_patch.xml && \
    echo '      <dependency>' >> /tmp/bom_patch.xml && \
    echo '        <groupId>org.bouncycastle</groupId>' >> /tmp/bom_patch.xml && \
    echo '        <artifactId>bcprov-jdk15on</artifactId>' >> /tmp/bom_patch.xml && \
    echo '        <version>1.70</version>' >> /tmp/bom_patch.xml && \
    echo '      </dependency>' >> /tmp/bom_patch.xml && \
    echo '      <dependency>' >> /tmp/bom_patch.xml && \
    echo '        <groupId>org.bouncycastle</groupId>' >> /tmp/bom_patch.xml && \
    echo '        <artifactId>bcpkix-jdk15on</artifactId>' >> /tmp/bom_patch.xml && \
    echo '        <version>1.70</version>' >> /tmp/bom_patch.xml && \
    echo '      </dependency>' >> /tmp/bom_patch.xml && \
    echo '      <dependency>' >> /tmp/bom_patch.xml && \
    echo '        <groupId>org.bouncycastle</groupId>' >> /tmp/bom_patch.xml && \
    echo '        <artifactId>bcprov-ext-jdk15on</artifactId>' >> /tmp/bom_patch.xml && \
    echo '        <version>1.70</version>' >> /tmp/bom_patch.xml && \
    echo '      </dependency>' >> /tmp/bom_patch.xml && \
    head -n $INSERT_LINE dubbo-dependencies-bom/pom.xml > /tmp/bom_new.xml && \
    cat /tmp/bom_patch.xml >> /tmp/bom_new.xml && \
    tail -n +$((INSERT_LINE + 1)) dubbo-dependencies-bom/pom.xml >> /tmp/bom_new.xml && \
    mv /tmp/bom_new.xml dubbo-dependencies-bom/pom.xml

# Fix: Delete LoggerTest.java file
# Reason: The test uses FailsafeErrorTypeAwareLogger and FailsafeLogger classes with incompatible API
# The testAllLogMethod calls adapter.getLogger() with 2 arguments, but the API only has 1-argument methods
RUN cd /testbed && rm -f dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java

# Fix: Delete MetricsSupportTest.java file
# Reason: The test passes ConcurrentHashMap<..., ConcurrentHashMap<...>> to fillZero()
# but the method signature expects Map<?, Map<T, AtomicLong>> - type mismatch
RUN cd /testbed && rm -f dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java

# Fix: Delete HttpUtilsTest.java file
# Reason: The test uses HttpUtils.parseCharset() method which doesn't exist in this milestone
RUN cd /testbed && rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java

# Fix: Delete AffinityRouteTest.java file
# Reason: The test imports org.apache.dubbo.rpc.cluster.router.affinity.config which doesn't exist
RUN cd /testbed && rm -f dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java

# Fix: Delete MetaCacheManagerTest.java file
# Reason: The test uses MetadataInfo.calRevision() method which doesn't exist
RUN cd /testbed && rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java

# Fix: Delete NacosNamingServiceWrapperTest.java file
# Reason: The test uses NacosNamingServiceWrapper.getAllInstancesWithoutSubscription() which doesn't exist
RUN cd /testbed && rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java

# Fix: Delete SimpleRegistryExporter.java and ConfigTest.java files
# Reason: SimpleRegistryExporter uses NetUtils.isReuseAddressSupported() which doesn't exist
# ConfigTest depends on SimpleRegistryExporter
RUN cd /testbed && rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java && \
    rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java

# Apply spotless on distribution modules to fix formatting issues
RUN cd /testbed && mvn spotless:apply -f dubbo-distribution/pom.xml -pl dubbo-all -q || true

# Configure git identity for commits
RUN git config --global user.email "test@test.com" && git config --global user.name "Test User"

# Commit patches and move END tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M019 on END state" && \
    git tag -f milestone-M019-end

# Install all modules for END state to local Maven repository
# This ensures dependencies are available when switching between states
RUN cd /testbed && mvn install -DskipTests -q -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true

# Create apply_patches.sh script inline
RUN cat > /usr/local/bin/apply_patches.sh << 'PATCH_SCRIPT'
#!/bin/bash
set -e
cd /testbed

echo ">>> Applying M019 patches..."

# Remove module references
sed -i '/<module>dubbo-plugin\/dubbo-spring6-security<\/module>/d' pom.xml
sed -i '/<module>dubbo-demo-spring-boot-servlet<\/module>/d' dubbo-demo/dubbo-demo-spring-boot/pom.xml 2>/dev/null || true

# Remove dubbo-spring6-security dependency blocks from distribution poms
for f in dubbo-distribution/dubbo-all/pom.xml dubbo-distribution/dubbo-bom/pom.xml dubbo-distribution/dubbo-all-shaded/pom.xml; do
    if [ -f "$f" ]; then
        sed -i '/<dependency>/{:a;N;/<\/dependency>/!ba;/dubbo-spring6-security/d}' "$f" 2>/dev/null || true
        sed -i '/<include>org.apache.dubbo:dubbo-spring6-security<\/include>/d' "$f"
    fi
done

# Fix empty dependencies elements
perl -i -0pe 's/<dependencies>\s*<\/dependencies>/<dependencies \/>/g' dubbo-distribution/dubbo-all/pom.xml 2>/dev/null || true
perl -i -0pe 's/<dependencies>\s*<\/dependencies>/<dependencies \/>/g' dubbo-distribution/dubbo-all-shaded/pom.xml 2>/dev/null || true

# Add netty_http3_version property if not exists
if ! grep -q "netty_http3_version" dubbo-dependencies-bom/pom.xml; then
    sed -i 's/<bouncycastle-bcprov_version>1.81<\/bouncycastle-bcprov_version>/<bouncycastle-bcprov_version>1.81<\/bouncycastle-bcprov_version>\n    <netty_http3_version>0.0.28.Final<\/netty_http3_version>/' dubbo-dependencies-bom/pom.xml
fi

# Add missing dependencies to BOM if not present
if ! grep -q "netty-incubator-codec-http3" dubbo-dependencies-bom/pom.xml; then
    LINE_NUM=$(grep -n '<artifactId>bcpkix-jdk18on</artifactId>' dubbo-dependencies-bom/pom.xml | cut -d: -f1)
    if [ -n "$LINE_NUM" ]; then
        INSERT_LINE=$((LINE_NUM + 2))
        head -n $INSERT_LINE dubbo-dependencies-bom/pom.xml > /tmp/bom_new.xml
        cat >> /tmp/bom_new.xml << 'BOMEOF'
      <dependency>
        <groupId>io.netty.incubator</groupId>
        <artifactId>netty-incubator-codec-http3</artifactId>
        <version>${netty_http3_version}</version>
      </dependency>
      <dependency>
        <groupId>org.bouncycastle</groupId>
        <artifactId>bcprov-jdk15on</artifactId>
        <version>1.70</version>
      </dependency>
      <dependency>
        <groupId>org.bouncycastle</groupId>
        <artifactId>bcpkix-jdk15on</artifactId>
        <version>1.70</version>
      </dependency>
      <dependency>
        <groupId>org.bouncycastle</groupId>
        <artifactId>bcprov-ext-jdk15on</artifactId>
        <version>1.70</version>
      </dependency>
BOMEOF
        tail -n +$((INSERT_LINE + 1)) dubbo-dependencies-bom/pom.xml >> /tmp/bom_new.xml
        mv /tmp/bom_new.xml dubbo-dependencies-bom/pom.xml
    fi
fi

# Remove broken test files
rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
rm -f dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java
rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java
rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java

# Delete LoggerTest.java - uses FailsafeErrorTypeAwareLogger/FailsafeLogger with incompatible API
rm -f dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java

# Delete MetricsSupportTest.java - type mismatch in fillZero() method call
rm -f dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java

echo ">>> M019 patches applied successfully"
PATCH_SCRIPT
RUN chmod +x /usr/local/bin/apply_patches.sh

# Set default git state to START and apply patches
RUN cd /testbed && git checkout milestone-M019-start && /usr/local/bin/apply_patches.sh

# Commit patches and move START tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M019 on START state" && \
    git tag -f milestone-M019-start

# Install all modules for START state to local Maven repository
RUN cd /testbed && mvn install -DskipTests -q -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true
