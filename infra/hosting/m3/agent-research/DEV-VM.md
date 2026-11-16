# #97 r2 development gates (review-owned execution)

This replaces the superseded MCP C1/C2/fence procedure. No old benchmark target,
marker, audit/settlement command, image or model budget is valid for r2. Current
actual model allowance is zero. Back prepares code and Docker pure units;
review/PM own VM execution. This document grants no VM/credential/model permission.

## Before execution

Confirm the development guest identity and occupancy and follow
[production/development parity](../../../k3s/PARITY.md). Use synthetic development
accounts and private inputs. Never bring production credentials/backups into the
fixture. Preserve #94 logging, #95 flags, provider/model pin, untrusted approvals,
Pod UID/caps/namespace/SA boundaries and #62 D1/VAP. Do not relax privilege or
Pod security to get user namespaces working.

Build ordinary `runtime` and `runner` targets from the exact reviewed SHA with
matching build metadata. Record manifest digests separately from Docker config
IDs. Keep an imported,008-compatible API+ordinary runner fallback and certify
human read/save. Back up/drain before the reviewed additive008 migration. Never
downgrade008, delete provenance or apply an obsolete source just to start it.
If retired `research-fence.json` exists on control storage, stop and report;
do not delete it, replay a mutation, or try a removed settlement/audit command.

## Gates before enabling a research grant

1. Review the API+CLI/skill code, strict result/receipt contract, purpose-separated
   token principal, expiry/revoke, actual active-turn status and bounded DB pool/
   lock handling. Run review-owned PostgreSQL/security/full regressions.
2. Prove on actual Codex0.160.1 and the unchanged Pod/kernel boundary that the
   grant readable root binds exactly one regular read-only file inode. Replace it
   atomically and verify old sandbox/FD cannot see the next token; parent/sibling,
   symlink/proc/FD/env and provider/control paths remain unavailable. Docker userns
   EPERM is an unexecuted probe, not a pass. No privileged fallback.
3. Submit the separate synthetic world443 L4 passthrough → Traefik TLS fixture
   source, exact temporary objects/origin/certificate/SNI/test-CA differences and
   restoration plan. Only then request fixture execution approval. The existing
   dev HTTP route is insufficient. Prove Cilium world443, TLS CA/hostname/SNI and
   actual API path separately. No new host/internal allow or TLS verification
   bypass. Restore temporary objects/relay/trust/origin and synthetic sessions.
4. Check wrong-purpose/missing/retired/expired/other-C/R/owner/public research
   tokens; broker8080/internal8081/runner service authentication must still reject
   them. Never put real internal credentials in the sandbox for a positive control.
5. Get a fresh actual-model budget from PM/user before issuing live grants or
   turn/start. Record reservations before dispatch and count unknown/failed sends.
   No retry, fake active grant or old MCP allowance bypass.
6. Under that separate allowance prove actual CLI/skill owner read→save→versions,
   original-thread resume/interrupt, protected raw working capture vs always-redacted
   app records, old token invalidity, safe API-owned receipts and F2/e2e. Human
   drafts/public snapshots stay unchanged. A failed model is not marked successful
   because one API write committed.

Development fixture success is not OCI production hairpin proof. Production
execution remains separately approved under
[R2-PRODUCTION-PREFLIGHT.md](R2-PRODUCTION-PREFLIGHT.md), including the actual D1
policy recheck immediately before sandbox network activation and008-compatible
recovery. A missing prerequisite stops activation rather than widening policy.
