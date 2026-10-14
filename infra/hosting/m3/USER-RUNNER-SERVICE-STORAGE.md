# Issue10 service control storage (source/tool review only)

This is the selected experimental access-snapshot path. It does not change the
existing live trial, issue workspace/API credentials, refresh the original cache,
prove provider authentication, or authorize enable/deployment. PM performs actual
operations after exact source/procedure review. The database stores only private
metadata, lifetime receipt IDs, token SHA256 fingerprints, keyed redaction
fingerprints, and the projection key; it never stores token plaintext.

## Storage and write boundary

`service-access.sqlite3` (format3) replaces the unbounded service-v2 JSON arrays.
It has a fixed metadata row, indexed requests/snapshots/rejections, and indexed
redaction literals. Receipts/history are never pruned, and there is no trial
15-minute/3-request gate or replacement512-literal cap in service mode. Physical
storage may still fill: a failed SQLite transaction refuses the admission and
preserves the prior committed state; operators must monitor capacity, not delete
receipts to restore admission. The metadata counter is not a usage limit.

A same-directory `service-access.lock` flock serializes cooperating processes,
including first creation and migration. SQLite transactions commit receipt+
counter, or snapshot+redaction, together, with DELETE journals and FULL
synchronization. A new database is first committed to a private staging file and
then atomically published. Unpublished files from a killed process are never
adopted. Stale in-memory credentials cannot consume or block a newer credential.
The Native's existing lifetime lease/dispatch locks are still required; these
storage locks do not prove Pod uniqueness or child termination.

The private control directory must be canonical, same UID,0700; DB, locks, and
SQLite journal must be same UID, single-link regular files without group/world
permissions (created0600). Symlinks/hardlinks/public files are refused. WAL/SHM
files and non-DELETE journal modes are refused without deleting/reconfiguring
unknown state. Keep native and control volumes disjoint. Credential ingress does
not disclose operator source/cache paths to the native app-server protocol.

Known retired token fingerprints are refused even with a new snapshot UUID.
The current credential can be supplied again, including under a new UUID.
Old snapshot IDs cannot be reused. This says nothing about the ordering of
previously unobserved tokens. Auth failure rejections and model/policy/quota
latches persist; supplying credentials is not a policy/quota release.

## Explicit service-v2 JSON conversion

This converts only the earlier experimental **service** JSON, not live trial
users' `access-ledger.json`, renewable-cache ledger, or other users' state.
The service runtime refuses silent adoption. Both `service-access-ledger.json`
and its `access-projection.json` are required and left byte-for-byte unchanged.
A missing projection journal refuses conversion because lost historical redaction
or a new projection key must not be silently substituted.

1. Keep service enable off. Stop the exact owner runner/native and supplier;
   independently verify no active/ambiguous work and process termination. Preserve
   the original native/control pair, owner/state/Pod identity, and source files.
   A successful flock alone is not this evidence. Do not read/copy the selected
   original credential cache for this metadata conversion.
2. Make a private offline full control/native backup and record checksums and
   identity metadata. Do not use existing live-trial backup scripts to assume the
   new format is covered; their archive allowlists have not been integrated yet.
3. Run the reviewed image as the existing control UID, network-none/read-only,
   no logs carrying input values, with only the exact private control mounted rw
   and the selected account reference file mounted ro. No provider, kubeconfig,
   Docker socket, original HOME, or refresh token mount is needed. Run inside
   Docker (placeholders refer to previously selected private metadata):

   ```text
   python -B -m ops.service_access_migrate --control /control --owner OWNER_UUID --state-id STATE_UUID --account-file /metadata/operator-account
   ```

   The source files must be same UID/private/single-link. The one-time legacy
   reader can consume JSON larger than the old runtime reader1MB limit and needs
   memory proportional to those old files; parsing/insertion failure retains the
   originals and publishes no target. Duplicate/corrupt receipts, owner/state/
   provider/context mismatch, projection MAC failure, or trial markers refuse.
4. Run the same command with `--check`. This explicitly scans SQLite integrity,
   receipt count/IDs, snapshot/rejection bindings and keyed redaction MACs. It is
   an offline audit, not a per-request full-history scan. Check old/new counts,
   original checksums, stable projection key/context/native MACs and dedup in a
   separate non-provider rehearsal before any actual enable.
5. Re-run conversion only against the same retained input files: the stored
   original hashes must match. An already converted database is audited and
   preserved; old files never overwrite new receipts/current credentials. A
   killed conversion may leave `.service-stage-*` and journals. Retain these as
   evidence, retry against unchanged originals, and do not rename an orphan into
   the live ledger. No cleanup of those orphan files is automatic.
6. Restart still has no credential in memory. The existing reviewed same-source
   supply procedure must provide a current valid snapshot. It preserves lifetime
   history and key/context. Expired/current rejected/known retired snapshots
   refuse; no provider turn is automatically replayed.

## Backup, interruption recovery, and rollback

Do not copy a running SQLite main file alone. For this reviewed source chunk,
backup is **offline only**: verified runner/native/supplier quiescence, then
`--check` to recover a valid hot DELETE journal and audit, then archive the full
private control directory together with its native state and bound identity.
Preserve any journal if recovery/audit refuses. Never remove a hot journal to
make startup pass; it may contain rollback data for a partially written main
file. Mount both files together for recovery. A WAL/unknown-format/permission
refusal is a manual recovery boundary, not permission to delete history.

Restoring an older valid backup can still lose newer admission receipts and
allow duplicate requests. Therefore **do not replace a populated current ledger
with an older snapshot as automatic rollback**. Stop admission, preserve every
current file and ambiguous request, and reconcile exact generations before
reviewed recovery. Rollback leaves format3 state and retained v2 originals in
place; old service-v2 code must not run against the retained stale JSON, and old
trial code must not automatically consume new service state. No downgrade or
live-trial-to-service conversion is included here. Broader per-user DB/controller,
archive tooling, and actual supply/enable acceptance remain separate gates.
