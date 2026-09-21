#!/bin/bash
# Fix test compilation errors by renaming problematic test files
# These tests reference methods/classes that don't exist at the milestone state
# Using rename instead of /* */ comments to avoid nested comment issues

cd /testbed

echo "Fixing test compilation issues..."

# Helper function to disable a test file by renaming it
disable_test_file() {
    local file="$1"
    if [ -f "$file" ]; then
        mv "$file" "${file}.disabled"
        echo "Disabled: $file"
    fi
}

# 1. Fix LoggerTest.java - uses non-existent getLogger(String, Class) overload
disable_test_file "dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java"

# 2. Fix MetricsSupportTest.java - uses fillZero with incompatible types
disable_test_file "dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java"

# 3. Fix HttpUtilsTest.java - parseCharset method does not exist
disable_test_file "dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java"

# 4. Fix AffinityRouteTest.java - AffinityServiceStateRouter class does not exist
disable_test_file "dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java"

# 5. Fix MetaCacheManagerTest.java - calRevision() method does not exist
disable_test_file "dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java"

# 6. Fix SimpleRegistryExporter.java - isReuseAddressSupported() method does not exist
# This is a test helper class used by other tests, so we need to fix it differently
# The issue is the if (NetUtils.isReuseAddressSupported()) check - we need to replace the whole if block
# with just the setReuseAddress(true) call
SIMPLE_REG_FILE="dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java"
if [ -f "$SIMPLE_REG_FILE" ]; then
    # Replace: if (NetUtils.isReuseAddressSupported()) { ... setReuseAddress(true); }
    # With: just setReuseAddress(true);
    sed -i 's/if (NetUtils.isReuseAddressSupported()) {/\/\/ Removed isReuseAddressSupported check - method does not exist in this milestone/g' "$SIMPLE_REG_FILE" 2>/dev/null
    # Remove the closing brace of the if statement (line after setReuseAddress)
    sed -i '/\/\/ SO_REUSEADDR should be enabled before bind./,+2 { /^[[:space:]]*}$/d }' "$SIMPLE_REG_FILE" 2>/dev/null
    echo "Fixed: $SIMPLE_REG_FILE"
fi

# 7. Fix NacosNamingServiceWrapperTest.java - getAllInstancesWithoutSubscription method does not exist
disable_test_file "dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java"

echo "Test compilation fixes applied successfully"
