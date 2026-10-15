# Issue #7: minimal path past the proven namespace mismatch — proposal only

No new operation is authorized by this document. This proposes the next
read-only scope for independent review; it implements no runtime/guard change.

## What the retained evidence rules out

Offline verification of /tmp/issue7-namespace-provenance.IqSJ9X confirms the
original host/controller references each match their explicit own-process
link; cross-observer links and device/inode differ. Within each observer,
time and time_for_children agree. Both captured monotonic/boottime offsets
are zero; boot/type/250ms pass. None of this authorizes replacing namespace
equality with small skew or equal offsets, or reconstructs old request19.

Changing whitespace or the host reader to its broker PID would not remove
the mismatch shown by this capture. A shared-clock/offset bridge would change
the existing safety model across host/controller/collector/holder and is
larger than the first corrective path below. Keep all current guards first.

## Proposed next scope: effective tooling runtime metadata only

Prepare one new clock-only capture at a separately reviewed source SHA/new
private0700 directory. Retain the current controller profile, host/save/done
whitelist,10s total observation budget and bounded local teardown. Before
teardown, read only the Docker-receipted ID of this invocation's controller,
checking its ID/image/name. The reviewed implementation procedure is in
ACCESS-NUMERIC-TRIAL.md; predictable names alone never authorize inspection
or stopping a container. Failed creation/name collision without that receipt
must leave foreign containers untouched:

1. Bounded Docker daemon metadata: DefaultRuntime and **runtime names only**.
   Do not collect runtime paths/arguments, daemon config files, sockets/TLS
   settings, other containers, process lists or environment.
2. Bounded inspect of that exact own controller: Name/Id/Image,
   Config.Image/User and selected HostConfig Runtime/Isolation/UsernsMode/
   NetworkMode/PidMode/ReadonlyRootfs/Privileged/CapDrop/SecurityOpt.
   Exclude Env, Mount source paths, credentials, logs and full inspect output.
3. Preserve the existing exact identity/provenance/clock evidence beside it.
   Missing/failed metadata is unknown; never substitute a guessed runtime.

Concrete read commands for the backend-authored bounded implementation:

```bash
docker info --format '{{json .DefaultRuntime}}'
docker info --format '{{range $name, $value := .Runtimes}}{{println $name}}{{end}}'
docker inspect --type container --format '{{json .Name}} {{json .Id}} {{json .Image}} {{json .Config.Image}} {{json .Config.User}} {{json .HostConfig.Runtime}} {{json .HostConfig.Isolation}} {{json .HostConfig.UsernsMode}} {{json .HostConfig.NetworkMode}} {{json .HostConfig.PidMode}} {{json .HostConfig.ReadonlyRootfs}} {{json .HostConfig.Privileged}} {{json .HostConfig.CapDrop}} {{json .HostConfig.SecurityOpt}}' VALIDATED_DOCKER_CREATED_ID_FROM_THIS_INVOCATION
```

These are reviewable command specifications, not a standalone script to run
against old or unrelated containers. Each command needs an outer bound within
the existing clock-only deadline; if all fields cannot be collected in time,
stop unknown without extending the deadline. No Kubernetes/prerequisite,
image inspection, arm/holder/query/CNP/worker, namespace operation or numeric
invocation is included. Docker info/own-container inspect is the only added
read scope and must be explicitly reviewed before execution.

## Conditional corrective path after that evidence

If effective runtime metadata identifies a supported alternative already
installed, propose a single **controller-only** explicit runtime selection
(for example --runtime=runc only if its availability and suitability are
established). Docker CLI supports --runtime; current daemon/runtime setup and
its effect are not inferred from namespace IDs or a default assumption.

That candidate is a separate profile change requiring exact source/argv,
security-impact review and offline counterexamples before one clock-only
trial. Preserve image/UID/read-only/capdrop/no-new-privileges/networknone,
mount restrictions, namespace equality and250ms; assess UsernsMode and other
isolation properties rather than silently discarding them. Do not install a
runtime, alter daemon defaults, restart Docker/K3s/Cilium or change a production
Pod/Service/policy. No setns/unshare, host PID/network mode or namespace mount.

If the existing runtime already is the candidate, metadata is incomplete,
required isolation cannot be retained, or the candidate still produces
different identities, refuse and re-scope from evidence. No automatic switch
or subsequent attempt. Runtime selection is a candidate, not a promised fix
or diagnosis of the current configuration.

Only after a separately approved clock-only candidate passes the unchanged
guards may PM seek a **new explicit numeric disposition** with refreshed full
baseline/owner/image/policy/clock gates. Existing actor-local collector/holder
namespace guards must also pass; controller success alone is insufficient.
Preserve18s/10s/28s/30s/lease,75s main/full45s cleanup/120s total and exact
ownership. A numeric TCP/TLS pass plus independent cleanup would still only
support the previously held, separately reviewed web-bootstrap/native step.
No actual web/HTTP/auth/Native success or final PR approval is implied.

## Required offline counterexamples before execution

| Input/condition | Required behavior |
| --- | --- |
| Same zero offsets/small skew but different identities | Existing guard still rejects |
| Missing daemon/own-container runtime fields | Unknown, no guessed selection |
| Own controller name/Id/image does not match this invocation | Reject inspect evidence; never inspect an unrelated container |
| Alternative not listed, or effective runtime already that candidate | No automatic profile trial |
| Candidate changes required isolation or requests privileged/host PID/network | Reject the candidate scope |
| Candidate identity mismatch, bool/invalid clock or skew beyond250ms | Refuse; no arm or numeric call |
| Read budget exhausted | Stop unknown; no extra capture/deadline extension |
| A successful clock-only observation | Evidence only; no automatic numeric replay |

This document retains the conditional future runtime proposal. The companion
metadata source now implements the corrected bounded capture; its exact SHA
still requires independent execution review. Source preparation and
future execution dispositions are separate; all consumed invocation and
DN12/Node/BPF/auth/state/PVC-content/API-enable/Native/turn/publication holds
remain. This proposal itself authorized no capture or profile/guard change.

The separately approved4e0afc8 metadata capture has now been consumed. Its
terminal runtime-name framing is replayed offline by the corrected parser;
the original unknown result is retained. Default and effective runtime both
read runc while the original namespace guard still rejects. Do not repeat a
capture for this parser fix or choose runc automatically. The conditional
next placement assessment is in ACCESS-CLOCK-PLACEMENT-PROPOSAL.md and requires
existing operator launch records and separate scope review before any action.
