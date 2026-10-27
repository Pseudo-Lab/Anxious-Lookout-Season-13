# Shell-surface A/B results (#64)

**Decision (user, 2026-10-28, relayed in #64):** adopt **B**, the native shell middle option. The follow-up
implementation is #68 (`task/native-shell-runner`). The full run **ended at 5 of 60 slots** and will not resume.
That leaves 7 graded runs: 4 from the pilot and 3 from the full run. A sample this small cannot support a
general performance claim. The decision is the user's, not the result of a statistical test.

Setup (see README):
- the same model (`gpt-6.1-sol`), Codex 0.160.1, prompts and fixtures;
- A runs the current runner surface: MCP shell bridge → ShellBroker → executor, read-only sandbox, 30 s and 64 KiB per command;
- B runs native shell, `apply_patch` and unified_exec in `workspace-write` (bubblewrap).

## Runs

Tokens are input / cached input (a subset of input) / output. "Tools" counts calls / failures / timeouts.
In every run, the JWT-shape check over the Pod's result and workspace paths came back clean (grep exit 1).

| # | Phase | Task | Config | Run | Graded | Wall s | Requests | Tools | File changes | Tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | pilot | fix-failing-test | A | `d785cc85` | **fail** (failures=2) | 20.4 | 6 | 3 / 2 / 0 | 0 | 68,211 / 55,040 / 368 |
| 2 | pilot | fix-failing-test | B | `187c51a6` | **pass** | 16.8 | 4 | 4 / 3 / 0 | 1 | 43,277 / 39,936 / 295 |
| 3 | pilot | slow-suite | A | `a5a3a70d` | **fail** (failures=1) | 57.2 | 6 | 4 / 2 / 1 | 0 | 72,005 / 62,336 / 350 |
| 4 | pilot | slow-suite | B | `e47d01f5` | **pass** | 106.0 | 15 | 5 / 2 / 0 | 1 | 182,502 / 156,928 / 908 |
| 5 | full, slot 1 | fix-failing-test | A | `271143aa` | **fail** (failures=2) | 27.5 | 5 | 3 / 2 / 0 | 0 | 54,459 / 42,112 / 293 |
| 6 | full, slot 1 | fix-failing-test | B | `1efbbe55` | **pass** | 19.6 | 5 | 5 / 2 / 0 | 1 | 54,778 / 42,752 / 321 |
| 7 | full, slot 1 | slow-suite | A | `dd3fbd74` | **fail** (failures=1) | 70.2 | 8 | 5 / 3 / 1 | 0 | 91,290 / 66,304 / 451 |

**Infrastructure failures (not model results):** both were refused with 429 before any model response.

| Slot | Run | What happened | Result file | Ledger |
|---|---|---|---|---|
| full slow-suite B 01 | `aa5e4cf7` | 429 Too Many Requests, "exceeded retry limit", within 0.5 s. No usage event, no assistant message. | `turnStatus failed`, `settled false`; the untouched fixture graded `failures=1` | STOP 1 |
| full fix-failing-test A 02 | `deca9f0e` | the same 429, 9 min 15 s after the previous campaign request. No usage event, no assistant message. | `turnStatus failed`, `settled false`; the untouched fixture graded `failures=2` | STOP 2 |

- Each unconfirmed run stopped the campaign at the gate, as designed.
- Each was settled at zero for that run only, after review read the rollout. The reason recorded in the ledger is: "PM-authorized zero accounting assumption; review verified 429/no assistant/no usage events; provider usage unconfirmed".
- The provider never confirmed the zero.

## Summary

| | A | B |
|---|---|---|
| Graded runs | 4 | 3 |
| Passed | **0 / 4** | **3 / 3** |
| 429 infrastructure failures | 1 | 1 |
| Wall s (min / median / max) | 20.4 / 42.4 / 70.2 | 16.8 / 19.6 / 106.0 |
| Input tokens (min / median / max) | 54,459 / 70,108 / 91,290 | 43,277 / 54,778 / 182,502 |
| Output tokens (min / median / max) | 293 / 359 / 451 | 295 / 321 / 908 |

**Usage totals:**

| Ledger | Input | Cached | Output |
|---|---|---|---|
| Pilot | 365,995 | 314,240 | 1,921 |
| Full run (5 slots) | 200,527 | 151,168 | 1,065 |
| **All** | **566,522** | **465,408** | **2,986** |

The full-run caps were 8M input and 60k output, with 4M / 30k per configuration.

## Failure patterns (observed, not root-caused)

- **A never changed a file.** All 4 A runs have 0 file changes, and none of A's recorded commands writes anything: they are test runs, `rg`, `find` and `cat`. The turns completed with the tests still failing. A has no native edit tool in its read-only sandbox, and the harness doesn't record the agent's messages, so this report can't say why A stopped short.
- **A timed out on slow-suite.** Both A slow-suite runs hit one shell timeout. The suite takes about 45 s, and the executor allows 30 s per command. That limit is part of A, the current surface, not an artefact of the harness.
- **B made exactly one file change per run and passed.** Tool failures in B (2–3 per run) are commands with a non-zero exit, such as the first test run against the failing fixture. Exits are not recorded per command. B's slow-suite pilot run (#4) was the most expensive run: 15 requests and 182,502 input tokens.

## Limits

- **7 graded runs:** 4 for A and 3 for B, on 2 tasks. Ending at 5 / 60 removes the planned 15-repeat distributions.
- **Two zero settles are assumptions:** the provider did not confirm the usage of either 429 run.
- **The 429 cause is unknown.** The user's most likely explanation is concurrent use of the same account on another device, with no 429 seen elsewhere. That is not confirmed.
  - Two model-free `account/rateLimits/read` reads (09:39Z and 09:47Z) showed the weekly window at 10%, resetting 2026-11-02T06:14:33Z, with no shorter window.
  - The 429 responses left no `Retry-After` or `x-ratelimit-*` headers in the Codex homes.
  - The 60 s slot gap (`513fb45`) was a mitigation attempt. It did not prevent the second 429.
- **Known limits of the environment:**
  - B reads the access file;
  - the JWT check is a pattern match;
  - a turn interrupted after a token event can exceed a share.
  - B ran under SELinux `spc_t`. Production would need its own policy (#68).

## Cleanup (2026-10-27 UTC on the development VM)

1. **Results exported.** The ledgers, result JSONs and console output were checked inside each Pod (`grep -rlE 'eyJ…\.eyJ' /pilot/results`: exit 1), copied out, and checked again locally (exit 1). The sha256 of every JSON matches in the Pod and in the copy. Nothing from `CODEX_HOME`, the sessions or the tokens was exported.
2. **`codex logout`** in `pilot-login` (16:23:17Z): rc 0. `auth.json` was present before and absent after.
3. **Secret `pilot-access` deleted** (rc 0). The namespace then listed no Secrets, and the projected `/access/access.json` was gone from pilot-a and pilot-b by 16:24:19Z.
4. **Namespace `shell-full` deleted** (UID `2388808f-…`).
   - PV `pvc-8a2a0537-3709-4453-a649-2e9f73d0d200`: NotFound.
   - LVMVolume: absent.
   - `lvs openebs-vg`: the LV is gone. The preserved LVs `617614d2…`, `pvc-2e293117…` and `pvc-57329fcd…` (broker) are unchanged.
   - The namespace object stays Terminating with all content removed and no finalizers left (`NamespaceDeletionDiscoveryFailure`: stale `metrics.k8s.io/v1beta1` discovery), like `shell-pilot`. It was not force-finalized.
5. **Server-side revocation:** not proven separately. The logout cleared the local login. The user chose not to sign out all devices, so there is no proof beyond the logout's rc 0.

**Kept for #68 on the development VM:** `kernel.yama.ptrace_scope=1`, the seccomp profile `shell-pilot-userns.json`, and the `back58-agent-b` image.
