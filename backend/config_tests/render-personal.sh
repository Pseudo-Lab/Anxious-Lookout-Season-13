#!/usr/bin/env bash
# Docker-only rendering, no up/run services, actual secrets, or provider requests.
set -euo pipefail
CONFIG_REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
CONFIG_RESULT_DIR=${1:?Set a disposable synthetic render output directory}
mkdir -p "$CONFIG_RESULT_DIR"
CONFIG_RESULT_DIR=$(cd "$CONFIG_RESULT_DIR" && pwd)
CONFIG_INPUT_FILE="$CONFIG_RESULT_DIR/bootstrap.env"
cat > "$CONFIG_INPUT_FILE" <<'ENV'
PERSONAL_PROJECT_NAME=anxious-s13-back-personal-config-synthetic
PERSONAL_API_IMAGE=synthetic-api-runtime:unstarted
PERSONAL_WEB_IMAGE=synthetic-web-runtime:unstarted
PERSONAL_DATABASE_NAME=synthetic_personal_db
PERSONAL_GATEWAY_PORT=28107
PERSONAL_GITHUB_CLIENT_ID=synthetic-github-client
PERSONAL_API_INPUT_DIR=/tmp/issue7-config-synthetic/api
PERSONAL_GITHUB_INPUT_DIR=/tmp/issue7-config-synthetic/github
PERSONAL_OPS_INPUT_DIR=/tmp/issue7-config-synthetic/ops
PERSONAL_DB_PASSWORD_FILE=/tmp/issue7-config-synthetic/db-password
PERSONAL_GITHUB_EGRESS_NETWORK=synthetic-reviewed-github-egress
PERSONAL_API_CPUS=0.5
PERSONAL_API_MEMORY=512m
PERSONAL_DB_CPUS=1
PERSONAL_DB_MEMORY=1g
PERSONAL_WEB_CPUS=0.5
PERSONAL_WEB_MEMORY=512m
PERSONAL_GATEWAY_CPUS=0.5
PERSONAL_GATEWAY_MEMORY=256m
ENV
cp "$CONFIG_INPUT_FILE" "$CONFIG_RESULT_DIR/enabled.env"
cat >> "$CONFIG_RESULT_DIR/enabled.env" <<'ENV'
PERSONAL_PLATFORM_ACCOUNT_ID=00000000-0000-4000-8000-000000000001
PERSONAL_RUNNER_STATE_ID=00000000-0000-4000-8000-000000000002
PERSONAL_RUNNER_IMAGE=synthetic-pinned-runner-runtime:unstarted
PERSONAL_CONTROL_DIR=/tmp/issue7-config-synthetic/control
PERSONAL_PROVIDER_EGRESS_NETWORK=synthetic-reviewed-provider-egress
PERSONAL_RUNNER_CPUS=1
PERSONAL_RUNNER_MEMORY=2g
ENV
render() {
  local config_input=$1 config_output=$2 config_mode=$3
  local -a config_files=(-f "$CONFIG_REPO_DIR/backend/compose.codex-bootstrap.yml")
  if [[ "$config_mode" == enabled ]]; then
    config_files+=(-f "$CONFIG_REPO_DIR/backend/compose.codex-personal.yml")
  fi
  env -i PATH="$PATH" docker compose --env-file "$config_input" --profile ops \
    "${config_files[@]}" config --format json > "$config_output"
}
render "$CONFIG_INPUT_FILE" "$CONFIG_RESULT_DIR/bootstrap.json" bootstrap
render "$CONFIG_RESULT_DIR/enabled.env" "$CONFIG_RESULT_DIR/enabled.json" enabled
: > "$CONFIG_RESULT_DIR/rejections.tsv"
for config_missing in PERSONAL_PROJECT_NAME PERSONAL_API_IMAGE PERSONAL_DB_PASSWORD_FILE \
  PERSONAL_OPS_INPUT_DIR PERSONAL_GITHUB_EGRESS_NETWORK PERSONAL_GATEWAY_PORT; do
  sed "/^${config_missing}=/d" "$CONFIG_INPUT_FILE" > "$CONFIG_RESULT_DIR/missing.env"
  if render "$CONFIG_RESULT_DIR/missing.env" "$CONFIG_RESULT_DIR/missing.json" bootstrap 2> "$CONFIG_RESULT_DIR/rejection.log"; then
    printf '%s\tunexpected-success\n' "$config_missing" >> "$CONFIG_RESULT_DIR/rejections.tsv"
    exit 1
  fi
  printf '%s\trejected\n' "$config_missing" >> "$CONFIG_RESULT_DIR/rejections.tsv"
done
for config_missing in PERSONAL_PLATFORM_ACCOUNT_ID PERSONAL_RUNNER_STATE_ID PERSONAL_RUNNER_IMAGE PERSONAL_CONTROL_DIR \
  PERSONAL_PROVIDER_EGRESS_NETWORK PERSONAL_RUNNER_CPUS PERSONAL_RUNNER_MEMORY; do
  sed "/^${config_missing}=/d" "$CONFIG_RESULT_DIR/enabled.env" > "$CONFIG_RESULT_DIR/missing.env"
  if render "$CONFIG_RESULT_DIR/missing.env" "$CONFIG_RESULT_DIR/missing.json" enabled 2> "$CONFIG_RESULT_DIR/rejection.log"; then
    printf '%s\tunexpected-success\n' "$config_missing" >> "$CONFIG_RESULT_DIR/rejections.tsv"
    exit 1
  fi
  printf '%s\trejected\n' "$config_missing" >> "$CONFIG_RESULT_DIR/rejections.tsv"
done
for config_empty in PERSONAL_PLATFORM_ACCOUNT_ID PERSONAL_RUNNER_STATE_ID PERSONAL_RUNNER_IMAGE PERSONAL_CONTROL_DIR \
  PERSONAL_PROVIDER_EGRESS_NETWORK PERSONAL_RUNNER_CPUS PERSONAL_RUNNER_MEMORY; do
  sed "s/^${config_empty}=.*/${config_empty}=/" "$CONFIG_RESULT_DIR/enabled.env" > "$CONFIG_RESULT_DIR/missing.env"
  if render "$CONFIG_RESULT_DIR/missing.env" "$CONFIG_RESULT_DIR/missing.json" enabled 2> "$CONFIG_RESULT_DIR/rejection.log"; then
    printf '%s-empty\tunexpected-success\n' "$config_empty" >> "$CONFIG_RESULT_DIR/rejections.tsv"
    exit 1
  fi
  printf '%s-empty\trejected\n' "$config_empty" >> "$CONFIG_RESULT_DIR/rejections.tsv"
done
# Use Compose's own YAML reader for dynamic routing data without new test deps.
{
  printf 'services:\n  parse-only:\n    image: synthetic-unused:unstarted\nx-routing:\n'
  sed 's/^/  /' "$CONFIG_REPO_DIR/backend/config/codex-personal-routes.yml"
} > "$CONFIG_RESULT_DIR/routes-wrapper.yml"
env -i PATH="$PATH" docker compose --env-file "$CONFIG_INPUT_FILE" \
  -f "$CONFIG_RESULT_DIR/routes-wrapper.yml" config --format json > "$CONFIG_RESULT_DIR/routes.json"
printf 'Synthetic Docker render complete; no services started\n'
