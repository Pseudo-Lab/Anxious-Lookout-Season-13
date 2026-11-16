# #99 retired-literal holdback rollout

Issue #99 and its accepted design (issuecomment-6911515502) own scope. This procedure replaces #95 B4 r3 and
addendum r3-a **only for retired-literal holdback**. Review approval and the user's separate deployment approval
are required before operating the cluster. This document does not authorize deployment.

## Behavior and inputs

Keep the existing renderer input:

```json
{"stream": {"enable": true, "text": true, "trace": false, "retired": true}}
```

The existing `stream.retired` → `CODEX_RUNNER_STREAM_RETIRED` → runner `STREAM_RETIRED` switch is reused.
Only service auth applies exclusion. Personal-cache and access-trial keep their previous holdback, including
refresh/id tokens. Broker envelope v2 still supplies access tokens, account and expiry metadata only.

With the switch on, the runner takes the install mutex and validates the durable ledger against its in-memory
snapshot. SHA256(accessToken), account, journal owner/trial/key and the install-validated claims must agree.
The protected set combines secrets/latest with an immutable set of literals validated during that install
(access token, account and email). It is rebuilt even if secrets/latest are empty or stale. A new Pod's successful
install publishes that set before Native starts, so its first turn can exclude retired legacy rows immediately.
No expiry, iat, anchor, N == R count proof or waiting period is required.

Unprotected long legacy rows leave **only** the holdback length calculation. Short literals and all covered-prefix
rules stay. The journal is not pruned; span detection, final records and complete command summaries still redact
retired literals. A retired literal split across emitted chunks may be visible in the live stream: this is the
accepted #95 decision, not a promise that the live stream equals final redaction for retired values.

Missing/stale snapshots retain the original full legacy holdback. Unreadable/invalid ledger, journal or context
stops stream text. Current literals without prefix fingerprints retain their full-length holdback.

## Images and operational sequence

1. Obtain review approval for the exact #99 commit, then prepare the reviewed API/controller and runner images
   using the project's existing image packaging/import procedure. Record immutable image references and the
   current Deployment/renderer input snapshots needed for rollback. The API/controller distribute the existing
   switch to newly created Pods; the runner image contains the behavior change.
2. **Keep the currently deployed broker image and configuration. Do not rebuild, replace or restart it for #99.**
   Its opt-in attest endpoint remains compatible but new runners cannot request attestation. The normal supply
   and 401 refresh paths still send the same requests/envelope checks.
3. Render the candidate with the input above and the reviewed images. Confirm `streamInput: true` and
   `retired: true`, and use the existing approved canary/drain/runner replacement process. No migration or DB
   changes are needed. Do not delete PVCs or the redaction history.
4. For the canary, test an ordinary new turn after installation. Also stop/recreate its runner using the approved
   idle/restart process and test the **first** turn after its new install. Read the summary below; confirm normal
   access, current protection, completion and visible incremental text before expanding to the other owners.
5. Do **not** run `python -B -m runner.stream_anchor`: that module has been removed. Do not run any old anchor
   script against old Pods. No sidecar initialization, 60-second owner spacing, 11-day wait or broker-first step
   applies. Existing `stream-retired.json`, its lock and SEEN/OFF journal rows remain on disk and are ignored by
   the new exclusion decision; do not clean them up. The old permanent-off state no longer blocks this decision.
6. Keep #95's **A12 broker refresh reader / A11 rollback** workflow running independently. The broker's real
   refresh path remains an operational check; this implementation does not verify it or replace its evidence.

Changes are limited to backend/infra. There is no frontend change, API/SSE contract change or new renderer key.

## Summary fields and acceptance

`runner_stream_summary` keeps values out of logs. New/changed fields:

| Field | Meaning |
| --- | --- |
| `retiredState` | Last planned flush: `off`, `unconditional`, `snapshot_missing`, `snapshot_mismatch`. `unconditional` means the current snapshot was verified, even if no legacy row needed exclusion. |
| `retiredExcludedRows` | Peak number of legacy long journal rows excluded in a planned flush of this turn. Already prefix-covered rows are not counted. This is **not** an expiry proof or the number of all historical credentials. |
| `retiredFallbackMissing` | Count of planned flushes retaining full legacy holdback because no snapshot was available. |
| `retiredFallbackMismatch` | Count of planned flushes retaining full legacy holdback because snapshot/account/install-validated claims did not agree with the ledger. |
| `legacyL`, `legacyCurrentL`, `legacyRetiredL` | Existing per-turn maxima **after** exclusion; a fallback early in the turn can keep a high maximum even after recovery. |

`retiredExcludedProven` and `retiredExcludedAssumed` are removed. The old `runner_stream_retired` sidecar/lifetime
log line is removed; no `pending/proven/assumed`, T0/anchor status or token lifetime evidence is produced by #99.
Counts describe plans, including discarded stale plans; use `staleFlushes` and the fallback counters with them.
Storage verification failures still use the existing stream text failure behavior and may prevent a policy from
being constructed; the fallback counters do not replace that failure path.

For a healthy current credential with fingerprint coverage and historical legacy rows, expect:

- `retiredState: unconditional`, `retiredExcludedRows > 0`, both fallback counters 0;
- `legacyCurrentL: 0`, `legacyRetiredL: 0`, `legacyL: 0`;
- ordinary text holdback near P = 16, `firstEventMs` near `firstDeltaMs`, with visible early incremental text.

A delta size, prefix match, command/output behavior and scheduling affect the peak; P is not a strict bound on
`holdbackPeakChars`. Do not turn a unit result into a production latency claim. If fallback occurs on the normal
install → first-turn path, stop expansion and report it. If `legacyCurrentL > 0`, current prefix coverage failed;
retain full protection and investigate rather than bypassing it.

After holdback is reduced, separately **measure** the existing websecure SSE path for buffering. Record the
first runner event versus browser arrival. Buffering changes, concurrent-conversation admission and other #97
work remain outside #99.

## Rollback and retained state

Set `stream.retired: false` through the existing renderer and approved runner replacement workflow. Old Pods keep
their old environment until replaced. Off restores the original a7c8ca0 holdback/redaction rules; it does not turn
off text streaming. The delay caused by historical literals is expected to return. Alternatively restore the
recorded previous API/controller/runner image references and Deployment snapshots. Keep the broker unchanged;
its A11 rollback is a separate decision if A12 calls for it.

No schema changes, journal deletions or sidecar rewrites occur. If reverting to #98 code after new installs,
its old proof history may be incomplete and remain pending/off; conservative full holdback is safe and expected.
Do not repair/reset that proof history automatically. Do not restore an older populated ledger as rollback.

## Validation handoff

Backend runs Docker unit tests only. Coverage includes first install/new Pod, missing/stale secrets/latest,
missing/mismatched snapshots, another writer, install commit/publication concurrency, stale-plan rejection,
corrupt storage, off identity, current literal split points and final/span redaction. One service generation uses
one validated store transaction and reuses immutable install-validated claims; it adds no provider request or
JWT reparse. Real disk contention and production end-to-end latency still require operational measurement.
Review owns the full backend integration/hosting gate and confirms the exact candidate before the PR.
