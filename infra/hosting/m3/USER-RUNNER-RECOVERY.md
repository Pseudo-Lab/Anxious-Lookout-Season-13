# Runner reservation diagnostics and recovery boundary

This procedure identifies affected users/generations without deleting or changing
user data. It is not an authorization to enable user runners or to act on an
operating cluster. Use the exact reviewed source/image and existing authorized
operator target/credential handling. Current source has **no safe release, reset,
force-recreate or override tool**. Missing an API object does not prove that the
old execution stopped or that a delayed create cannot still arrive.

## Read-only identification

Run `python -B -m ops.runner_recovery` inside the reviewed Docker image using the
existing API-role DATABASE_URL supplied by the authorized secret mechanism. Do
not put the URL, credentials or tokens in command arguments/history or reports.
The tool reads the reviewed runner schema in a repeatable-read, read-only
transaction. It performs no Kubernetes/provider calls and no DB mutations.

```text
python -B -m ops.runner_recovery --owner OWNER_UUID
python -B -m ops.runner_recovery --limit 100
python -B -m ops.runner_recovery --after LAST_OWNER_UUID --limit 100
```

The last returned owner is a keyset cursor when a full page is returned. A cursor
may lead to an empty final page. Each invocation is a separate snapshot; quiesce
relevant controllers/admissions under the authorized operations plan before using
multiple pages as an incident-wide accounting. Do not claim an exact global
capacity snapshot from changing pages.

Output is private metadata only: owner/state, generation/revision, phase, Pod UID,
activity timestamp, active/ambiguous counts, uncompleted admission count, current
generation's issued ensure receipts and `capacityHeld`. It excludes conversations,
file contents, request bodies/IDs, token/credential data, URLs and native paths.
Treat even account/Pod IDs as private incident evidence; share only aggregate
impact or the affected user's own safe status externally. Save output privately
with target, source SHA and observation time. The tool reports
`releaseToolAvailable:false` unconditionally.

| nextEvidence | Meaning and permitted next investigation |
|---|---|
| lookup_only | A creation permission may have been delivered. Reconcile the exact generation by authoritative lookup only. |
| native_evidence_required | Bound Pod/possibly active runtime; reconcile admitted work and verify actual runtime quiescence/termination. |
| already_stopped | Registry records a completed termination barrier; retained receipts are historical, not new create permission. Independently verify the earlier evidence before operational reuse. |
| no_create_permission | No bound Pod/current generation create receipt; may be waiting/unprepared. It does not authorize a direct create outside the controller protocol. |

`capacityHeld` mirrors current controller accounting: bound Pod or issued create
permission holds a slot except after idle_stopped. It is not an observation of
cluster CPU/memory or proof of a live child. A waiting account may be indirectly
affected by another owner's unresolved held slot. Inventory all pages under a
consistent incident plan to identify that impact; don't change max_pods merely to
hide stuck reservations. Operational limits remain unselected/default0.

## Lookup-only resolution

A reviewed controller integration can use `Controller.pending()` and
`Controller.reconcile(Fence)` with the exact Kube Plan. Reconciliation checks the
current DB identity/fence and the full Pod/storage/transport binding. A valid
existing Pod may be bound even if the account has since lost eligibility; this
tracks an existing resource and does not enable sending or create a replacement.
Read the registry/inventory again after binding. It remains unreachable/ambiguous
until actual execution evidence is connected. No CLI currently performs this
mutation; it belongs to the source-reviewed controller integration.

For absent, deleting, foreign, drifted or unreachable objects, preserve the
reservation and all current evidence. A404, expired timeout, clock threshold,
empty Pod list, successful volume flock, or user reload is insufficient to release
the slot or start a new generation. Do not edit outbox.settled, delete registry or
receipt rows, reset phase by SQL, restore an old DB snapshot, remove a PVC, or use
the test-only phase fixture to bypass this condition. Never run fixture code on
operating data.

## Explicit termination and release — incomplete activation prerequisite

A safe release path must prove all of the following for the exact generation:

1. No original controller worker, delayed request, automatic retry, old credential
   or alternate creator can still produce a Pod for the abandoned generation.
   Stopping one worker is not proof that an accepted Kubernetes request is drained.
2. All potentially created objects are reconciled by immutable identity. If an
   execution existed, actual prior process termination and storage lease release
   must be verified even across node partition; API-object absence alone is not it.
3. Every admitted request is completed from its original durable evidence or is
   explicitly preserved as unresolved. Time alone cannot clear running/ambiguous
   work; historical receipt IDs and completion timestamps cannot be rewritten.
4. A durable terminal/tombstone transition prevents late old-generation writes or
   create reuse. Only then may the slot be released and a new entry advance a
   generation, retaining the stable owner/state and every user record/volume.

The current adapter/native interface does not provide this evidence end-to-end.
There is therefore **no supported operator release command at this stage**, and
no generic `--verified`, `--force` or manually asserted JSON proof is accepted.
Implementation depends on the subsequent actual native termination/late-create
barrier integration. This diagnostic/procedure source does not complete unit2 or
satisfy the activation gate. Until that exists and is independently reviewed,
leave the reservation held and report activation blocked for unresolved cases.

## Incident/user communication

Record privately: exact owner/state/generation/revision, known UID or absence,
capacityHeld and uncompleted/ambiguous counts, last authoritative observations,
source SHA, what was attempted, and which of the four release conditions lacks
evidence. Report the affected account and capacity wait impact to PM through its
inbox/issue workflow without credential or conversation content.

Suggested user-facing operator message (not a new server/UI status):

> 환경 연결과 준비 결과를 확인하고 있습니다. 대화 기록과 입력 내용은 유지됩니다.
> 현재는 새 실행을 안전하게 시작할 수 있는지 확인이 필요해 전송을 잠시 제한하고
> 있습니다. 새로고침이나 반복 전송으로 복구가 보장되지는 않습니다. 운영자가 확인한
> 결과와 다음 조치를 안내하겠습니다.

Do not promise imminent recovery, a replacement Pod or completed model execution.
The current UI remains runner_unreachable with GET backoff; it must not infer
operator-required status from elapsed time. If later server evidence warrants a
new explicit operator-required signal, review that contract with front first.
