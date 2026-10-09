#!/bin/sh
# 실제 API + 실제 adapter + offline protocol fixture 세션 UI 검증(Docker). back의 backend/compose.m3-integration.yml과
# backend/compose.m3-offline.yml을 읽기 전용으로 사용한다. project anxious-s13-front-it, gateway 127.0.0.1:18100.
# 모델/Codex provider/credential/인터넷 없음(verification=fixture). 실제 Codex resume 검증이 아니다.
#
#   sh scripts/e2e/live-offline.sh           # 빌드 → 새 fixture DB → 계정 2개 승인 → runner 구성 → 브라우저 → 정리
#   E2E_KEEP=1 sh scripts/e2e/live-offline.sh  # 확인용으로 컨테이너·볼륨·임시 디렉터리를 남김
set -eu

REPO=$(cd "$(dirname "$0")/../.." && pwd)
E2E_DIR="$REPO/scripts/e2e"
SHA=$(git -C "$REPO" rev-parse HEAD)
AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PROJECT=anxious-s13-front-it
export API_RUNTIME_IMAGE=anxious-s13-front-it-api
export WEB_RUNTIME_IMAGE=anxious-s13-front-it-web
export M3_FIXTURE_IMAGE=anxious-s13-front-it-fixture
export M3_GATEWAY_PORT=18100
BASE="docker compose -p $PROJECT -f $REPO/backend/compose.m3-integration.yml"
OFF="$BASE -f $REPO/backend/compose.m3-offline.yml"
PW_VERSION=1.56.1
PW_IMAGE="mcr.microsoft.com/playwright:v$PW_VERSION-noble"
BROWSER=front-it-offline-browser-$$
SHARED=$(mktemp -d)
chmod 777 "$SHARED"
OFFLINE_FIXTURE_DIR=$(mktemp -d /tmp/m3-offline-front-XXXXXX)
chmod 0777 "$OFFLINE_FIXTURE_DIR"
export OFFLINE_FIXTURE_DIR
# setup 전에도 overlay compose 해석이 되도록 임시 값(실제 값은 승인 후 설정)
export OFFLINE_ACCOUNT_A=pending OFFLINE_ACCOUNT_B=pending
echo "== offline live sha=$SHA built=$AT shared=$SHARED fixture=$OFFLINE_FIXTURE_DIR"

docker build -q --target runtime -f "$REPO/backend/Dockerfile" -t "$API_RUNTIME_IMAGE" \
  --build-arg APP_GIT_SHA="$SHA" --build-arg APP_BUILT_AT="$AT" "$REPO" >/dev/null
docker build -q --target test -f "$REPO/backend/Dockerfile" -t "$M3_FIXTURE_IMAGE" \
  --build-arg APP_GIT_SHA="$SHA" --build-arg APP_BUILT_AT="$AT" "$REPO" >/dev/null
docker build -q -f "$REPO/infra/hosting/web.Dockerfile" -t "$WEB_RUNTIME_IMAGE" \
  --build-arg WEB_GIT_SHA="$SHA" --build-arg WEB_BUILT_AT="$AT" --build-arg NEXT_PUBLIC_BASE_PATH= "$REPO" >/dev/null

cleanup() {
  docker rm -f "$BROWSER" >/dev/null 2>&1 || true
  if [ "${E2E_KEEP:-0}" != 1 ]; then
    $OFF down --volumes >/dev/null 2>&1 || true
    # UID 10001이 만든 fixture 파일은 컨테이너로 지운다(이 실행이 만든 임시 디렉터리만)
    docker run --rm -v "$OFFLINE_FIXTURE_DIR:/d" alpine sh -c 'rm -rf /d/private' >/dev/null 2>&1 || true
    rmdir "$OFFLINE_FIXTURE_DIR" 2>/dev/null || true
    rm -f "$SHARED"/*.id "$SHARED"/*.ok "$SHARED"/*.account "$SHARED"/offline.ready "$SHARED"/restart.request "$SHARED"/restart.done
    rmdir "$SHARED" 2>/dev/null || true
  fi
}
trap cleanup EXIT

wait_ready() {
  i=0
  until [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$M3_GATEWAY_PORT/api/research/health" || true)" = 200 ]; do
    i=$((i + 1)); [ $i -gt 60 ] && { echo "api not ready"; $OFF ps; exit 1; }
    sleep 2
  done
}
wait_file() {
  j=0
  while [ ! -s "$SHARED/$1" ]; do
    [ "$(docker inspect -f '{{.State.Running}}' "$BROWSER" 2>/dev/null)" = true ] || return 1
    j=$((j + 1)); [ $j -gt 900 ] && return 1
    sleep 1
  done
}

$OFF down --volumes >/dev/null 2>&1 || true
$BASE up -d db mock-github
$BASE run --rm migrate
$BASE up -d api web gateway
wait_ready
echo "== base stack ready"

docker run -d --name "$BROWSER" --network host --ipc=host -e HOME=/tmp \
  -e ORIGIN="http://127.0.0.1:$M3_GATEWAY_PORT" -e SHARED=/shared \
  -v "$E2E_DIR:/e2e:ro" -v "$SHARED:/shared" "$PW_IMAGE" sh -c "
    mkdir -p /tmp/t && cd /tmp/t && npm i --silent playwright@$PW_VERSION >/dev/null 2>&1 &&
    cp /e2e/e2e-research-offline.mjs . && node e2e-research-offline.mjs" >/dev/null

STATUS=0
run_flow() {
  for name in A B; do
    wait_file "$name.id" || return 1
    ID=$(cat "$SHARED/$name.id")
    case "$ID" in *[!0-9]*|'') echo "invalid fixture id"; return 1;; esac
    $BASE run --rm admin --github-id "$ID" --approved true --role editor \
      --actor fixture:front-it --reason "Verified disposable test identity ($name)" >/dev/null
    touch "$SHARED/$name.ok"
    wait_file "$name.account" || return 1
    echo "== approved fixture account $name ($ID)"
  done
  OFFLINE_ACCOUNT_A=$(cat "$SHARED/A.account"); OFFLINE_ACCOUNT_B=$(cat "$SHARED/B.account")
  export OFFLINE_ACCOUNT_A OFFLINE_ACCOUNT_B
  $OFF run --rm fixture-setup
  $OFF up -d api offline-a offline-b web gateway
  wait_ready
  touch "$SHARED/offline.ready"
  echo "== offline runners ready"
  wait_file restart.request || return 0
  $OFF restart api offline-a offline-b
  wait_ready
  touch "$SHARED/restart.done"
  echo "== restarted api + adapters"
}
run_flow || STATUS=1

BSTATUS=$(docker wait "$BROWSER")
docker logs "$BROWSER" 2>&1
[ "$BSTATUS" = 0 ] || STATUS=$BSTATUS
echo "== exit $STATUS"
exit "$STATUS"
