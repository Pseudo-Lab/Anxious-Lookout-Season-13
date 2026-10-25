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
| CiliumNetworkPolicy `codex-broker` | ingress: `codex-runner` user-runner Pods → 8080 only. egress: kube-dns 53 (DNS proxy), kube-apiserver 6443, `toFQDNs` `auth.openai.com` and `chatgpt.com` on 443 |

The runner side adds an explicit egress rule to `codex-broker:8080` in
`user-runner-e2e/40-runner-network.json`. The runner's broader egress narrowing is #62.

`values.json` (see `values.example.json`):

| Key | Value |
| --- | --- |
| `image` | the reviewed runner image digest; the broker module is in every runtime image |
| `accountId` | the ChatGPT workspace id the runners already expect (`providerAccountId`); an identifier, not a credential |
| `storageClass` | the CSI class from #63 |
| `accessMode` | `ReadWriteOncePod` on CSI. `ReadWriteOnce` is allowed only on a non-CSI class until #63 |

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
- **Network check (development VM):**
  - With the policy applied, Hubble must show only DNS, the API server, `auth.openai.com:443` and `chatgpt.com:443` as forwarded from the broker.
  - A connection to any other address must show as dropped.
  - Refusals from other Pods must show as policy drops at the broker's 8080.

## Applying to production

This document does not authorize execution; production apply is a separate approval. Order:

1. Render and review.
2. Apply the objects and the runner CNP.
3. Log in.
4. Confirm `/healthz`.
5. Release runner images carrying the broker client. Existing bindings move with `ops.runner_binding_image` (rollout README).
6. Canary: one runner reports `available`.
7. Run the host supply retirement steps (rollout README).
