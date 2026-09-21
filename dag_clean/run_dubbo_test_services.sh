#!/usr/bin/env bash
# Start/stop DAG-wide test services from assets already baked into the base SIF.
#
# This script receives no milestone, commit, problem statement, or test-list
# input.  Every clean node is tested with the same two ZooKeeper endpoints used
# by Dubbo's own test helpers and by older tests that hard-code 2181/2182.

set -Eeuo pipefail

action=${1:?usage: run_dubbo_test_services.sh start|stop RUNTIME_DIR}
runtime=${2:?usage: run_dubbo_test_services.sh start|stop RUNTIME_DIR}

stop_services() {
  local pid_file pid attempt
  shopt -s nullglob
  for pid_file in "$runtime"/zookeeper-*.pid; do
    pid=$(<"$pid_file")
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for attempt in $(seq 1 50); do
    local alive=0
    for pid_file in "$runtime"/zookeeper-*.pid; do
      pid=$(<"$pid_file")
      if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        alive=1
      fi
    done
    [[ "$alive" == 0 ]] && break
    sleep 0.1
  done
  for pid_file in "$runtime"/zookeeper-*.pid; do
    pid=$(<"$pid_file")
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
}

port_open() {
  local port=$1 fd
  if exec {fd}<>"/dev/tcp/127.0.0.1/$port" 2>/dev/null; then
    exec {fd}>&-
    exec {fd}<&-
    return 0
  fi
  return 1
}

zookeeper_ready() {
  local port=$1 fd response=
  if ! exec {fd}<>"/dev/tcp/127.0.0.1/$port" 2>/dev/null; then
    return 1
  fi
  printf 'ruok' >&"$fd"
  IFS= read -r -t 2 -n 4 response <&"$fd" || true
  exec {fd}>&-
  exec {fd}<&-
  [[ "$response" == "imok" ]]
}

services_healthy() {
  local port pid_file pid
  for port in 2181 2182; do
    pid_file="$runtime/zookeeper-$port.pid"
    [[ -s "$pid_file" ]] || return 1
    pid=$(<"$pid_file")
    [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null || return 1
    zookeeper_ready "$port" || return 1
  done
}

write_manifest() {
  local status=$1 phase=$2
  printf '{"status":"%s","phase":"%s","ports":[2181,2182],"source":"baked-zookeeper-archive","checked_at":"%s"}\n' \
    "$status" "$phase" "$(date -Is)" > "$runtime/manifest.json"
}

case "$action" in
  stop)
    [[ -d "$runtime" ]] || exit 0
    stop_services
    exit 0
    ;;
  probe)
    if services_healthy; then
      write_manifest ready post_test
      exit 0
    fi
    write_manifest unhealthy post_test
    exit 43
    ;;
  start)
    ;;
  *)
    echo "unknown action: $action" >&2
    exit 2
    ;;
esac

rm -rf -- "$runtime"
mkdir -p "$runtime/unpacked"

# Never mistake a service left by another job or a failed prior cleanup for a
# service owned by this endpoint run.  A clean node observation must be tied to
# the PIDs started below.
for port in 2181 2182; do
  if port_open "$port"; then
    echo "port $port is already in use before service startup" >&2
    write_manifest unhealthy preexisting_listener
    exit 43
  fi
done

archive=/testbed/.tmp/zookeeper/apache-zookeeper-bin.tar.gz
[[ -s "$archive" ]] || {
  echo "missing baked ZooKeeper archive: $archive" >&2
  exit 40
}
tar -xzf "$archive" -C "$runtime/unpacked"
zk_server=$(find "$runtime/unpacked" -type f -path '*/bin/zkServer.sh' -print -quit)
[[ -n "$zk_server" ]] || {
  echo "ZooKeeper archive has no bin/zkServer.sh" >&2
  exit 41
}
chmod +x "$zk_server"

for port in 2181 2182; do
  instance="$runtime/zookeeper-$port"
  mkdir -p "$instance/data" "$instance/log"
  printf '%s\n' \
    'tickTime=2000' \
    'initLimit=10' \
    'syncLimit=5' \
    "dataDir=$instance/data" \
    "clientPort=$port" \
    'admin.enableServer=false' \
    '4lw.commands.whitelist=ruok' > "$instance/zoo.cfg"
  # ZooKeeper enables the local JMX agent by default.  That agent crashes in
  # this Apptainer/cgroup-v1 environment.  ZooKeeper's own MBean registry also
  # initializes the JDK operating-system MXBean, so disable JDK container
  # metrics for this support process as well.  Neither container metrics nor
  # JMX is part of the repository test contract.
  # Keep the server PID file outside ZooKeeper's shared /tmp default.  Dubbo's
  # own test helper starts/stops short-lived instances and would otherwise
  # mistake this DAG-wide service for one of its children and terminate it.
  JMXDISABLE=true \
    SERVER_JVMFLAGS="-XX:-UseContainerSupport -Dzookeeper.jmx.log4j.disable=true -Dlog4j2.disableJmx=true" \
    ZOO_LOG_DIR="$instance/log" \
    ZOOPIDFILE="$instance/zookeeper.pid" \
    "$zk_server" start-foreground "$instance/zoo.cfg" \
    > "$runtime/zookeeper-$port.log" 2>&1 &
  printf '%s\n' "$!" > "$runtime/zookeeper-$port.pid"
done

ready=0
consecutive_ready=0
for _attempt in $(seq 1 200); do
  if services_healthy; then
    consecutive_ready=$((consecutive_ready + 1))
    if [[ "$consecutive_ready" -ge 20 ]]; then
      ready=1
      break
    fi
  else
    consecutive_ready=0
  fi
  sleep 0.1
done

if [[ "$ready" != 1 ]]; then
  echo "ZooKeeper services did not become ready on 2181/2182" >&2
  write_manifest unhealthy startup
  tail -n 80 "$runtime"/zookeeper-*.log >&2 || true
  stop_services
  exit 42
fi

write_manifest ready startup
