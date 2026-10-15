#!/usr/bin/env bash
set -euo pipefail
repo=$(git rev-parse --show-toplevel)
evidence=$(mktemp -d /tmp/runner-policy-check.XXXXXX)
python_image=sha256:54b5608ca7caec4a427ae497c00ef2afd9faa38c9ed5e6c845ec35bf7a50805d
go_image=golang@sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c
draft="$repo/infra/hosting/m3/user-runner-policy-draft"
# Dependencies only; no cluster credentials or network-capable test execution.
docker run --rm -v "$draft/celcheck:/work:ro" -v anxious-s13-back-cel-mod:/go/pkg/mod \
  -w /work "$go_image" go mod download
docker run --rm --network none -v anxious-s13-back-cel-build:/cache "$go_image" \
  chown -R "$(id -u):$(id -g)" /cache
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" -v "$repo/backend:/source:ro" -v "$draft:/draft:ro" \
  -v "$evidence:/evidence" -w /source -e PYTHONPATH=/source --entrypoint python "$python_image" -B \
  -m ops.runner_policy --values /draft/schema/synthetic-values.json \
  --inventory /draft/schema/pod-v1.36.4.json --network-template /draft/network.json.in \
  --output /evidence/render
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw -v "$repo/backend:/source:ro" -v "$draft:/draft:ro" \
  -w /source -e PYTHONPATH=/source --entrypoint python "$python_image" -B \
  -m pytest -q -p no:cacheprovider config_tests/test_runner_policy.py config_tests/test_runner_kube.py
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" --tmpfs /tmp:rw,exec -e GOCACHE=/cache \
  -v "$draft/celcheck:/work:ro" -v anxious-s13-back-cel-mod:/go/pkg/mod:ro \
  -v anxious-s13-back-cel-build:/cache -v "$draft/schema:/inventory:ro" \
  -v "$evidence/render:/suite:ro" -w /work "$go_image" \
  go run -mod=readonly . /inventory/pod-v1.36.4.json /suite/suite.json
printf 'Synthetic review evidence: %s/render\n' "$evidence"
