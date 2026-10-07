#!/usr/bin/env bash
# Docker-only probe tests. Fresh project/volume; no k3s or PM secrets/data.
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
hosting_dir="$repo_dir/infra/hosting"
source "$hosting_dir/probes.env"
: "${API_RUNTIME_IMAGE:?Set reviewed immutable API runtime}"
: "${WEB_RUNTIME_IMAGE:?Set reviewed immutable web runtime}"
project="anxious-back-probes-$$"
fixture="$project-failure"
dc() { docker compose -p "$project" -f "$hosting_dir/compose.test.yml" -f "$hosting_dir/compose.probes.yml" "$@"; }
cleanup() {
  docker rm -f "$fixture" >/dev/null 2>&1 || true
  dc down >/dev/null 2>&1 || true
  # Keep the owned test volume; never prune/delete data as test cleanup.
}
trap cleanup EXIT
check() {
  local container=$1 label=$2 expected=$3
  shift 3
  local started status elapsed deadline=$M2_API_PROBE_TIMEOUT_SECONDS
  if [[ "$1" == node ]]; then deadline=$M2_WEB_PROBE_TIMEOUT_SECONDS; fi
  started=$(date +%s%N)
  if timeout "$deadline" docker exec "$container" "$@" >/dev/null 2>&1; then status=0; else status=$?; fi
  elapsed=$(( ($(date +%s%N) - started) / 1000000 ))
  [[ "$status" == "$expected" ]] || { echo "FAIL $label exit=$status expected=$expected duration_ms=$elapsed" >&2; exit 1; }
  if [[ "$label" == *delayed* ]]; then
    [[ "$elapsed" -ge 1500 ]] || { echo "FAIL delay fixture was not exercised" >&2; exit 1; }
  fi
  printf 'PASS %s exit=%s duration_ms=%s deadline_s=%s\n' "$label" "$status" "$elapsed" "$deadline"
}
wait_probe() {
  local container=$1
  shift
  local deadline=$M2_API_PROBE_TIMEOUT_SECONDS
  if [[ "$1" == node ]]; then deadline=$M2_WEB_PROBE_TIMEOUT_SECONDS; fi
  for _ in {1..30}; do
    if timeout "$deadline" docker exec "$container" "$@" >/dev/null 2>&1; then return; fi
    sleep 1
  done
  echo 'FAIL disposable runtime did not become healthy' >&2
  exit 1
}
dc up -d db
dc run --rm --no-deps -T test python -m app.migrate --revision 0001_auth
dc up -d api web
api=$(dc ps -q api)
web=$(dc ps -q web)
wait_probe "$api" python -m app.probe /healthz
wait_probe "$web" node -e "$M2_WEB_PROBE_JS"
docker inspect "$api" "$web" --format '{{.Name}} CPU={{.HostConfig.NanoCpus}} memory={{.HostConfig.Memory}} user={{.Config.User}} readOnly={{.HostConfig.ReadonlyRootfs}}'
for _ in {1..4}; do
  check "$api" api-health-healthy 0 python -m app.probe /healthz
  check "$api" api-ready-healthy 0 python -m app.probe /readyz
  check "$web" web-healthy 0 node -e "$M2_WEB_PROBE_JS"
done
# Exercise the same cgroup quotas with a competing CPU task, not extra CPU limits.
docker exec "$api" python -c 'import subprocess,sys,pathlib; p=subprocess.Popen([sys.executable,"-c","while True: pass"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True); pathlib.Path("/tmp/probe-worker.pid").write_text(str(p.pid))'
for _ in {1..4}; do
  check "$api" api-health-cpu-busy 0 python -m app.probe /healthz
  check "$api" api-ready-cpu-busy 0 python -m app.probe /readyz
done
for _ in {1..4}; do
  check "$api" api-health-cpu-overlap 0 python -m app.probe /healthz &
  health_check_pid=$!
  check "$api" api-ready-cpu-overlap 0 python -m app.probe /readyz &
  ready_check_pid=$!
  wait "$health_check_pid"
  wait "$ready_check_pid"
done
docker exec "$api" python -c 'import os,signal,pathlib; os.kill(int(pathlib.Path("/tmp/probe-worker.pid").read_text()),signal.SIGTERM)'
docker exec "$web" node -e "const fs=require('fs'),cp=require('child_process');const p=cp.spawn(process.execPath,['-e','for(;;){}'],{detached:true,stdio:'ignore'});fs.writeFileSync('/tmp/probe-worker.pid',String(p.pid));p.unref()"
for _ in {1..4}; do check "$web" web-cpu-busy 0 node -e "$M2_WEB_PROBE_JS"; done
docker exec "$web" node -e "process.kill(Number(require('fs').readFileSync('/tmp/probe-worker.pid','utf8')),'SIGTERM')"
dc stop db
check "$api" api-ready-db-stopped 1 python -m app.probe /readyz
check "$api" api-health-db-stopped 0 python -m app.probe /healthz
dc up -d db
wait_probe "$api" python -m app.probe /readyz
check "$api" api-ready-db-recovered 0 python -m app.probe /readyz
for workload in api web; do
  for mode in refused non200 delayed; do
    if [[ "$workload" == api ]]; then
      image=$API_RUNTIME_IMAGE; cpu=0.25; memory=256m
      command=(python /ops/probe_http_fixture.py "$mode")
    else
      image=$WEB_RUNTIME_IMAGE; cpu=0.5; memory=512m
      command=(node /ops/probe_http_fixture.mjs "$mode")
    fi
    docker run -d --name "$fixture" --network none --user 10001:10001 --read-only --tmpfs /tmp \
      --cpus "$cpu" --memory "$memory" --cap-drop ALL --security-opt no-new-privileges \
      -v "$hosting_dir:/ops:ro" "$image" "${command[@]}" >/dev/null
    ready=0
    for _ in {1..30}; do
      if docker exec "$fixture" sh -c 'test -f /tmp/probe-fixture-ready'; then ready=1; break; fi
      sleep 1
    done
    [[ "$ready" == 1 ]] || exit 1
    if [[ "$workload" == api ]]; then
      check "$fixture" "api-health-$mode" 1 python -m app.probe /healthz
      check "$fixture" "api-ready-$mode" 1 python -m app.probe /readyz
    else
      check "$fixture" "web-$mode" 1 node -e "$M2_WEB_PROBE_JS"
    fi
    docker rm -f "$fixture" >/dev/null
  done
done
echo 'PASS constrained runtime probes, CPU competition and bounded failure detection; retained disposable volume'
