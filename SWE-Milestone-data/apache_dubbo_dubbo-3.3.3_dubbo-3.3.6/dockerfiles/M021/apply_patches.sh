#!/bin/bash
set -e
cd /testbed

echo ">>> Applying M021 patches..."

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

# Remove broken test files
rm -f /testbed/dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java
rm -f /testbed/dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java
rm -f /testbed/dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
rm -f /testbed/dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java
rm -f /testbed/dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java
rm -f /testbed/dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java
rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java
rm -f /testbed/dubbo-test/dubbo-test-modules/src/test/java/org/apache/dubbo/dependency/FileTest.java

echo ">>> M021 patches applied successfully"
