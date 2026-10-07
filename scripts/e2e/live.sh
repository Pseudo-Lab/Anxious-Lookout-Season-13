#!/bin/sh
# 실제 API 통합 화면 검증(Docker). back이 제공한 격리 fixture backend/compose.m3-integration.yml을
# 읽기 전용으로 사용하고 project anxious-s13-front-it, gateway 127.0.0.1:18100만 쓴다.
# 실제 GitHub/Codex/운영 자원이 아니다(fixture provider, Codex 미연결, 공개 쓰기 policy_pending).
#
#   sh scripts/e2e/live.sh          # 빌드 → 새 fixture DB → 브라우저 검증 → 이 project만 정리
#   E2E_KEEP=1 sh scripts/e2e/live.sh  # 확인용으로 컨테이너·볼륨을 남김
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
COMPOSE="docker compose -p $PROJECT -f $REPO/backend/compose.m3-integration.yml"
PW_VERSION=1.56.1
PW_IMAGE="mcr.microsoft.com/playwright:v$PW_VERSION-noble"
BROWSER=front-it-browser-$$
if [ -n "${E2E_SHARED:-}" ]; then SHARED=$E2E_SHARED; OWN_SHARED=0; else SHARED=$(mktemp -d); OWN_SHARED=1; fi
chmod 777 "$SHARED"
echo "== live sha=$SHA built=$AT shared=$SHARED"

# 1) 이미지 (back 레시피와 같은 target/arg)
docker build -q --target runtime -f "$REPO/backend/Dockerfile" -t "$API_RUNTIME_IMAGE" \
  --build-arg APP_GIT_SHA="$SHA" --build-arg APP_BUILT_AT="$AT" "$REPO" >/dev/null
docker build -q --target test -f "$REPO/backend/Dockerfile" -t "$M3_FIXTURE_IMAGE" \
  --build-arg APP_GIT_SHA="$SHA" --build-arg APP_BUILT_AT="$AT" "$REPO" >/dev/null
docker build -q -f "$REPO/infra/hosting/web.Dockerfile" -t "$WEB_RUNTIME_IMAGE" \
  --build-arg WEB_GIT_SHA="$SHA" --build-arg WEB_BUILT_AT="$AT" --build-arg NEXT_PUBLIC_BASE_PATH= "$REPO" >/dev/null

cleanup() {
  docker rm -f "$BROWSER" >/dev/null 2>&1 || true
  if [ "${E2E_KEEP:-0}" != 1 ]; then $COMPOSE down --volumes >/dev/null 2>&1 || true; fi
  # 이 실행이 만든 임시 승인 교환 디렉터리만 지운다
  if [ "$OWN_SHARED" = 1 ]; then rm -f "$SHARED"/A.id "$SHARED"/A.ok "$SHARED"/B.id "$SHARED"/B.ok; rmdir "$SHARED" 2>/dev/null || true; fi
}
trap cleanup EXIT

# 2) 이 project만 새로 시작(이전 실행의 일회용 볼륨 제거)
$COMPOSE down --volumes >/dev/null 2>&1 || true
$COMPOSE up -d db mock-github
$COMPOSE run --rm migrate
$COMPOSE up -d api web gateway
i=0
until [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$M3_GATEWAY_PORT/api/research/health" || true)" = 200 ]; do
  i=$((i + 1)); [ $i -gt 60 ] && { echo "gateway/api not ready"; $COMPOSE ps; exit 1; }
  sleep 2
done
echo "== stack ready"

# 3) 브라우저(호스트 loopback origin). 계정 승인 요청은 $SHARED/<name>.id로 받는다.
docker run -d --name "$BROWSER" --network host --ipc=host -e HOME=/tmp \
  -e ORIGIN="http://127.0.0.1:$M3_GATEWAY_PORT" -e BASE_PATH= -e SHARED=/shared \
  -v "$E2E_DIR:/e2e:ro" -v "$SHARED:/shared" "$PW_IMAGE" sh -c "
    mkdir -p /tmp/t && cd /tmp/t && npm i --silent playwright@$PW_VERSION >/dev/null 2>&1 &&
    cp /e2e/e2e-research-live.mjs . && node e2e-research-live.mjs" >/dev/null

# 4) 운영자 승인(fixture 계정만). 승인은 기존 session을 폐기하므로 브라우저가 같은 identity로 다시 로그인한다.
for name in A B; do
  j=0
  while [ ! -s "$SHARED/$name.id" ]; do
    if [ "$(docker inspect -f '{{.State.Running}}' "$BROWSER" 2>/dev/null)" != true ]; then break 2; fi
    j=$((j + 1)); [ $j -gt 600 ] && { echo "no approval request for $name"; break 2; }
    sleep 1
  done
  ID=$(cat "$SHARED/$name.id")
  case "$ID" in *[!0-9]*|'') echo "invalid fixture id"; exit 1;; esac
  $COMPOSE run --rm admin --github-id "$ID" --approved true --role editor \
    --actor fixture:front-it --reason "Verified disposable test identity ($name)" >/dev/null
  touch "$SHARED/$name.ok"
  echo "== approved fixture account $name ($ID)"
done

STATUS=$(docker wait "$BROWSER")
docker logs "$BROWSER" 2>&1
echo "== exit $STATUS"
exit "$STATUS"
