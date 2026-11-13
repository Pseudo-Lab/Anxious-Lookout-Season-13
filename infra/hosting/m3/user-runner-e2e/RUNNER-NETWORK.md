# #62 runner network narrowing: switch procedure

Issue [#62](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/62). The decisions are in the issue thread (2026-11-14):

- **D1 (a):** runner internet egress is `world` 443 only. The domain restriction waits for #70.
- **D2:** no shell-specific network rule.
- **`hosting-web-ingress`:** included.
- **Requirement 6:** 6a only (user decision, 2026-11-14), as step E below. 6b, the Pod envelope VAP, moved to #86.

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

## Step E: runner subresource VAP (requirement 6a)

`50-runner-subresources-vap.json` holds ValidatingAdmissionPolicy `runner-subresources` and its binding (`Deny`). It covers Pods in codex-runner only, matched by `kubernetes.io/metadata.name`:
- **Refused:** `pods/exec`, `pods/attach`, `pods/portforward`, `pods/proxy` (CONNECT) and `pods/ephemeralcontainers` (UPDATE), unless the requester is in `system:masters`.
  - `pods/proxy` was added by PM decision on 2026-11-14. It is the API server's HTTP proxy to a Pod port, the same access class as port-forward, and nothing in the repository uses it.
- **Effect today:** none for any service account. On 2026-11-14, `kubectl auth can-i … --subresource` showed that no non-admin service account has these rights. The policy guards against later RBAC drift.
- **Operators keep their access:** `system:masters` (`k3s kubectl` on the node) still passes, so review and operator scripts that `kubectl exec` into runner Pods keep working.
- **Untouched:** plain Pod create, delete and get (runner-controller, API, broker) are not matched.

Apply:
1. `kubectl apply --dry-run=server -f 50-runner-subresources-vap.json`. It must succeed with no error.
2. Apply the policy item, then the binding item.

The API server needs no restart.

**VM proof.** Each check needs the exact output and rc.

Test identity:
- A temporary ServiceAccount `vap-probe` in a scratch namespace. On the development VM, reuse the empty `issue62-probe` namespace left from steps B–C (kept because of the metrics APIService fault).
- A Role in codex-runner granting it:
  - `get` on `pods`;
  - `create` on `pods/exec`, `pods/attach`, `pods/portforward`;
  - `get` and `create` on `pods/proxy`;
  - `patch` on `pods/ephemeralcontainers`.
- Use it with `--as=system:serviceaccount:<scratch>:vap-probe` from the admin kubeconfig. Impersonation does not add `system:masters`.
- Target: one runner Pod. The exec runs `true` only and reads no user data.

| # | When | Request | Expected |
| --- | --- | --- | --- |
| E0 | before the binding | `vap-probe`: `kubectl exec <runner> -c runner -- true` | rc 0. Proves RBAC allows it, so a later refusal is the policy's |
| E1 | after | `vap-probe`: same exec | refused, message starts `runner-subresources:`. **Proves CONNECT matching on k3s v1.36** |
| E2 | after | `vap-probe`: `kubectl attach <runner> -c runner` (short timeout) and `kubectl port-forward pod/<runner> :8080` | refused, same message |
| E2p | before and after | `vap-probe`: `kubectl get --raw /api/v1/namespaces/codex-runner/pods/<runner>:8080/proxy/healthz` | **before:** passes admission, so any answer is the proxy's own, typically an error. The runner CNP admits only the API and the controller on 8080, so the API server's connection is expected to fail; record what it is. **After:** refused with the `runner-subresources:` message. Admission refusal is not a network result: this row does not claim anything about that path's network policy |
| E3 | after | `vap-probe`: `kubectl debug <runner> --image=<runner image> --target=runner -- true` (ephemeral container) | refused, same message. No ephemeral container added (check `spec.ephemeralContainers` stays absent) |
| E4 | after | admin (`system:masters`): `kubectl exec <runner> -c runner -- true` | rc 0 |
| E5 | after | plain operations: the runner-controller creates and deletes a runner Pod (stop and re-entry); the API reads Pods | unchanged; no `runner-subresources` refusal in either log |
| E6 | after | a Pod in another namespace: admin and `vap-probe` (with a Role there) exec | not matched by the policy |

Afterwards, delete the temporary Role, RoleBinding and ServiceAccount. Delete the scratch namespace only while the metrics APIService is Available (PARITY metrics row); otherwise leave it empty, as `issue62-probe` is now.

Rollback: delete the binding `runner-subresources`, then the policy. With the binding gone, the policy has no effect.

## Production

Production does the same steps and checks, after two things:
- this procedure's VM results are approved;
- the production read-only looks are on #62. Round 1 (2026-11-14) is there; round 2 covers the routes, ConfigMaps, codex-trial workloads and the other apps.

**Production's public routing is not `render.sh`'s.** It comes from the #7 root HTTPS package (`infra/hosting/m3/https_prepare.py`, `ROOT-HTTPS-P1.md`):
- IngressRoute `hosting-root-https-v1` on `websecure` with TLS. Its API rule already excludes `(Path(/api/internal) || PathPrefix(/api/internal/))`, and the Middleware `hosting-root-encoding-v1` rejects encoded `/`, `\`, NUL, `;`, `%`, `?` and `#` (400).
- `hosting` on `web`, which P1 replaced with a GET/HEAD-only redirect to HTTPS. Round 2 confirms the live spec.
- The API reads the immutable ConfigMap `hosting-root-https-v1`, not `hosting-config`.

So on production:

- **Never apply the `render.sh` output as a whole.** It would replace the P1 routes and ConfigMap reference. Apply only the #62 objects:
  - Service `api-internal`;
  - the three m2-hosting CNPs;
  - the api Deployment image (it must also add `containerPort` 8081 `internal`);
  - the runner-controller image;
  - the runner CNP;
  - the step E VAP and binding (round 1 look: production has no VAP).
  The step boundaries are the same. Each object comes from the render output, compared with the live object before apply.
- **Step A's IngressRoute change does not apply.** The live HTTPS route already closes `/api/internal`. The check is that the public `/api/internal` forms (including the encoded ones in `route_probe.py`) answer 404 or 400 through `https://` and `http://`.
- **Switch flag.** `hosting-root-https-v1` is immutable, so when a transition is needed, step A sets `API_INTERNAL_CALLBACK_ON_PUBLIC=true` as an `env` entry on the api Deployment (`env` overrides `envFrom`), and step D removes it.
- **No transition if no runner Pod.** Production had **no runner Pod** on 2026-11-14. If that is still true at the switch, and the new runner-controller is running before any Pod is created, no Pod has the 8080 env. Then:
  - the flag stays `false`;
  - `hosting-api-ingress` is applied without the transition rule;
  - the runner CNP goes straight to `40-runner-network.json`.
  Steps A–D collapse into A, B, C with the final forms. Re-check the Pod count (with its rc) right before choosing.
- **codex-trial (#7) and the other namespaces.**
  - Repository evidence: no codex-trial workload uses m2-hosting's database (`infra/codex-trial/check_inputs.py` refuses an m2-hosting DSN). The #7 access-web runner's callback to the m2-hosting API ran through `hosting-access-web-link-v1`, deleted in #13 C.
  - The #62 policies therefore admit no codex-trial source, and any codex-trial Pod is refused at the API and PostgreSQL.
  - Round 2 confirms whether any codex-trial or other-app workload is running and mentions `m2-hosting`. Any that does needs a decision before the switch, not an allowlist entry by default.
- Production takes no release of this source outside the switch (PARITY "Render output and the live node").
