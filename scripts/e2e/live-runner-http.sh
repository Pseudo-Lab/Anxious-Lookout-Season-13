#!/bin/sh
# #10 실제 FastAPI HTTP fixture + 일회용 PostgreSQL + 웹 런타임에서 실행 환경 준비/상태/권한/선접수 UI를 확인한다(Docker).
# back의 backend/compose.m3-integration.yml + backend/compose.user-runner-http-test.yml을 읽기 전용으로 사용한다
# (infra/hosting/m3/USER-RUNNER-HTTP-FIXTURE.md). project anxious-s13-front-runner-http-<pid>, gateway 127.0.0.1:18110.
# 실제 Pod/controller/provider/모델은 없다. phase는 test-image 전용 runner-phase로 정한 합성 값이다(운영 증거 아님).
#
#   sh scripts/e2e/live-runner-http.sh            # 빌드 → 새 DB → 계정 승인 → 브라우저(phase·역할·flag 요청 처리) → 정리
#   E2E_KEEP=1 sh scripts/e2e/live-runner-http.sh # 확인용으로 컨테이너·볼륨·임시 디렉터리를 남김
#
# 브라우저 ↔ 호스트 조율(SHARED): <name>.id/<name>.role → 승인 → <name>.ok, 승인 후 <name>.account.
# 요청 req-<n>.cmd(한 줄) → req-<n>.done("ok"|"fail"):
#   phase <accountUUID> <preparing|waiting|unreachable|ready|stopping|stopped|failed|not_prepared>
#   reserve <accountUUID>   # 미바인딩 생성 예약(되돌릴 수 없음, 그 계정의 이후 phase 변경 거부)
#   inspect <accountUUID>   # 읽기 전용 스냅샷 JSON을 done 둘째 줄에 남긴다
#   role <githubId> <editor|commenter>
#   flag <true|false>   # API 컨테이너를 다시 만들어 CODEX_USER_RUNNERS_ENABLE을 바꾼다
set -eu

REPO=$(cd "$(dirname "$0")/../.." && pwd)
E2E_DIR="$REPO/scripts/e2e"
SHA=$(git -C "$REPO" rev-parse HEAD)
AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PROJECT=anxious-s13-front-runner-http-$$
export API_RUNTIME_IMAGE=anxious-s13-front-rh-api
export WEB_RUNTIME_IMAGE=anxious-s13-front-rh-web
export M3_FIXTURE_IMAGE=anxious-s13-front-rh-fixture
export M3_GATEWAY_PORT=18110
export RUNNER_HTTP_ENABLE=true
RH="docker compose -p $PROJECT -f $REPO/backend/compose.m3-integration.yml -f $REPO/backend/compose.user-runner-http-test.yml"
PW_VERSION=1.56.1
PW_IMAGE="mcr.microsoft.com/playwright:v$PW_VERSION-noble"
BROWSER=front-runner-http-browser-$$
SHARED=$(mktemp -d)
chmod 777 "$SHARED"
OUT=${E2E_LOG_DIR:-$(mktemp -d)}
mkdir -p "$OUT/shots" && chmod 777 "$OUT/shots"
echo "== runner http live sha=$SHA built=$AT project=$PROJECT shared=$SHARED"

docker build -q --target runtime -f "$REPO/backend/Dockerfile" -t "$API_RUNTIME_IMAGE" \
  --build-arg APP_GIT_SHA="$SHA" --build-arg APP_BUILT_AT="$AT" "$REPO" >/dev/null
docker build -q --target test -f "$REPO/backend/Dockerfile" -t "$M3_FIXTURE_IMAGE" \
  --build-arg APP_GIT_SHA="$SHA" --build-arg APP_BUILT_AT="$AT" "$REPO" >/dev/null
docker build -q -f "$REPO/infra/hosting/web.Dockerfile" -t "$WEB_RUNTIME_IMAGE" \
  --build-arg WEB_GIT_SHA="$SHA" --build-arg WEB_BUILT_AT="$AT" --build-arg NEXT_PUBLIC_BASE_PATH= "$REPO" >/dev/null

cleanup() {
  docker rm -f "$BROWSER" >/dev/null 2>&1 || true
  if [ "${E2E_KEEP:-0}" != 1 ]; then
    # 이 실행이 만든 고유 project만 지운다.
    $RH down --volumes >/dev/null 2>&1 || true
    rm -f "$SHARED"/*
    rmdir "$SHARED" 2>/dev/null || true
  fi
}
trap cleanup EXIT

wait_ready() {
  i=0
  until [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$M3_GATEWAY_PORT/api/research/health" || true)" = 200 ]; do
    i=$((i + 1)); [ $i -gt 60 ] && { echo "api not ready"; $RH ps; exit 1; }
    sleep 2
  done
}
running() { [ "$(docker inspect -f '{{.State.Running}}' "$BROWSER" 2>/dev/null)" = true ]; }

$RH up -d db mock-github
$RH run --rm migrate
$RH run --rm runner-migrate
$RH up -d api web gateway
wait_ready
echo "== stack ready (flag=$RUNNER_HTTP_ENABLE)"

docker run -d --name "$BROWSER" --network host --ipc=host -e HOME=/tmp \
  -e ORIGIN="http://127.0.0.1:$M3_GATEWAY_PORT" -e SHARED=/shared -e SHOTS_DIR=/shots \
  -v "$E2E_DIR:/e2e:ro" -v "$SHARED:/shared" -v "$OUT/shots:/shots" "$PW_IMAGE" sh -c "
    mkdir -p /tmp/t && cd /tmp/t && npm i --silent playwright@$PW_VERSION >/dev/null 2>&1 &&
    cp /e2e/e2e-runner-http.mjs . && node e2e-runner-http.mjs; rc=\$?; chown -R $(id -u):$(id -g) /shots; exit \$rc" >/dev/null

approve() { # name
  ID=$(cat "$SHARED/$1.id"); ROLE=$(cat "$SHARED/$1.role" 2>/dev/null || echo editor)
  case "$ID" in *[!0-9]*|'') echo "invalid fixture id"; return 1;; esac
  case "$ROLE" in editor|commenter) ;; *) echo "invalid role"; return 1;; esac
  $RH run --rm admin --github-id "$ID" --approved true --role "$ROLE" \
    --actor fixture:front-it --reason "Verified disposable test identity ($1)" >/dev/null
  touch "$SHARED/$1.ok"
  echo "== approved fixture account $1 ($ROLE)"
}

uuid() { echo "$1" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'; }
OUTPUT=
handle() { # line
  set -- $1
  OUTPUT=
  case "$1" in
    phase)
      uuid "$2" || return 1
      case "$3" in preparing|waiting|unreachable|ready|stopping|stopped|failed|not_prepared) ;; *) return 1;; esac
      $RH run --rm -T runner-phase --account "$2" --phase "$3" >/dev/null ;;
    reserve)
      uuid "$2" || return 1
      $RH run --rm -T runner-phase --account "$2" --phase provisioning --creation-reserved >/dev/null ;;
    inspect)
      uuid "$2" || return 1
      OUTPUT=$($RH run --rm -T runner-phase --account "$2" --inspect 2>/dev/null | tail -n 1) ;;
    role)
      case "$2" in *[!0-9]*|'') return 1;; esac
      case "$3" in editor|commenter) ;; *) return 1;; esac
      $RH run --rm admin --github-id "$2" --approved true --role "$3" \
        --actor fixture:front-it --reason "Disposable test identity role change" >/dev/null ;;
    flag)
      case "$2" in true|false) ;; *) return 1;; esac
      RUNNER_HTTP_ENABLE=$2 $RH up -d --force-recreate api >/dev/null 2>&1
      wait_ready ;;
    *) return 1 ;;
  esac
}

STATUS=0
n=1
while running; do
  for f in "$SHARED"/*.id; do
    [ -e "$f" ] || continue
    name=$(basename "$f" .id)
    [ -e "$SHARED/$name.ok" ] || approve "$name" || STATUS=1
  done
  if [ -s "$SHARED/req-$n.cmd" ]; then
    line=$(head -n 1 "$SHARED/req-$n.cmd")
    if handle "$line"; then printf 'ok\n%s\n' "$OUTPUT" > "$SHARED/req-$n.done"; echo "== req $n: $line $OUTPUT"
    else echo fail > "$SHARED/req-$n.done"; echo "== req $n FAILED: $line"; fi
    n=$((n + 1))
    continue
  fi
  sleep 0.5
done

BSTATUS=$(docker wait "$BROWSER")
docker logs "$BROWSER" 2>&1
[ "$BSTATUS" = 0 ] || STATUS=$BSTATUS
$RH logs api > "$OUT/api.log" 2>&1 || true
echo "== exit $STATUS (evidence: $OUT)"
exit "$STATUS"
