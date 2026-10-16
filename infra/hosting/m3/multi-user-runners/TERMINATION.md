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
The reviewed operator command is `journalctl -u k3s -o export --no-pager` redirected
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
