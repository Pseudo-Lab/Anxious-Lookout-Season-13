# Issue 13: one real login owner, one runner

This is an operational candidate for PM execution. Docker tests use fake Kubernetes
and a fake provider transport; they do not prove a live model answer. The deployment
is k3s `m2-hosting/api`, not Docker Compose. Keep existing trial ConfigMaps, volumes,
network rules and runner mapping. No frontend change is needed for preparation or
logout. The backend API image needs this commit; the pinned existing runner image
can be reused because its service supply socket already exists.

## Inputs and ownership

PM privately identifies the existing approved editor/admin account UUID and selected
ChatGPT account/source. Do not commit either account, tokens, runtime bindings or
kubeconfig. Default isolated namespace is `codex-runner-rehearsal`; its name and UID
are inputs. A different existing isolated namespace needs the same restricted PSA,
`runner-default-deny` KNP and tokenless `runner-workload` SA. Do not use `m2-hosting`
as the runner namespace. The runner SA receives no RBAC or token.

Prepare two private directories: an operator UID0 mode0700 input directory and an
empty output parent. Input files are UID0 mode0600, with kube API CA, bounded
operator kube token, API database URL and admin database URL. Database URLs are
mounted as files, never command-line values. Mount the existing host parent
`/var/lib/anxious-lookout` at the SAME absolute path into the preparation container.
The selected fresh `hostRoot` beneath it must exist mode0700. Preparation creates
per-state subdirectories, not synthetic directories.

`inputs.json` schema (example identities are placeholders, not operational values):

```json
{
  "owner": "<existing login account UUID>",
  "namespace": "codex-runner-rehearsal",
  "namespaceUid": "<observed namespace UID>",
  "hostRoot": "/var/lib/anxious-lookout/user-runners",
  "node": "anxious-lookout",
  "podCIDR": "10.42.0.0/16",
  "providerAccountId": "<selected ChatGPT account id>",
  "image": "<reviewed runner image>@sha256:<digest>",
  "callback": "http://api.m2-hosting.svc.cluster.local:8080/api/internal/research/tools",
  "api": "https://10.0.0.107:6443",
  "kubeTokenFile": "/inputs/kube-token",
  "caFile": "/inputs/ca.crt"
}
```

## One execution for owner preparation

Build/import the backend image through the existing Docker release workflow. Set
`RUNNER_TOOL_IMAGE` to its digest, `RUNNER_INPUT_DIR` to the private input directory,
and `RUNNER_OUTPUT_PARENT` to an existing private output parent. Use a reviewed
bounded preparation credential with namespace/PV/PVC/Secret creation rights; this
credential is NOT given to the API or runner.

```sh
docker run --rm --user 0:0 --network host --read-only \
  --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --security-opt no-new-privileges \
  --mount "type=bind,src=$RUNNER_INPUT_DIR,dst=/inputs,readonly" \
  --mount "type=bind,src=$RUNNER_OUTPUT_PARENT,dst=/output" \
  --mount type=bind,src=/var/lib/anxious-lookout,dst=/var/lib/anxious-lookout \
  -e DATABASE_URL_FILE=/inputs/database-url \
  -e ADMIN_DATABASE_URL_FILE=/inputs/admin-database-url \
  --entrypoint python "$RUNNER_TOOL_IMAGE" -B -m ops.user_runner_prepare \
  --inputs /inputs/inputs.json --output /output/owner
```

This verifies namespace UID/PSA/default-deny/tokenless SA; runs the additive runtime
migration; binds an existing eligible login account; creates the generation-1
registry/outbox intent, fresh host state and control markers, Retain PVs/PVCs and
immutable transport Secret; and emits runtime.json, transport-token, supply-inputs.json
and preparation.json. The output directory and registry receipt are the preparation
permit. No provider credential is read, Pod is created, policy is changed or resource
is deleted. Partial failure is retained for reconciliation; never erase the output
and retry or overwrite synthetic storage. PVC binding can settle asynchronously;
verify both Bound before enable. Do not copy the root-owned preparation.json into
the API Secret.

## Review candidates and activation (PM)

1. Snapshot the existing API Deployment, `hosting-access-web-link-v1` CNP,
   `runner-callback-draft` CNP, ResourceQuota and baseline API health/trial response
   to a private directory. Export live link JSON; the renderer retains its trial
   egress rules and default-deny settings. Record API and runner image digests.
2. Run `python -B -m ops.user_runner_deployment` **inside Docker**, as UID10001,
   with the generated owner directory mounted read-only, existing link JSON mounted
   read-only and a writable private output parent. Arguments:
   `--config /owner/runtime.json --api-image <digest> --existing-link /baseline/link.json
   --output /review/candidate`. It emits `resources.json` and `api-patch.json`.
3. Request review of these concrete candidates BEFORE changing feature/max_pods,
   provider egress or performing resource deletion. Both CNPs are updates to existing
   names: no new CNP/VAP layer. RBAC grants API SA hosting namespace read plus create
   Pods (Kubernetes cannot resourceName-scope create), get/delete only this owner's
   Pod, and get only its PVCs/Secret. It grants no exec, bind, secret mutation or runner
   permissions. The Pod itself remains automount=false and has no kube token.
4. After the user's cleanup decision, remove the synthetic Pod and confirm zero
   Pods in the target namespace before enabling. The quota remains named
   `runner-bootstrap-no-pods` but changes count/pods to 1 in resources.json. A quota
   failure consumes the one create reservation; do not retry by deleting receipts.
5. Create the private input Secret without printing its content:
   `kubectl -n m2-hosting create secret generic hosting-user-runner-v1
   --from-file=runtime.json=<owner-output>/runtime.json
   --from-file=transport-token=<owner-output>/transport-token`.
   Do not replace an existing Secret without reconciling its identity first.
6. After review, `kubectl apply -f <candidate>/resources.json`, then
   `kubectl -n m2-hosting patch deployment api --type strategic
   --patch-file <candidate>/api-patch.json`. Record `CODEX_USER_RUNNERS_ENABLE=true`,
   `CODEX_USER_RUNNER_MAX_PODS=1`, target account and SHA privately. The API patch
   keeps all original containers' fields/envFrom/volumes except explicit overrides.
   The API-only projected SA token rotates (1-hour request lifetime), and the client
   rereads it on each Kubernetes call. A nonroot init container copies only the
   runtime binding/transport token into API-owned private tmpfs. No host paths or
   cluster credential are mounted into the runner Pod.
7. Wait for API rollout/readyz. Log in as the operator; the existing UI prepares once
   using POST /api/research/codex/runner. Kubernetes Pod metadata and immutable volume
   bindings are verified before selecting its IP. Until credentials are supplied,
   status is `not_configured`, not an available runner.

Provider egress is only chatgpt.com TCP443, with DNS queries for chatgpt.com and the
internal callback name. The callback uses the existing authenticated internal API
route; public ingress excludes that route. API↔runner is selector-scoped TCP8080.
Only the API gets kube-apiserver6443. Do not widen provider domains to fix an error
without evidence/review. The pinned native bundle uses access-only ChatGPT login;
no refresh/id token is supplied to it. Original source refresh ownership stays with
the operator's existing client.

## Credential supply and one response

Use `backend/ops/user_runner_supply.sh` on the operator host. The script runs the
exporter in Docker and pipes directly to the existing runner-local same-UID supply
socket via verified kubectl exec (no TTY, no file containing exported access token,
no stdout capture). Export the script's required variables: reviewed tool image,
operator kubeconfig, target namespace/Pod/observed UID, explicit source directory
and basename, supply-input directory and basename. Copy only the generated
`supply-inputs.json` metadata into an operator-owned mode0700 directory as mode0600;
its UID must match the selected source file and the script's invoking UID. Never
change permissions or contents of the user's original auth source. The script
exports only accessToken/accountId/expiry bound to owner/state/snapshot; expiry and
account mismatch fail closed. A new supply action uses a fresh snapshotId; no source
renewal happens automatically. Shell xtrace is disabled before credential transport.

After the safe `{accepted:true}` receipt, verify status then send exactly one simple
web message and capture the response (never credentials). Tool callbacks additionally
need a scoped tool call in the live acceptance run if claimed as verified. Verify a
second nonselected login still returns codexRunner:false and unchanged trial status,
including trial remaining/expiry fields. Do not infer this from a synthetic unit test.

## Stop and rollback

Logout calls UID-preconditioned Pod deletion after a durable dispatch fence and
before invalidating the login session. On deletion error, logout returns 503 and
keeps the session for retry; model dispatch stays fenced. The explicit authenticated
DELETE /api/research/codex/runner uses Origin/CSRF and returns
`{terminationRequested:true}` after API acceptance. A terminating Pod or 404 is not a
proof of volume release. PM observes the exact UID's disappearance with kubectl.
The ordinary Kubernetes grace period is retained; there is no force deletion.

This task intentionally retains registry UID/reservation and storage after stop as
`runner_failed`, so a subsequent preparation cannot recreate or release an uncertain
instance. Re-entry/capacity release/drain/idle hardening belongs to #11. Logout does
not delete other sessions, Secret, PVC, PV, native history or provider source.

Rollback BEFORE removing runtime access: stop the target Pod through the new API
(or PM UID-preconditioned reviewed deletion if unavailable), observe disappearance,
then set API env `CODEX_USER_RUNNERS_ENABLE=false` and
`CODEX_USER_RUNNER_MAX_PODS=0` and restore the saved Deployment/image/replicas. Restore
both saved CNPs and quota only after target shutdown, preserving trial access. Remove
new Role/RoleBinding/ClusterRole/ClusterRoleBinding and Secret only under the reviewed
cleanup authorization. Setting the flag false alone disables new routing, but does
not delete an already running Pod. Keep artifacts/volumes/registry for reconciliation.

## Existing #10 resources

Re-use only namespace, default-deny KNP and tokenless workload SA. Update
runner-callback-draft and quota in place. No synthetic Pod, PVC/PV, Secret, host
state, permit or rehearsal DB is adopted. The synthetic Pod and rehearsal DB,
binding-less VAPs and one-shot Roles, unused runner-controller-api-draft and
runner-resolver-draft are PM's user-approved cleanup candidates. Synthetic PV/PVC,
transport-synthetic and host state are separately approved retained-data deletion
candidates; fresh owner storage does not overwrite them. Do not delete these merely
because the tool produced a candidate.

Live acceptance still requires runner→other runner and runner→API6443 denial evidence
(Cilium verdict or equivalent), successful provider response, logout deletion,
nonselected trial check, final flag/max_pods record and the approved #10 cleanup
record. Docker tests alone satisfy none of those live claims.
