# #62 runner network narrowing: switch procedure

Issue [#62](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/62). The decisions are in the issue thread (2026-11-14):

- **D1 (a):** runner internet egress is `world` 443 only. The domain restriction waits for #70.
- **D2:** no shell-specific network rule.
- **`hosting-web-ingress`:** included.
- **Requirement 6 (VAP):** waiting for a user decision and **not part of this procedure**.

The development VM goes first; review runs it. Production follows the same order only with separate user approval, after the production read-only inventory is recorded on #62.

## What changes

| Object | Before | After |
| --- | --- | --- |
| API process | `uvicorn app.main:create_app`, port 8080 | `python -m app.serve`, public 8080 + internal 8081. 8081 serves only `POST /api/internal/research/tools`. 8080 answers 404 under the decoded `/api/internal` prefix. With `API_INTERNAL_CALLBACK_ON_PUBLIC=true`, the exact callback path is served only to requests with no proxy header |
| Service `m2-hosting/api-internal` | absent | 8081 → `hosting-api` |
| IngressRoute `hosting` | routes all of `/api/` | excludes `PathPrefix(/api/internal)`, which also covers the encoded forms Traefik matches undecoded |
| CNPs `hosting-{api,web,postgres}-ingress` | absent (default allow) | ingress only, see `infra/hosting/README.md` |
| CNP `codex-runner/runner-callback-draft` | egress broker 8080 + `toEntities: all` | `40-runner-network.json`: broker 8080, kube-dns 53, `hosting-api` 8081, `world` 443 |
| runner Pod env `RESEARCH_TOOL_CALLBACK_URL` | `http://api.m2-hosting.svc.cluster.local:8080/…` | `http://api-internal.m2-hosting.svc.cluster.local:8081/…`, rendered by the controller (`runner_kube.CALLBACK_URL`). Stored binding plans are not changed |

Ingress, ingressDeny and egressDeny of the runner CNP are unchanged. The broker CNP is unchanged.

**Existing runner Pods are not drained.**
- The new controller still recognises a Pod whose spec differs from the current render only by the old callback env value.
- That Pod keeps calling back on 8080 until it ends (idle stop, logout or failure).
- The transition rule and the API flag keep that path open until step D.

## Evidence method (Hubble substitute)

Cilium has Hubble disabled on both nodes (PARITY "Hubble"). For drop evidence, this procedure uses the agent's own monitor:

```sh
k3s kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium-dbg monitor --type drop --type policy-verdict
```

- Run it for the length of each negative-probe window.
- Keep the raw output with its time window and the probe's source endpoint ID (`cilium-dbg endpoint list`).
- A negative result passes only with both of these:
  - the probe's refusal or timeout;
  - a drop record (`Policy denied`) for that source, destination and port in the same window.
  A timeout alone does not pass.

**Why the monitor and not Hubble:**
- The monitor reads the same datapath events Hubble would export.
- It needs no Cilium value change and no agent restart. Enabling Hubble means a `cilium upgrade` and an agent restart, which this switch should not add.
- If PM or the user want Hubble output literally, enable it only in a recorded window and disable it after (PARITY "Hubble" row).

Also record `cilium-dbg endpoint list`: policy enforcement ingress/egress for the API, web, postgres and runner endpoints, before and after each step.

## Preconditions

- New runtime image (API and runner-controller) built from this branch. The runner image is unchanged.
- Live specs saved before each step:
  - the runner CNP;
  - the api Deployment and ConfigMap;
  - the runner-controller Deployment.
  These are the rollback copies.
- No conversation `running` at the step boundaries A and D (a read-only check, the same way earlier runner stages checked it).

## Step A: listeners, Service, IngressRoute, controller

1. Render with `M2_RUNNER_CALLBACK_TRANSITION=true`.
2. Apply only these objects:
   - ConfigMap `hosting-config` (now has `API_INTERNAL_CALLBACK_ON_PUBLIC: "true"`);
   - Deployment `api`;
   - Service `api-internal`;
   - IngressRoute `hosting`.
   Not the CNPs.
3. Wait for the API rollout.
4. Set the runner-controller Deployment image to the same runtime image and wait for its rollout.

Checks:
- An existing runner (if any) still completes a tool callback.
- A runner Pod created after step A has the 8081 env and completes a tool callback.
- Through Traefik (public origin, `curl --resolve` as in DEV-VM.md), Traefik answers 404 with no API hit for:
  - `/api/internal/research/tools`;
  - the encoded forms in `backend/tests/route_probe.py` (`%2F`, `%5C`, `%3B`, `%00`, `%252F` after `internal`).
  This holds while the flag is `true`.
- From inside the API Pod over loopback, with the flag `true`:
  - a POST to `http://127.0.0.1:8080/api/internal/research/tools` carrying `X-Forwarded-For` answers 404;
  - the same POST without proxy headers reaches the route: the route's own 401 or 422 (no token, or an invalid body) is the expected answer, not the gate's 404.
- `/api/research/...` through Traefik still works.

Rollback:
1. Apply the saved api Deployment, ConfigMap and IngressRoute.
2. Delete Service `api-internal`.
3. Apply the saved runner-controller Deployment.

## Step B: m2-hosting ingress CNPs

Apply `hosting-postgres-ingress`, then `hosting-api-ingress` (transition render, so it includes runner → 8080), then `hosting-web-ingress`. Wait for `Valid=True` on each, and for endpoint policy realization.

Checks:
- Positive:
  - public web and API through Traefik;
  - API `/readyz`;
  - runner-controller reconciliation;
  - the host `issue16-controller` DB connection, if its timer runs on the VM;
  - a migration-style `app=hosting-ops` Job connects (or the next scheduled one; do not run a migration just for this).
- **Existing DB connections:** take `pg_stat_activity` `backend_start` and `client_addr` per connection before and after the apply. Read it with `kubectl exec postgres-0 -- psql`, no DSN files. Record whether the API's pooled connections survive. If they are cut, record that the pool reconnects (`/readyz`, one authenticated request).
- Negative, from a probe Pod in a scratch namespace (not codex-runner or m2-hosting):
  - API 8080 and 8081;
  - web 8080;
  - postgres 5432.
  Each needs a refusal or timeout plus a monitor drop record. Delete the probe namespace afterwards, but only while the metrics APIService is Available (PARITY metrics row).

Rollback: delete the three new CNPs. They are new objects, so deletion is the exact inverse and returns m2-hosting to default allow.

## Step C: runner egress allowlist (transition form)

1. Save the live `runner-callback-draft`.
2. Apply `40-runner-network-transition.json`.

Checks:
- Positive:
  - one runner turn with a tool callback (8081);
  - broker token fetch (8080);
  - provider `chatgpt.com` 443.
- Negative, from inside a runner Pod (`kubectl exec … -c runner -- python -I -c` with a short connect timeout). Each needs a drop record:
  - kube-apiserver 6443 (already denied);
  - node IP `10.0.0.107` 22 and 10250;
  - postgres 5432;
  - web 8080;
  - runner-control controller 8080;
  - a world address on port 80;
  - another runner Pod.
- **D2 behaviour line:** a command run by the native shell sandbox cannot connect to `chatgpt.com:443`. Prefer a real turn's shell command. If that is not possible, `codex sandbox` with `--include-managed-config` (native-shell README section 5) is acceptable; record which was used. This is the first behavioural proof. #68 V3c observed only the separate network namespace.

Rollback: apply the saved live spec.

## Step D: close 8080 for runners

**Precondition:** no runner Pod has the old env. Check every Pod in codex-runner and count values of `RESEARCH_TOOL_CALLBACK_URL`. Record the lookup rc separately from the count.

1. Apply `40-runner-network.json`.
2. Render with `M2_RUNNER_CALLBACK_TRANSITION=false`.
3. Apply `hosting-config` and `hosting-api-ingress`.
4. Restart the api Deployment so the flag change takes effect (`envFrom` is read at start).

Checks:
- From a runner Pod, `hosting-api` 8080: drop record.
- 8081 callback in a runner turn: success.
- The public listener itself answers 404 under `/api/internal`, now with or without proxy headers. Check it with a POST to `http://127.0.0.1:8080/api/internal/research/tools` from inside the API Pod (`kubectl exec … -c api -- python -I -c`; loopback, so no policy is involved). This holds even if a public route were added by mistake later. Traefik already answers 404.

Rollback:
1. Re-apply the transition runner CNP.
2. Re-render with `true`.
3. Apply `hosting-config` and `hosting-api-ingress`.
4. Restart the API.

## Production

- Same steps and checks, after:
  - the user's read-only inventory (#62 step 0, items 1–6) is on the issue;
  - this procedure's VM results are approved.
- If production has other in-cluster clients of the API or PostgreSQL (for example `codex-trial`), the m2-hosting allowlists change first, in source.
- Production takes no release of this source outside the switch (PARITY "Render output and the live node").
