# #7 P1: 선택 launcher allowlist와 active-store 관찰 한계

PM의 `2026-10-10-pm-issue7-session-p0-next-evidence.md`에 대한 **절차 검토용**입니다.
P0는 완료됐고 반복하지 않습니다. 현재 PM 작업 세션 선택을 유지합니다. 아래 P1은
새로운 입력 접근 범위이며 P0 승인이나 UID minor 종료로 실행 승인된 것이 아닙니다.
Backend는 원본 process에 실행하지 않았습니다. Auth RPC, credential 공급, 세션
변경, 새 listener/hook, process 전환, 배포는 포함하지 않습니다.

## 기존 근거와 새 관찰

| 구분 | 대상/확인된 사실 | 추가로 말할 수 있는 것 |
| --- | --- | --- |
| P0 재사용 | supervisor와 연결된 선택 PM process identity가 안정적; optional candidate/reference 미입력 | candidate 소유/active-store/native persistence는 계속 unknown |
| 기존 환경 요약 | 선택 process의 HOME presence; 기존 config store override unset/default 관찰 | 기본값만으로 live auth source를 정할 수 없음 |
| P1 새 입력 | 같은 현재 process의 `/proc/<PID>/cmdline`, `/proc/<PID>/environ` 두 파일만 | 알려진 flag token, CLI store enum override, 고정 환경 key presence |
| 지원 metadata 검토 | pinned 0.160.1 schema의 `config/read`와 local source | protocol surface는 존재하지만 선택 embedded runtime에 연결할 허용 외부 입구는 아직 입증되지 않음 |

과거 PID를 자동 선택하지 않습니다. 현재 supervisor-linked PID/start를 PM이 private로
확정하고 P0 근거에 연결합니다. 이번 identity 전후 검사는 새 읽기의 대상 일관성 검사이며
P0 optional file 수집/owner lookup을 반복하지 않습니다.

## 정확한 입력과 출력

필터는 `backend/ops/process_launch_fields.py`이며 SHA256은
`866b2e5829109ca3a036b485e86638c3f7013b7e342254dda5ca4cc17693d1eb`입니다.
고정 두 입력을 각각 최대 131072 bytes까지 읽고 NUL termination/크기를 검사합니다.
큰 입력, 사라진 process, 접근 실패, empty cmdline, 빠진 config 인자는 실패입니다.

**두 raw process buffer는 inline credential을 포함할 수 있습니다.** 필터가 비밀 값을
decode/출력/파일로 저장하지 않아도 raw bytes는 메모리로 읽습니다. 따라서
`rawProcessBuffersRead=true`, `secretValuesDecoded=false`, `credentialFilesRead=false`를
분리합니다. P0의 `secretContentRead=false`를 P1에 그대로 사용하지 않습니다. 이 정확한
raw 입력 접근이 허용되지 않으면 실행하지 않고 operator의 기존 비밀 아닌 field report만
사용하거나 unknown으로 남깁니다. Raw argv/environ 복사, dump, hash, 로그는 하지 않습니다.

출력 allowlist:

- `environmentPresence`: HOME, CODEX_HOME, OPENAI_API_KEY,
  OPENAI_FEDERATION_RULE_ID, OPENAI_IDENTITY_TOKEN_FILE,
  OPENAI_WORKLOAD_IDENTITY_CONTEXT의 존재 boolean만. 값/경로/길이는 내보내지 않음.
- `profileFlagPresent`, `remoteFlagPresent`, `noDaemonFlagPresent`: 알려진 token 존재만.
  Profile 이름/remote 주소는 내보내지 않음.
- `cliStoreOverride`: 알려진 `-c`/`--config` 표현식의
  `cli_auth_credentials_store` 값이 정확한 file/keyring/auto/ephemeral scalar일 때만 enum.
  미인식 값은 `unrecognized`, 미관찰은 null. `storeOverrideCount`와
  `otherConfigOverridePresent`만 추가 출력. 마지막 관찰 enum은 effective config가 아님.
- `workloadIdentitySelectionMarked`: 두 workload marker 중 하나의 presence.
  `workloadIdentityConfigurationValidated=false`, `activeAuthMode=not_observed`,
  `activeStoreBinding=not_established`, `resolvedCredentialBackend=not_observed` 유지.
- `argvObservation=lexical_not_cli_resolution`,
  `environmentObservation=proc_initial_environment_not_live_auth`, 접근 범위 boolean.

이 필터는 full CLI parser가 아닙니다. `--` 뒤는 관찰하지 않고 일부 short/positional
문법은 해석하지 않습니다. Prompt/다른 옵션 값에 나타난 알려진 token도 lexical 관찰일
수 있어 operator의 기존 비밀 아닌 launcher record와 연결하기 전 실제 flag 적용을
주장하지 않습니다. `/proc/environ`은 초기 환경 영역이며 process 내부 환경 변경과
live AuthManager 상태를 반영한다고 가정하지 않습니다. 여러 buffer의 원자 snapshot도
아닙니다. Presence false는 현재 in-memory auth mode의 배제가 아닙니다.

## PM 실행 block: 입력 범위 검토 후에만

P1-R1 독립 검증에서 이전 exact proc bind mount는 OCI/runc propagation permission
오류(exit126)로 실패했습니다. 아래 수정안은 **proc mount 없이** same-UID host `head`가
각 파일을 최대 131073 bytes만 읽어 base64 한 줄씩 pipe로 전달합니다. Container는 stdin의
정확한 두 frame을 길이 제한/strict base64/NUL 검사 후 처리하고 여분 입력을 거절합니다.
131073번째 byte가 있으면 oversize로 거절하며 성공한 truncation으로 처리하지 않습니다.
Base64는 framing이며 비밀 보호가 아닙니다. Raw/encoded buffer는 파일·argv·env·로그에
저장하지 않습니다. Shell trace/session stdin recording이 활성화된 환경에서는 실행하지
않습니다. Docker에는 filter source 하나만 bind하고 입력은 `-i` pipe이며 TTY는 없습니다.
Transport frame의 base64 decode는 수행합니다. 출력 `secretValuesDecoded=false`는 secret
field 값을 text/credential 구조로 해석하지 않는다는 의미이며 raw secret bytes가 메모리에
없다는 주장이 아닙니다.
Docker daemon 메모리 전달도 추가 입력 경계에 포함하며, daemon의 기존 stdin 비저장 운영
조건을 확인하지 못하면 실행하지 않습니다. 실패 시 파일 전달 방식으로 대체하지 않습니다.

PM은 자신이 이미 선택한 host namespace process에 대해 same-UID로 실행합니다.
Sandbox PID namespace에서 대상이 안 보이면 PM의 이미 승인된 host 운영 위치를
사용합니다. 다른 PID 탐색/대체, target 권한 변경, sudo process read, ptrace,
`--privileged`, host PID namespace, 전체 `/proc`/HOME mount는 금지합니다.
Docker daemon 접근은 기존 승인된 wrapper만 사용하며 target UID 권한을 확대하지 않습니다.

Private 입력: `LAUNCH_PID`, `LAUNCH_START`(현재 supervisor-linked identity),
`LAUNCH_FILTER`(검토된 필터의 absolute canonical path), `LAUNCH_PRIVATE_DIR`(새 directory).
외부 0700 operator workspace에서 stdout/stderr를 private 파일로 redirect하고 전체
subshell에 15s timeout을 적용합니다. Shell trace는 끕니다. Docker image는 기존 API
Docker ID이며 CLI가 없고 pull/network 없이 사용합니다. Wrapper `LAUNCH_DOCKER`는 PM이
이미 사용하는 shell function으로 `docker` 또는 기존 승인된 `sudo docker`만 호출합니다.
새 권한 요청이나 daemon 설정 변경은 이 절차에 포함하지 않습니다.

```sh
set -euo pipefail
set +x
umask 077
[[ $LAUNCH_PID =~ ^[0-9]+$ && $LAUNCH_START =~ ^[0-9]+$ ]]
[[ $LAUNCH_PRIVATE_DIR =~ ^/tmp/issue7-session-launch-[A-Za-z0-9_-]+$ && ! -e $LAUNCH_PRIVATE_DIR ]]
[[ $LAUNCH_FILTER == /* && ! -L $LAUNCH_FILTER && -f $LAUNCH_FILTER ]]
[[ $(realpath -e -- "$LAUNCH_FILTER") == "$LAUNCH_FILTER" ]]
[[ $(sha256sum -- "$LAUNCH_FILTER" | awk '{print $1}') == 866b2e5829109ca3a036b485e86638c3f7013b7e342254dda5ca4cc17693d1eb ]]
mkdir -m 0700 -- "$LAUNCH_PRIVATE_DIR"

LAUNCH_STAT=$(cat "/proc/$LAUNCH_PID/stat")
LAUNCH_FIELDS=${LAUNCH_STAT##*) }
[[ $(printf '%s\n' "$LAUNCH_FIELDS" | awk '{print $20}') == "$LAUNCH_START" ]]
awk '/^(Uid|Gid):/ {print}' "/proc/$LAUNCH_PID/status" > "$LAUNCH_PRIVATE_DIR/identity.before"
LAUNCH_UID=$(awk '/^Uid:/ {print $3}' "$LAUNCH_PRIVATE_DIR/identity.before")
LAUNCH_GID=$(awk '/^Gid:/ {print $3}' "$LAUNCH_PRIVATE_DIR/identity.before")
[[ $LAUNCH_UID == "$(id -u)" && $LAUNCH_GID == "$(id -g)" ]]
awk '/^(Uid|Gid):/ {if ($2 != $3 || $3 != $4 || $4 != $5) exit 1}' "$LAUNCH_PRIVATE_DIR/identity.before"
stat -Lc '%d|%i|%u|%g|%f|%s|%y|%z' -- "/proc/$LAUNCH_PID/exe" > "$LAUNCH_PRIVATE_DIR/exe.before"

{
  head -c 131073 -- "/proc/$LAUNCH_PID/cmdline" | base64 --wrap=0
  printf '\n'
  head -c 131073 -- "/proc/$LAUNCH_PID/environ" | base64 --wrap=0
  printf '\n'
} 2> "$LAUNCH_PRIVATE_DIR/transport.stderr" | \
LAUNCH_DOCKER run --rm -i --pull=never --network none --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --user "$LAUNCH_UID:$LAUNCH_GID" --entrypoint python \
  --mount "type=bind,src=$LAUNCH_FILTER,dst=/filter.py,readonly" \
  sha256:5a2a95d93810bcfc035bb883d1096376d9f2cf6f48fc99f88f1f8c308c308566 \
  -B /filter.py --stdin-base64 > "$LAUNCH_PRIVATE_DIR/fields.unaccepted.json" \
  2> "$LAUNCH_PRIVATE_DIR/filter.stderr"

LAUNCH_STAT=$(cat "/proc/$LAUNCH_PID/stat")
LAUNCH_FIELDS=${LAUNCH_STAT##*) }
[[ $(printf '%s\n' "$LAUNCH_FIELDS" | awk '{print $20}') == "$LAUNCH_START" ]]
awk '/^(Uid|Gid):/ {print}' "/proc/$LAUNCH_PID/status" > "$LAUNCH_PRIVATE_DIR/identity.after"
stat -Lc '%d|%i|%u|%g|%f|%s|%y|%z' -- "/proc/$LAUNCH_PID/exe" > "$LAUNCH_PRIVATE_DIR/exe.after"
cmp -s "$LAUNCH_PRIVATE_DIR/identity.before" "$LAUNCH_PRIVATE_DIR/identity.after"
cmp -s "$LAUNCH_PRIVATE_DIR/exe.before" "$LAUNCH_PRIVATE_DIR/exe.after"
[[ $(sha256sum -- "$LAUNCH_FILTER" | awk '{print $1}') == 866b2e5829109ca3a036b485e86638c3f7013b7e342254dda5ca4cc17693d1eb ]]
mv -- "$LAUNCH_PRIVATE_DIR/fields.unaccepted.json" "$LAUNCH_PRIVATE_DIR/fields.json"
unset LAUNCH_STAT LAUNCH_FIELDS LAUNCH_UID LAUNCH_GID
```

Identity/access/timeout/size failure는 관찰 중단입니다. 중간 unaccepted 출력을 성과로
공유하지 않고 unknown을 보고합니다. 다른 process/image/path/credential로 대체하지 않습니다.
UID/GID filesystem 차이도 이번에는 중단합니다. Docker/pipe 접근 제약으로 거절돼도 다른
권한이나 daemon으로 우회하지 않습니다. Private errors에는 path가 나올 수 있어 공유하지
않습니다. 이 block의 exit0는 제한 입력 취득 성공이며 active binding 승인이 아닙니다.

## pinned source의 의미와 남은 부족 항목

대상 source는 rust-v0.160.1 / commit
`d27764b82f7118f674371e6d6e76271d9d606edb`입니다.
`codex-rs/login/src/auth/workload_identity.rs`의
`ProcessEnvironment::has_marker`는 federation rule 또는 identity token file 환경 변수의
존재로 workload identity 선택을 나타냅니다. 한쪽만 설정하면 validation failure가 될 수
있습니다. P1은 assertion file을 열지 않고 값도 검증하지 않아 성공/주체/active auth를
판정하지 않습니다.

`cli_auth_credentials_store` override는 설정에서 온 후보입니다. file/keyring/auto/ephemeral
이어도 resolved backend, managed 대 external memory auth, 최신 refresh와의 association은
미증명입니다. CODEX_HOME/profile presence도 실제 path/store가 아닙니다. Config followup이
필요하면 operator가 이미 아는 정확한 file 하나와 field allowlist를 별도로 범위화합니다.
이번에는 config 내용을 읽지 않습니다. 0.160.1 profile 해석은 `<name>.config.toml`이며
legacy `[profiles]`나 이전 `profile=` 전제로 HOME을 탐색하지 않습니다.

Generated schema의 `ConfigReadResponse`는 config/origins/optional layers를 갖지만,
선택 embedded instance의 허용된 metadata endpoint는 현재 근거에 없습니다.
`config/read` 실행도 이번 P1에 포함하지 않습니다. 별도 0.162.1 daemon, 새 CLI process,
`codex login status`는 선택 instance의 live manager가 아닙니다. Login status는 새로운
config/auth 읽기를 동반해 대체 방법으로 사용하지 않습니다. `account/read` 등 auth RPC도
포함하지 않습니다.

현재 **허용된 관찰 입구에서 live active-store binding을 확정하는 방법은 아직
실증되지 않았습니다**. P1은 launch에서 온 후보/반증 자료를 늘립니다. 입구 부재를
모든 버전의 불가능성으로 주장하지 않습니다. 미지의 endpoint 발견 scan이나 hook을 만들지
않고 신뢰할 수 있는 기존 비밀 아닌 selected-runtime report가 없으면 한계를 보고합니다.
Writer inventory는 selected_process_only_not_exhaustive, candidateOwnerMatches는 unknown,
actualResume는 not_tested를 유지하며 전체 client/refresh continuity/binding 완료로
판정하지 않습니다.

## P1-R1 수정 검증

기존 API image에서 stdin frame의 정상 입력과 missing/invalid base64/extra frame/
newline 누락/NUL 누락/decoded oversize/encoded oversize 거절을 Docker로 확인했습니다.
수정된 위 shell block을 그대로 추출해 새 disposable Docker process의 host PID/start와
same UID/GID를 입력해 timeout15s로 실행했고 exit0입니다. Known store enum/profile token/
workload presence를 관찰했고 start/exe/UID 전후 검사도 통과했습니다. 결과와 private
stderr에 합성 secret sentinel이 없었습니다. 합성 process는 종료·삭제했습니다.
Private 합성 증거는 `/tmp/issue7-launch-e2e-r1-mSmxce/`와
`/tmp/issue7-session-launch-back-r1-1791665659/`입니다.
이는 pipe 방식의 해당 Docker 환경 재현이며 원본 process의 접근 가능성/성공,
raw 입력 승인, active-store association 또는 auth 성공을 증명하지 않습니다.
