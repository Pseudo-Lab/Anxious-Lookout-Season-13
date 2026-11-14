# #90 chat stream — development VM `anxious-dev` procedure

Issue: https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/90.

- **Source**: `IMAGE_SHA`, the reviewed code commit given in the review/PM message. It is not `27973da`: the API evidence-log fix and P5-0 (default off) come after it. Design r3, contract v1.1.
- **Roles**: back writes this file; review checks it and its hash; PM runs the commands in its session.
- **On error**: a nonzero rc is reported, never re-run.
- **Turn budget**: phases P0–P4 use **no Codex turn**. P5 needs the user's turn budget first and is not run before that.

All guest commands run as root inside the guest. Chain time-coupled commands in one ssh command (relay gaps).

**Executable blocks (r3, PM 2026-11-14)**: the commands PM runs are reviewed scripts in `infra/hosting/m3/chat-stream/vm/`, delivered in stages. Each stage's pins exist only after the previous stage's output, as in #68 (`native-shell/vm/`):

| Stage | Script(s) | Runs where | Status |
|---|---|---|---|
| A | `i90-look.py` (P0 remainder), `i90-host-build.sh` (P1 build) | guest / development server | this revision |
| B | `i90-import.sh` (P1 import, pinned to the stage A build output) | guest | after the build output |
| C | `i90-switch.py` (P2: api image + runner binding, survey → expected file → precheck → run) | guest | after the import |
| D | P3/P4 blocks (L measurement on the stopped volume; API flag; P4-0..4 with review's client; session) | guest | after P2 |

Guest layout (the native68 pattern):
- evidence and private files: `/root/issue90/` (root 0700). P0 is already there in `/root/issue90/p0/`.
- archives copied from the development server: `/home/opc/issue90-images/`.
- the stage scripts: `/root/issue90/`, each checked against its reviewed sha256 before it runs.

## P0 — read-only inventory (before anything changes)

Done by PM (2026-11-14): the api and runner-controller Deployments, the full image list, no `RESEARCH_STREAM_ENABLE`, no runner Pod.

**Remainder**: `python3 -I -B /root/issue90/i90-look.py | tee /root/issue90/p0/look.txt; echo "rc=${PIPESTATUS[0]}"` (bash).
- It prints, read only: every registry row joined to its binding (fence, `plan.image`, `oldPlanSha256` = the tool's plan digest, pending work, proofs); the totals (open stops/entries/outbox/admissions, live sessions, `running` conversations); client sessions; both Deployments (UID, replicas, each container's and init container's name, image, env names, and the values of only the stream/idle/allowlist names); runner Pods; the `anxious-dev-runtime`/`anxious-dev-runner` image rows.
- Its SQL runs inside `BEGIN READ ONLY` as postgres in `postgres-0` (pa-switch.py `rows`).
- If `running` is not 0, stop here.

## P1 — build on the development server, import into the guest

**Build (stage A)**: on the development server, in this repository's checkout, never sudo:

```sh
infra/hosting/m3/chat-stream/vm/i90-host-build.sh "$IMAGE_SHA" /home/infmedix/issue90-build-$(date -u +%Y%m%dT%H%M%SZ)
echo "rc=$?"
```

- **What it is**: `native-shell/vm/pa-host-build.sh` (reviewed) with only listed changes (its header names them; `config_tests/test_i90_vm.py` checks every changed line).
- **Context**: `git archive "$IMAGE_SHA"`, never the working tree.
- **Output**: both images and their archives (`anxious-dev-runner:90-<short>`, `anxious-dev-runtime:90-<short>`), the runner gate and content checks, and the runtime's `/app/release.json` sha.
- **Send back to back**: the final lines (host ids, archive sha256s) and the output directory.

**Evidence directory (once, before stage B; r4)**: `i90-import.sh` and `i90-switch.py` (stage C precheck) both
refuse rc 3 while `/root/issue90/evidence` is missing, and neither creates it. Before the first stage B run, in a guest
root shell, once (the block review cleared on 2026-11-14 after the first import refused rc 3 with nothing imported):

```sh
set -eu
[ "$(id -u)" = 0 ]
[ -d /root/issue90 ]
[ ! -L /root/issue90 ]
[ "$(stat -c '%u:%g:%a' /root/issue90)" = '0:0:700' ]
[ ! -e /root/issue90/evidence ]
[ ! -L /root/issue90/evidence ]
install -d -m 0700 -o root -g root /root/issue90/evidence
[ "$(stat -c '%u:%g:%a' /root/issue90/evidence)" = '0:0:700' ]
stat -c '%u:%g:%a %n' /root/issue90/evidence
```

- rc 0 and the last line `0:0:700 /root/issue90/evidence` → stage B may run. Anything else: stop and report. An
  existing path, a symlink, or a parent that is not root:root 0700 is never changed or reused by this block.
- Run it once only: the directory then stays for stage C, whose precheck also needs it with no earlier
  `i90-switch-*` entry.
- r5: every check is its own simple command. `set -e` does not stop on a failed left side of `A && B`, so the r4
  form (`[ ! -e … ] && [ ! -L … ]`) let an existing directory reach `install` (its mode changed, rc 0; review
  reproduced it). The 2026-11-14 run was on an absent path and is not repeated.

**Import (stage B)**:
- Copy the two archives to `/home/opc/issue90-images/` (the native68 path pattern). Check the evidence directory
  above. Then run `i90-import.sh` (pa-import.sh pattern, both archives).
- It checks the chain: source sha → host id → archive sha256 → import digest → CRI id = host id, then the digest reference.
- back writes it with the stage A output pins; review clears it by hash.

## P2 — deploy with both flags off (stage C, `i90-switch.py`)

**Correction (r3)**: earlier revisions said to render `infra/hosting/render.sh` and apply Deployment `api`. On this VM that would replace the live api spec: it has an init container (`runner-pool-input`) and sets its env directly (no envFrom, P0), and `render.sh` produces neither. **`render.sh` is not used.**

`i90-switch.py` follows `native-shell/vm/pa-switch.py` (reviewed): survey → `i90-expected.json` (written by back from the survey output only, cleared by review by hash) → precheck → run. The run does:
1. **Window**: runner-controller and api scaled to 0 by UID-preconditioned patch, every owner `idle_stopped`, no runner Pod, no client session.
2. **Runner binding**: `ops.runner_binding_image` report → apply for the owner(s) the expected file names. `newImage` is the stage B runner digest reference; the fence and `oldPlanSha256` come from the survey.
3. **api image**: an image-only, UID-preconditioned change of the api Deployment. Only the containers that run the runtime image move to the stage B runtime digest reference; nothing else in the spec changes, and `RESEARCH_STREAM_ENABLE` is not set.
4. **Reopen**: api to 1 (`/api/health` 200, `/api/version` = `IMAGE_SHA`), then runner-controller to 1 (its image unchanged), then an observation period with no unexpected runner Pod.

It also keeps pa-switch.py's precheck rc 3, forward report, in-window undo, FREEZE and evidence rules.

**Install (r5)**: after review clears the pinned `i90-switch.py` and `vm/i90-expected.json` by hash, PM installs each
with `vm/i90-install.sh` (as root, the script as the argument of `sh -c`, stdin = the file, `$1` = its guest name,
`$2` = its reviewed sha256). It writes `/root/issue90/<name>` root:root 0600 through a checked temp file; the survey
copy of `i90-switch.py` already there is kept as `i90-switch.py.r0`, never overwritten. A nonzero rc is reported, not
re-run. Then `precheck <expected sha256>` (read only), and `run` only after its rc 0 and PM's go.
- The runner-controller image stays as it is for P0–P4. With both stream flags unset, `IMAGE_SHA` renders a runner Pod exactly as the current controller does (P5-0 adds keys only for `true`; `runner_controller.py` is unchanged since c50c41c), and the new API accepts that Pod. The controller moves to the same runtime image only at P5, with the flags (P5-0 below).

**Regression checks (flags off, no turn; stage D block)**:
- `/api/health` and `/api/version` show `IMAGE_SHA`.
- `/api/research/codex/status` keeps its earlier shape. Prepare one runner entry (the existing prepare path) and check it reaches `available: true` (an explicit signal, not an empty log).
- `GET /api/research/sessions` and `GET /api/research/sessions/{id}` give the same items as before for an existing conversation.
- `GET …/events` answers 404 `stream_disabled`, and `POST …/interrupt` answers 404.
- The browser session follows `infra/k3s/DEV-VM.md` (token file in the guest, `session_digest` computed in the API image); the stage D block creates and removes it.
- **Sending a message is a Codex turn**: it is in P5 (S0), not here.

**Stage D1 (r6)**: `vm/i90-d1.py` does the list above (`check` read only; `run` once as a systemd unit, installed
with `i90-install.sh`). Its order, rc table and evidence (`/root/issue90/evidence/i90-d1-<UTC>/`) are in its
docstring; the stage C facts (images, the five other owners) come from `i90-expected.json` at its reviewed hash.
- The runner entry is generation 30 of …5805 on the stage C runner image; ready = `/codex/status` `available: true`,
  `reason: null` at the read +65 s after the container start (reads at +5/+35/+65 s). It is then stopped with
  `DELETE /codex/runner` (the stop is sent also when the start check fails) and must be released by one completed
  stop with drain receipt 1.
- Cleanup (r7, review 518c088): after the entry, any exception the process can handle in the start check still sends
  the stop (rc 5); only a second interrupt does not (rc 130, the runner left to its idle stop). The session revoke
  runs in a `finally` and never raises; an evidence write failure is remembered (rc 9 when every step passed, also
  for out.txt or exit.json after the result line, r8: the last `result rc=` line is the result).
- "The same items as before": no GET was recorded under the old API before stage C, so D1 checks the shape and
  records each of the three newest conversations' item count and items digest. Those are the baseline P4/P5
  compare against; an old-vs-new comparison is not claimed.

**Rollback**: `i90-switch.py`'s reverse path. The runner binding goes back to its P0 `plan.image` (report/apply, `idle_stopped`), and the api containers go back to their P0 images (the same image-only change). Nothing in the DB schema changes in #90, so no data step.

## P3 — L measurement (read only, no turn)

L is the longest recorded secret literal of a runner's redaction journal. Streamed text waits L−1 chars (design r3 B1).

The stage D block does this; the steps it takes:
1. For an owner whose runner is in `idle_stopped` (no Pod), the control PVC name is the binding plan's `control.name` (the plan in P0's `look.txt`). Its PV path: `kubectl -n codex-runner get pvc <name> -o jsonpath='{.spec.volumeName}'`, then `kubectl get pv <pv> -o jsonpath='{.spec.local.path}{.spec.hostPath.path}'`.
2. Run `journal_lengths.py` (its reviewed sha256 checked first) on that directory, read only, in a throwaway container with no network, a read-only root, all capabilities dropped and the PV mounted read only. The block names the exact container image and command.

- **Output**: one JSON line `{"ok", "literals", "L", "holdbackChars", "lengths": {length: count}}`. No literal, digest, filter or MAC.
- **Exit codes**: 2 = refused input (missing, symlink, non-regular file, hot `-journal`/`-wal`), 3 = database error. Report the rc, do not retry.
- **Read only**: lstat checks before any open, and `mode=ro&immutable=1`. Tests: `config_tests/test_journal_lengths.py` (unchanged files, refusals).
- If L is large (hundreds of chars or more), the prefix optimization is decided as a separate design inside #90 (PM decision 2026-11-14).

**Stage D2 (r7)**: `vm/i90-d2.py measure` (read only, after D1; writes nothing on the guest) does steps 1–2 for
…5805 and adds what the review minor asked: the plan's `control` `{name, uid, volume}` must match PVC uid, PVC
Bound to that PV, and the PV's claimRef (namespace, name, uid); …5805 idle_stopped with no Pod and no runner Pod.
The container is the guest Docker tool image `i90-expected.json` names (`toolImage`), `--network none --read-only
--cap-drop ALL --security-opt no-new-privileges --security-opt label=disable --user 10001:10001`, the PV path read
only at `/control`. `journal_lengths.py` (installed root 0600, its sha256 pinned) is not mounted: the exact hashed
bytes go to `python3 -I -B - /control` on stdin. `label=disable` instead of a `:z` relabel, which would write the
volume's SELinux labels. rc in its docstring (0 measured, 2 input refused, 3 refused before, 4 database error, 80
container not proved gone).

## P4 — runner and API shutdown with a real runner (no turn)

These need **`RESEARCH_STREAM_ENABLE=true` on the api Deployment** (a state change, PM decision). `RUNNER_STREAM_TEXT` stays off.
- An idle stream carries `runner`/`idle` frames and heartbeats only, so no Codex turn is needed.
- review writes the measuring client: it holds an `/events` stream open and records when each frame arrives and when the stream ends.

Client: review's `artifacts/review/issue90/p4_client.py` (sha256 `e185f7f9d4fd396eaa17ebad41649a776221903be632ef1a0bdf6b4d01c1c877`, usage in `P4-CLIENT.md`).

**Evidence, kept apart** (review 70e92e7):
- **Runner side**: the drain receipt, Pod deletion time, runner log `Application shutdown complete`.
- **Browser stream**: the frames the client saw, and when it ended.
- **API slot**: the `api_stream_summary` line the API writes when a stream's slot is released (one line per closed connection, `reason` included).

After the runner stops, the API relay is **expected to stay open**. It sends `runner{available:false}`, then waits in its not-ready loop (up to `NOT_READY_LIMIT_SECONDS`, 600 s) for a runner to come back. The browser stream ending is therefore **not** a pass condition. The client closes it explicitly once it has seen `available:false`.

| # | Action | Pass |
|---|---|---|
| P4-0 | Slot baseline: the same user opens 3 streams (the per-user cap); a 4th answers 429 `stream_limit`. Close one from the client, wait for its `api_stream_summary` line, then open again | 4th refused while 3 are open; one `api_stream_summary` per close; a new stream accepted after the line |
| P4-1 | Reading stream open; stop the runner with the existing path (`DELETE /api/research/codex/runner`; runner-controller drains) | Runner side: drain receipt `clean: true`, Pod gone within its 20 s grace from the stop. Browser: `runner{available:false}` arrives (the stream itself may stay open). Then the client closes; API: one `api_stream_summary` for it |
| P4-2 | As P4-1, by idle stop (`CODEX_USER_RUNNER_IDLE_SECONDS`, 1800 s unless PM lowers it for the test) | As P4-1 |
| P4-3 | Stream open with a reader that stopped reading (`SO_RCVBUF` small, no reads); `kubectl -n m2-hosting rollout restart deploy/api` | Old API Pod terminated well inside its grace (pod timestamps); API log has no lasting `Waiting for connections to close`; the new Pod serves `/api/health`. The client sees a local deadline or reset, not a clean SSE end; that is expected |
| P4-4 | P4-1 with a reader that stopped reading | Runner side only (the client cannot see frames): drain receipt `clean: true`, Pod gone within 20 s, runner log `Application shutdown complete`. After the client closes: one `api_stream_summary` |

Not covered here, because it needs event volume and therefore a turn: slow reader → relay 10 s stall → runner connection closed inside the runner's 20 s grace. That is S6 in P5.

**Stage D3 (r9)**: `vm/i90-d3.py` (`check` read only; `flag-on`, `p4`, `p4-2`, `flag-off` once each as systemd
units, each on its own PM go; order and rc in its docstring and the D3 sheet, which also gives the rc → `flag-off`
table).
- `flag-on` / `flag-off`: one UID/resourceVersion/container-name tested JSON patch of the api Deployment, applied to a
  local copy first and sent only when the result has the wanted template hash. `flag-on` records the template's
  `kubectl.kubernetes.io/restartedAt` state (value or absent); `flag-off` removes the env entry and **restores that
  state** (removed only when it was absent), on a compare-and-set of the value P4-3 left (recorded by `p4`), and prints
  `api template sha256 after flag-off: …; stage C apiTemplateSha256: …; equal: …` (PM/review D3).
- Client: review's `p4_client.py` (pinned), standard library only, in throwaway containers of the guest Docker tool
  image (`toolImage`; the stage C runtime image is in k3s containerd only), `--network host`, `--connect-address
  10.0.0.107` with `Host: 192.88.99.1` (nothing is sent to 192.88.99.1). Client copy and Cookie value 0400 owned by
  10001 in a private directory removed at the end.
- Each P4 step ends with every client proved gone and every log follower stopped; summaries are counted per step in
  that step's own window. Every action prints `verdict P4-n: True|False|unknown` and `verdict overall pass:` (false on
  any False or unknown) and the flag state at the end.
- P4-2 and the idle clock: the registry's `last_activity` changes on an entry, a message and a turn completion
  (`runner_registry` / `lifecycle`); the runner's `idle_since` at app start and turn start/end. A `/codex/status` read
  or an SSE stream (heartbeats) touches neither, and `p4-2` makes no status read after the three ready reads. The
  release time after the entry is reported; release outside [idle, idle + 600 s] is a False verdict and a finding.
- r10 (review 9e6704e request-changes, PM stop policy 2026-11-14):
  - `check` and every mutation enforce their conditions (rc 3, nothing changed): `check` / `flag-on` the stage C
    baseline (UID, 1/1, image, no flag, stage C template, …5805 idle_stopped, no runner Pod, no live session);
    `p4` / `p4-2` the same with the flag; `flag-off` UID and image (its patch is hash-checked).
  - `flag-on` saves the restartedAt baseline durably **before** the patch (else rc 3, nothing sent); the value P4-3
    leaves is printed and saved; if that save fails the action stops with rc 9 and `flag-off` refuses until back
    records it.
  - Required evidence per step: P4-1/P4-4 the stream at 200, (no-read) `reader_paused`, the client still running at
    the stop; P4-3 the same before the restart (else no restart, False), then the old Pod gone inside its grace, its
    log captured, exactly one summary. Reference only: `Waiting for connections to close` lines.
  - **Stop policy**: in `p4`, a False or unknown step ends the action after its own cleanup; no later client, runner
    entry or API restart; `stopped at:` and `verdict overall pass: False`.
  - Cleanup results decide the final rc after the step rc (kept as `stepRc`; 7/8 stay): runner release not proved 6
    (the cleanup sends the stop and proves the release), a client container or the private files left 80, the
    session revoke 70, evidence 9 - each whatever the step rc (r11, review 65291e6: a live session is never hidden
    behind a measurement failure). `verdict overall pass` is computed after them. r12 (PM: one rule for every exit
    path): `final_rc()` alone decides the final rc - normal end, step failure, exception, signal - and is applied
    again after the last writes (out.txt's result line, exit.json); a changed code prints a last `verdict overall
    pass: False` and `result rc=`. A signal inside the cleanup never skips the rest of it. p4 / p4-2 also count any
    leftover `issue90-i90-d3-*` container (the probe included) as 80.
  - r13 (D3-d 2026-11-14: P4-0 False from a parser defect): `kubectl logs --timestamps` prints the node's local offset
    (+09:00); the window compared text against UTC and dropped every line. Summary lines are now compared as instants
    (RFC 3339 with Z or ±HH:MM) and their payload parsed as JSON (`session` exact, `reason` may be null). A one-time
    `p4-rerun` (own evidence directory and guard: only after exactly one `p4` that ended rc 0 without a pass; the
    earlier evidence is kept) repeats P4-0..P4-4; `flag-off` takes the restartedAt value of the newest of p4 /
    p4-rerun.
  - The client containers run with `--security-opt label=disable` (as D2); before any runner start a probe container
    with the same image, user and mounts must read both private files (else rc 5, nothing changed).

## P5 — measurements with real Codex turns (only after the user's turn budget)

**Prerequisite P5-0 (code, separate review)**: the runner Pod env must be able to carry `RUNNER_STREAM_TEXT=true` and `STREAM_TRACE=true`.
- **What checks the Pod env**: `runner_kube.py` renders a fixed env, and its Pod verification (`KubePods._pod`) requires the live Pod spec to equal the render exactly. The runner admission draft (`user-runner-policy-draft`) is gated and not applied. An earlier version of this file said "the runner policy checks it"; the check is the code.
- **The change** (its own commit):
  - `CODEX_RUNNER_STREAM_TEXT` / `CODEX_RUNNER_STREAM_TRACE` (`true`/`false`, default `false`; anything else refuses) in the process env of runner-controller and API add exactly those keys with `"true"`.
  - By default the render is byte-identical to before.
  - `_pod` also accepts a Pod rendered under the other flag state (only the exact `"true"` keys may differ), so a flag change never strands a running Pod. Every other env difference is still refused.
- **Turning text on (P5, after review)**: move the runner-controller Deployment to the `IMAGE_SHA` runtime image (it renders the Pods), then set `CODEX_RUNNER_STREAM_TEXT=true` (and `CODEX_RUNNER_STREAM_TRACE=true` for S4) on **both** the runner-controller Deployment and the api Deployment, and wait for both rollouts.
  - A runner Pod created after that has the keys; one created before keeps working without them until its next entry.
  - Off again: set them back to `false` (or remove them) the same way. `RESEARCH_STREAM_ENABLE=false` on the API alone already closes every browser stream.

| # | Purpose | Turns | Notes |
|---|---|---|---|
| S0 | Regression: a normal send with both flags off (record, tool callback, refresh) | 1 | Can run right after P2 |
| S1 | V1 live item ids vs `thread/read` ids; first text time; `runner_stream_summary` incl. `legacyL` (= L from the live journal) | 1 | text on |
| S2 | V3 delta methods and the tool items code-mode actually emits (a turn that runs one shell command with output) | 1 | text on |
| S3 | V2 Stop mid-answer: partial answer in `thread/read` or display-only; `turnStatus: interrupted`; GET = resume record | 1 | text on |
| S4 | D7 per-hop delay (① API Pod→runner, ② API Service, ③ Traefik `web`, ④ host forward) on one long answer; `STREAM_TRACE` | 2 | text on; repeat once for spread |
| S5 | §8 large command output (output caps, event count/bytes) and reload mid-turn (snapshot → continue) | 1 | text on |
| S6 | Slow reader with a real runner: browser stops reading mid-answer → relay stall 10 s → runner connection closed → runner SIGTERM/drain completes inside 20 s grace | 1 | text on |
| | **Total** | **8** | PM's suggested cap was 10 |

- DB writes (S1, S4, S5): `pg_stat_force_next_flush()` before and after, `research.conversations` `n_tup_upd`/`n_tup_ins`, with no other activity on the VM.
- Refresh UPDATEs are counted from the API log `conversation_refresh_write`.
- Production TLS/h2 is not covered by any of this.
