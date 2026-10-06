#!/bin/sh
# 웹 산출물 Docker 검증: 빌드 → 운영과 같은 조건으로 실행 → Playwright(Chromium) 화면 검증.
# 실제 FastAPI/PostgreSQL/GitHub가 아니라 계약(api-contracts/m2-api-v1.md)을 흉내 내는 모의 API를 쓴다.
#
#   sh scripts/e2e/run.sh node  [base]   # k3s용 standalone. 예: sh scripts/e2e/run.sh node /m2
#   sh scripts/e2e/run.sh pages [base]   # GitHub Pages용 정적 export(API 없음). 기본 base /Anxious-Lookout-Season-13
#
# E2E_INSECURE_ORIGIN=1: 127.0.0.1 대신 컨테이너의 비-loopback IP로 접속한다. 공인 IP HTTP처럼
# 브라우저가 secure context가 아닌 origin에서도 동작하는지 확인한다(isSecureContext === false 단언).
#
# 저장소는 읽기 전용으로 마운트하고 빌드·실행 산출물은 임시 디렉터리($E2E_WORK)에만 만든다.
set -eu

MODE=${1:-node}
case "$MODE" in
  node) BASE=${2:-} ;;
  pages) BASE=${2:-/Anxious-Lookout-Season-13} ;;
  *) echo "usage: $0 node|pages [base]" >&2; exit 2 ;;
esac

REPO=$(cd "$(dirname "$0")/../.." && pwd)
E2E_DIR="$REPO/scripts/e2e"
WORK=${E2E_WORK:-$(mktemp -d)}
NAME="web-e2e-$MODE-$$"
SHA=$(git -C "$REPO" rev-parse HEAD)
NODE_IMAGE=node:22-alpine
PW_VERSION=1.56.1
PW_IMAGE="mcr.microsoft.com/playwright:v$PW_VERSION-noble"
echo "== mode=$MODE base='$BASE' sha=$SHA work=$WORK"

# 1) 빌드 (저장소 복사본에서 frozen install → lint → typecheck → build)
mkdir -p "$WORK/out"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e NEXT_TELEMETRY_DISABLED=1 \
  -e MODE="$MODE" -e BASE="$BASE" -e SHA="$SHA" \
  -v "$REPO:/src:ro" -v "$WORK/out:/out" "$NODE_IMAGE" sh -euc '
    mkdir /tmp/w && cd /src
    tar --exclude=./node_modules --exclude=./.git --exclude=./.pnpm-store --exclude=./.next --exclude=./out -cf - . | tar -xf - -C /tmp/w
    cd /tmp/w
    P="npx -y pnpm@9"
    $P install --frozen-lockfile >/tmp/install.log 2>&1 || { tail -20 /tmp/install.log; exit 1; }
    $P run lint
    $P run typecheck
    if [ "$MODE" = node ]; then
      WEB_GIT_SHA=$SHA WEB_BUILT_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ) $P run version:write
      NEXT_PUBLIC_BASE_PATH=$BASE $P run build
      cp -r public .next/standalone/ && cp -r .next/static .next/standalone/.next/
      tar -C .next/standalone -cf /out/app.tar .
    else
      NEXT_PUBLIC_BASE_PATH=$BASE $P run build:pages
      tar -C out -cf /out/app.tar .
    fi'
mkdir -p "$WORK/app" && tar -xf "$WORK/out/app.tar" -C "$WORK/app"

# 2) 실행 (node: 읽기 전용 FS·비root·HOSTNAME=0.0.0.0 + 모의 게이트웨이 / pages: 정적 서버)
if [ "$MODE" = node ]; then
  docker run -d --rm --name "$NAME" --read-only --tmpfs /tmp --user 1000:1000 \
    -e NODE_ENV=production -e NEXT_TELEMETRY_DISABLED=1 -e PORT=3000 -e HOSTNAME=0.0.0.0 -e BASE_PATH="$BASE" \
    -v "$WORK/app:/app:ro" -v "$E2E_DIR:/e2e:ro" -w /app "$NODE_IMAGE" \
    sh -c 'node server.js & exec node /e2e/mock-gateway.mjs' >/dev/null
  SCRIPT=e2e.mjs
else
  docker run -d --rm --name "$NAME" --read-only --user 1000:1000 -e OUT_DIR=/out -e PREFIX="$BASE" \
    -v "$WORK/app:/out:ro" -v "$E2E_DIR:/e2e:ro" "$NODE_IMAGE" node /e2e/pages-static.mjs >/dev/null
  SCRIPT=e2e-pages.mjs
fi
trap 'docker stop "$NAME" >/dev/null 2>&1 || true' EXIT
sleep 4

ORIGIN_HOST=127.0.0.1
EXPECT_INSECURE=0
if [ "${E2E_INSECURE_ORIGIN:-0}" = 1 ]; then
  ORIGIN_HOST=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$NAME")
  EXPECT_INSECURE=1
fi
echo "== origin http://$ORIGIN_HOST:8080"

# 3) 브라우저 검증
STATUS=0
docker run --rm --network "container:$NAME" --ipc=host -e HOME=/tmp \
  -e ORIGIN="http://$ORIGIN_HOST:8080" -e EXPECT_INSECURE="$EXPECT_INSECURE" -e BASE_PATH="$BASE" -e EXPECT_SHA="$SHA" \
  -v "$E2E_DIR:/e2e:ro" "$PW_IMAGE" sh -c "
    mkdir -p /tmp/t && cd /tmp/t && npm i --silent playwright@$PW_VERSION >/dev/null 2>&1 &&
    cp /e2e/$SCRIPT . && node $SCRIPT" || STATUS=$?

if [ "$MODE" = node ]; then
  echo "== server log: EACCES=$(docker logs "$NAME" 2>&1 | grep -c EACCES || true) NoFallbackError=$(docker logs "$NAME" 2>&1 | grep -c NoFallbackError || true)"
  [ "$(docker logs "$NAME" 2>&1 | grep -c EACCES || true)" = 0 ] || STATUS=1
fi
echo "== exit $STATUS (work dir: $WORK)"
exit $STATUS
