#!/usr/bin/env bash
# Start/stop the task-independent services baked into the unified Dubbo SIF.

set -Eeuo pipefail

action=${1:?usage: run_unified_dubbo_test_services.sh start|stop|probe RUNTIME_DIR}
runtime=${2:?usage: run_unified_dubbo_test_services.sh start|stop|probe RUNTIME_DIR}
current_host=$(hostname)

# This helper contains an rm -rf and consumes PID files.  Keep that authority
# scoped to the one bind-mounted directory created by the runner; accepting an
# arbitrary caller-controlled path would make both operations unsafe.
[[ "$runtime" == /dag-output/test-services-runtime ]] || {
  echo "runtime must be /dag-output/test-services-runtime" >&2
  exit 2
}
[[ ! -L "$runtime" ]] || {
  echo "runtime directory must not be a symlink" >&2
  exit 2
}

pid_owned_by_runtime() {
  local pid=$1 port=$2 cmdline
  [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/cmdline" ]] || return 1
  cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline")
  [[ "$cmdline" == *"$runtime/zookeeper-$port/zoo.cfg"* ]] || return 1
  [[ "$cmdline" == *zookeeper* || "$cmdline" == *ZooKeeper* ]]
}

runtime_is_from_other_host() {
  local marker="$runtime/hostname"
  [[ -e "$marker" ]] || return 1
  [[ -f "$marker" && ! -L "$marker" ]] || {
    echo "unsafe runtime hostname marker: $marker" >&2
    return 2
  }
  [[ "$(<"$marker")" != "$current_host" ]]
}

stop_services() {
  local pid_file pid port attempt alive ownership_error=0
  shopt -s nullglob
  for pid_file in "$runtime"/zookeeper-*.pid; do
    [[ -f "$pid_file" && ! -L "$pid_file" ]] || {
      echo "unsafe ZooKeeper PID file: $pid_file" >&2
      ownership_error=1
      continue
    }
    port=${pid_file##*-}
    port=${port%.pid}
    pid=$(<"$pid_file")
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      if ! pid_owned_by_runtime "$pid" "$port"; then
        echo "refusing to signal unowned PID $pid from $pid_file" >&2
        ownership_error=1
        continue
      fi
      kill "$pid" 2>/dev/null || true
    fi
  done
  for attempt in $(seq 1 50); do
    alive=0
    for pid_file in "$runtime"/zookeeper-*.pid; do
      [[ -f "$pid_file" && ! -L "$pid_file" ]] || continue
      port=${pid_file##*-}
      port=${port%.pid}
      pid=$(<"$pid_file")
      if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null \
        && pid_owned_by_runtime "$pid" "$port"; then
        alive=1
      fi
    done
    [[ "$alive" == 0 ]] && break
    sleep 0.1
  done
  for pid_file in "$runtime"/zookeeper-*.pid; do
    [[ -f "$pid_file" && ! -L "$pid_file" ]] || continue
    port=${pid_file##*-}
    port=${port%.pid}
    pid=$(<"$pid_file")
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null \
      && pid_owned_by_runtime "$pid" "$port"; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  return "$ownership_error"
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
  [[ "$response" == imok ]]
}

services_healthy() {
  local port pid_file pid
  for port in 2181 2182; do
    pid_file="$runtime/zookeeper-$port.pid"
    [[ -s "$pid_file" && -f "$pid_file" && ! -L "$pid_file" ]] || return 1
    pid=$(<"$pid_file")
    [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null || return 1
    pid_owned_by_runtime "$pid" "$port" || return 1
    zookeeper_ready "$port" || return 1
  done
}

write_manifest() {
  local status=$1 phase=$2 temporary
  mkdir -p "$runtime"
  temporary="$runtime/.manifest.json.tmp.$$"
  printf '{"status":"%s","phase":"%s","ports":[2181,2182],"source":"common-runtime-asset","checked_at":"%s"}\n' \
    "$status" "$phase" "$(date -Is)" > "$temporary"
  mv -f -- "$temporary" "$runtime/manifest.json"
}

case "$action" in
  stop)
    [[ -d "$runtime" ]] || exit 0
    runtime_host_status=0
    runtime_is_from_other_host || runtime_host_status=$?
    if [[ "$runtime_host_status" -eq 0 ]]; then
      # A requeued allocation cannot signal processes on the old host.  The
      # owning Slurm step has ended; start will discard this shared-state
      # residue before creating local services.
      exit 0
    elif [[ "$runtime_host_status" -eq 2 ]]; then
      exit 2
    fi
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
  start) ;;
  *) echo "unknown action: $action" >&2; exit 2 ;;
esac

if [[ -d "$runtime" ]]; then
  runtime_host_status=0
  runtime_is_from_other_host || runtime_host_status=$?
  if [[ "$runtime_host_status" -eq 0 ]]; then
    :
  elif [[ "$runtime_host_status" -eq 2 ]]; then
    exit 2
  else
    if ! stop_services; then
      write_manifest unhealthy unsafe_stale_pid
      exit 44
    fi
  fi
fi
rm -rf -- "$runtime"
mkdir -p "$runtime/unpacked"
printf '%s\n' "$current_host" > "$runtime/hostname"
for port in 2181 2182; do
  if port_open "$port"; then
    echo "port $port is already in use before service startup" >&2
    write_manifest unhealthy preexisting_listener
    exit 43
  fi
done

archive=/opt/swe-milestone-unified/runtime-assets/zookeeper/apache-zookeeper-bin.tar.gz
[[ -s "$archive" ]] || {
  echo "missing common ZooKeeper archive: $archive" >&2
  write_manifest unhealthy missing_runtime_asset
  exit 40
}
tar -xzf "$archive" -C "$runtime/unpacked"
zk_server=$(find "$runtime/unpacked" -type f -path '*/bin/zkServer.sh' -print -quit)
[[ -n "$zk_server" ]] || {
  echo "ZooKeeper archive has no bin/zkServer.sh" >&2
  write_manifest unhealthy invalid_runtime_asset
  exit 41
}
chmod +x "$zk_server"

for port in 2181 2182; do
  instance="$runtime/zookeeper-$port"
  mkdir -p "$instance/data" "$instance/log"
  printf '%s\n' \
    tickTime=2000 initLimit=10 syncLimit=5 \
    "dataDir=$instance/data" "clientPort=$port" \
    admin.enableServer=false 4lw.commands.whitelist=ruok > "$instance/zoo.cfg"
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
