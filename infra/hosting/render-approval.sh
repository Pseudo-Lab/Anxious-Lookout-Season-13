#!/usr/bin/env bash
# Operator supplies VERIFIED identity and authorization; never chooses an admin.
set -euo pipefail
: "${GITHUB_ID:?Set verified numeric provider ID}"
: "${APPROVED:?Set true or false}"
: "${ROLE:?Set commenter, editor or admin}"
: "${ACTOR:?Set verified operator reference}"
: "${REASON:?Set audit reason}"
[[ "$GITHUB_ID" =~ ^[1-9][0-9]{0,31}$ ]] || exit 1
[[ "$APPROVED" == true || "$APPROVED" == false ]] || exit 1
[[ "$ROLE" == commenter || "$ROLE" == editor || "$ROLE" == admin ]] || exit 1
# YAML literal safety; Unicode audit reasons remain available via the CLI directly.
[[ "$ACTOR" =~ ^[a-zA-Z0-9._:@/\ -]{1,200}$ && "$REASON" =~ ^[a-zA-Z0-9._:@/\ -]{1,200}$ ]] || exit 1
hosting_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$hosting_dir/render-migration.sh" | sed \
  -e 's/name: auth-alembic-0001/generateName: auth-approval-/' \
  -e "s|command: \[python, -m, app.migrate, --revision, \"0001_auth\"\]|command: [python, -m, app.admin, --github-id, \"$GITHUB_ID\", --approved, \"$APPROVED\", --role, \"$ROLE\", --actor, \"$ACTOR\", --reason, \"$REASON\"]|"
