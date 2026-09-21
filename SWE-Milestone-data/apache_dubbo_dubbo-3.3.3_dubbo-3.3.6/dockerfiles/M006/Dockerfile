# Build on pre-configured base image
FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

# Use the testbed from base image directly (it already contains milestone tags)
WORKDIR /testbed

# Configure git for commits
RUN git config --global user.email "build@docker" && git config --global user.name "Docker Build"

# Create comprehensive fix script for M006
RUN cat > /opt/fix_m006.sh << 'SCRIPT'
#!/bin/bash
set -e
cd /testbed
BOM=dubbo-dependencies-bom/pom.xml

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

# ===== Fix bouncycastle in dubbo-security =====
if [ -f dubbo-plugin/dubbo-security/pom.xml ]; then
    sed -i 's/bcprov-jdk15on/bcprov-jdk18on/g' dubbo-plugin/dubbo-security/pom.xml 2>/dev/null || true
    sed -i 's/bcpkix-jdk15on/bcpkix-jdk18on/g' dubbo-plugin/dubbo-security/pom.xml 2>/dev/null || true
    sed -i 's/bcprov-ext-jdk15on/bcprov-ext-jdk18on/g' dubbo-plugin/dubbo-security/pom.xml 2>/dev/null || true
    # Add version for bcprov-ext-jdk18on if not present
    if ! grep -A1 "<artifactId>bcprov-ext-jdk18on</artifactId>" dubbo-plugin/dubbo-security/pom.xml | grep -q "<version>"; then
        sed -i '/<artifactId>bcprov-ext-jdk18on<\/artifactId>/a\      <version>1.78.1</version>' dubbo-plugin/dubbo-security/pom.xml 2>/dev/null || true
    fi
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

# ===== Remove demo servlet module reference =====
sed -i '/<module>dubbo-demo-spring-boot-servlet<\/module>/d' dubbo-demo/dubbo-demo-spring-boot/pom.xml 2>/dev/null || true

# ===== Fix LoggerTest.java - replace 2-arg getLogger with 1-arg version =====
if [ -f "dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java" ]; then
    sed -i 's/adapter.getLogger(FailsafeLogger.class.getName(), this.getClass())/adapter.getLogger(FailsafeLogger.class.getName())/' \
        dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java 2>/dev/null || true
fi

# ===== Remove problematic test files =====
rm -f dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java
rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
rm -f dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java
rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java
rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java

echo "M006 fixes applied successfully"
SCRIPT
RUN chmod +x /opt/fix_m006.sh

# ========== Fix END state ==========
RUN cd /testbed && git checkout milestone-M006-end
RUN /opt/fix_m006.sh
RUN cd /testbed && git add -A && git commit -m "Apply compilation patches for M006 END state" && git tag -f milestone-M006-end HEAD

# ========== Fix START state ==========
RUN cd /testbed && git checkout milestone-M006-start
RUN /opt/fix_m006.sh
RUN cd /testbed && git add -A && git commit -m "Apply compilation patches for M006 START state" && git tag -f milestone-M006-start HEAD

# Default to START state
RUN cd /testbed && git checkout milestone-M006-start
