#!/usr/bin/env bash
# Docker-only parser/HTTP fixtures; never touches the cluster or public fixture IP.
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
test_image=${ROUTING_TEST_IMAGE:-anxious-hosting-api-test}
fixture_prefix="anxious-back-route-$$"
fixture_dir=$(mktemp -d)
cleanup() {
  docker rm -f "$fixture_prefix-probe" "$fixture_prefix-gateway" "$fixture_prefix-server" >/dev/null 2>&1 || true
  docker network rm "$fixture_prefix" >/dev/null 2>&1 || true
  rm -f "$fixture_dir/routes.yml" "$fixture_dir/rendered.yml"
  rmdir "$fixture_dir"
}
trap cleanup EXIT
chmod 755 "$fixture_dir"
docker run --rm --network none --read-only --tmpfs /tmp \
  -v "$repo_dir/infra/hosting:/ops:ro" --entrypoint bash "$test_image" -c '
    set -euo pipefail
    export WEB_IMAGE="fixture/web@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    export API_IMAGE="fixture/api@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    for origin in http://10.0.0.1 http://169.254.169.254 http://m2.invalid http://93.184.216.34:80 http://93.184.216.34:08081; do
      if M2_PUBLIC_ORIGIN="$origin" bash /ops/render.sh >/dev/null 2>&1; then exit 1; fi
    done
    M2_PUBLIC_ORIGIN=http://93.184.216.34 bash /ops/render.sh
    bash /ops/render-migration.sh >/tmp/migration.yml
    grep -Fq "command: [python, -m, app.migrate, --revision, \"0001_auth\"]" /tmp/migration.yml
    GITHUB_ID=123456 APPROVED=true ROLE=editor ACTOR=fixture REASON=fixture bash /ops/render-approval.sh >/tmp/approval.yml
    grep -Fq "command: [python, -m, app.admin," /tmp/approval.yml
    ! grep -q "app.migrate" /tmp/approval.yml
  ' > "$fixture_dir/rendered.yml"
api_rule=$(sed -n 's/^    - match: //p' "$fixture_dir/rendered.yml" | sed -n '1p')
web_rule=$(sed -n 's/^    - match: //p' "$fixture_dir/rendered.yml" | sed -n '2p')
[[ -n "$api_rule" && -n "$web_rule" ]] || exit 1
cat > "$fixture_dir/routes.yml" <<YAML
http:
  routers:
    api:
      rule: "$api_rule"
      entryPoints: [web]
      service: fixture
      middlewares: [api]
    web:
      rule: "$web_rule"
      entryPoints: [web]
      service: fixture
      middlewares: [web]
    existing-android:
      rule: 'Path(\`/android-agent\`) || PathPrefix(\`/android-agent/\`)'
      entryPoints: [web]
      service: fixture
      middlewares: [android]
  middlewares:
    api: {headers: {customRequestHeaders: {X-Fixture-Service: api}}}
    web: {headers: {customRequestHeaders: {X-Fixture-Service: web}}}
    android: {headers: {customRequestHeaders: {X-Fixture-Service: android}}}
  services:
    fixture:
      loadBalancer:
        servers: [{url: 'http://fixture:8080'}]
YAML
docker network create --internal "$fixture_prefix" >/dev/null
docker run -d --name "$fixture_prefix-server" --network "$fixture_prefix" --network-alias fixture \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  "$test_image" python -m tests.route_fixture >/dev/null
docker run -d --name "$fixture_prefix-gateway" --network "$fixture_prefix" --network-alias gateway \
  --read-only --tmpfs /tmp --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges \
  -v "$fixture_dir/routes.yml:/etc/traefik/routes.yml:ro" \
  rancher/mirrored-library-traefik:3.7.8@sha256:4299bbed850421258fc5448c2e0e6ad350981d4d335a68de11b92448aedbefe5 \
  --entrypoints.web.address=:8080 --providers.file.filename=/etc/traefik/routes.yml --api.dashboard=false >/dev/null
docker run --rm --name "$fixture_prefix-probe" --network "$fixture_prefix" \
  --read-only --tmpfs /tmp "$test_image" python -m tests.route_probe
