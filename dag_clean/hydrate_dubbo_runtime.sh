#!/usr/bin/env bash
# Hydrate a frozen, DAG-wide Maven runtime closure before offline node tests.
# This script has no repository checkout, milestone, test-list, or task input.

set -Eeuo pipefail

repository=${1:?usage: hydrate_dubbo_runtime.sh MAVEN_REPOSITORY}
artifacts=(
  # M003.1's authoritative repaired tree pins Surefire 3.5.2, while the other
  # Dubbo nodes use 3.5.3.  The final common runtime must contain the union;
  # silently rewriting the node POM would change node semantics.
  org.apache.maven.surefire:surefire-junit-platform:3.5.2
  org.apache.maven.surefire:surefire-junit4:3.5.2
  org.apache.maven.surefire:surefire-junit-platform:3.5.3
  org.apache.maven.surefire:surefire-junit4:3.5.3
)
expected=(
  "$repository/org/apache/maven/surefire/surefire-junit-platform/3.5.2/surefire-junit-platform-3.5.2.jar"
  "$repository/org/apache/maven/surefire/surefire-junit4/3.5.2/surefire-junit4-3.5.2.jar"
  "$repository/org/apache/maven/surefire/surefire-junit-platform/3.5.3/surefire-junit-platform-3.5.3.jar"
  "$repository/org/apache/maven/surefire/surefire-junit4/3.5.3/surefire-junit4-3.5.3.jar"
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
  printf 'hydrated_artifact=%s\n' "$artifact"
  sha256sum "${expected[$index]}"
done
