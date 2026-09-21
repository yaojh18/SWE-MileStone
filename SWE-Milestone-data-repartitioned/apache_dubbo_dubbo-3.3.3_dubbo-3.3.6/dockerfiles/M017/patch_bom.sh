#!/bin/bash
# Patch dubbo-dependencies-bom/pom.xml to add missing dependencies

cd /testbed

# Add netty_http3_version and bouncycastle15on_version properties after bouncycastle-bcprov_version line
sed -i '/<bouncycastle-bcprov_version>/a\    <netty_http3_version>0.0.28.Final</netty_http3_version>\n    <bouncycastle15on_version>1.70</bouncycastle15on_version>' dubbo-dependencies-bom/pom.xml

# Create a temp file with the additional dependencies to inject
# Note: bcprov-jdk15on/bcpkix-jdk15on latest version is 1.70 (newer versions use jdk18on naming)
cat > /tmp/extra_deps.xml << 'EOF'
      <dependency>
        <groupId>io.netty.incubator</groupId>
        <artifactId>netty-incubator-codec-http3</artifactId>
        <version>${netty_http3_version}</version>
      </dependency>
      <dependency>
        <groupId>org.bouncycastle</groupId>
        <artifactId>bcprov-jdk15on</artifactId>
        <version>${bouncycastle15on_version}</version>
      </dependency>
      <dependency>
        <groupId>org.bouncycastle</groupId>
        <artifactId>bcpkix-jdk15on</artifactId>
        <version>${bouncycastle15on_version}</version>
      </dependency>
      <dependency>
        <groupId>org.bouncycastle</groupId>
        <artifactId>bcprov-ext-jdk15on</artifactId>
        <version>${bouncycastle15on_version}</version>
      </dependency>
EOF

# Find the line number after bcpkix-jdk18on dependency closing tag and insert
LINE_NUM=$(grep -n 'bcpkix-jdk18on' dubbo-dependencies-bom/pom.xml | head -1 | cut -d: -f1)
if [ -n "$LINE_NUM" ]; then
    # Find the closing </dependency> tag after this line
    END_LINE=$(tail -n +$LINE_NUM dubbo-dependencies-bom/pom.xml | grep -n '</dependency>' | head -1 | cut -d: -f1)
    INSERT_LINE=$((LINE_NUM + END_LINE))

    # Split file and insert
    head -n $INSERT_LINE dubbo-dependencies-bom/pom.xml > /tmp/bom_part1.xml
    tail -n +$((INSERT_LINE + 1)) dubbo-dependencies-bom/pom.xml > /tmp/bom_part2.xml
    cat /tmp/bom_part1.xml /tmp/extra_deps.xml /tmp/bom_part2.xml > dubbo-dependencies-bom/pom.xml
fi

echo "BOM patched successfully"
