#!/usr/bin/env bash
# Local production builds/archives only. No live import/source/auth/native startup.
set -euo pipefail
set +x
umask 077
ACCESS_BUILD_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
ACCESS_BUILD_SHA=${1:?Exact committed source SHA}
ACCESS_BUILD_OUTPUT=${2:?Fresh private output directory under /tmp}
[[ $ACCESS_BUILD_SHA =~ ^[a-f0-9]{40}$ && $(git -C "$ACCESS_BUILD_ROOT" rev-parse HEAD) == "$ACCESS_BUILD_SHA" ]]
[[ -z $(git -C "$ACCESS_BUILD_ROOT" status --porcelain) ]]
[[ $ACCESS_BUILD_OUTPUT =~ ^/tmp/issue7-access-build-[A-Za-z0-9_-]+$ && ! -e $ACCESS_BUILD_OUTPUT ]]
mkdir -m 0700 -- "$ACCESS_BUILD_OUTPUT"
ACCESS_BUILD_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
for ACCESS_BUILD_ROLE in api runner; do
  ACCESS_BUILD_TARGET=runtime
  [[ $ACCESS_BUILD_ROLE != runner ]] || ACCESS_BUILD_TARGET=runner
  ACCESS_BUILD_REPO="docker.io/library/anxious-s13-access-$ACCESS_BUILD_ROLE-${ACCESS_BUILD_SHA:0:7}"
  docker buildx build --load --platform linux/arm64 --target "$ACCESS_BUILD_TARGET" \
    --build-arg "APP_GIT_SHA=$ACCESS_BUILD_SHA" --build-arg "APP_BUILT_AT=$ACCESS_BUILD_AT" \
    --label "org.opencontainers.image.revision=$ACCESS_BUILD_SHA" \
    --metadata-file "$ACCESS_BUILD_OUTPUT/$ACCESS_BUILD_ROLE.metadata.json" \
    -f "$ACCESS_BUILD_ROOT/backend/Dockerfile" -t "$ACCESS_BUILD_REPO:build" "$ACCESS_BUILD_ROOT" \
    > "$ACCESS_BUILD_OUTPUT/$ACCESS_BUILD_ROLE.build.log" 2>&1
  docker image inspect "$ACCESS_BUILD_REPO:build" > "$ACCESS_BUILD_OUTPUT/$ACCESS_BUILD_ROLE.inspect.json"
  docker image save -o "$ACCESS_BUILD_OUTPUT/$ACCESS_BUILD_ROLE.tar" "$ACCESS_BUILD_REPO:build"
  docker run --rm --pull=never --network none --read-only --tmpfs /tmp \
    --cap-drop ALL --security-opt no-new-privileges:true --user "$(id -u):$(id -g)" \
    --mount "type=bind,src=$ACCESS_BUILD_ROOT/infra/hosting/m3,dst=/checks,readonly" \
    --mount "type=bind,src=$ACCESS_BUILD_OUTPUT,dst=/evidence,readonly" \
    --entrypoint python "$ACCESS_BUILD_REPO:build" -B /checks/access_image_audit.py \
    "/evidence/$ACCESS_BUILD_ROLE.tar" "/evidence/$ACCESS_BUILD_ROLE.metadata.json" \
    "/evidence/$ACCESS_BUILD_ROLE.inspect.json" "$ACCESS_BUILD_REPO" "$ACCESS_BUILD_SHA" \
    > "$ACCESS_BUILD_OUTPUT/$ACCESS_BUILD_ROLE.audit.json"
done
jq -n --arg sourceSha "$ACCESS_BUILD_SHA" \
  --arg api "$(jq -r .reference "$ACCESS_BUILD_OUTPUT/api.audit.json")" \
  --arg runner "$(jq -r .reference "$ACCESS_BUILD_OUTPUT/runner.audit.json")" \
  '{sourceSha:$sourceSha,api:$api,runner:$runner}' > "$ACCESS_BUILD_OUTPUT/images.json"
printf 'Production API/runner build and local archive audit complete; no import/deployment/auth performed\n'
