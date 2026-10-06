#!/usr/bin/env bash
# Isolated Compose project only. No k3s mutations or real OAuth settings.
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo_dir"
test_compose=infra/hosting/compose.test.yml
dc() { docker compose -p anxious-s13-back-test -f "$test_compose" "$@"; }
dc build mock-github
dc run --rm test
dc up -d api
dc run --rm test python -m tests.live_probe seed
dc stop db
dc run --rm --no-deps test python -m tests.live_probe outage
dc up -d db
dc run --rm test python -m tests.live_probe persist
