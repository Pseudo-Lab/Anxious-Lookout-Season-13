# #7 remaining work: narrow readonly owner/source preflight

Update: the user selected current working-session Codex authentication; the
independent-profile option below is historical and superseded. Platform A lookup
has now completed privately. Follow SHARED-SESSION-AUTH.md for actual-runtime
applicability and the needed owner-side integration; do not ask for another profile
or repeat A. B remains format-only and is not shared-refresh proof.

Proposal for independent procedure review, then PM execution. Source/runtime70 and
infra packageb678 remain unchanged; current live API70/nativeoff is independently
verified. Stage1 import/rollout/PV proof is reused. This procedure does not authorize
source supply, state initialization, runner startup/enable or provider requests.
No actual account/source diagnosis has been executed by back.

## Inputs and sequencing

| Step | Required selection | Permitted observation | Still not established |
|---|---|---|---|
| A: owner lookup | Exact existing website GitHub login chosen by owner/PM | One existing approved editor/admin account UUID, via READ ONLY SELECT | Browser possession, Codex provider identity or source ownership |
| B: source diagnosis | One logical existing server profile selected by owner; PM resolves its exact canonical file from known profile configuration | File metadata/structure and stability, booleans only | Authentication/entitlement, independent grant, absence of other writers, latest-token continuity |
| C: operation review | Actual owner/state, source lifetime/refresh evidence, exact provider routing, resolved manifests/defaulted runner baseline | Review input/diff and target boundaries | Startup/turn success until separately authorized execution and independent result review |

A can run once its login is selected without waiting for Codex profile selection.
B must wait for that profile/file selection; it does not require declaring exclusive
refresh merely to inspect format. Passing A+B does not authorize C or start native.
The pending profile/shared-grant answer is not supplied by the user's instruction
to finish #7. No account enumeration or guessed owner/provider IDs.

## A. Existing login account READ ONLY lookup

PM uses current stage1 evidence and fresh metadata to choose exactly one Ready API
Pod in m2-hosting, verifies expected Deployment/Pod UID and uid-api5a2a95d… image/
runtime70, and confirms existing nativeoff config/owner-map empty. This is readonly
identity verification, not a repeat rollout/import. Do not select a stale8fa Pod.
Use its existing API DB environment; do not export database/GitHub/cookie secrets.

Private operator workspace outside Git/messages (0700; umask077, set +x) contains
`selected-login.json` with exactly `{"githubLogin":"<existing-selected-login>"}`.
Review helper source hash is
`4878eae5c417f22495de90c93785649e744974e19fdbcbea63f21ca9fa8f8822`.
Do not edit helper or paste selected values into command strings. Example shell
commands; task variables below are already privately selected, not inferred defaults:

```sh
set -euo pipefail
set +x
umask 077
UID_LOOKUP_SCRIPT=$(cat infra/hosting/m3/owner_lookup.py)
timeout 15s kubectl exec -i -n m2-hosting "$UID_READY_API_POD" -c api -- \
  python -B -c "$UID_LOOKUP_SCRIPT" \
  < "$UID_PRIVATE_DIR/selected-login.json" \
  > "$UID_PRIVATE_DIR/owner-result.json" 2> "$UID_PRIVATE_DIR/owner-lookup.err"
unset UID_LOOKUP_SCRIPT
```

This transient interpreter executes the exact reviewed helper, not an app-server.
No live source file/environment/process modification, migration or Secret injection.
`Settings.load()` reads existing Pod configuration/credentials in memory; SQLAlchemy
connects only to existing DB. Helper sets TRANSACTION READ ONLY and LOCAL query5s /
lock2s, binds login as parameter, SELECTs id/role/is_approved, requires exactly one
eligible account, disposes connection. It never INSERTs/UPDATEs/promotes accounts.
No selected result is reused after role/approval changes without fresh verification.

Retain output/errors privately. The account UUID is platform identity; provider
account IDs are unrelated opaque values. Compare privately to the selected user's
existing authenticated `/api/auth/me` accountId when available. That route also
returns CSRF data: never copy its cookie/CSRF response into shared evidence or use a
new login just to perform this lookup. If browser proof is unavailable, report that
limit; SQL lookup alone does not impersonate or prove user session possession.
Public report contains only eligibleExactOne true/false and readOnly true, not login,
UUID, role history or session data. Record target metadata privately. On absent,
multiple/ineligible/timeout/error stop this step; no fallback selection or promotion.

## B. Exact selected file, network-none metadata only

User names the existing logical profile; PM resolves one known exact cache file
without HOME traversal, keyring extraction, searching other worktrees or token output.
No new login, stopping original clients, cache copy, refresh, logout, chmod/chown of
original source. Unknown location or unsupported backend is a reported missing input.
The file choice and actual canonical parent chain are reviewed privately before run.
No guessed provider account ID or grant.json is needed for format-only diagnosis.

The diagnostic uses exact **API** image5a2a95d… (no vendor CLI); no runner/runtime
control/native mount and no cloud/kube/Docker socket or original HOME mount.
Operator variables are privately selected. Reject symlinks/nonregular files before
mount; helper additionally uses O_RDONLY/O_NOFOLLOW, private permissions/single link,
matching Unix UID, ≤1MB and descriptor-before/after plus path stamp checks.
File bind mounts can retain a stale inode; compare host dev/inode/UID/GID/mode/nlink/
size/mtime/ctime before/after too. A read can update filesystem access time; no source
content/ownership/permission rewrite is requested. Stable stamps do not prove writer
absence or authoritative latest cache.

Example readonly metadata shell procedure (errors/stamps private, no tracing):

```sh
set -euo pipefail
set +x
umask 077
# UID_SELECTED_SOURCE is the reviewed canonical absolute file, never a default HOME path.
[[ "$UID_SELECTED_SOURCE" == /* && "$UID_SELECTED_SOURCE" != *,* && "$UID_SELECTED_SOURCE" != *$'\n'* ]]
[[ -f "$UID_SELECTED_SOURCE" && ! -L "$UID_SELECTED_SOURCE" ]]
[[ "$(realpath -e -- "$UID_SELECTED_SOURCE")" == "$UID_SELECTED_SOURCE" ]]
stat -c '%d|%i|%u|%g|%f|%h|%s|%y|%z' -- "$UID_SELECTED_SOURCE" > "$UID_PRIVATE_DIR/source-before.stat"
UID_SOURCE_UNIX_USER=$(stat -c '%u' -- "$UID_SELECTED_SOURCE")
UID_SOURCE_UNIX_GROUP=$(stat -c '%g' -- "$UID_SELECTED_SOURCE")
UID_DIAG_CONTAINER="issue7-selected-source-diag-$(cat /proc/sys/kernel/random/uuid)"
# Ensure owned container removal on timeout/error; no other container cleanup.
trap 'docker rm -f "$UID_DIAG_CONTAINER" >/dev/null 2>&1 || true' EXIT
UID_DIAG_EXIT=0
timeout 15s docker run --rm --pull=never --name "$UID_DIAG_CONTAINER" \
  --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --user "$UID_SOURCE_UNIX_USER:$UID_SOURCE_UNIX_GROUP" \
  --env HOME=/no-host-home --env CODEX_HOME=/no-host-codex-home \
  --mount "type=bind,src=$UID_SELECTED_SOURCE,dst=/source/auth.json,readonly" \
  --entrypoint python \
  sha256:5a2a95d93810bcfc035bb883d1096376d9f2cf6f48fc99f88f1f8c308c308566 \
  -B -m ops.source_metadata --source /source/auth.json \
  > "$UID_PRIVATE_DIR/source-result.json" 2> "$UID_PRIVATE_DIR/source-diagnostic.err" \
  || UID_DIAG_EXIT=$?
stat -c '%d|%i|%u|%g|%f|%h|%s|%y|%z' -- "$UID_SELECTED_SOURCE" > "$UID_PRIVATE_DIR/source-after.stat"
[[ -f "$UID_SELECTED_SOURCE" && ! -L "$UID_SELECTED_SOURCE" ]]
[[ "$(realpath -e -- "$UID_SELECTED_SOURCE")" == "$UID_SELECTED_SOURCE" ]]
cmp -s "$UID_PRIVATE_DIR/source-before.stat" "$UID_PRIVATE_DIR/source-after.stat"
[[ $UID_DIAG_EXIT == 0 ]]
```

Run with fail-fast shell semantics in a dedicated PM diagnostic subshell; use target's
reviewed Docker privilege wrapper if required. Failed checks invalidate the entire
observation; don't run later steps after an error. The owned EXIT cleanup always runs.
Do not enable shell tracing or print Docker errors containing private mount paths.
Do not relax original permissions if container access fails. As private before/after
metadata, record inode stability/format outcome; no token/hash/JWT/timestamps/path in
shared evidence. Baked helper source hash
`8e6a35c7c91123a9ce1de623472c348ea311bd7e4d1b32dfb2c3cd2599c3e6c0`.

Expected result: structureCompatible, sourceFilePrivate/sourceReadStable booleans;
bindingProvided=false/providerBindingMatches=false, authenticated=false,
sourceAuthoritative=not_established, refreshOwnership=owner_evidence_required,
modelAccess=not_tested. Exit0 means readable compatible structure only. Readonly file
access does not assert selected-profile authority or all same-grant consumers. On
keyring/unsupported format/failure report it; no conversion, copy or native startup.

## Current-code feasibility for shared original grants

`backend/runner/app.py:121-142` creates its own private HOME/CODEX_HOME and launches
pinned `codex app-server --listen stdio://` through Popen pipes. There is no current
existing-client socket/IPC/attach endpoint or refresh-owner forwarding parameter.
`account/read(refreshToken=false)` does not disable later native refresh.
`runner/auth.py:79-85` requires exclusive-managed-native/hostGrantQuiescent; those
booleans are declarations needing external evidence, not proof from file diagnostics.
`auth.py:119-147` leases only private control, copies managed cache to private native
home and marks nativeActive. The lock does not cover the original PM/back/IDE grant.
`auth.py:213-228` syncs the latest native cache only into private control at clean close;
it neither updates nor coordinates an unchanged original client. UID/state/thread
markers and Cilium do not change this refresh ownership boundary.

Thus the **current implementation** cannot meet “same renewable grant, original
clients keep running unchanged, no cache supply/update” by connecting to an existing
client. Claiming this would require another adapter/refresh-owner protocol plus
owner/tool/ledger/thread/projection isolation work and review; no such change is
implemented or included in this finish instruction. This is a code-bound conclusion,
not a claim that every external client/API lacks such capabilities.

Conditional existing-scope path: owner selects an **already-existing independent
login/profile grant** which does not share the original active clients' renewable
grant, with privately established ownership/non-interference and latest-cache
continuity. It must also fit reviewed private-use/source handoff; selecting it alone
is not admission. Different file paths or matching/different provider account IDs
alone cannot establish independent renewable grants. No new profile/grant is created
and no original session is stopped.
Minimum single user choice if existing sessions share the grant: **which already
existing independent Codex login/profile should this test use?** The website login
for A is a separate platform identity, never guessed from that provider profile.
If no such existing profile can be selected, the constrained actual stage2 remains
blocked by source topology; do not request a UUID/token or silently widen scope to
create credentials/API billing/existing-client bridge.

## Delivery and remaining acceptance

Review this narrow A/B procedure before PM actual execution. No runtime rebuild,
image policy change or stage1 replay. Helpers retain prior approved100/source67+1
coverage; document-only addition. Once selections and A/B results exist, author the
actual resolved owner/state/provider manifest/diff and reviewed server-defaulted
lifecycle baseline for next operation review. Source/refresh ownership and supported
routing remain separate prerequisites before supply/startup. Preserve original HTTPS,
DB/PVC/Secrets and two explicit turns with total3/no auto-resend, same-thread clean
replacement; actual provider/tool/resume result and final PR/public posting remain
unclaimed.
