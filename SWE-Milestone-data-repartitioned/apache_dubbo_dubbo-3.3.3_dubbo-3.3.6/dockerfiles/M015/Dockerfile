# Build on pre-configured base image
FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

WORKDIR /testbed
# Use the testbed from base image directly (it already contains milestone tags)

# Configure git identity for commits
RUN git config --global user.email "test@test.com" && git config --global user.name "Test User"

# Checkout to END state (all features available)
RUN cd /testbed && git checkout milestone-M015-end

# Apply pre-build patches for END state
RUN cd /testbed && \
    rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java && \
    rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java && \
    rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java && \
    rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java && \
    rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java && \
    rm -rf dubbo-demo && \
    sed -i "/<module>dubbo-demo/d" pom.xml

# Commit patches and move END tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M015 on END state" && \
    git tag -f milestone-M015-end

# Create a pre-build script that removes problematic test files before any compilation
# This script will be called before test compilation in test_config.json
# IMPORTANT: Place script in /usr/local/bin/ so it persists across git checkout operations
#
# Files to remove:
# 1. HttpUtilsTest.java - uses HttpUtils.parseCharset() method that doesn't exist
# 2. MetaCacheManagerTest.java - uses MetadataInfo.calRevision() method that doesn't exist
#    (calRevision is a static method on RevisionResolver, not an instance method on MetadataInfo)
# 3. NacosNamingServiceWrapperTest.java - uses NacosNamingServiceWrapper.getAllInstancesWithoutSubscription() that doesn't exist
# 4. SimpleRegistryExporter.java - uses NetUtils.isReuseAddressSupported() that doesn't exist
# 5. ConfigTest.java - uses SimpleRegistryExporter which is also removed
# 6. dubbo-demo module - contains compilation errors in main source (not tests), also remove from pom.xml
RUN echo '#!/bin/bash' > /usr/local/bin/pre-build.sh && \
    echo 'rm -f /testbed/dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java' >> /usr/local/bin/pre-build.sh && \
    echo 'rm -f /testbed/dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java' >> /usr/local/bin/pre-build.sh && \
    echo 'rm -f /testbed/dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java' >> /usr/local/bin/pre-build.sh && \
    echo 'rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java' >> /usr/local/bin/pre-build.sh && \
    echo 'rm -f /testbed/dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java' >> /usr/local/bin/pre-build.sh && \
    echo 'rm -rf /testbed/dubbo-demo' >> /usr/local/bin/pre-build.sh && \
    echo 'sed -i "/<module>dubbo-demo/d" /testbed/pom.xml' >> /usr/local/bin/pre-build.sh && \
    chmod +x /usr/local/bin/pre-build.sh

# Set default git state to START and apply patches
RUN cd /testbed && git checkout milestone-M015-start && /usr/local/bin/pre-build.sh

# Commit patches and move START tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M015 on START state" && \
    git tag -f milestone-M015-start
