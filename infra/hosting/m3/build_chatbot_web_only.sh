#!/usr/bin/env bash
# Local web archive only, after Front source approval. No live import or native.
set -euo pipefail
set +x
umask 077
CHATBOT_WEB_TOOLS_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
CHATBOT_WEB_SHA=${1:?Approved exact web source SHA}
CHATBOT_WEB_OUTPUT=${2:?Fresh /tmp/issue9-chatbot-web-SUFFIX directory}
CHATBOT_WEB_ROOT=${3:?Clean dedicated approved app worktree}
CHATBOT_WEB_API_ID='sha256:b032048fc6aa396d5fef4bab3f8991855bad257c918adced4500d74e36ae3288'
CHATBOT_WEB_BASE='e9f59434997bd9ca376f72e83d12e93b36eb9dbe'
[[ $CHATBOT_WEB_SHA =~ ^[a-f0-9]{40}$ && $CHATBOT_WEB_SHA != "$CHATBOT_WEB_BASE" ]]
[[ $CHATBOT_WEB_ROOT =~ ^/tmp/issue9-chatbot-source-[A-Za-z0-9_-]+$ && $(realpath "$CHATBOT_WEB_ROOT") == "$CHATBOT_WEB_ROOT" ]]
[[ $(git -C "$CHATBOT_WEB_ROOT" rev-parse HEAD) == "$CHATBOT_WEB_SHA" && -z $(git -C "$CHATBOT_WEB_ROOT" status --porcelain) ]]
git -C "$CHATBOT_WEB_ROOT" merge-base --is-ancestor "$CHATBOT_WEB_BASE" "$CHATBOT_WEB_SHA"
[[ -z $(git -C "$CHATBOT_WEB_ROOT" diff --name-only "$CHATBOT_WEB_BASE" "$CHATBOT_WEB_SHA" -- backend) ]]
[[ -z $(git -C "$CHATBOT_WEB_TOOLS_ROOT" status --porcelain) ]]
[[ $CHATBOT_WEB_OUTPUT =~ ^/tmp/issue9-chatbot-web-[A-Za-z0-9_-]+$ && ! -e $CHATBOT_WEB_OUTPUT ]]
CHATBOT_WEB_TOOLS_SHA=$(git -C "$CHATBOT_WEB_TOOLS_ROOT" rev-parse HEAD)
CHATBOT_WEB_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
CHATBOT_WEB_REPO="docker.io/library/anxious-s13-chatbot-web-${CHATBOT_WEB_SHA:0:7}"
mkdir -m 0700 -- "$CHATBOT_WEB_OUTPUT"
docker buildx build --load --platform linux/arm64 -f "$CHATBOT_WEB_ROOT/infra/hosting/web.Dockerfile" \
  --build-arg "WEB_GIT_SHA=$CHATBOT_WEB_SHA" --build-arg "WEB_BUILT_AT=$CHATBOT_WEB_AT" --build-arg NEXT_PUBLIC_BASE_PATH= \
  --label "org.opencontainers.image.revision=$CHATBOT_WEB_SHA" --metadata-file "$CHATBOT_WEB_OUTPUT/web.metadata.json" \
  -t "$CHATBOT_WEB_REPO:build" "$CHATBOT_WEB_ROOT" > "$CHATBOT_WEB_OUTPUT/web.build.log" 2>&1
docker image inspect "$CHATBOT_WEB_REPO:build" > "$CHATBOT_WEB_OUTPUT/web.inspect.json"
docker image save -o "$CHATBOT_WEB_OUTPUT/web.tar" "$CHATBOT_WEB_REPO:build"
docker run --rm --pull=never --network none --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges:true --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$CHATBOT_WEB_TOOLS_ROOT/infra/hosting/m3,dst=/checks,readonly" \
  --mount "type=bind,src=$CHATBOT_WEB_OUTPUT,dst=/evidence,readonly" --entrypoint python "$CHATBOT_WEB_API_ID" \
  -B /checks/access_image_audit.py /evidence/web.tar /evidence/web.metadata.json /evidence/web.inspect.json \
  "$CHATBOT_WEB_REPO" "$CHATBOT_WEB_SHA" > "$CHATBOT_WEB_OUTPUT/web.audit.json"
jq -n --arg webSourceSha "$CHATBOT_WEB_SHA" --arg web "$(jq -r .reference "$CHATBOT_WEB_OUTPUT/web.audit.json")" \
  '{webSourceSha:$webSourceSha,web:$web}' > "$CHATBOT_WEB_OUTPUT/web-release.json"
jq -n --arg webSourceSha "$CHATBOT_WEB_SHA" --arg base "$CHATBOT_WEB_BASE" --arg operatorSourceSha "$CHATBOT_WEB_TOOLS_SHA" \
  --arg builtAt "$CHATBOT_WEB_AT" '{webSourceSha:$webSourceSha,apiSourceSha:$base,runnerSourceSha:$base,operatorSourceSha:$operatorSourceSha,builtAt:$builtAt,imported:false}' \
  > "$CHATBOT_WEB_OUTPUT/build.json"
printf 'Web-only production archive audited; existing API/runner unchanged, no import or native startup\n'
