#!/bin/bash
# Apply patches that are needed for both START and END states
# This script should be run after git checkout to ensure dependencies are available

cd /testbed

echo "Applying patches..."

# 1. Add missing dubbo-spring6-security/pom.xml if not exists
if [ ! -f "dubbo-plugin/dubbo-spring6-security/pom.xml" ]; then
    cp /tmp/dubbo-spring6-security-pom.xml dubbo-plugin/dubbo-spring6-security/pom.xml 2>/dev/null || true
fi

# 2. Check if BOM needs patching (look for netty_http3_version property)
if ! grep -q 'netty_http3_version' dubbo-dependencies-bom/pom.xml 2>/dev/null; then
    /tmp/patch_bom.sh
fi

# 3. Apply test compilation fixes if test files still exist (haven't been disabled yet)
/tmp/fix_test_compilation.sh 2>/dev/null || true

echo "Patches applied successfully"
