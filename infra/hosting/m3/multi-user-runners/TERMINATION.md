# Termination source building blocks — not an enabled observer

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
These modules are not yet wired to DB stop intents, slot release, HTTP stopping
entry intents, or a legacy recovery CLI. They do not enable production stopping.
The full observer and legacy recovery gates in PROVISIONING.md remain outstanding.

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
