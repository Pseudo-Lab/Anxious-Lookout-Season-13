# codex-broker: operator credential broker (#60)

The broker holds the operator's ChatGPT login for the user runners and hands each runner
an access token only. It replaces host supply (`issue16-supply`, removed in #60). Design
and measurements are in #58 C.

- **Login:** once, with `codex login --device-auth` inside the broker Pod.
- **Refresh:**
  - The broker runs the pinned Codex app-server in managed mode with the `file` store. The app-server refreshes on its own about five minutes before expiry.
  - When a token with less than 30 minutes left is requested, the broker forces `account/read {refreshToken: true}` first.
- **Delivery (pull):**
  - Each runner Pod has a projected ServiceAccount token (audience `codex-broker`, 600 s) at `/run/codex-broker/token`. `automountServiceAccountToken` stays false.
  - The runner calls `POST /v1/access` at start and about 15 minutes before its access expires.
  - It calls again when the app-server reports a 401 (`account/chatgptAuthTokens/refresh`). A forced refresh runs at most once a minute.
- **Identity check:**
  - TokenReview must accept the token for audience `codex-broker` as `system:serviceaccount:codex-runner:runner-workload`, with the bound Pod name and UID.
  - A fresh Pod GET must match that UID. The Pod must be Running, not terminating, labelled `anxious.io/managed=user-runner`, and named `runner-<owner hex>`.
  - The runner's requested owner and state must equal the Pod's labels.
  - Only then does the broker issue envelope v2. The runner's `ServiceAccessAuth.install` still checks owner, state, account and expiry against its ledger.

## Objects (`python -m ops.broker_render --values values.json`)

| Object | Notes |
| --- | --- |
| Namespace `codex-broker` | PSA `restricted` |
| ServiceAccount `codex-broker` | default token automount (TokenReview and Pod GET) |
| ClusterRole `codex-broker-tokenreview` | `tokenreviews` create only |
| Role `codex-broker-runner-pods` (codex-runner) | `pods` get only; the API gets no new right |
| Service `codex-broker:8080` | |
| StatefulSet `codex-broker` | replicas 1, runner image with `uvicorn broker.app:create_app`, UID/GID 10001, read-only root, all capabilities dropped, no requests/limits, PVC `data` 1Gi with `CODEX_HOME=/data/codex` (0700) |
| CiliumNetworkPolicy `codex-broker` | ingress: `codex-runner` user-runner Pods and the host (kubelet probes; the cluster sets `allow-localhost: policy`) → 8080. egress: kube-dns 53 (plain L4, no DNS proxy), kube-apiserver 6443, `world` on 443 (default). The `fqdn` switch renders `toFQDNs` `auth.openai.com` and `chatgpt.com` on 443 instead, with all broker DNS through the proxy |

The runner side adds an explicit egress rule to `codex-broker:8080` in
`user-runner-e2e/40-runner-network.json`. The runner's broader egress narrowing is #62.

**Egress: `world` by default (user decision on #60, 2026-10-29).**
- **What it renders:** `toEntities: world` on TCP 443. The broker reaches **any internet host on 443**, not only
  `auth.openai.com` and `chatgpt.com`.
- **Weaker isolation, accepted by the user.** The policy no longer restricts the destination domain. The other
  limits stay: TCP 443 only, kube-dns 53, kube-apiserver 6443, and ingress from runner Pods and the host only.
  The broker runs no shell. The runner policies are not affected by this decision.
- **DNS stays off the Cilium DNS proxy:** the kube-dns rule has no `rules.dns`, so no proxy redirect is installed
  for the broker endpoint.

**Why not `fqdn`:**
- `"egress": "fqdn"` renders `toFQDNs` for the two names. It needs the DNS proxy (`l7Proxy: true`, set in
  `infra/k3s/cilium-values.yaml`), and its kube-dns rule carries `rules.dns`, so **every** broker lookup goes
  through the proxy, `kubernetes.default.svc` included.
- On the development VM that proxy path fails: all lookups time out, and so do TokenReview and Pod GET. With the
  policy deleted, the same lookups work.
- Candidate 1 (`dnsProxy.enableTransparentMode=false`) changed nothing, applied or reverted (see
  `infra/k3s/README.md` "Cilium DNS proxy (#60)").
- The domain restriction and the remaining candidate (`bpf.tproxy`) are
  [#70](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/70). Production has the same Cilium
  values as the development VM (`infra/k3s/PARITY.md`), so **do not use `fqdn` in production before #70 shows it
  working.**

`values.json` (see `values.example.json`):

| Key | Value |
| --- | --- |
| `image` | the reviewed runner image digest; the broker module is in every runtime image |
| `accountId` | the ChatGPT workspace id the runners already expect (`providerAccountId`); an identifier, not a credential |
| `storageClass` | the CSI class from #63 |
| `accessMode` | `ReadWriteOncePod` on CSI. `ReadWriteOnce` is allowed only on a non-CSI class until #63 |
| `egress` | `world` (default, #60 user decision; any host on 443) or `fqdn` (explicit switch, the two names only; not working until #70) |

## Operating requirements

- **Workspace prerequisites:** for a ChatGPT workspace account, a workspace admin enables device code login and Codex local use in the workspace permissions first. Without them the login fails with a message to contact the admin.
- **First login** (operator, interactive):
  `kubectl -n codex-broker exec -it codex-broker-0 -- codex login --device-auth`.
  Open the printed URL on any device, sign in, and enter the one-time code. The code is
  not a credential once used. The broker restarts its app-server when `auth.json` changes.
- **Single writer:** refresh tokens rotate on every use (`refresh_token_reused` otherwise).
  - Never copy `auth.json` in or out.
  - Never log the same session in elsewhere.
  - Never scale above one replica.
  - A device-auth login on the operator's laptop is a separate session and is fine.
- **No backup:** the broker PVC is excluded from backups (`infra/k3s/backup.sh` covers the control plane only). Losing it means logging in again; a restored copy would break rotation.
- **Volume:** a 0700 subdirectory owned by UID 10001. The Pod `fsGroup` applies to the empty volume root only (`OnRootMismatch`), so `auth.json` stays 0600 and the broker refuses any other mode. Root on the node can still read it, the same exposure as the former host file.
- **Logout and revocation:**
  1. `kubectl -n codex-broker exec codex-broker-0 -- codex logout` revokes the refresh token at OpenAI (`POST /oauth/revoke`, measured in #58 C) and deletes `auth.json`.
  2. Delete the PVC (`kubectl -n codex-broker delete pvc data-codex-broker-0` after scaling the StatefulSet to 0). With a Retain class, also delete the PV and its backing volume.
  3. If the broker is gone or damaged, sign that session out in the ChatGPT security settings (or sign out all sessions).
  4. Runners keep their current access token until it expires. Stop them (`DELETE /api/research/codex/runner`) when access must end immediately.
- **Mock OAuth (development only):** the broker passes `CODEX_REFRESH_TOKEN_URL_OVERRIDE` to its app-server
  only when it is `http://` on a loopback address (a mock sidecar in the broker Pod); any other value is
  ignored. The production manifest never sets it.
- **Network check (development VM, default `world`):** under the same policy, show the allowed destinations
  succeeding and the others dropped by policy:
  - A runner Pod reaches the broker's 8080; another namespace's Pod is dropped.
  - The broker resolves `kubernetes.default.svc` and the provider names, and reaches the API (TokenReview,
    Pod GET), `auth.openai.com:443` and `chatgpt.com:443`.
  - An internet host on a port other than 443 is dropped.
  - The broker endpoint has no `cilium-dns-egress` redirect (`cilium-dbg status --verbose` on the agent).

  Use Hubble or `cilium-dbg monitor --type drop` on the agent: a failed connection alone is not proof of a
  policy drop. An internet host on 443 that is not a provider is **allowed** under `world`. That is the accepted
  difference, not a failure.
- **Runner 401 refresh harness:** `infra/hosting/m3/check_runner_companion.sh <runner-test image ID> <pinned
  models.json> broker` runs the actual runner in `access-service` mode (ServiceAccessAuth, envelope v2) with the
  pinned app-server, `--network none`, against a loopback broker. The production Service name resolves to
  127.0.0.1 inside the probe only, so `BrokerClient` keeps its `.svc` check and its own HTTP client. One upstream
  401 must make the app-server ask for a refresh. The runner then gets exactly one `refresh=true` from the
  broker and retries the failed request once with the new token. The turn completes with nothing blocked. A broker
  403, the same token, a timeout and a removed refresher (`unwired`, the sensitivity case) each end in
  `codex_auth_revoked` with no replay. Output is booleans and counts only. The broker's own upstream refresh is
  outside this harness and is checked against the live broker.

## Applying to production

This document does not authorize execution; production apply is a separate approval. Order:

1. Render and review.
2. Apply the objects and the runner CNP.
3. Log in.
4. Confirm `/healthz`.
5. Release runner images carrying the broker client. Existing bindings move with `ops.runner_binding_image` (rollout README).
6. Canary: one runner reports `available`.
7. Run the host supply retirement steps (rollout README).
