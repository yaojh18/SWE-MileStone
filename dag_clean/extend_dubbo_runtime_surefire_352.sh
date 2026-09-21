#!/usr/bin/env bash
# Download only the Maven artifacts needed by repaired M003.1 nodes.

set -Eeuo pipefail

repository=${1:?usage: extend_dubbo_runtime_surefire_352.sh MAVEN_REPOSITORY}
artifacts=(
  org.apache.maven.surefire:surefire-junit-platform:3.5.2
  org.apache.maven.surefire:surefire-junit4:3.5.2
)
expected=(
  "$repository/org/apache/maven/surefire/surefire-junit-platform/3.5.2/surefire-junit-platform-3.5.2.jar"
  "$repository/org/apache/maven/surefire/surefire-junit4/3.5.2/surefire-junit4-3.5.2.jar"
)

mkdir -p "$repository"
cd /tmp
for index in "${!artifacts[@]}"; do
  artifact=${artifacts[$index]}
  mvn --no-transfer-progress -B \
    -Dmaven.repo.local="$repository" \
    org.apache.maven.plugins:maven-dependency-plugin:3.8.1:get \
    -Dartifact="$artifact" \
    -Dtransitive=true
  test -s "${expected[$index]}"
  printf 'downloaded_extension_artifact=%s\n' "$artifact"
  sha256sum "${expected[$index]}"
done
