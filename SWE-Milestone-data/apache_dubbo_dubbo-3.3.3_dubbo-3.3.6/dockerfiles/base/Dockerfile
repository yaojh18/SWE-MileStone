FROM azul/zulu-openjdk:21

# Install git, wget, and Maven
RUN apt-get update && apt-get install -y \
    git \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Maven 3.9.9
ENV MAVEN_VERSION=3.9.9
ENV MAVEN_HOME=/opt/maven
ENV PATH="${MAVEN_HOME}/bin:${PATH}"
RUN curl -fsSL https://archive.apache.org/dist/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz | tar -xzC /opt \
    && ln -s /opt/apache-maven-${MAVEN_VERSION} ${MAVEN_HOME}

# Git configuration
RUN git config --global --add safe.directory /testbed

# Set working directory
WORKDIR /testbed

# Copy repository content
COPY . /testbed/

# Checkout to base SHA
RUN git checkout 8e49668a45

# Set environment variables for Maven
ENV MAVEN_OPTS="-XX:+UseG1GC -XX:InitiatingHeapOccupancyPercent=45 -XX:+UseStringDeduplication -XX:-TieredCompilation -XX:TieredStopAtLevel=1 -Dmaven.javadoc.skip=true -Dmaven.wagon.http.retryHandler.count=5 -Dmaven.wagon.httpconnectionManager.ttlSeconds=120"

# Download Zookeeper (required for tests)
RUN mkdir -p /testbed/.tmp/zookeeper && \
    (wget -c https://archive.apache.org/dist/zookeeper/zookeeper-3.7.2/apache-zookeeper-3.7.2-bin.tar.gz -O /testbed/.tmp/zookeeper/apache-zookeeper-bin.tar.gz || \
     wget -c https://apache.website-solution.net/zookeeper/zookeeper-3.7.2/apache-zookeeper-3.7.2-bin.tar.gz -O /testbed/.tmp/zookeeper/apache-zookeeper-bin.tar.gz || \
     wget -c http://apache.stu.edu.tw/zookeeper/zookeeper-3.7.2/apache-zookeeper-3.7.2-bin.tar.gz -O /testbed/.tmp/zookeeper/apache-zookeeper-bin.tar.gz || \
     wget -c http://ftp.jaist.ac.jp/pub/apache/zookeeper/zookeeper-3.7.2/apache-zookeeper-3.7.2-bin.tar.gz -O /testbed/.tmp/zookeeper/apache-zookeeper-bin.tar.gz)

# Download dependencies
RUN mvn dependency:go-offline -B || true

# Build project and install to local repository (without running tests)
RUN mvn clean install -DskipTests -B -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true

# Set environment variables
ENV DISABLE_FILE_SYSTEM_TEST=true
ENV PYTHONUNBUFFERED=1
