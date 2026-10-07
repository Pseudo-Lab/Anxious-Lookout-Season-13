#!/usr/bin/env bash
# Fresh isolated Docker update/rollback; never switches live images/config/data.
set -euo pipefail
hosting_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$hosting_dir/probes.env"
: "${CANDIDATE_API_IMAGE:?Set tested immutable candidate API}"
: "${CANDIDATE_WEB_IMAGE:?Set tested immutable candidate web}"
: "${CANDIDATE_SHA:?Set exact candidate source SHA}"
: "${CANDIDATE_BUILT_AT:?Set exact candidate UTC build time}"
original_api=anxious-hosting-api@sha256:843880452e3b53c62c46fc932356263461b43aeed23b0b3f46896f36b1767e6f
original_web=anxious-hosting-web@sha256:199a065195bfff1f8f8b67668dcd57c0ad7c4a53073bb1729380f38674ee4cfa
original_sha=684d627db464b2e531cf3fce3633e8f75aa93d44
original_built=2026-10-06T16:49:41Z
[[ "$CANDIDATE_API_IMAGE" != "$original_api" && "$CANDIDATE_WEB_IMAGE" != "$original_web" ]] || exit 1
export API_RUNTIME_IMAGE=$original_api WEB_RUNTIME_IMAGE=$original_web
project="anxious-back-release-$$"
dc() { docker compose -p "$project" -f "$hosting_dir/compose.test.yml" -f "$hosting_dir/compose.release-check.yml" "$@"; }
cleanup() { dc down >/dev/null 2>&1 || true; }
trap cleanup EXIT
dc up -d --wait db mock-github
dc run --rm --no-deps -T test python -m app.migrate --revision 0001_auth
dc up -d api web gateway
check() {
  dc run --rm --no-deps -T test python /ops/release_roundtrip.py verify "$1" "$2" "$3" "$4"
  timeout "$M2_API_PROBE_TIMEOUT_SECONDS" docker exec "$(dc ps -q api)" python -m app.probe /readyz
  timeout "$M2_WEB_PROBE_TIMEOUT_SECONDS" docker exec "$(dc ps -q web)" node -e "$M2_WEB_PROBE_JS"
}
dc run --rm --no-deps -T test python /ops/release_roundtrip.py seed
check "$original_sha" "$original_built" "$original_sha" "$original_built"
export API_RUNTIME_IMAGE=$CANDIDATE_API_IMAGE
dc up -d --no-deps api
check "$CANDIDATE_SHA" "$CANDIDATE_BUILT_AT" "$original_sha" "$original_built"
export WEB_RUNTIME_IMAGE=$CANDIDATE_WEB_IMAGE
dc up -d --no-deps web
check "$CANDIDATE_SHA" "$CANDIDATE_BUILT_AT" "$CANDIDATE_SHA" "$CANDIDATE_BUILT_AT"
docker run --rm --network "container:$(dc ps -q gateway)" \
  -v "$hosting_dir/release_browser.mjs:/opt/driver/release_check.mjs:ro" \
  anxious-hosting-browser-test node release_check.mjs "$CANDIDATE_SHA" "$CANDIDATE_BUILT_AT"
export WEB_RUNTIME_IMAGE=$original_web
dc up -d --no-deps web
check "$CANDIDATE_SHA" "$CANDIDATE_BUILT_AT" "$original_sha" "$original_built"
export API_RUNTIME_IMAGE=$original_api
dc up -d --no-deps api
check "$original_sha" "$original_built" "$original_sha" "$original_built"
dc run --rm --no-deps -T test python /ops/release_roundtrip.py finish
echo 'PASS original→candidate→original distinct-image roundtrip; preserved DB/admin/audit/mock-session and final logout'
