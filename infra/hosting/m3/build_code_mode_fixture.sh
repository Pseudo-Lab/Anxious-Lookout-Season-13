#!/usr/bin/env bash
set -euo pipefail
COMPANION_SOURCE_DIR=$(cd "$(dirname "$0")" && pwd)
COMPANION_ASSETS=${1:?Directory containing official host.tar.gz}
COMPANION_BASE=sha256:6fc02fa74a1b61b51301d57b3a54a263744e020dc1052d5070ada73bfdc33f87
[[ $(docker image inspect anxious-s13-uid-runner-70fbad4 --format '{{.Id}}') == "$COMPANION_BASE" ]]
[[ -f "$COMPANION_ASSETS/host.tar.gz" && ! -L "$COMPANION_ASSETS/host.tar.gz" ]]
[[ $(sha256sum "$COMPANION_ASSETS/host.tar.gz" | cut -d' ' -f1) == e5e027e6689efda2e3570aa600179f0ebb18632803350e152ed6c9b97dcf9741 ]]
COMPANION_CONTEXT=$(mktemp -d /tmp/issue7-code-mode-build.XXXXXX)
cp "$COMPANION_ASSETS/host.tar.gz" "$COMPANION_CONTEXT/host.tar.gz"
cp "$COMPANION_SOURCE_DIR/install_fixture_host.py" "$COMPANION_CONTEXT/install_fixture_host.py"
cp "$COMPANION_SOURCE_DIR/Dockerfile.code-mode-fixture" "$COMPANION_CONTEXT/Dockerfile"
# No tag: candidate receives a distinct immutable ID, existing images untouched.
docker build --pull=false --network=none --iidfile "$COMPANION_ASSETS/candidate-image-id" "$COMPANION_CONTEXT"
[[ $(docker image inspect anxious-s13-uid-runner-70fbad4 --format '{{.Id}}') == "$COMPANION_BASE" ]]
rm -rf "$COMPANION_CONTEXT"
