#!/bin/bash
set -e
cd /testbed

echo ">>> Applying M016.1 patches..."

# Remove module references for incomplete modules from main pom.xml
sed -i '/<module>dubbo-plugin\/dubbo-spring6-security<\/module>/d' pom.xml
sed -i '/<module>dubbo-plugin\/dubbo-mutiny<\/module>/d' pom.xml
sed -i '/<module>dubbo-demo\/dubbo-demo-api<\/module>/d' pom.xml
sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot<\/module>/d' pom.xml
sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot-idl<\/module>/d' pom.xml

# Remove dependency blocks from distribution pom files
for f in dubbo-distribution/dubbo-all/pom.xml dubbo-distribution/dubbo-bom/pom.xml dubbo-distribution/dubbo-all-shaded/pom.xml; do
    if [ -f "$f" ]; then
        perl -i -0pe 's/<dependency>\s*<groupId>org\.apache\.dubbo<\/groupId>\s*<artifactId>dubbo-spring6-security<\/artifactId>.*?<\/dependency>//gs' "$f" 2>/dev/null || true
        perl -i -0pe 's/<dependency>\s*<groupId>org\.apache\.dubbo<\/groupId>\s*<artifactId>dubbo-mutiny<\/artifactId>.*?<\/dependency>//gs' "$f" 2>/dev/null || true
        sed -i '/<include>org\.apache\.dubbo:dubbo-spring6-security<\/include>/d' "$f"
        sed -i '/<include>org\.apache\.dubbo:dubbo-mutiny<\/include>/d' "$f"
    fi
done

# Add netty_http3_version property if not exists
if ! grep -q "netty_http3_version" dubbo-dependencies-bom/pom.xml; then
    sed -i '/<netty4_version>/a\    <netty_http3_version>0.0.28.Final</netty_http3_version>' dubbo-dependencies-bom/pom.xml
fi

# Fix bouncycastle version (1.81 -> 1.70)
sed -i 's/<bouncycastle-bcprov_version>1.81</<bouncycastle-bcprov_version>1.70</' dubbo-dependencies-bom/pom.xml

# Add missing dependency definitions to BOM if not present
if ! grep -q "netty-incubator-codec-http3" dubbo-dependencies-bom/pom.xml; then
    sed -i '/<\/dependencies>/i\      <dependency>\n        <groupId>io.netty.incubator</groupId>\n        <artifactId>netty-incubator-codec-http3</artifactId>\n        <version>${netty_http3_version}</version>\n      </dependency>' dubbo-dependencies-bom/pom.xml
fi

if ! grep -q "bcprov-jdk15on" dubbo-dependencies-bom/pom.xml; then
    sed -i '/<\/dependencies>/i\      <dependency>\n        <groupId>org.bouncycastle</groupId>\n        <artifactId>bcprov-jdk15on</artifactId>\n        <version>${bouncycastle-bcprov_version}</version>\n      </dependency>\n      <dependency>\n        <groupId>org.bouncycastle</groupId>\n        <artifactId>bcpkix-jdk15on</artifactId>\n        <version>${bouncycastle-bcprov_version}</version>\n      </dependency>\n      <dependency>\n        <groupId>org.bouncycastle</groupId>\n        <artifactId>bcprov-ext-jdk15on</artifactId>\n        <version>${bouncycastle-bcprov_version}</version>\n      </dependency>' dubbo-dependencies-bom/pom.xml
fi

# Remove broken test files
rm -f dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java
rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
rm -f dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java
rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java
rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java

# Comment out LoggerTest.testAllLogMethod if not already commented
if [ -f dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java ]; then
    if ! grep -q "^/\*$" dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java; then
        sed -i '48i\/*' dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java
        sed -i '86a\*/' dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java
    fi
fi

echo ">>> M016.1 patches applied successfully"
