# Build on pre-configured base image
FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

WORKDIR /testbed
# Use the testbed from base image directly (it already contains milestone tags)

# Create inline patch script in /usr/local/bin (persists across git checkout)
RUN cat > /usr/local/bin/apply_patches.sh << 'SCRIPT'
#!/bin/bash
set -e
cd /testbed

# Remove test files with pre-existing compilation issues
rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java
rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java
rm -rf dubbo-demo/dubbo-demo-spring-boot/dubbo-demo-spring-boot-servlet/src/main/java/org/apache/dubbo/springboot/demo/servlet/

# Apply Bouncy Castle fix
sed -i 's/<bouncycastle-bcprov_version>1.70</<bouncycastle-bcprov_version>1.81</g' dubbo-dependencies-bom/pom.xml
sed -i 's/<artifactId>bcprov-jdk15on</<artifactId>bcprov-jdk18on</g' dubbo-dependencies-bom/pom.xml
sed -i 's/<artifactId>bcpkix-jdk15on</<artifactId>bcpkix-jdk18on</g' dubbo-dependencies-bom/pom.xml
perl -i -0pe 's/<dependency>\s*<groupId>org\.bouncycastle<\/groupId>\s*<artifactId>bcprov-ext-jdk15on<\/artifactId>\s*<version>\$\{bouncycastle-bcprov_version\}<\/version>\s*<\/dependency>//gs' dubbo-dependencies-bom/pom.xml
sed -i 's/<artifactId>bcprov-jdk15on</<artifactId>bcprov-jdk18on</g' dubbo-plugin/dubbo-security/pom.xml
sed -i 's/<artifactId>bcpkix-jdk15on</<artifactId>bcpkix-jdk18on</g' dubbo-plugin/dubbo-security/pom.xml
perl -i -0pe 's/<dependency>\s*<groupId>org\.bouncycastle<\/groupId>\s*<artifactId>bcprov-ext-jdk15on<\/artifactId>\s*<\/dependency>//gs' dubbo-plugin/dubbo-security/pom.xml

echo "Patches applied successfully"
SCRIPT
RUN chmod +x /usr/local/bin/apply_patches.sh

# Configure git identity for commits
RUN git config --global user.email "test@test.com" && git config --global user.name "Test User"

# Checkout to END state and apply patches
RUN cd /testbed && git checkout milestone-M010-end && /usr/local/bin/apply_patches.sh

# Commit patches and move END tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M010 on END state" && \
    git tag -f milestone-M010-end

# Checkout to START state (default state for container) and apply patches
RUN cd /testbed && git checkout milestone-M010-start && /usr/local/bin/apply_patches.sh

# Commit patches and move START tag to the new commit
RUN cd /testbed && \
    git add -A && \
    git commit -m "Apply compilation patches for milestone M010 on START state" && \
    git tag -f milestone-M010-start
