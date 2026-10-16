# Termination source checkpoints — no live deployment approval

The runner's private authenticated `POST /lifecycle/drain` accepts exactly stopId,
ownerId, stateId, podUid (canonical UUID strings) and generation (positive integer).
The caller must bind generation to the current registry and Pod UID under its owner
fence. The runner verifies owner/state/Pod UID, serializes drain with supply, turn
admission and history RPCs, and refuses busy or non-idle broker projections.
Once drain starts, supply and dispatch remain fenced even if shutdown fails.

The response and private retained `native/termination-<PodUID>.json` contain the
request identity, version=1 and a boolean clean. A clean native exit requires an
initially live child, no active request, no forced termination, exit code zero,
completed worker and valid binding. A never-started, not-configured runner may
record clean only if neither broker nor native home exists. Repeat of the same
request returns its receipt; another request cannot overwrite it. Receipt does
not clear nativeAttached, ledger history, DB admissions or conversation state.

Receipt is evidence of native drain only. The adapter HTTP process is still alive.
It does not establish that the Pod/cgroup is dead or authorize slot release. The
future stop worker must persist a request before sending it, validate the returned
identity and clean flag, issue ordinary UID-preconditioned Pod deletion, and combine
that receipt with fresh host proof before committing a lifecycle transition.

`ops.runner_termination.HostTermination` is a separate root-only host verifier. It
requires the host PID namespace, a canonical read-only host proc path, a trusted
root-owned CRI socket and executable, expected Node name/UID and boot ID. Fresh
root host inventory must independently supply `host_pid_namespace` and
`host_cgroup_namespace`, obtained on that host with `readlink /proc/1/ns/pid`
and `readlink /proc/1/ns/cgroup`, paired with the boot ID. The observer refuses
missing references and compares both its own namespaces and the proc PID1
namespaces with them. Never populate these inputs by discovering namespaces
inside the observer container. Run the observer with host PID **and cgroup**
namespaces; sharing only a boot ID does not prove process visibility.
Fresh
Pod absence and Ready Node identity/heartbeat bracket CRI plus cgroup inspection.
Matching ready sandboxes, non-exited containers, processes, unknown ownership,
query errors, incomplete proc visibility, boot/Node drift or observations taking
over 30 seconds refuse proof. Caller must fence creators and verify the old Pod
was assigned to this Node; the verifier does not infer that relationship.
Every CRI ownership UID must be a canonical UUID before comparison; an empty or
malformed UID is unknown ownership and blocks evidence.

For the real isolated-PID regression, pass `TEST_PARENT_PID_NAMESPACE` and
`TEST_PARENT_CGROUP_NAMESPACE` to the Docker test service, capturing each with
`readlink /proc/1/ns/{pid,cgroup}` in the invoking parent environment. Keep the
test container in its default isolated PID namespace. The test asserts that the
reference differs from its own namespace and that construction refuses it. This
regression skips if independent references were not supplied; reported validation
must explicitly include an invocation with them.

Tests use fake Native/process/CRI/proc and real fixture adapter tests inside Docker.
The first checkpoint introduced only these modules. The subsequent DB integration
is described below. Production deployment and the legacy recovery procedure need
their own review; source tests do not establish live termination.

## Legacy node evidence preservation

PM found the old UID in this host's current-boot k3s journal and containerd log.
The journal is volatile. Before a reviewed legacy attempt, retain a root-only
export in a newly allocated mode0700 evidence directory; do not reboot first.
The operator command is `journalctl -u k3s -b -o json --no-pager` redirected
inside that private directory to an exclusively created file (umask077, noclobber).
Also preserve the relevant containerd log and metadata/digests privately. Never
copy raw journal records, credentials or conversation content into message files.
Export is evidence collection, not authorization to release the runner.

The evidence package must link the old Pod UID and container IDs, journal _BOOT_ID,
local hostname, current host boot ID, and Kubernetes Node UID/name. Confirm the
k3s node-name mapping for OS hostname `simple` to Node `anxious-lookout`; the name
alone is insufficient. The source record is not a runner shutdown receipt and
cannot substitute for present CRI/proc proof or DB/native uncertainty reconciliation.

## DB observer integration (subsequent source checkpoint)

The additive `ops.runner_termination_migrate` creates and validates the exact
runner_termination schema. API credentials can insert only intent columns; they
cannot insert/update node anchors, drain receipts, host proof, completed_at or
consumed_at. Install `db/016_runner_observer_grants.sql` once and give its NOLOGIN
capability to a separate observer login. It can read session validity and conversation
owner/state metadata, but cannot change approval or session expiry. API startup does
not migrate. During the reviewed cutover stop old API/controller instances before
migrating and replacing them, so old synchronous DELETE behavior cannot race the
new observer. The legacy stop path remains only when this schema is absent.

With the schema installed, DELETE/logout records a UID-bound durable stop intent;
it does not issue Kubernetes DELETE from the API. GET projects `stopping` immediately.
Bound dispatch and registry entry check that intent under the owner lock. An explicit
prepare while stopping returns 202 and persists a coalesced key tied to the originating
app session; GET never creates intents. Logout's FK cascade removes that session's
pending intent. Invalid/expired sessions, revoked membership and excluded/disabled
selection never restart an owner during release. Session hashes stay in DB only.

`ops.runner_stop_observer` first records the scheduled Pod's Node UID/boot attribution
before drain. It verifies exact native receipt and no pending admissions/running
conversations, commits stopping while retaining the UID, then issues normal deletion
with a UID precondition. Missing DELETE responses preserve the reservation. A later
iteration reads the retained exact receipt through the bound PV and verifies host
termination; only then does one transaction release the slot and, if an eligible
session intent remains, advance one generation through the existing registry/outbox.
Repeated completion never releases a later Pod. Existing state/history/create receipts
are retained. Errors and unknown evidence leave the stop/slot pending.

Private root inputs contain exactly `pool` (the version2 pool configuration), `host`
(HostTermination constructor fields including independent namespace references), and
`storageRoot`. Set OBSERVER_DATABASE_URL_FILE to the observer login secret. Set the
same feature/allowlist/singleton environment as the API for **re-entry** selection;
cleanup remains possible when that flag is off. Kubernetes observer credentials
need existing pool reads, named Node/PV reads and Pod DELETE, never Pod CREATE/exec.
Expose only the retained storage roots, trusted CRI socket, reviewed crictl executable,
private configuration and CA/service-account inputs, plus host proc, to the root image.
The exact host invocation and image pin remain deployment-review gates.

The command is a finite batch, `python -B -m ops.runner_stop_observer --inputs
/private/observer.json --cursor-file /private/observer-cursor.json --limit 10`, or
`--owner <UUID>` for one owner. The private persisted cursor advances even over a
refused owner and wraps at the end so a blocked prefix cannot starve later stops.
Run it via a reviewed external systemd oneshot/timer; it is not an inbox watcher.
The CLI emits fixed status/owner metadata only. Native/host fakes plus disposable DB
validate sequencing, uncertain DELETE, duplicate release, permissions and HTTP intents;
they do not replace the live visibility/mount/crash/credential gates.

## Narrow legacy zero-admission recovery

`ops.runner_legacy_reconcile report|apply --inputs /private/legacy.json --report
/reports/legacy.json` uses the same pool, host, storageRoot fields as the observer,
plus `fence` (owner, state, generation, revision, podUid), `journal` (private JSONL
export path), `hostname` and `expectedAccountId`. All paths must resolve to the
same mounted paths used by the private input. Root owns configuration, journal,
DSN and report; directories are mode0700 and files mode0600. Never publish them.

The target is the imported legacy uncapped layout only. Supply the independently
reviewed old Pod-to-host relationship, including the k3s node-name override;
the tool validates matching journal UID/unit/boot/hostname, but cannot recover a
historical Node-name override from today's configuration. PM must preserve that
relationship evidence and reject mismatches before invoking either mode.
Use fresh trusted host namespace references, Node UID and boot ID. A reboot
invalidates this same-boot path rather than silently accepting old proof.

Report holds the owner fence, requires FAILED with the exact UID/generation/revision,
zero DB admissions (including completed ones), and no running conversations. It
checks fresh host proof and pinned PV/PVC/namespace bindings, takes existing native
and control file leases, and audits owner/state/account markers, an empty broker
and a structurally valid service ledger with zero lifetime requests. Retained native
auth.json or SQLite sidecars refuse recovery. Report never updates registry or
retained files; it exclusively creates a private digest report for operator review.
PM's admissions=0, pending=0 and failed/idle conversations satisfy only the DB
preconditions. The eight generation-1 entry receipts are preserved, not admissions.

Apply re-runs all checks, requires an identical approved report, repeats file and
fresh host verification, and atomically records distinct legacy audit evidence and
releases into idle_stopped. It never manufactures a native drain receipt. The
owner/state/generation, ledger, broker and history remain unchanged. No Pod is
created or deleted. Repeated apply recognizes the completed old UID even after a
new normal entry has started a later generation, without releasing that generation.
Any uncertainty remains FAILED. This path cannot reconcile a nonempty broker or
any historical admission: those require separate evidence and review.

Before report/apply, stop old API/controllers and any external creator for this
owner; the DB owner lock fences current cooperating code but cannot fence an old
binary or external kubectl. Install and validate the termination schema and observer
role grants, import the retained binding without re-preparation, preserve the
volatile journal, and review the explicit target fence. Use a dedicated observer DB
login; neither API credentials nor a superuser belong in this command. The existing
binding's runner image must separately be reviewed for the new drain/supply contract
before enabling later generations. Recovery does not rewrite immutable bindings.

The Docker image is built from a committed source archive, not the working tree:

```sh
git archive <reviewed-source-sha> | docker build -f backend/Dockerfile --target provisioner \
  --build-arg APP_GIT_SHA=<reviewed-source-sha> --build-arg APP_BUILT_AT=<UTC-time> \
  -t anxious-s13-back-observer16:legacy-source -
```

Use the local content-addressed image ID recorded with the review request and
`--pull=never`; a tag is insufficient. The exact commands for that built artifact
are recorded below after the image build. Host proc/CRI and namespace visibility,
retained real paths, database role and network access must pass PM's report run
before any apply is considered. Tests use a real disposable PostgreSQL and real
temporary ledger, with fake host/Kubernetes evidence, not the live environment.

### Pinned legacy artifact and command (source review required before execution)

Source `e5906b0af363d0513fdb648b889cc18b3f432a5c`, built
2026-10-16T08:37:32Z, local arm64 image ID
`sha256:35a9a0f342586f3ecdefcda5c57f622972efedfda9e36dba90a1c653d70bf323`.
This ID refers to the local Docker artifact, not a remotely published tag. Preserve
or transfer the image with its content identity if executing on another daemon.

PM prepares `/run/issue16-recovery-inputs` (root0700) with `legacy.json`, `observer-dsn`,
`journal.jsonl`, `kube-token`, `ca.crt`; config references these as `/private/...`.
Prepare a separate empty root0700 `/run/issue16-recovery-report` for the report.
Set host.proc to `/host/proc`, host.crictl to `/usr/local/bin/crictl`, and endpoint to
`unix:///run/k3s/containerd/containerd.sock`. Private pool config uses the reviewed
reachable API/CA and namespace UID; do not disable TLS. Supply independent trusted
host PID/cgroup references and boot/Node identities from the current inventory.
The recorded legacy fence is owner `ff4b718f-de46-4c10-9195-c993eff9ded9`, state
`71c84103-a643-4cfc-a3f6-ef6079e48d50`, generation1, revision8, Pod
`1c4552b5-bfb3-4b23-b3c3-a0cae4b72163`; reconfirm it before use.

```sh
# READ-ONLY report: no DB/retained state mutation; writes only /reports.
docker run --rm --pull=never --user 0:0 --network host --pid=host --cgroupns=host \
  --read-only --cap-drop ALL --cap-add DAC_READ_SEARCH --cap-add SYS_PTRACE \
  --security-opt no-new-privileges \
  --mount type=bind,src=/proc,dst=/host/proc,readonly \
  --mount type=bind,src=/run/k3s/containerd/containerd.sock,dst=/run/k3s/containerd/containerd.sock,readonly \
  --mount type=bind,src=/usr/local/bin/crictl,dst=/usr/local/bin/crictl,readonly \
  --mount type=bind,src=/var/lib/anxious-lookout/user-runners,dst=/var/lib/anxious-lookout/user-runners,readonly \
  --mount type=bind,src=/run/issue16-recovery-inputs,dst=/private,readonly \
  --mount type=bind,src=/run/issue16-recovery-report,dst=/reports \
  -e OBSERVER_DATABASE_URL_FILE=/private/observer-dsn \
  --entrypoint python \
  sha256:35a9a0f342586f3ecdefcda5c57f622972efedfda9e36dba90a1c653d70bf323 \
  -B -m ops.runner_legacy_reconcile report --inputs /private/legacy.json --report /reports/legacy.json
```

After independent review of the private report and current inputs, use the identical
command with `report` replaced by `apply` and the `/reports` bind made `readonly`.
Apply writes only the narrowly scoped DB audit/registry transaction; retained mounts
stay read-only. No automatic apply follows report. Any report refusal preserves the
state and requires inspecting the evidence boundary, never a direct SQL reset.
The socket read-only bind does not restrict CRI methods by itself: this reviewed
binary only invokes inventory calls, and runtime-socket trust stays with the host
operator. Host namespaces and SYS_PTRACE are confined to this finite trusted tool,
not granted to API or runner workloads. CLI --help passed with network disabled;
the privileged report/apply invocation has **not** been run on the live host.

## Idle reclamation

The finite observer accepts `--idle` to include READY/UNREACHABLE owners whose last DB
activity is at least `CODEX_USER_RUNNER_IDLE_SECONDS` old (default1800). It merges
idle candidates and existing stop intents into the same sorted persisted cursor,
so a repeatedly refused owner cannot starve later owners. With `--owner`, it
rechecks that single owner instead. Run via the external reviewed systemd timer;
no internal inbox watcher or permanent polling process is introduced.

The DB candidate query is only a hint. Under the owner lock, the worker rechecks
current phase, UID, activity time against `clock_timestamp()`, active/ambiguous
flags, admissions and running conversations. It also calls the bound authenticated
`POST /lifecycle/idle` and requires exact owner/state/Pod UID plus idle=true. Native
idle duration uses a monotonic clock reset on startup, actual turn admission and
worker completion; GET/history polling does not reset it. Active/unknown/failed
broker work refuses native idle evidence. This extra clock prevents an old DB
timestamp from reclaiming a just-completed turn. A restarted adapter waits the full
interval again. A fresh dispatch or changed activity causes refusal. Bootstrap
UNREACHABLE/ambiguous state may become a stop intent only with that current native
evidence; ambiguity is not cleared until the regular clean drain. It records the
ordinary durable stop intent without changing the
registry phase, clearing ambiguity or releasing capacity. Subsequent dispatch is
fenced by that intent, and existing native drain/receipt/UID-delete/host proof logic
performs the actual stop and release. It does not call Registry.idle with an invented
quiescence flag. Expired/revoked users can still be cleaned up; new entry remains
subject to the existing membership/session/feature checks. GET does not update
last_activity or create a re-entry intent. The previously pinned legacy image
predates this idle option; build a newly reviewed artifact before enabling it.
