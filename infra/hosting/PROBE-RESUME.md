# Probe timeout correction and PM resume plan

Issue #4. PM reported the 2026-10-07 deployment reached owned PostgreSQL/PVC and completed Alembic 0001_auth/v1, but healthy API startup execs exceeded kubelet's default 1s timeout at 250m/256Mi. API was scaled to zero; web/IngressRoute remain uncreated. These are PM reports, not live checks executed by back. PM separately rotated initial exposed API credentials/key; this fix neither reads nor restores those values.

## Reviewed change and image reuse

The renderer sources probes.env and sets **10s exec timeout on all three API probes**, **5s on both web probes**. API HTTP client remains bounded at 2s; web fetch now uses AbortSignal.timeout(2000). API startup runs every 10s/failureThreshold 20 (200s allowance); web startup every 5s/failureThreshold 30 (150s), below existing 300s rollout deadline. API readiness runs every 10s and liveness every 20s, both failureThreshold 3; web readiness stays every 5s/threshold 3. Lower API frequency reduces interpreter fork CPU cost. A candidate 5s API exec limit passed sequential CPU competition but failed overlapping health/readiness, so it was replaced before handoff. DB readiness fails independently of process health. Exec stays on Pod loopback, preserving existing Host/proxy and network-policy boundaries.

This trades longer nominal API failure recognition for CPU headroom: readiness about 30s and liveness about 60s across three consecutive failed checks, plus the last exec duration/scheduling; restart also includes the existing termination grace. These are configuration budgets, not measured kubelet guarantees. Once called, HTTP failures still use the 2s request bound. API handlers return DB-error 503 independently of waiting for the readiness threshold. Web readiness remains nominal 15s across three failures plus exec/scheduling. PM must observe actual node timings after resume.

No API/web application, Dockerfile, dependency, image, DB schema, credential, resource budget or policy changes. **No image rebuild/import/migration/Secret recreation is required.** Reuse the PM-verified 684d627 images and matching metadata: API local digest 843880452e3b53c62c46fc932356263461b43aeed23b0b3f46896f36b1767e6f, web 199a065195bfff1f8f8b67668dcd57c0ad7c4a53073bb1729380f38674ee4cfa; verify exact already-imported containerd references separately. The new source SHA covers configuration and test/plan tools, while runtime source remains 684d627/2026-10-06T16:49:41Z.

## Isolated reproduction

```sh
sudo env \
  API_RUNTIME_IMAGE=anxious-hosting-api@sha256:843880452e3b53c62c46fc932356263461b43aeed23b0b3f46896f36b1767e6f \
  WEB_RUNTIME_IMAGE=anxious-hosting-web@sha256:199a065195bfff1f8f8b67668dcd57c0ad7c4a53073bb1729380f38674ee4cfa \
  bash infra/hosting/verify-probes.sh
```

The script creates a fresh probes-only Compose project/volume on an internal network, uses disposable test credentials, and executes actual unchanged runtime probes under **API 250m/256Mi, web 500m/512Mi, UID10001/read-only/tmpfs/cap-drop/no-new-privileges**. It times the full Docker exec including interpreter launch under the same configured deadline, checks baseline/CPU-competing/overlapping health+ready success, DB stop/recovery, and connection-refused/non-200/10s-delay failures in network-none fixtures. Delay cases must actually wait at least 1.5s and return failure before the 10s API/5s web outer deadline. Cleanup removes only its containers/network and retains its own disposable volume. It never connects to PM/live/earlier-role fixture DBs or secrets. Docker timings and failure simulations do not prove kubelet behavior on the live node; PM records that separately after review.

## PM-only staged reapplication

After source/plan review, synchronize the approved source and check current live API replica count remains zero and web/route absence, refreshed baseline and owned resource state. Use a new release subdirectory; retain the previous artifacts unchanged. Pass the **actual already-verified imported immutable refs**, not placeholder/local-store assumptions:

```sh
# PM fills these exact already-imported digest-qualified refs before executing.
API_IMAGE='VERIFIED_API_REFERENCE@sha256:VERIFIED_DIGEST' \
  WEB_IMAGE='VERIFIED_WEB_REFERENCE@sha256:VERIFIED_DIGEST' \
  M2_PUBLIC_ORIGIN=http://140.245.79.96 \
  bash infra/hosting/render.sh > /PRIVATE/RELEASE/probe-fix.yaml
sudo /usr/local/bin/k3s kubectl create --dry-run=client \
  -f /PRIVATE/RELEASE/probe-fix.yaml -o json > /PRIVATE/RELEASE/probe-fix-decoded.json
jq -s '
  [.[] | if .kind=="List" then .items[] else . end
   | select(.kind=="Deployment" and .metadata.name=="api" and .metadata.namespace=="m2-hosting")]
  | if length!=1 then error("Expected one owned API Deployment")
    else .[0] | .spec.replicas=0 end
' /PRIVATE/RELEASE/probe-fix-decoded.json > /PRIVATE/RELEASE/probe-fix-api-zero.json
jq -s '
  [.[] | if .kind=="List" then .items[] else . end
   | select(.kind=="Deployment" and .metadata.name=="web" and .metadata.namespace=="m2-hosting")]
  | if length!=1 then error("Expected one owned web Deployment") else .[0] end
' /PRIVATE/RELEASE/probe-fix-decoded.json > /PRIVATE/RELEASE/probe-fix-web.json
```

Only extract/apply these Deployment objects; the full stream contains already-existing DB/storage/policy/config and is not this reapplication target. Confirm API image, env/secret references, service account/security/volumes/resources remain current; only probe fields change and API is held at zero. Any extra live drift requires review rather than silently overwriting PM's injected configuration. The Node command change is a Deployment field, not an image change.

```sh
sudo /usr/local/bin/k3s kubectl apply --dry-run=server -f /PRIVATE/RELEASE/probe-fix-api-zero.json
sudo /usr/local/bin/k3s kubectl diff -f /PRIVATE/RELEASE/probe-fix-api-zero.json
# diff exit 1 is expected differences; >1 is an error, stop and inspect.
sudo /usr/local/bin/k3s kubectl apply -f /PRIVATE/RELEASE/probe-fix-api-zero.json
sudo /usr/local/bin/k3s kubectl -n m2-hosting scale deployment/api --replicas=1
sudo /usr/local/bin/k3s kubectl -n m2-hosting rollout status deployment/api --timeout=300s
```

Observe Ready and stable restart count for at least 60s after startup, with actual startup/readiness/liveness event outcomes and CPU/memory/throttling/Pod counts. Verify DB-role readiness and API version using secret-safe commands. Start no overlapping rollout. If API still times out/fails, scale only API back to zero, preserve logs/events without secrets and report the actual timings/resources; keep web/route unpublished. Restoring the known-bad 1s probe configuration is not a recovery action.

Only after stable API, server dry-run/diff and apply the web Deployment, wait for its rollout and exec-readiness stability, and record actual web resources/version. Continue the previously reviewed owned Service/Ingress exposure stages after prerequisites; compare /android-agent and existing service/M1 baseline. No PG/PVC/Namespace/policy/credential changes, no repeated migration Job or password rollback. Live kubelet/route/guard results and actual provider acceptance remain PM execution plus independent review; this document is a proposed resume procedure.
