# #7 선택 작업 세션의 적용성: 비밀 내용 없는 1차 preflight

PM 실행 전 독립 절차 검토용입니다. 이 문서는 운영 실행/추가 접근 승인을 대신하지
않습니다. 현재 선택은 **이미 확정한 작업 세션 인증**이며 새 profile/login/key 선택을
요구하지 않습니다. 전체 client 전환의 포괄 승인도 아직 요청하지 않습니다.

## 재사용할 근거와 대상

PM의 `/tmp/issue7-shared-runtime-metadata/`에 선택 PM/back/review 실행파일/환경
존재 여부/connection metadata가 있습니다. 당시0.160.1 embedded/socketpair 관찰과
별도0.162.1 daemon의 미확정 관계를 재사용합니다. Config의 store override unset 및
default provider 관찰은 기본값 정보일 뿐 active auth를 입증하지 않습니다.
`/tmp/issue7-selected-owner-readonly/owner-result.json`의 기존 exact-one eligible UID
결과는 private reference로 재사용합니다. UUID를 재조회/공유 출력하지 않습니다.

이번 새 대상은 **PM이 기존 선택 기록/자신의 launcher에서 식별한 한 작업 client**입니다.
PM 자신의 선택 세션을 우선 관찰하면 다른 role의 지침이나 세션 내용을 읽을 필요가 없습니다.
현재 PID와 시작 identity를 launcher/감독자의 기존 기록에 연결하세요. 같은 이름의 process를
검색하거나 예전 PID만으로 현재 process를 자동 선택하지 않습니다.

필수 private 입력(0700 operator directory, umask077/set+x):

| 입력 | 출처/용도 |
| --- | --- |
| `PREFLIGHT_PID` | 현재 선택한 기존 client의 supervisor/launcher record; 숫자 PID 하나 |
| `PREFLIGHT_START` | 해당 기록과 연결된 /proc stat starttime tick; PID reuse 거절용 |
| `PREFLIGHT_SOURCE_FILE` (선택) | 기존 profile metadata에서 이미 알려진 정확한 canonical candidate file; 현재 active라는 선언 아님 |
| `PREFLIGHT_NATIVE_ARTIFACT` (선택) | 기존 launcher/이미 가진 session reference에 연결된 정확한 native persistence file; HOME/rollout 탐색 금지 |
| private source/config/owner reference | 기존 evidence 위치; 내용 복사 없이 근거 연결 |

PID/start identity 또는 정확한 경로가 없으면 해당 부분은 unknown/미실행으로 남깁니다.
새 UI 명령이나 original control socket RPC로 그것을 알아내는 작업은 1차 범위 밖입니다.
선택 UID와 provider identity는 서로 다른 값이며 둘을 username/email로 추측하지 않습니다.

## P0: 허용 관찰과 PM 명령

아래는 host 운영 metadata 명령입니다. language build/test가 아니며 native executable을
실행하지 않습니다. 모든 결과/errors는 private directory로 redirect하고 공유 메시지에는
boolean/unknown/관찰 종류만 보고합니다. auth.json/config/rollout **내용을 cat/hash/parse하지
않고**, /proc cmdline/environ/mem도 읽지 않습니다. 기존 source_metadata format helper도
cache 내용을 읽으므로 이번 P0에서 실행하지 않습니다.

```sh
set -euo pipefail
set +x
umask 077
: "${PREFLIGHT_SOURCE_FILE:=}"
: "${PREFLIGHT_NATIVE_ARTIFACT:=}"
[[ $PREFLIGHT_PID =~ ^[0-9]+$ && $PREFLIGHT_START =~ ^[0-9]+$ ]]
[[ $PREFLIGHT_PRIVATE_DIR =~ ^/tmp/issue7-session-preflight-[A-Za-z0-9_-]+$ && ! -e $PREFLIGHT_PRIVATE_DIR ]]
mkdir -m 0700 -- "$PREFLIGHT_PRIVATE_DIR"
[[ -r /proc/$PREFLIGHT_PID/stat && -r /proc/$PREFLIGHT_PID/status ]]

# Kernel process metadata only; split after closing comm parenthesis so spaces in
# comm cannot shift starttime. Selected client must still match its launch identity.
PREFLIGHT_STAT=$(cat "/proc/$PREFLIGHT_PID/stat")
PREFLIGHT_FIELDS=${PREFLIGHT_STAT##*) }
PREFLIGHT_OBSERVED_START=$(printf '%s\n' "$PREFLIGHT_FIELDS" | awk '{print $20}')
[[ $PREFLIGHT_OBSERVED_START == "$PREFLIGHT_START" ]]
printf '%s\n' "$PREFLIGHT_OBSERVED_START" > "$PREFLIGHT_PRIVATE_DIR/process-start.before"
awk '/^(Name|Uid|Gid|PPid):/ {print}' "/proc/$PREFLIGHT_PID/status" > "$PREFLIGHT_PRIVATE_DIR/process-identity.txt"
readlink -e -- "/proc/$PREFLIGHT_PID/exe" > "$PREFLIGHT_PRIVATE_DIR/executable-path.txt"
stat -Lc '%d|%i|%u|%g|%f|%h|%s|%y|%z' -- "/proc/$PREFLIGHT_PID/exe" > "$PREFLIGHT_PRIVATE_DIR/executable.before.stat"
readlink -e -- "/proc/$PREFLIGHT_PID/cwd" > "$PREFLIGHT_PRIVATE_DIR/cwd-path.txt"

# Optional known files only. Empty input means unknown, not discovery/fallback.
for PREFLIGHT_ITEM in "$PREFLIGHT_SOURCE_FILE" "$PREFLIGHT_NATIVE_ARTIFACT"; do
  [[ -n $PREFLIGHT_ITEM ]] || continue
  [[ $PREFLIGHT_ITEM == /* && $PREFLIGHT_ITEM != *$'\n'* ]]
  [[ -f $PREFLIGHT_ITEM && ! -L $PREFLIGHT_ITEM ]]
  [[ $(realpath -e -- "$PREFLIGHT_ITEM") == "$PREFLIGHT_ITEM" ]]
  stat -c '%d|%i|%u|%g|%f|%h|%s|%y|%z' -- "$PREFLIGHT_ITEM" >> "$PREFLIGHT_PRIVATE_DIR/known-files.before.stat"
done

# Same process at observation end. File metadata stamps alone never prove no writer.
PREFLIGHT_STAT=$(cat "/proc/$PREFLIGHT_PID/stat")
PREFLIGHT_FIELDS=${PREFLIGHT_STAT##*) }
PREFLIGHT_OBSERVED_START=$(printf '%s\n' "$PREFLIGHT_FIELDS" | awk '{print $20}')
[[ $PREFLIGHT_OBSERVED_START == "$PREFLIGHT_START" ]]
stat -Lc '%d|%i|%u|%g|%f|%h|%s|%y|%z' -- "/proc/$PREFLIGHT_PID/exe" > "$PREFLIGHT_PRIVATE_DIR/executable.after.stat"
cmp -s "$PREFLIGHT_PRIVATE_DIR/executable.before.stat" "$PREFLIGHT_PRIVATE_DIR/executable.after.stat"
for PREFLIGHT_ITEM in "$PREFLIGHT_SOURCE_FILE" "$PREFLIGHT_NATIVE_ARTIFACT"; do
  [[ -n $PREFLIGHT_ITEM ]] || continue
  [[ -f $PREFLIGHT_ITEM && ! -L $PREFLIGHT_ITEM && $(realpath -e -- "$PREFLIGHT_ITEM") == "$PREFLIGHT_ITEM" ]]
  stat -c '%d|%i|%u|%g|%f|%h|%s|%y|%z' -- "$PREFLIGHT_ITEM" >> "$PREFLIGHT_PRIVATE_DIR/known-files.after.stat"
done
if [[ -f $PREFLIGHT_PRIVATE_DIR/known-files.before.stat ]]; then
  cmp -s "$PREFLIGHT_PRIVATE_DIR/known-files.before.stat" "$PREFLIGHT_PRIVATE_DIR/known-files.after.stat"
fi
unset PREFLIGHT_STAT PREFLIGHT_FIELDS PREFLIGHT_OBSERVED_START
```

Use a fresh private output directory name created by this subshell, initialize
optional input variables to empty, and impose a short timeout (for example15s) on the
subshell. No sudo privilege widening or chmod/chown of original targets. Permission
failure, missing file or any check failure aborts the observation, without retrying an
unselected process/path. A script reviewer may package these exact metadata operations
after checking the actual target; no new collector or fixture project is needed here.

Optional exact selected fd reference: only if an existing launcher/debug record already
names the relevant fd number, PM may `readlink /proc/<same PID>/fd/<known FD>` and stat
the known persistence/cache path. **Never open the fd, connect socket, consume pipe,
enumerate other processes or scan HOME/session databases.** An opened artifact links
that PID to a file at that time, not the complete current auth or selected thread.
An absent auth fd does not imply ephemeral mode; a file cache is often opened briefly.

Subshell 자체의 stdout/stderr는 먼저 존재하는 별도0700 operator workspace의 파일로
redirect하세요. 새 PREFLIGHT_PRIVATE_DIR 내부 파일로 outer redirect하면 mkdir 전에
실패할 수 있습니다. 실패 출력에도 경로가 포함될 수 있으므로 공개하지 않습니다.

## 출력과 판정: 공유는 요약만

| 관찰 | 말할 수 있는 것 | 확정하지 못하는 것 |
| --- | --- | --- |
| PID/start/exe/cwd consistency | 선택 process가 안정적으로 관찰됐고 identity가 같다면 기존 version/path 근거 연결 가능 | 실제 effective config, active auth/cache/account, 모든 descendant/writer |
| 정확한 candidate file metadata | candidate가 존재하고 owner/mode/inode를 기록함; process UID와 private 비교 가능 | 현재 선택 credential/format/auth 유효성/latest refresh/단일 writer |
| 정확한 native artifact/reference metadata | 이미 알려진 persistence artifact 존재 및 좁은 process/file 관계 | 내부 conversation ID/완전한 history/실제 resume 성공 |
| pinned CLI/schema/source의 remote+resume 지원 | 해당 버전에 command/protocol surface가 있음 | 선택 thread가 tool/config/identity를 보존하며 이전·resume 가능한지 |

공유 결과 template:
`selectedProcessStable=true|false|unknown`, `knownCandidateMetadataObserved=true|false`,
`effectiveCredentialStore=unknown|supported_nonsecret_evidence`, `activeStoreBinding=not_established`,
`selectedNativeReference=known|unknown`, `durableArtifactMetadata=observed|unknown`,
`resumeSupport=version_surface_only|unresolved`, `actualResume=not_tested`,
`writerInventory=selected_process_only_not_exhaustive`, `authRpcExecuted=false`,
`secretContentRead=false`, `sourceSupplyOrTransition=false`.

신뢰된 기존 **비밀이 아닌** 기록이 실제 store/resume 관계를 제공할 때만 그 근거를
인용하세요. Private reference/type/date를 연결하며 stat 성공으로 결과를 만들어내지
않습니다. PID/path/inode/UUID/credential timestamp와 raw launcher/config는 공유하지
않습니다. 오래된 artifact 존재나 CLI의 remote 지원은 현재 세션의 resume 성공이 아닙니다.

## P0가 부족할 때: 필요한 추가 접근을 항목별로 보고

P0만으로 store 종류와 active binding이 확정되지 않을 수 있습니다. 아래 추가 접근은
**아직 허용된 것으로 간주하지 않습니다.** 부족 항목·정확한 대상·허용 출력·비실행 대안을
PM/review에 제시하고 별도 범위화합니다.

1. **Effective config provenance:** 선택 client 하나의 launcher allowlist 항목과 이미
   알려진 config chain에서 `cli_auth_credentials_store`, HOME/CODEX_HOME/profile,
   remote/daemon/auth-mode override presence를 확인하는 제한된 읽기입니다. 전체 argv/
   environ은 inline token/secret을 포함할 수 있어 P0에서 dump/parse하지 않습니다.
   Operator가 이미 가진 비밀 아닌 field report를 우선합니다. 부족하면 정확한 PID/config
   file/field와 secret 값을 decode/retain하지 않는 reviewed 방법의 추가 승인이 필요합니다.
   다른 role 지침 파일, HOME/keyring 탐색은 대상이 아닙니다. 설정이 file/keyring/auto/
   ephemeral이어도 현재 memory의 auth mode와 일치한다고 가정할 수 없습니다.
2. **Active store association:** selected runtime의 지원되는 metadata/status에서 resolved
   store backend, memory-vs-managed/external mode와 private store reference를 확인하는
   접근이 필요할 수 있습니다. 현재 embedded runtime의 외부 metadata 입구는 미확인입니다.
   별도 daemon의 account/read로 대체하지 않습니다. 지원 입구가 없으면 그 제한을 보고하고
   hook/hot patch/새 socket을 만들지 않습니다. Cache JSON 진단이 필요하다면 단일 canonical
   file의 명시적인 추가 내용 읽기이며 P0 성공 후 자동 실행하지 않습니다. 그 경우도 현재
   memory source 관계는 별도 증거가 필요합니다.
3. **선택 thread persistence:** 이미 가진 native thread reference와 **정확한 파일 하나**의
   allowlisted session metadata(ID/version/cwd/source 형식)를 대조하는 추가 읽기입니다.
   파일은 prompt/instruction/secret을 포함할 수 있어 P0에서는 내용을 읽지 않습니다.
   DB 전체 조회나 rollout 열거는 필요하지 않습니다. 실제 thread/read/resume는 별도
   operation gate이며 metadata 대조만으로 provider/native resume 수용을 주장하지 않습니다.
4. **Writer 범위:** 같은 store/grant의 다른 client·daemon/IDE/자동 job에 대한 operator
   inventory와, 목록에 올린 그 대상만의 metadata 확인이 필요합니다. 기존 세 PID 관찰만으로
   all writers 완료를 선언할 수 없습니다. 숨은 memory consumer 또는 다른 path의 같은
   renewable grant는 file stamp로 식별할 수 없어 추가 owner 근거가 필요할 수 있습니다.

실제 auth RPC/credential 추출/refresh/source 공급이 필요한 단계는 이유와 부작용
(account/read의 routing bootstrap 등)을 별도 운영 계획으로 제시합니다. P0와 섞지
않습니다. 현재 대상이나 지원된 관찰 수단이 확정되지 않으면 unknown으로 멈춥니다.

## 중단 조건과 다음 판단

PID reuse/종료, exe identity 변경, unknown path/link/nonregular, 필요한 권한 부족,
candidate UID 불일치 또는 reference 미특정이면 해당 관찰을 중단하고 변경을 시도하지
않습니다. Metadata가 관찰 중 바뀌면 불안정/미확정으로 보고하며 writer를 중단시켜
성공시키지 않습니다. 실제 store가 memory/keyring/external인지 불명확하면 file로 변환하지
않습니다. Resume 대상이 없으면 새 thread로 대체 성공을 만들지 않습니다. 정상 종료/
재연결의 영향과 복구 패키지가 구체화되기 전에 전체 client 전환 승인을 사용자에게
요청하지 않습니다.

결과는 PM/review의 제한된 관찰 및 다음 추가 접근 판단 입력입니다. Native 공급/turn/
중단/전환/authority·gateway 시작/설정 수정/운영 image build·import·deploy는 수행하지
않습니다. 기존 session/auth/API70 nativeoff/runner Pod0와 총3/두 성공 turn 및 공개 게시
hold를 유지합니다.
