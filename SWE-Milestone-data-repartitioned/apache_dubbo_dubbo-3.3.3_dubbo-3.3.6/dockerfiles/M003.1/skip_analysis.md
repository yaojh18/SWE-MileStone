# Skip Analysis Report for M003.1

## Milestone Overview
- **Milestone ID**: M003.1
- **Commits**:
  - 803e5c2: Add Mutiny module infrastructure and build configuration
  - 5c0bd7f: fixed the issue that the file name of message.proto is not supported

## Files Modified by Milestone Commits

### Commit 803e5c2 (Add Mutiny module)
- .artifacts
- dubbo-dependencies-bom/pom.xml
- dubbo-distribution/dubbo-all-shaded/pom.xml
- dubbo-distribution/dubbo-all/pom.xml
- dubbo-distribution/dubbo-bom/pom.xml
- dubbo-plugin/dubbo-compiler/src/main/resources/MutinyDubbo3TripleInterfaceStub.mustache
- dubbo-plugin/dubbo-compiler/src/main/resources/MutinyDubbo3TripleStub.mustache
- dubbo-plugin/dubbo-mutiny/pom.xml
- pom.xml

### Commit 5c0bd7f (Fix message.proto filename issue)
- dubbo-demo/dubbo-demo-spring-boot-idl/dubbo-demo-spring-boot-idl-provider/src/main/proto/message.proto
- dubbo-demo/dubbo-demo-spring-boot-idl/dubbo-demo-spring-boot-idl-provider/src/test/java/org/apache/dubbo/springboot/idl/demo/MessageServiceTest.java
- dubbo-plugin/dubbo-compiler/src/main/resources/Dubbo3TripleInterfaceStub.mustache
- dubbo-plugin/dubbo-compiler/src/main/resources/Dubbo3TripleStub.mustache
- dubbo-plugin/dubbo-compiler/src/main/resources/MutinyDubbo3TripleInterfaceStub.mustache
- dubbo-plugin/dubbo-compiler/src/main/resources/MutinyDubbo3TripleStub.mustache
- dubbo-plugin/dubbo-compiler/src/main/resources/ReactorDubbo3TripleInterfaceStub.mustache
- dubbo-plugin/dubbo-compiler/src/main/resources/ReactorDubbo3TripleStub.mustache

## Skipped/Removed Files

The following files were removed or skipped because they reference APIs that don't exist in the current source code state:

### HttpUtilsTest.java
- **Location**: dubbo-remoting/dubbo-remoting-http12/src/test/java/org/apache/dubbo/remoting/http12/message/codec/HttpUtilsTest.java
- **Reason**: References `HttpUtils.parseCharset(String)` method that doesn't exist in the current source code
- **Classification**: Unrelated to milestone commits - file was included in milestone tags but not in the actual commits

### AffinityRouteTest.java
- **Location**: dubbo-cluster/src/test/java/org/apache/dubbo/rpc/cluster/router/affinity/AffinityRouteTest.java
- **Reason**: References `AffinityServiceStateRouter` class that doesn't exist in the current source code
- **Classification**: Unrelated to milestone commits - file was included in milestone tags but not in the actual commits

### dubbo-mutiny/src/test/**
- **Location**: dubbo-plugin/dubbo-mutiny/src/test/
- **Reason**: The milestone adds the dubbo-mutiny module structure but doesn't include source code, only pom.xml and templates
- **Classification**: Incomplete module setup in milestone

### dubbo-spring6-security/**
- **Location**: dubbo-plugin/dubbo-spring6-security/
- **Reason**: The module directory exists but lacks pom.xml and source code in the milestone commits
- **Classification**: Incomplete module setup in milestone

## Patches Applied

### BOM Modifications
- Added `<mutiny.version>2.9.0</mutiny.version>` property
- Added mutiny dependency to dependencyManagement

### Root pom.xml Modifications
- Added `<module>dubbo-plugin/dubbo-mutiny</module>` to modules list

### File Restoration from Base
Most source directories were restored from the base image (8e49668a45) because the milestone tags contain many unrelated changes that cause compilation failures.

## Test Configuration

The test_config.json is configured to run tests using Maven with the following settings:
- Skip Spotless formatting checks
- Skip Checkstyle checks
- Skip RAT license checks
- Allow test failures (maven.test.failure.ignore=true)

## Environment Requirements

- Java 21 (provided by base image)
- Maven (provided by base image)
- All dependencies pre-cached in base image
