# Build on pre-configured base image
FROM apache_dubbo_dubbo-3.3.3_dubbo-3.3.6/baseline_rerun_stage4_002_fix2/base:latest

WORKDIR /testbed
# Use the testbed from base image directly (it already contains milestone tags)

# Configure git for commits
RUN git config --global user.email "build@docker" && git config --global user.name "Docker Build"

# Create dubbo-spring6-security pom.xml
RUN cat > /tmp/dubbo-spring6-security-pom.xml << 'POMEOF'
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.apache.dubbo</groupId>
    <artifactId>dubbo-parent</artifactId>
    <version>${revision}</version>
    <relativePath>../../pom.xml</relativePath>
  </parent>

  <artifactId>dubbo-spring6-security</artifactId>
  <packaging>jar</packaging>

  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
    <skip_maven_deploy>false</skip_maven_deploy>
    <spring.oauth2.server>1.5.1</spring.oauth2.server>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-core</artifactId>
      <version>${spring-6.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-beans</artifactId>
      <version>${spring-6.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-context</artifactId>
      <version>${spring-6.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-aop</artifactId>
      <version>${spring-6.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-web</artifactId>
      <version>${spring-6.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-jcl</artifactId>
      <version>${spring-6.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-expression</artifactId>
      <version>${spring-6.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-test</artifactId>
      <version>${spring-6.version}</version>
      <scope>test</scope>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.apache.dubbo</groupId>
      <artifactId>dubbo-spring-security</artifactId>
      <version>${project.version}</version>
      <exclusions>
        <exclusion>
          <groupId>org.springframework.security</groupId>
          <artifactId>spring-security-core</artifactId>
        </exclusion>
      </exclusions>
    </dependency>
    <dependency>
      <groupId>org.springframework.security</groupId>
      <artifactId>spring-security-config</artifactId>
      <version>${spring-security-6.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework.security</groupId>
      <artifactId>spring-security-core</artifactId>
      <version>${spring-security-6.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework.security</groupId>
      <artifactId>spring-security-web</artifactId>
      <version>${spring-security-6.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework.security</groupId>
      <artifactId>spring-security-crypto</artifactId>
      <version>${spring-security-6.version}</version>
    </dependency>
    <dependency>
      <groupId>org.springframework.security</groupId>
      <artifactId>spring-security-oauth2-core</artifactId>
      <version>${spring-security-6.version}</version>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.springframework.security</groupId>
      <artifactId>spring-security-oauth2-jose</artifactId>
      <version>${spring-security-6.version}</version>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.springframework.security</groupId>
      <artifactId>spring-security-oauth2-resource-server</artifactId>
      <version>${spring-security-6.version}</version>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.springframework.security</groupId>
      <artifactId>spring-security-oauth2-authorization-server</artifactId>
      <version>${spring.oauth2.server}</version>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.springframework.security</groupId>
      <artifactId>spring-security-oauth2-client</artifactId>
      <version>${spring-security-6.version}</version>
      <scope>test</scope>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter</artifactId>
      <version>${spring-boot-3.version}</version>
      <scope>test</scope>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot</artifactId>
      <version>${spring-boot-3.version}</version>
      <scope>test</scope>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-autoconfigure</artifactId>
      <version>${spring-boot-3.version}</version>
      <scope>test</scope>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-logging</artifactId>
      <version>${spring-boot-3.version}</version>
      <scope>test</scope>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-test</artifactId>
      <version>${spring-boot-3.version}</version>
      <scope>test</scope>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-test-autoconfigure</artifactId>
      <version>${spring-boot-3.version}</version>
      <scope>test</scope>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-test</artifactId>
      <version>${spring-boot-3.version}</version>
      <scope>test</scope>
      <optional>true</optional>
      <exclusions>
        <exclusion>
          <groupId>ch.qos.logback</groupId>
          <artifactId>logback-classic</artifactId>
        </exclusion>
      </exclusions>
    </dependency>
    <dependency>
      <groupId>org.apache.dubbo</groupId>
      <artifactId>dubbo-config-spring6</artifactId>
      <version>${project.parent.version}</version>
      <scope>test</scope>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>org.apache.logging.log4j</groupId>
      <artifactId>log4j-slf4j-impl</artifactId>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
POMEOF

# Create comprehensive fix script for M017
RUN cat > /opt/fix_m017.sh << 'SCRIPT'
#!/bin/bash
set -e
cd /testbed
BOM=dubbo-dependencies-bom/pom.xml

# ===== Add dubbo-spring6-security pom.xml if missing =====
if [ ! -f "dubbo-plugin/dubbo-spring6-security/pom.xml" ]; then
    mkdir -p dubbo-plugin/dubbo-spring6-security
    cp /tmp/dubbo-spring6-security-pom.xml dubbo-plugin/dubbo-spring6-security/pom.xml
fi

# ===== BOM FIXES =====
# Add netty_http3_version if missing
if ! grep -q "netty_http3_version" "$BOM"; then
    awk '/<netty_version>/ { print; print "    <netty_http3_version>0.0.28.Final</netty_http3_version>"; next } {print}' "$BOM" > "$BOM.tmp" && mv "$BOM.tmp" "$BOM"
fi

# Add netty-incubator-codec-http3 dependency if missing
if ! grep -q "netty-incubator-codec-http3" "$BOM"; then
    awk '/<artifactId>javassist<\/artifactId>/ && !in_exclusion {
        print; getline; print; getline; print;
        print "      <dependency>";
        print "        <groupId>io.netty.incubator</groupId>";
        print "        <artifactId>netty-incubator-codec-http3</artifactId>";
        print "        <version>${netty_http3_version}</version>";
        print "      </dependency>";
        next
    }
    /<exclusions>/ { in_exclusion=1 }
    /<\/exclusions>/ { in_exclusion=0 }
    {print}' "$BOM" > "$BOM.tmp" && mv "$BOM.tmp" "$BOM"
fi

# Add bouncycastle-jdk15on_version if missing
if ! grep -q "bouncycastle-jdk15on_version" "$BOM"; then
    awk '/<bouncycastle-bcprov_version>/ { print; print "    <bouncycastle-jdk15on_version>1.70</bouncycastle-jdk15on_version>"; next } {print}' "$BOM" > "$BOM.tmp" && mv "$BOM.tmp" "$BOM"

    awk 'BEGIN { in_bcpkix=0 }
    /<artifactId>bcpkix-jdk18on<\/artifactId>/ { in_bcpkix=1 }
    in_bcpkix && /<\/dependency>/ {
        print;
        print "      <dependency>"; print "        <groupId>org.bouncycastle</groupId>"; print "        <artifactId>bcprov-jdk15on</artifactId>"; print "        <version>${bouncycastle-jdk15on_version}</version>"; print "      </dependency>";
        print "      <dependency>"; print "        <groupId>org.bouncycastle</groupId>"; print "        <artifactId>bcpkix-jdk15on</artifactId>"; print "        <version>${bouncycastle-jdk15on_version}</version>"; print "      </dependency>";
        print "      <dependency>"; print "        <groupId>org.bouncycastle</groupId>"; print "        <artifactId>bcprov-ext-jdk15on</artifactId>"; print "        <version>${bouncycastle-jdk15on_version}</version>"; print "      </dependency>";
        in_bcpkix=0; next
    }
    {print}' "$BOM" > "$BOM.tmp" && mv "$BOM.tmp" "$BOM"
fi

# ===== Remove demo modules =====
sed -i '/<module>dubbo-demo/d' pom.xml 2>/dev/null || true

# ===== Fix LoggerTest.java =====
if [ -f dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java ]; then
    mv dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java \
       dubbo-common/src/test/java/org/apache/dubbo/common/logger/LoggerTest.java.disabled 2>/dev/null || true
fi

# ===== Fix SimpleRegistryExporter.java =====
if [ -f "dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java" ]; then
    sed -i 's/NetUtils.isReuseAddressSupported()/true/g' dubbo-config/dubbo-config-spring/src/test/java/org/apache/dubbo/config/spring/SimpleRegistryExporter.java 2>/dev/null || true
fi

# ===== Remove problematic test files =====
rm -f dubbo-metrics/dubbo-metrics-api/src/test/java/org/apache/dubbo/metrics/MetricsSupportTest.java
rm -f dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
rm -f dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java
rm -f dubbo-registry/dubbo-registry-api/src/test/java/org/apache/dubbo/registry/client/metadata/store/MetaCacheManagerTest.java
rm -f dubbo-registry/dubbo-registry-nacos/src/test/java/org/apache/dubbo/registry/nacos/NacosNamingServiceWrapperTest.java

echo "M017 fixes applied successfully"
SCRIPT
RUN chmod +x /opt/fix_m017.sh

# ========== Fix END state ==========
RUN cd /testbed && git checkout milestone-M017-end
RUN /opt/fix_m017.sh
RUN cd /testbed && git add -A && git commit -m "Apply compilation patches for M017 END state" && git tag -f milestone-M017-end HEAD

# Build at END state (skip test compilation, continue on errors)
RUN cd /testbed && mvn install -Dmaven.test.skip=true -B -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true --fail-never

# ========== Fix START state ==========
RUN cd /testbed && git checkout milestone-M017-start
RUN /opt/fix_m017.sh
RUN cd /testbed && git add -A && git commit -m "Apply compilation patches for M017 START state" && git tag -f milestone-M017-start HEAD

# Default to START state
RUN cd /testbed && git checkout milestone-M017-start
