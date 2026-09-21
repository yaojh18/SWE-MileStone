#!/bin/bash
set -e
cd /testbed

# Remove test files with pre-existing compilation issues

# Remove HttpUtilsTest.java - uses parseCharset() method that doesn't exist in HttpUtils
rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java

# Remove MetaCacheManagerTest.java - uses calRevision() method that doesn't exist on MetadataInfo
rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java

# Remove NacosNamingServiceWrapperTest.java - uses getAllInstancesWithoutSubscription() method that doesn't exist
rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java

# Remove SimpleRegistryExporter.java and ConfigTest.java - SimpleRegistryExporter uses isReuseAddressSupported()
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java
rm -f dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/ConfigTest.java

# Remove dubbo-demo-spring-boot-servlet module files - uses ServerSentEvent class that doesn't exist
rm -rf dubbo-demo/dubbo-demo-spring-boot/dubbo-demo-spring-boot-servlet/src/main/java/org/apache/dubbo/springboot/demo/servlet/

# Apply Bouncy Castle fix (the fix from milestone commit 6939e3c)
# Update version in BOM
sed -i 's/<bouncycastle-bcprov_version>1.70</<bouncycastle-bcprov_version>1.81</g' dubbo-dependencies-bom/pom.xml

# Update artifact IDs in BOM
sed -i 's/<artifactId>bcprov-jdk15on</<artifactId>bcprov-jdk18on</g' dubbo-dependencies-bom/pom.xml
sed -i 's/<artifactId>bcpkix-jdk15on</<artifactId>bcpkix-jdk18on</g' dubbo-dependencies-bom/pom.xml

# Remove bcprov-ext-jdk15on dependency from BOM
# This is a 5-line block in BOM - need to delete from <dependency> containing bcprov-ext-jdk15on through </dependency>
# Use perl for multiline pattern matching
perl -i -0pe 's/<dependency>\s*<groupId>org\.bouncycastle<\/groupId>\s*<artifactId>bcprov-ext-jdk15on<\/artifactId>\s*<version>\$\{bouncycastle-bcprov_version\}<\/version>\s*<\/dependency>//gs' dubbo-dependencies-bom/pom.xml

# Update artifact IDs in dubbo-security
sed -i 's/<artifactId>bcprov-jdk15on</<artifactId>bcprov-jdk18on</g' dubbo-plugin/dubbo-security/pom.xml
sed -i 's/<artifactId>bcpkix-jdk15on</<artifactId>bcpkix-jdk18on</g' dubbo-plugin/dubbo-security/pom.xml

# Remove bcprov-ext-jdk15on dependency from dubbo-security
# This is a 4-line block - need to delete from <dependency> containing bcprov-ext-jdk15on through </dependency>
perl -i -0pe 's/<dependency>\s*<groupId>org\.bouncycastle<\/groupId>\s*<artifactId>bcprov-ext-jdk15on<\/artifactId>\s*<\/dependency>//gs' dubbo-plugin/dubbo-security/pom.xml

echo "Patches applied successfully"
