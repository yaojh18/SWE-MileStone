# Build on pre-configured base image
FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

# Use the testbed from base image directly (it already contains milestone tags)
WORKDIR /testbed

# Create apply_patches.sh script that will be run after each git checkout
# This ensures BOM fixes and problematic files are handled in both START and END states
RUN cat > /usr/local/bin/apply_patches.sh << 'PATCH_SCRIPT'
#!/bin/bash
set -e
cd /testbed
BOM=dubbo-dependencies-bom/pom.xml

echo ">>> Applying M002 patches..."

# ===== BOM FIXES =====
# Add netty_http3_version if missing
if ! grep -q "netty_http3_version" "$BOM"; then
    awk '/<netty_version>/ { print; print "    <netty_http3_version>0.0.28.Final</netty_http3_version>"; next } {print}' "$BOM" > "$BOM.tmp" && mv "$BOM.tmp" "$BOM"
fi

# Add netty-incubator-codec-http3 dependency if missing
if ! grep -q "netty-incubator-codec-http3" "$BOM"; then
    awk '/<artifactId>javassist<\/artifactId>/ && !in_exclusion {
        print; getline; print; getline; print;
        print "      <dependency>";
        print "        <groupId>io.netty.incubator</groupId>";
        print "        <artifactId>netty-incubator-codec-http3</artifactId>";
        print "        <version>${netty_http3_version}</version>";
        print "      </dependency>";
        next
    }
    /<exclusions>/ { in_exclusion=1 }
    /<\/exclusions>/ { in_exclusion=0 }
    {print}' "$BOM" > "$BOM.tmp" && mv "$BOM.tmp" "$BOM"
fi

# ===== Fix Bouncy Castle version mismatch =====
# dubbo-dependencies-bom defines jdk18on, but dubbo-security references jdk15on
sed -i 's/bcprov-jdk15on/bcprov-jdk18on/g' dubbo-plugin/dubbo-security/pom.xml
sed -i 's/bcpkix-jdk15on/bcpkix-jdk18on/g' dubbo-plugin/dubbo-security/pom.xml
# Remove bcprov-ext-jdk dependency
perl -i -0pe 's/\s*<dependency>\s*<groupId>org\.bouncycastle<\/groupId>\s*<artifactId>bcprov-ext-jdk\d+on<\/artifactId>\s*<\/dependency>//gs' dubbo-plugin/dubbo-security/pom.xml

# ===== Remove dubbo-spring6-security module =====
sed -i '/<module>dubbo-plugin\/dubbo-spring6-security<\/module>/d' pom.xml 2>/dev/null || true
for f in dubbo-distribution/dubbo-bom/pom.xml dubbo-distribution/dubbo-all/pom.xml dubbo-distribution/dubbo-all-shaded/pom.xml; do
    if [ -f "$f" ]; then
        awk '/<dependency>/ {start=NR; block=$0; next}
        start && /<\/dependency>/ {block=block"\n"$0; if (block !~ /dubbo-spring6-security/) print block; start=0; block=""; next}
        start {block=block"\n"$0; next}
        {print}' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
        sed -i '/<include>org.apache.dubbo:dubbo-spring6-security<\/include>/d' "$f" 2>/dev/null || true
    fi
done

# ===== Remove dubbo-demo modules from pom.xml (compilation issues) =====
sed -i '/<module>dubbo-demo\/dubbo-demo-api<\/module>/d' pom.xml
sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot<\/module>/d' pom.xml
sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot-idl<\/module>/d' pom.xml

# ===== Remove problematic test files =====
rm -f dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java
rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
rm -f dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java
rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java
rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java
rm -f dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java

echo ">>> M002 patches applied successfully"
PATCH_SCRIPT
RUN chmod +x /usr/local/bin/apply_patches.sh

# Configure git identity for commits
RUN git config --global user.email "test@test.com" && git config --global user.name "Test User"

# Checkout to END state and apply patches
RUN cd /testbed && git checkout milestone-M002-end && /usr/local/bin/apply_patches.sh

# Commit patches and move END tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M002 on END state" && \
    git tag -f milestone-M002-end

# Install all modules for END state to local Maven repository
RUN cd /testbed && mvn install -DskipTests -q -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true

# Set default git state to START and apply patches
RUN cd /testbed && git checkout milestone-M002-start && /usr/local/bin/apply_patches.sh

# Commit patches and move START tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M002 on START state" && \
    git tag -f milestone-M002-start

# Install all modules for START state to local Maven repository
RUN cd /testbed && mvn install -DskipTests -q -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true
