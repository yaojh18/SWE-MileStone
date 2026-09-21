# Build on pre-configured base image
FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

WORKDIR /testbed
# Use the testbed from base image directly (it already contains milestone tags)

# Create apply_patches.sh script that will be run after each git checkout
# This ensures problematic test files are removed in both START and END states
RUN cat > /usr/local/bin/apply_patches.sh << 'PATCH_SCRIPT'
#!/bin/bash
set -e
cd /testbed
BOM=dubbo-dependencies-bom/pom.xml

echo ">>> Applying M003.2 patches..."

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

# Add bouncycastle-jdk15on_version if missing
if ! grep -q "bouncycastle-jdk15on_version" "$BOM"; then
    awk '/<bouncycastle-bcprov_version>/ { print; print "    <bouncycastle-jdk15on_version>1.70</bouncycastle-jdk15on_version>"; next } {print}' "$BOM" > "$BOM.tmp" && mv "$BOM.tmp" "$BOM"

    awk 'BEGIN { in_bcpkix=0 }
    /<artifactId>bcpkix-jdk18on<\/artifactId>/ { in_bcpkix=1 }
    in_bcpkix && /<\/dependency>/ {
        print;
        print "      <dependency>"; print "        <groupId>org.bouncycastle</groupId>"; print "        <artifactId>bcprov-jdk15on</artifactId>"; print "        <version>${bouncycastle-jdk15on_version}</version>"; print "      </dependency>";
        print "      <dependency>"; print "        <groupId>org.bouncycastle</groupId>"; print "        <artifactId>bcpkix-jdk15on</artifactId>"; print "        <version>${bouncycastle-jdk15on_version}</version>"; print "      </dependency>";
        print "      <dependency>"; print "        <groupId>org.bouncycastle</groupId>"; print "        <artifactId>bcprov-ext-jdk15on</artifactId>"; print "        <version>${bouncycastle-jdk15on_version}</version>"; print "      </dependency>";
        in_bcpkix=0; next
    }
    {print}' "$BOM" > "$BOM.tmp" && mv "$BOM.tmp" "$BOM"
fi

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

# ===== Remove dubbo-demo modules from pom.xml =====
sed -i '/<module>dubbo-demo\/dubbo-demo-api<\/module>/d' pom.xml
sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot<\/module>/d' pom.xml
sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot-idl<\/module>/d' pom.xml

# ===== Fix test compilation issues =====
# Fix LoggerTest.java - replace 2-arg getLogger with 1-arg version
sed -i 's/adapter.getLogger(FailsafeLogger.class.getName(), this.getClass())/adapter.getLogger(FailsafeLogger.class.getName())/' \
    dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java 2>/dev/null || true

# Remove problematic test files
rm -f dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java
rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
rm -f dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java
rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java
rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java

# Remove all dubbo-mutiny test files - they reference classes that don't exist in this milestone
rm -rf dubbo-plugin/dubbo-mutiny/src/test/java/

echo ">>> M003.2 patches applied successfully"
PATCH_SCRIPT
RUN chmod +x /usr/local/bin/apply_patches.sh

# Checkout to END state and apply patches
RUN cd /testbed && git checkout milestone-M003.2-end && /usr/local/bin/apply_patches.sh

# Configure git identity for commits
RUN git config --global user.email "test@test.com" && git config --global user.name "Test User"

# Commit patches and move END tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M003.2 on END state" && \
    git tag -f milestone-M003.2-end

# Install all modules for END state to local Maven repository
RUN cd /testbed && mvn install -DskipTests -q -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true

# Set default git state to START and apply patches
RUN cd /testbed && git checkout milestone-M003.2-start && /usr/local/bin/apply_patches.sh

# Commit patches and move START tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M003.2 on START state" && \
    git tag -f milestone-M003.2-start

# Install all modules for START state to local Maven repository
RUN cd /testbed && mvn install -DskipTests -q -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true
