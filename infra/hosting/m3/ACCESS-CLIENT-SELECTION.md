# Issue #7: bounded client-selection collector

Run only after independent exact-source/argv review and PM disposition, once
from the same trusted root operator launch environment. This is a separate
client-only observation: no controller/clock capture or daemon query.

```bash
/bin/sh /reviewed/checkout/infra/hosting/m3/run_client_selection.sh \
  /reviewed/checkout /usr/bin/docker /private/client-selection-NEW EXACT_REVIEWED_40_HEX_SHA
```

Use a clean exact-SHA checkout, trusted executable absolute Docker CLI ending
in /docker, and new empty caller-owned0700 directory. The launcher must be a
noninteractive POSIX-shell invocation as shown. Do not run the internal Bash
worker directly or source either script into the caller. Existing Docker
selection variables/config are retained; there is no --host/--context override.

The launcher detects inherited xtrace/verbose, trace/debug shell options and
nonempty BASH_ENV/ENV before reading any selection values. POSIX noninteractive
startup does not evaluate those Bash startup hooks. The internal Bash worker
starts with only shell startup/option-control variables removed, retaining
DOCKER_CONTEXT/DOCKER_HOST/config selection unchanged. Unsafe launch conditions
yield unknown/unsafe_launch without a CLI selection read. Exported SHELLOPTS/
BASHOPTS presence also refuses, including when POSIX Bash resets an imported
option. Values are discarded by presence checks, never captured. Outer timeout covers
startup/git/filesystem checks as well as the collector; no raw hook contents
are saved. Caller-side hooks before process launch are outside this collector.

One absolute3s BOOTTIME deadline covers startup, reads, parsing and evidence
writes; outer timeout reserves0.25s kill grace within it. Per CLI producer gets
at most0.75s+0.25kill, tightened to remaining time. Coproc startup is gated until
owned child PID/FDs are captured. Read at most129 name bytes or16 enum bytes
plus one overflow-detection byte, directly into Bash memory with preserved
LF/NUL framing. Require EOF plus producer exit0 and exactly one terminal LF.
NUL, extra lines, whitespace/invalid/oversized names, missing LF, failed/late
producer and invalid/overflow enum output remain unknown. No raw intermediate
files, shell command substitution of selection output, retry or fallback.

Classify a nonempty DOCKER_HOST only when DOCKER_CONTEXT is empty. Otherwise
read only the CLI's current context and directly formatted transport of that
one captured name. A nonempty DOCKER_CONTEXT must match the captured CLI name
exactly; disagreement stops unknown before inspect. Context names remain
ephemeral and quoted; no enumeration or different context lookup occurs.
When DOCKER_CONTEXT=default and DOCKER_HOST is nonempty, matching names do not
establish the endpoint-selection mechanism: refuse unknown/ambiguous_default_context
before inspect rather than assign a source or transport from that equality.
Exported DOCKER_TLS/DOCKER_TLS_VERIFY/DOCKER_CERT_PATH presence refuses before
any CLI command (tls_setup_outside_scope); values are discarded, not captured.
This conservative refusal prevents default-context initialization from loading
certificate material. No certificate/config file is opened by the collector.

The isolated actual CLI counterexample returns default when both variables
are nonempty, contrary to the proposal's documented-priority assumption. The
collector refuses that disagreement instead of applying an override or
reporting the different context as selected. It is not an observation of
production selection. Docker's compatibility branch is visible in the
[official CLI source](https://github.com/docker/cli/blob/master/cli/command/cli.go);
the [CLI reference](https://docs.docker.com/reference/cli/docker/#environment-variables)
states the documented priority. Neither source establishes daemon placement.
The tested mounted binary is Docker29.6.1 build8900f1d, SHA256
60f19de561e759ac14391332aacf8c10f5f9a3e53b691bdf3e1af4149bdb158e.
Tests pin/report that identity in isolation; upstream master is corroboration
of the branch, not proof of the installed build or production behavior.

Only budget.json, result.json and complete.json are written. Result contains
the fixed schema from ACCESS-CLOCK-CLIENT-SELECTION-PROPOSAL.md: selection
source, two nonempty flags, transport/status, fixed reason; placement ALWAYS
unproven, action authorization ALWAYS false, controllerCreated=false and
daemonQueries/kubernetesCalls0. It contains no endpoint/name/config/TLS/proxy/
credential value. CLI stderr is discarded. All files are private and bounded
by fixed fields (<4096 bytes). No new resource requires a stop/delete.

Require process exit0, complete.completedCs < complete.deadlineCs matching
budget.deadlineCs = budget.startCs+300. Missing receipt or late/partial output
means the whole result is unknown, regardless of an intermediate field. Exit0
means bounded observation completion; result.status can still be unknown.

Offline tests run inside network-none root Docker with no daemon socket. Real
Docker CLI is mounted read-only; client configurations are fresh test-owned
files only. Actual CLI/template tests cover four schemes, missing/empty endpoint,
selected/default/missing contexts and both-variable disagreement. Substitutes
cover raw framing/NUL/overflow, producer exit/timeouts after valid prefix,
startup/remaining-deadline exhaustion and unique-sentinel non-disclosure on
stdout/stderr/evidence, including unsafe launch paths.

No guard/profile/environment selection changes, new actor/clock capture,
numeric replay, API-enable/auth/state/PVC-content/Native/turn or publication.
Connection selection is not physical location or authentication verification.
Authoritative launch/runtime provenance and a separate reviewed candidate are
still required before any corrective placement action; no automatic next step.
