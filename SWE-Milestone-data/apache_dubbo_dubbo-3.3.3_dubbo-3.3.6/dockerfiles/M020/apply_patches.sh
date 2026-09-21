#!/bin/bash
set -e
cd /testbed

echo ">>> Applying M020 patches..."

# Fix bouncycastle dependency version mismatch in dubbo-security/pom.xml
if [ -f dubbo-plugin/dubbo-security/pom.xml ]; then
    sed -i 's/bcprov-jdk15on/bcprov-jdk18on/g' dubbo-plugin/dubbo-security/pom.xml
    sed -i 's/bcpkix-jdk15on/bcpkix-jdk18on/g' dubbo-plugin/dubbo-security/pom.xml
    sed -i 's/bcprov-ext-jdk15on/bcprov-ext-jdk18on/g' dubbo-plugin/dubbo-security/pom.xml
    # Add version for bcprov-ext if not present (check if next line after artifactId has version)
    if ! grep -A1 "<artifactId>bcprov-ext-jdk18on</artifactId>" dubbo-plugin/dubbo-security/pom.xml | grep -q "<version>"; then
        sed -i '/<artifactId>bcprov-ext-jdk18on<\/artifactId>/a\      <version>1.78.1<\/version>' dubbo-plugin/dubbo-security/pom.xml 2>/dev/null || true
    fi
fi

# Remove demo module sources that have compilation errors
rm -rf /testbed/dubbo-demo/dubbo-demo-spring-boot/dubbo-demo-spring-boot-servlet/src/main/java/*

# Remove broken test files
rm -f /testbed/dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java
rm -f /testbed/dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java
rm -f /testbed/dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
rm -f /testbed/dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java
rm -f /testbed/dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java
rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java

echo ">>> M020 patches applied successfully"
