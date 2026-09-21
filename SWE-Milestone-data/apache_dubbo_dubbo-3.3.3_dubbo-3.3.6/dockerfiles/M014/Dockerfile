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

echo ">>> Applying M014 patches..."

# Remove HttpUtilsTest.java - references parseCharset() method that doesn't exist in HttpUtils class
rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java

# Remove MetaCacheManagerTest.java - references calRevision() method that doesn't exist on MetadataInfo class
rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java

# Remove NacosNamingServiceWrapperTest.java - references getAllInstancesWithoutSubscription() method that doesn't exist
rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java

# Remove SimpleRegistryExporter.java and ConfigTest.java - reference NetUtils.isReuseAddressSupported() which doesn't exist
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java

# Remove dubbo-demo modules from pom.xml - they may have broken code
sed -i '/<module>dubbo-demo\/dubbo-demo-api<\/module>/d' pom.xml
sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot<\/module>/d' pom.xml
sed -i '/<module>dubbo-demo\/dubbo-demo-spring-boot-idl<\/module>/d' pom.xml

echo ">>> M014 patches applied successfully"
PATCH_SCRIPT
RUN chmod +x /usr/local/bin/apply_patches.sh

# Checkout to END state and apply patches
RUN cd /testbed && git checkout milestone-M014-end && /usr/local/bin/apply_patches.sh

# Configure git identity for commits
RUN git config --global user.email "test@test.com" && git config --global user.name "Test User"

# Commit patches and move END tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M014 on END state" && \
    git tag -f milestone-M014-end

# Install all modules for END state to local Maven repository
RUN cd /testbed && mvn install -DskipTests -q -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true

# Set default git state to START and apply patches
RUN cd /testbed && git checkout milestone-M014-start && /usr/local/bin/apply_patches.sh

# Commit patches and move START tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M014 on START state" && \
    git tag -f milestone-M014-start

# Install all modules for START state to local Maven repository
RUN cd /testbed && mvn install -DskipTests -q -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true
