# Issue 16 cutover — PM execution after source and operational review

Use [rollout/README.md](rollout/README.md) for the #18 values schema, offline renderer,
pool API patch, RBAC replacement, quota, worker units and shared-mount/restore setup.
This supplies the installation artifacts referenced below; it does not execute D4.

This is the ordered procedure for the approved source, not evidence of a deployment.
PM builds/imports committed artifacts and executes operations; back does not change
live state. A source `approved:` gate, PR creation, live authorization, operational
acceptance and human merge are distinct decisions. Record failures as failures and
stop at the affected gate. Never reset runtime UID/phase/ledger or recreate retained
storage to make a step pass. Use the linked documents for exact tool inputs:
[provisioning](PROVISIONING.md), [termination/recovery](TERMINATION.md),
[host supply](HOST-SUPPLY.md).

## 테스트 수준 경로 — D4, 이번 cutover의 기준

D4(2026-10-16)는 기존 운영자 runner 상태와 데이터를 폐기하고 운영자·편집자
모두 같은 신규 준비 경로로 전환합니다. **이번 실행에는 legacy binding import,
legacy recovery report/apply, binding-image 교체, 운영자 uncapped 예외를 사용하지
않습니다.** 아래의 보존형 5·6단계, 복원 검증·rollback 리허설은 **운영 전환 시**
절차이며 D4/PR 승인 조건이 아닙니다. 두 owner 전체 E2E도 PR gate로 재도입하지
않습니다. 구현 도구와 보존형 문서는 향후 용도로 그대로 유지합니다.

실행 순서는 **대상/백업 기록 → mutator stop → 삭제 → migration/grants → 새 이미지
배포 → pool API·설정·worker 설치 → 사용자 계정 canary → 대상 확대**입니다.
Migration에 쓸 새 worker artifact는 공통 2단계대로 사전에 build/import합니다.
PM의 요청대로 삭제는 반드시 백업 후 실행합니다. 백업 이후 변경이 있었다면 중지
시점의 DB/파일을 추가 백업합니다. D4는 별도 복원 리허설을 요구하지 않지만, 백업
명령 성공·파일 존재/크기·digest는 기록합니다. 다음은 PM이 실행할 명령이며 back은
실행하지 않습니다. 기존 데이터로 되돌릴 필요가 생기면 이 백업을 사용한 별도
복구 작업으로 다루며, 삭제 후 보존형 legacy 절차를 실행하지 않습니다.

### D4-A. 삭제 대상과 중지 조건

- app account는 유지: owner `ff4b718f-de46-4c10-9195-c993eff9ded9`.
- 폐기할 기존 state: `71c84103-a643-4cfc-a3f6-ef6079e48d50`.
- namespace `codex-runner`; old Pod `runner-ff4b718fde464c109195c993eff9ded9`,
  UID `1c4552b5-bfb3-4b23-b3c3-a0cae4b72163` (마지막 보고에서는 이미 부재).
- native PVC/PV 이름 `native-71c84103a6434cfca3f6ef6079e48d50`, control PVC/PV
  이름 `control-71c84103a6434cfca3f6ef6079e48d50`. 아래 UID와 live inventory를
  대조합니다. 정확한 이름/UID가 다르면 중단하고 실제 매핑을 보고합니다.
- transport Secret `transport-71c84103a6434cfca3f6ef6079e48d50`.
- retained root **하나만**:
  `/var/lib/anxious-lookout/user-runners/71c84103a6434cfca3f6ef6079e48d50`.
- owner의 기존 대화 **2건(failed 1, idle 1)**은 archive가 아니라 DB에서 삭제합니다.
  해당 대화 ID를 참조하는 `research.idempotency.response.id` 캐시도 함께 삭제합니다.
  auth.accounts/sessions, 다른 연구 문서·자료·publication 및 다른 owner 데이터는 유지합니다.

기존 API, controller/preparation, observer/idle, supply timer와 외부 Pod creator를
중지하고 유지보수 동안 새로운 entry가 없음을 확인합니다. `codex-trial`, 다른
namespace, 정책/SA/quota 및 다른 state 리소스는 이 삭제 목록에 없습니다.
namespace 전체 삭제, label 전체 delete, DB TRUNCATE/DROP 또는 디렉터리 wildcard는
사용하지 않습니다. Pod 삭제 timeout, UID 불일치, 조회 실패 또는 old UID의 살아 있는
CRI/container/process가 보이면 중단합니다. 데이터 폐기 결정이 force-delete나
살아 있는 프로세스의 저장소 제거를 의미하지는 않습니다. 종료 증거 수집을 위해
legacy DB recovery/apply를 실행할 필요는 없습니다.

### D4-B. 정확한 Kubernetes 및 retained 삭제 명령

검토한 kubeconfig/context를 선택한 PM shell에서 실행합니다. `set -euo pipefail`을
사용하고 Secret 내용은 출력하지 않습니다. namespaced 리소스는 `-n codex-runner`로
한정하며, PV만 cluster scope입니다. UID check 뒤 삭제 동안도 모든 creator를
중지한 상태로 유지합니다.

```sh
set -euo pipefail
# RUNNER_KUBECONFIG는 검토된 기존 private kubeconfig 경로.
: "${RUNNER_KUBECONFIG:?}"
d4_delete() {
  local kind="$1" object="$2" expected="$3" actual
  actual=$(kubectl --kubeconfig "$RUNNER_KUBECONFIG" -n codex-runner \
    get "$kind" "$object" --ignore-not-found -o jsonpath='{.metadata.uid}')
  if [ -z "$actual" ]; then return; fi
  [ "$actual" = "$expected" ] || { printf 'D4 UID mismatch: %s/%s\n' "$kind" "$object" >&2; return 1; }
  kubectl --kubeconfig "$RUNNER_KUBECONFIG" -n codex-runner \
    delete "$kind" "$object" --wait=true --timeout=120s
}
d4_delete pod runner-ff4b718fde464c109195c993eff9ded9 1c4552b5-bfb3-4b23-b3c3-a0cae4b72163
# Confirm old-UID host CRI/proc absence before deleting its storage references.
d4_delete pvc native-71c84103a6434cfca3f6ef6079e48d50 7a8f701a-2fc6-4efb-a2ad-d30777d8084a
d4_delete pvc control-71c84103a6434cfca3f6ef6079e48d50 1a84b7c2-8d6a-4662-8586-f6a32e83510d
d4_delete pv native-71c84103a6434cfca3f6ef6079e48d50 a73b597f-2b23-4fcf-8923-b42601423bdb
d4_delete pv control-71c84103a6434cfca3f6ef6079e48d50 3fbe053b-5f87-49a1-8f86-7750b265e207
d4_delete secret transport-71c84103a6434cfca3f6ef6079e48d50 36458e86-7ab9-49a7-97ed-3962431b01ca
```

Retain PV 삭제는 host 파일을 지우지 않습니다. 위 작업이 성공하고 아래 DB transaction이
commit된 뒤, root host shell에서 해당 경로만 제거합니다. 이 기존 경로는 uncapped
디렉터리이며 loop volume이 아닙니다. mount가 발견되면 자동 unmount하지 않습니다.

```sh
set -euo pipefail
d4_old_root=/var/lib/anxious-lookout/user-runners/71c84103a6434cfca3f6ef6079e48d50
[ ! -L "$d4_old_root" ]
[ "$(readlink -m -- "$d4_old_root")" = "$d4_old_root" ]
d4_mounts=$(findmnt -rn -o TARGET)
if printf '%s\n' "$d4_mounts" | awk -v p="$d4_old_root" \
  '$0==p || index($0,p"/")==1 {found=1} END {exit !found}'; then
  printf 'D4: retained subtree still mounted; stop\n' >&2
  exit 1
fi
rm -rf --one-file-system -- "$d4_old_root"
```

### D4-C. DB의 owner 한정 폐기 transaction

PM이 backup한 DB의 administrator session에서 `ON_ERROR_STOP=1`로 다음 SQL을
실행합니다. 예상한 old FAILED generation1/revision8 및 대화 2건이 아니면 전체
transaction이 실패합니다. 내용을 새 baseline에 맞춰 임의로 넓히지 않습니다.
새 #16 schema가 아직 없을 수 있어 그 테이블만 존재 여부를 확인해 삭제합니다.
기존 runtime/research schema는 필수입니다. FK 자식 → 부모 순서입니다.

```sql
BEGIN;
DO $d4$
DECLARE
  target uuid := 'ff4b718f-de46-4c10-9195-c993eff9ded9';
  old_state uuid := '71c84103-a643-4cfc-a3f6-ef6079e48d50';
BEGIN
  PERFORM 1 FROM auth.accounts WHERE id=target FOR UPDATE;
  IF NOT EXISTS (SELECT 1 FROM runner_runtime.registry
      WHERE owner_id=target AND state_id=old_state AND phase='runner_failed'
        AND generation=1 AND revision=8
        AND pod_uid='1c4552b5-bfb3-4b23-b3c3-a0cae4b72163'::uuid) THEN
    RAISE EXCEPTION 'D4 baseline changed; no reset';
  END IF;
  IF (SELECT count(*) FROM research.conversations WHERE owner_id=target) <> 2
     OR (SELECT count(*) FROM research.conversations WHERE owner_id=target AND state='failed') <> 1
     OR (SELECT count(*) FROM research.conversations WHERE owner_id=target AND state='idle') <> 1
     OR EXISTS (SELECT 1 FROM runner_runtime.admissions WHERE owner_id=target)
     OR EXISTS (SELECT 1 FROM runner_runtime.outbox WHERE owner_id=target AND NOT settled) THEN
    RAISE EXCEPTION 'D4 work/conversation baseline changed';
  END IF;
  DELETE FROM research.idempotency i WHERE i.owner_id=target
    AND i.response->>'id' IN (SELECT id::text FROM research.conversations WHERE owner_id=target);
  DELETE FROM research.conversations WHERE owner_id=target;
  IF to_regclass('runner_termination.entries') IS NOT NULL THEN
    EXECUTE 'DELETE FROM runner_termination.entries WHERE owner_id=$1' USING target;
    EXECUTE 'DELETE FROM runner_termination.stops WHERE owner_id=$1' USING target;
  END IF;
  IF to_regclass('runner_bindings.resources') IS NOT NULL THEN
    EXECUTE 'DELETE FROM runner_bindings.resources WHERE owner_id=$1' USING target;
    EXECUTE 'DELETE FROM runner_bindings.bindings WHERE owner_id=$1' USING target;
  END IF;
  DELETE FROM runner_runtime.admissions WHERE owner_id=target;
  DELETE FROM runner_runtime.entry_receipts WHERE owner_id=target;
  DELETE FROM runner_runtime.outbox WHERE owner_id=target;
  DELETE FROM runner_runtime.registry WHERE owner_id=target AND state_id=old_state;
END; $d4$;
COMMIT;
```

이 transaction은 app account를 삭제하지 않습니다. 삭제 후 owner의 registry,
outbox/receipts/admissions, optional binding/termination, conversation 행이 0인지
read-only로 확인합니다. 다른 owner가 있으면 보존하며 그 행을 지워 전역 count를
0으로 맞추지 않습니다. 예상대로 기존 operator만 있었다면 registry 전체가 비게 됩니다.

### D4-D. 빈 상태로 시작하는 설정과 검증

그 뒤 아래 공통 2·3단계의 새 committed image build/import 및 migration/grants를
실행합니다(현재 환경에 맞게 migration을 먼저 해도 API/worker 시작 전 모두 완료).
보존형 4단계의 **기존 operator import는 건너뜁니다**. 기존 auth account를 그대로
쓰되 새 entry가 새 state UUID/generation1을 만들고 새 bounded storage를 준비합니다.

- API: `CODEX_USER_RUNNERS_ENABLE=true`, `CODEX_USER_RUNNER_POOL_CONFIG_FILE` 설정.
  `CODEX_USER_RUNNER_ACCOUNT_ID`와 v1 `CODEX_USER_RUNNER_CONFIG_FILE`은 제거합니다.
  canary는 `CODEX_USER_RUNNER_ALLOWLIST=<승인된 사용자 계정 UUID>`로 한정하고 이후
  API/worker 모두에서 제거 또는 확대합니다. 운영자도 같은 신규 경로로 들어옵니다.
- API/worker: `CODEX_USER_RUNNER_MAX_PODS=3`,
  `CODEX_USER_RUNNER_IDLE_SECONDS=1800`(설정 가능), storage는
  `CODEX_RUNNER_NATIVE_SIZE_GIB=5`, `CODEX_RUNNER_CONTROL_SIZE_GIB=1`.
- **새 preparation output root**를 사용합니다. 예:
  `/var/lib/anxious-lookout/issue16-d4/provision-output`(root0700, 처음엔 비어 있음).
  worker의 `--outputs` 및 관련 mount를 이 경로로 맞춥니다. 기존 owner UUID 이름의
  output/runtime.json을 재사용하면 옛 state를 import하려 하므로 registry/retained
  디렉터리 삭제만으로는 충분하지 않습니다. 옛 output은 backup 위치에 보존하고
  이번 worker에 mount하지 않습니다. supply attempt/cursor와 observer cursor도 새
  D4 전용 private 디렉터리를 사용합니다. provider 원본 auth.json은 삭제하지 않습니다.

코드 확인: `Registry.enter`는 없는 owner row에 새 state를 만들고 ensure outbox를
기록합니다. Pool 초기화는 schema만 검증하며 기존 operator binding을 요구하지
않습니다. worker는 binding 없음 + 새 output root에서 prepare → publish → create로
진행합니다. 기존 operator 전용 binding이 필수인 경로는 없습니다. HostRoot/shared
mount, namespace/SA/network, DB schemas와 credentials는 여전히 준비되어야 합니다.
관련 기존 Docker 회귀(대상 singleton 없음, publication 전 pool, 신규 bounded 준비)를
재실행했으며, 실제 전체 빈 환경/host/provider 실행은 PM canary에서 확인합니다.

사용자 계정 canary에서 첫 entry→고유 state/PV/PVC/Secret/Pod→자동 supply→실제 응답을
확인하고 대상을 확대합니다. 실패는 안전하게 중단·보고하며 재현되지 않은 성공을
기록하지 않습니다. 별도 restore/rollback 리허설이나 두 owner 전체 E2E를 D4 PR 승인
조건으로 요구하지 않습니다. 이는 live 검증을 실행했다고 주장하는 것도 아닙니다.

## 보존형 운영 전환 절차 — D4에는 위에서 참조한 공통 항목만 적용

아래의 상태 보존·복원 검증·rollback 리허설·두 owner 전체 E2E 조건은 운영 전환용
절차입니다. D4 테스트 경로와 충돌하면 위 테스트 수준 경로가 우선합니다.

## 1. Freeze scope and preserve evidence

Keep the current singleton account selection and existing version1 runtime env
until migration/import checks succeed. Do not broaden the allowlist or remove the
singleton yet. Inventory API/controller/runner image digests, replica counts, env
selectors, namespace/Node/PVC/PV/Secret UIDs, registry generation/revision/phase/UID,
outbox/admissions/entry receipts, conversations, retained paths and account markers.
Record source and image metadata publicly; credentials, journal, broker, ledger and
full operational inputs stay in a durable root0700 evidence directory on the host,
with files root0600. Do not use a volatile /run directory as the only evidence copy.

**Before any reboot**, export the current-boot k3s journal as JSONL and preserve the
relevant containerd log with metadata/digests. For a reviewed new evidence directory,
use umask077 and noclobber with `journalctl -u k3s -b -o json --no-pager`. Check the
export size against the recovery tool's 16,000,000-byte cap; retain the complete
original privately and produce a reviewed bounded subset if necessary. A large
export is not evidence of absence. Independently record host PID/cgroup namespace
IDs, boot ID, Node identity, and the historical hostname-to-Node mapping. Reboot
invalidates the currently reviewed same-boot legacy recovery path.

Take a verified database backup and retained-storage snapshot/backup through the
existing operational backup procedure before mutation. Verify restoration on a
separate target. Export existing immutable binding plan and resource rows privately;
record their digests and original image pin. Do not expose transport credentials.

## 2. Build, record and import immutable artifacts

PM chooses the **full 40-character reviewed commit SHA** and builds from its git
archive. The documentation commit can be included: use the final source SHA that
review approves, not the historical legacy SHA e5906b0. A clean archive excludes
local edits, .env, private inputs and untracked artifacts. Build all three backend
targets from the same SHA; do not rebuild one role later under the same tag.

```sh
# Set this to the exact reviewed SHA; this example does not select a moving branch.
task_source_sha=<reviewed-40-character-sha>
task_built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
for task_target in runtime runner provisioner; do
  git archive "$task_source_sha" | docker build -f backend/Dockerfile \
    --target "$task_target" --build-arg APP_GIT_SHA="$task_source_sha" \
    --build-arg APP_BUILT_AT="$task_built_at" \
    -t "anxious-s13-issue16-$task_target:$task_source_sha" -
done
```

`runtime` is the API. `runner` includes the pinned native bundle and the new drain,
idle and pre-stdin UID supply checks. `provisioner` is the trusted worker image and
contains provisioning, observer/idle, host supply, legacy recovery and binding-image
maintenance. It is **not** the API image. Override its ENTRYPOINT to python when
running modules other than ops.runner_provision_worker.

Record build logs and metadata at
`agent-worktree-comm/artifacts/pm/issue16-rollout/<source-sha>/images.json` and link
that sanitized record in issue16. For each target include sourceSHA, builtAt,
architecture, build target, build tag, local Docker image ID, config ID if distinct,
registry repository digest (when published), and imported containerd image identity.
Leave unavailable fields null, never invent digest values. Also record the digest of
the exported OCI/Docker archive and exact import command/output. Tags are labels,
not immutable pins. No source change is needed just to fill this operational record.

For each build inspect `docker image inspect <tag>` and inspect `/app/release.json`
using the **recorded local image ID**:

```sh
docker run --rm --pull=never --network none --read-only --cap-drop ALL \
  --entrypoint cat <local-image-id> /app/release.json
```

Require release.sha to equal task_source_sha. Require the current node's platform:
Linux/arm64 in production, Linux/amd64 only on the x86_64 development VM
(`infra/k3s/DEV-VM.md`). For the runner, verify the native bundle checksums against
backend/runner/install_codex.py; test the offline protocol suite in Docker. For the
worker, run network-none `python -B -m <module> --help` for runner_provision_worker,
runner_stop_observer, runner_host_supply, runner_legacy_reconcile and
runner_binding_image. Compare module SHA256 values with files from the same archive.
The worker modules are under ops.*. CLI help is a packaging check, not a live proof.

Choose the reviewed registry path or the existing reviewed k3s offline-import
procedure. Capture the registry **repository@sha256:manifest** digest for every
Kubernetes image reference. Do not substitute a Docker config ID as a repository
digest. For offline import, save/load the exact archive into k3s containerd and
verify `k3s ctr -n k8s.io images ls`/CRI inspection resolves the intended repository
name and digest; fail if that exact pin is not available without pulling another
tag. For host Docker commands use the verified local content-addressed ID with
`--pull=never`. The old legacy-only image pin in TERMINATION.md is superseded for
this cutover by this new record; preserve it solely as historical provenance.

## 3. Stop mutators, migrate, then install capabilities

Put Codex entry into the existing maintenance/feature-off boundary, stop the old
API instance(s), preparation/controllers and any external Pod creators. Preserve
the singleton selection in their configuration. Stop timers, including restore,
observer and supply timers; do not merely change an env file under a running process.
This avoids old synchronous stop behavior racing the new schema. Verify no old
process can issue dispatch, prepare, exec or deletion for these owners.

Run these modules from the recorded worker image, with a read-only private admin
DSN mount and ADMIN_DATABASE_URL_FILE. Never put a password/DSN on the command line:

1. `python -B -m app.runner_migrate` (validate/add the runtime schema).
2. `python -B -m ops.runner_bindings_migrate`.
3. `python -B -m ops.runner_termination_migrate`.

Install backend/db/016_runner_controller_grants.sql and
backend/db/016_runner_observer_grants.sql using the reviewed database administrator
channel. These scripts CREATE ROLE once; if roles already exist, compare their
exact grants and membership rather than blindly replaying CREATE ROLE or ignoring
errors. Provision separate login secrets and grant the NOLOGIN capabilities only
to their intended controller/observer logins. API retains no binding mutation,
host storage, source-file or exec capability. Never grant admin to a worker service.
The explicit binding-image maintenance command below uses a short-lived admin DSN
only during frozen maintenance; no new permanent UPDATE grant is introduced.

Verify DB schema/column privilege checks using the new source. Verify private
version2 pool config, CA/TLS and projected token paths and expiry/rotation. Use
separate Kubernetes identities for read-only API, creator/controller, observer
(GET/DELETE) and host supply (GET/exec). Validate named namespace/Node/PV access and
isolated namespace RBAC. API no longer needs DELETE with the new stop schema; remove
legacy DELETE when the old implementation has been stopped. Workload SAs still
have no token/API rights. Do not grant CRI or host auth access to API/runner Pods.

## 4. Import and install stopped service units

Run the immutable operator binding import from PROVISIONING.md with controller
credentials, exact existing v1 runtime/UIDs and uncapped retained paths. Import is
read-only to storage/Kubernetes and inserts only the binding; it cannot release the
legacy FAILED state. Compare imported plan/resources against the private baseline.
Keep the existing operator uncapped exception; new owners use D3 5GiB+1GiB by
default and require allocation+10GiB available. Preserve configured size inventory
for bounded restore/audit; never resize old images by changing environment values.

Install, but do not enable, reviewed external systemd oneshot/timer definitions:
preparation, observer `--idle`, host supply, and bounded-volume restore. Each unit
uses the corresponding recorded image ID and private credential mounts from its
specialized document. Provisioning requires reviewed shared mount propagation and
root filesystem enforcement; observer requires host PID+cgroup/proc+CRI evidence;
host supply requires none of those privileges and must not receive a Docker socket.
Use durable cursor/attempt paths. Supply OnFailure must route sanitized outcomes to
the operator's existing alert channel, with deduplication; no credentials in logs.
The source supplies finite CLIs, not an installed service manager configuration.
Review the generated units, exact mount/network/capability set, kubeconfig plugins,
source UID and read-only source mount **before enabling any timer**. Bounded restore
must precede k3s/preparation after reboot; validate real cap/space and restore in an
isolated test before allowing new production bounded owners. No reboot before
legacy evidence/recovery has completed under its same-boot contract.

## 5. Legacy report, independent review, then apply — 운영 전환 시 (D4 제외)

Use TERMINATION.md's exact report/apply mount and namespace command, substituting
only the newly verified worker local image ID and the reviewed current private
inputs. Preserve the evidence on durable storage; mount a root-only report directory.
Verify fresh host references and historical Pod attribution. Run **report only**.
A refusal is a blocker, not permission for direct SQL cleanup. Independently review
its owner/state/generation/revision/UID and retained digests/empty audit. Only then,
under the existing operational authorization, run apply with read-only report mount.
Verify idle_stopped, UID null, same state/generation and all eight legacy entry
receipts/history/ledger preserved. Do not start new entry between recovery and the
image transition. If evidence no longer meets the tool's narrow zero-admission
conditions, escalate the concrete mismatch; do not broaden the recovery algorithm.

## 6. Explicit immutable binding image transition — 운영 전환 시 (D4 제외)

The normal publish/import/worker/API paths remain immutable. Do not patch an old
running Pod to the new image or edit resources to make a spec check pass. A separate
admin-only `ops.runner_binding_image` maintenance operation changes **only**
plan.image (and, with `executorImage`, a v2 plan's executor image; #59) after proved termination. All entry/creators/supply/observer remain
stopped. Freshly verify Pod absence and the existing retained resource identities;
archive those read-only checks with the private before-plan/resources export.

Capture the complete old plan JSON from runner_bindings.bindings under the
reviewed admin channel. Compute its canonical hash inside the new worker image,
using `ops.runner_binding_image.plan_digest` (sorted JSON, compact separators),
not a whitespace-sensitive file hash. Supply a root0600 JSON file containing
exactly owner, state, generation, revision, oldPlanSha256 and newImage. The fence
is the **post-recovery** registry fence, not the earlier FAILED revision. newImage
must be the recorded runner repository@sha256 digest, never the worker/API image.

With admin credentials mounted read-only, the worker needs only DB access and
private input/report paths; no CRI/Kube/source/retained-volume mounts:

```sh
# Substitute the recorded worker local image ID and reviewed private paths.
docker run --rm --pull=never --user 0:0 --read-only --cap-drop ALL \
  --security-opt no-new-privileges --network <reviewed-db-network> \
  --mount type=bind,src=<private-input-dir>,dst=/private,readonly \
  --mount type=bind,src=<private-report-dir>,dst=/reports \
  -e ADMIN_DATABASE_URL_FILE=/private/admin-dsn --entrypoint python \
  <worker-local-image-id> -B -m ops.runner_binding_image report \
  --inputs /private/binding-image.json --report /reports/binding-image.json
```

Independently review the report, then use the same invocation with `apply` and a
read-only report mount. It locks membership/owner and binding row, requires the
exact STOPPED fence, no active/ambiguous/pending work, no unsettled outbox or pending
stop/entry intent, and a completed same-generation termination audit. It validates
the full expected plan hash and new strict digest image, then compare-and-swaps
only image. Registry, generation, resource rows and published_at are unchanged.
Existing normal-path DB roles cannot perform this write. Repeating the old command
after success refuses the changed hash: inspect the after-plan instead of retrying.
Archive before/after/report digests in the operational record.

Both images must implement the same retained broker/ledger/native format and
owner/state/reference contract. The new runner must contain drain, monotonic idle,
and `access_supply --expected-pod-uid` support before normal observer/supply is
started. API, worker and runner come from this same reviewed source record. Format
incompatibility blocks cutover; recovery and image migration never rewrite history.
For a reverse image change the same stopped/proved fence, fresh report, old plan
hash and reviewed compatibility are required; never reverse image on a live Pod.

## 7. Canary, expansion and acceptance

Start the new API in pool mode while retaining singleton/explicit canary selection;
remove the mutually exclusive v1 config env only in this reviewed rollout. Start
preparation, observer/idle and supply units with the matching selector/feature env.
Enable one canary owner's authenticated normal entry, verifying same state and next
generation, exactly one creation permit/Pod and unchanged retained history. Verify
native login, real provider response, idle/logout drain, UID-precondition deletion,
host termination proof and session-authorized re-entry. Healthy GET polling must
not create work, reset idle, re-login or consume source permits. Verify operator
alerts with synthetic failure, not by damaging real credentials.

Only after the canary passes, remove the singleton or set the explicit wider
allowlist consistently on API and all workers, preserving feature flag, maxPods
and idleSeconds settings. Restart/reload each process; env edits alone do not apply.
For two approved test owners independently verify distinct state/Pod/PVC/PV/Secret
UIDs, source account sharing without token exposure, history/turn isolation,
cross-owner API denial, Pod-to-Pod and API-server network denies, actual responses,
logout/relogin and idle reclaim, capacity_wait at the configured limit, one slot
release then one waiting-owner admission, revoked session cancellation and no GET
side effects. Capture sanitized results and private evidence locations in issue16.
This gate needs actual cluster/provider tests; Docker source tests do not satisfy it.

## 8. Rollback and stop conditions — 운영 전환 시 리허설 (D4 필수 아님)

On evidence/identity/permission/space/provider failure stop expansion, disable entry
and stop timers/creators. Preserve all unknown permits, receipts and retained data.
Use the reviewed new observer to terminate any newly started runner cleanly where
possible; absent proof keeps its slot reserved. Do not force-delete/reset to unblock
rollback. Preserve FAILED/STOPPING and send the concrete blocker to back/review.

Prefer configuration rollback to the singleton with the new schema-compatible
API/workers. Do not restore the old synchronous-delete API while termination schema
and new workers are active. Stop all processes before any binary rollback, and
verify old/new schema and data compatibility on a restored isolated backup first.
No automatic schema drop/downgrade is supplied. A database backup restore is a
separate operator-reviewed recovery with all writers stopped and data consistency
checks; do not overwrite newer conversation/ledger history blindly. Image reversal
uses step6 only after proven stop and compatible retained formats. Never delete
new owners' retained volumes, resources or evidence as rollback cleanup. Keep the
reviewed new worker artifact available until every outstanding intent is resolved.

## Source review record and remaining execution gates

As of the final source preparation, issue16 has no open reviewer minor backlog
items; earlier front design comments are not new backend minor findings. No minor
sweep commit is needed. All prior feature slices have partial approval; this document
and the explicit binding-image tool are submitted with the final single source SHA
for full-scope **source** review. Image build/import, private reports, generated
units/RBAC/network, host/reboot/provider/two-owner execution and rollback rehearsal
remain PM evidence requirements. Do not describe an unrun gate as passed.
