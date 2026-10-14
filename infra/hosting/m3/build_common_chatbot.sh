#!/usr/bin/env bash
# Local three-role production artifacts only; no live import/start/supply.
set -euo pipefail
set +x
umask 077
CHATBOT_BUILD_TOOLS_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
CHATBOT_BUILD_ROOT=${3:-$CHATBOT_BUILD_TOOLS_ROOT}
CHATBOT_BUILD_SHA=${1:?Final reviewed committed shared source SHA}
CHATBOT_BUILD_OUTPUT=${2:?Fresh /tmp/issue9-chatbot-build-SUFFIX directory}
[[ $CHATBOT_BUILD_SHA =~ ^[a-f0-9]{40}$ && $(git -C "$CHATBOT_BUILD_ROOT" rev-parse HEAD) == "$CHATBOT_BUILD_SHA" ]]
[[ -z $(git -C "$CHATBOT_BUILD_ROOT" status --porcelain) ]]
[[ $(realpath "$CHATBOT_BUILD_ROOT") == "$CHATBOT_BUILD_ROOT" ]]
[[ $CHATBOT_BUILD_ROOT == "$CHATBOT_BUILD_TOOLS_ROOT" || $CHATBOT_BUILD_ROOT =~ ^/tmp/issue9-chatbot-source-[A-Za-z0-9_-]+$ ]]
CHATBOT_BUILD_TOOLS_SHA=$(git -C "$CHATBOT_BUILD_TOOLS_ROOT" rev-parse HEAD)
[[ -z $(git -C "$CHATBOT_BUILD_TOOLS_ROOT" status --porcelain) ]]
[[ -z $(git -C "$CHATBOT_BUILD_TOOLS_ROOT" diff --name-only "$CHATBOT_BUILD_SHA" "$CHATBOT_BUILD_TOOLS_SHA" -- backend src public scripts package.json package-lock.json .dockerignore infra/hosting/web.Dockerfile) ]]
[[ $CHATBOT_BUILD_OUTPUT =~ ^/tmp/issue9-chatbot-build-[A-Za-z0-9_-]+$ && ! -e $CHATBOT_BUILD_OUTPUT ]]
mkdir -m 0700 -- "$CHATBOT_BUILD_OUTPUT"
CHATBOT_BUILD_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
for CHATBOT_BUILD_ROLE in api runner web; do
  CHATBOT_BUILD_REPO="docker.io/library/anxious-s13-chatbot-$CHATBOT_BUILD_ROLE-${CHATBOT_BUILD_SHA:0:7}"
  CHATBOT_BUILD_ARGS=(--target runtime -f "$CHATBOT_BUILD_ROOT/backend/Dockerfile"
    --build-arg "APP_GIT_SHA=$CHATBOT_BUILD_SHA" --build-arg "APP_BUILT_AT=$CHATBOT_BUILD_AT")
  if [[ $CHATBOT_BUILD_ROLE == runner ]]; then
    CHATBOT_BUILD_ARGS[1]=runner
  elif [[ $CHATBOT_BUILD_ROLE == web ]]; then
    CHATBOT_BUILD_ARGS=(-f "$CHATBOT_BUILD_ROOT/infra/hosting/web.Dockerfile"
      --build-arg "WEB_GIT_SHA=$CHATBOT_BUILD_SHA" --build-arg "WEB_BUILT_AT=$CHATBOT_BUILD_AT" --build-arg NEXT_PUBLIC_BASE_PATH=)
  fi
  docker buildx build --load --platform linux/arm64 "${CHATBOT_BUILD_ARGS[@]}" \
    --label "org.opencontainers.image.revision=$CHATBOT_BUILD_SHA" \
    --metadata-file "$CHATBOT_BUILD_OUTPUT/$CHATBOT_BUILD_ROLE.metadata.json" \
    -t "$CHATBOT_BUILD_REPO:build" "$CHATBOT_BUILD_ROOT" > "$CHATBOT_BUILD_OUTPUT/$CHATBOT_BUILD_ROLE.build.log" 2>&1
  docker image inspect "$CHATBOT_BUILD_REPO:build" > "$CHATBOT_BUILD_OUTPUT/$CHATBOT_BUILD_ROLE.inspect.json"
  docker image save -o "$CHATBOT_BUILD_OUTPUT/$CHATBOT_BUILD_ROLE.tar" "$CHATBOT_BUILD_REPO:build"
  # The API image runs Python stdlib to audit every role, including the Node web.
  CHATBOT_BUILD_AUDITOR="docker.io/library/anxious-s13-chatbot-api-${CHATBOT_BUILD_SHA:0:7}:build"
  docker run --rm --pull=never --network none --read-only --tmpfs /tmp \
    --cap-drop ALL --security-opt no-new-privileges:true --user "$(id -u):$(id -g)" \
    --mount "type=bind,src=$CHATBOT_BUILD_TOOLS_ROOT/infra/hosting/m3,dst=/checks,readonly" \
    --mount "type=bind,src=$CHATBOT_BUILD_OUTPUT,dst=/evidence,readonly" \
    --entrypoint python "$CHATBOT_BUILD_AUDITOR" -B /checks/access_image_audit.py \
    "/evidence/$CHATBOT_BUILD_ROLE.tar" "/evidence/$CHATBOT_BUILD_ROLE.metadata.json" \
    "/evidence/$CHATBOT_BUILD_ROLE.inspect.json" "$CHATBOT_BUILD_REPO" "$CHATBOT_BUILD_SHA" > "$CHATBOT_BUILD_OUTPUT/$CHATBOT_BUILD_ROLE.audit.json"
done
jq -n --arg sourceSha "$CHATBOT_BUILD_SHA" \
  --arg api "$(jq -r .reference "$CHATBOT_BUILD_OUTPUT/api.audit.json")" \
  --arg runner "$(jq -r .reference "$CHATBOT_BUILD_OUTPUT/runner.audit.json")" \
  --arg web "$(jq -r .reference "$CHATBOT_BUILD_OUTPUT/web.audit.json")" \
  '{sourceSha:$sourceSha,api:$api,runner:$runner,web:$web}' > "$CHATBOT_BUILD_OUTPUT/images.json"
jq -n --arg applicationSourceSha "$CHATBOT_BUILD_SHA" --arg toolsSourceSha "$CHATBOT_BUILD_TOOLS_SHA" --arg builtAt "$CHATBOT_BUILD_AT" \
  '{applicationSourceSha:$applicationSourceSha,toolsSourceSha:$toolsSourceSha,builtAt:$builtAt,imported:false}' > "$CHATBOT_BUILD_OUTPUT/build.json"
printf 'Three production archives audited locally; no import, deployment, auth or model dispatch\n'
