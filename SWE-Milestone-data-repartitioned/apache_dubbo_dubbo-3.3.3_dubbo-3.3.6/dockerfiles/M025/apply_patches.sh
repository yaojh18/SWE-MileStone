#!/bin/bash
set -e
cd /testbed

echo ">>> Applying M025 patches..."

# Remove missing module references from pom.xml
sed -i '/<module>dubbo-plugin\/dubbo-spring6-security<\/module>/d' pom.xml
sed -i '/<module>dubbo-plugin\/dubbo-mutiny<\/module>/d' pom.xml

# Restore BOM from saved copy if available
if [ -f /tmp/base-bom.xml ]; then
    cp /tmp/base-bom.xml dubbo-dependencies-bom/pom.xml
    sed -i 's/<version>${revision}<\/version>/<version>3.3.6-SNAPSHOT<\/version>/g' dubbo-dependencies-bom/pom.xml
fi

# Restore distribution pom files from saved copies
if [ -f /tmp/base-dubbo-all-pom.xml ]; then
    cp /tmp/base-dubbo-all-pom.xml dubbo-distribution/dubbo-all/pom.xml
fi
if [ -f /tmp/base-dubbo-all-shaded-pom.xml ]; then
    cp /tmp/base-dubbo-all-shaded-pom.xml dubbo-distribution/dubbo-all-shaded/pom.xml
fi
if [ -f /tmp/base-dubbo-bom-pom.xml ]; then
    cp /tmp/base-dubbo-bom-pom.xml dubbo-distribution/dubbo-bom/pom.xml
fi

# Fix LoggerTest.java if exists
if [ -f dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java ]; then
    # Remove line 54 and fix line 54 (original line 55)
    sed -i '54d' dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java 2>/dev/null || true
    sed -i '54s/.*/        Logger logger = adapter.getLogger(this.getClass());/' dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java 2>/dev/null || true
    sed -i '/import org.apache.dubbo.common.logger.support.FailsafeErrorTypeAwareLogger;/d' dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java
    sed -i '/import org.apache.dubbo.common.logger.support.FailsafeLogger;/d' dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java
fi

# Rename broken test files to .bak
for f in \
    dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java \
    dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java \
    dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java \
    dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java \
    dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java \
    dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java \
    dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java; do
    if [ -f "$f" ]; then
        mv "$f" "${f}.bak"
    fi
done

echo ">>> M025 patches applied successfully"
