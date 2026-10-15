# #7 현재 로그인 후보의 access-only 웹 테스트

2026-10-11 사용자 구현 지시는 이전 Option2 구현 미승인/자동 선택 금지 경계를 대체합니다.
원래 세션을 유지하고 명시적으로 선택한 canonical 로그인 파일 **하나**에서 access/account/
expiry만 추출하는 제한 테스트입니다. Shared authority 전환, 모든 writer 증명, embedded
memory와 file의 완전 동일성은 이 테스트의 선행조건이 아닙니다. 장기 refresh/#7 전체 완료를
주장하지 않습니다. 공급/배포는 source·절차 review 후 PM이 실행하며 공개 게시 hold 유지입니다.

규약: [공식 App Server external tokens](https://learn.chatgpt.com/docs/app-server#3c-log-in-with-externally-managed-chatgpt-tokens-chatgptauthtokens).
실제 pinned0.160.1 `account/login/start(type=chatgptAuthTokens)`를 사용합니다. API key/SIWC/
api.openai.com 변환이나 새 로그인은 없습니다. refresh callback은 safe error로 거절하고 웹
admission을 latch합니다. Named `access_only_openai` provider는 동일 authenticated
`https://chatgpt.com/backend-api/codex` Responses 경로에 request/stream retry0 및 WebSocket
off를 적용하기 위한 설정입니다. Companion/금지 기능/model pin은 기존 검토된 코드를 재사용합니다.
Loopback native 검증은 이 설정의 구조/동작만 확인하며 실제 계정·라우팅·모델 권한은 PM의
실제 공급/turn 결과로 확인합니다. 승인되지 않은 routing host가 필요하면 deny/stop하며 넓히지 않습니다.

## 데이터와 만료

- Runner env `CODEX_AUTH_MODE=access-only`, `CODEX_ACCESS_CONTROL_DIR=/control/access-web`.
  Source auth.json/HOME/config는 Pod에 mount하지 않습니다. Native fresh HOME, ephemeral
  auth이며 refresh/ID token을 받지 않습니다. Snapshot은 host/transport/runner/native 메모리만
  경유하고 DB/Secret/PVC/브라우저/env/argv/log에 저장하지 않습니다.
- Control PVC에는 owner/state/provider account binding, window/전체 reservation과 keyed
  projection journal만 저장합니다. Native PVC에는 web thread/history/broker bookmark를 저장합니다.
  기존 `/native/private`나 원래 세션 state를 채택하지 않고 fresh `/native/access-web`을 씁니다.
- JWT는 exp/nbf/형식·account claim과 선택 file의 account_id를 확인합니다. Signature 검증/
  provider entitlement는 local decode로 주장하지 않습니다. Opaque/expired/계정 불일치/
  API-key/부적절 owner·mode·link/크기·변경 파일은 safe code로 거절합니다.
- Admission deadline = min(token exp−60s, 해당 창 시작+15분), 최초 공급 시 최소120s token
  수명이 필요합니다. 같은 창 재공급은 15분을 연장하거나 사용한도를 리셋하지 않습니다.
  만료/401 때 original cache refresh/copy-back은 없습니다. 이미 소비한 요청은 자동 재전송하지 않습니다.
- 각 창은 **총3 admission**, 실패/불명 포함입니다. 초기 PM live acceptance는 같은 대화의
  두 명시적 성공 turn/tool/follow-up을 이 한도 안에서 확인합니다. 사용자 후속 시험은 운영자가
  새 windowId+newWindow=true로 새3회/최대15분 창을 명시적으로 엽니다. 전체 old request dedup,
  account/state/thread/projection은 보존하여 PM 시험으로 사용자가 영구 차단되지 않습니다.

## 로컬 build 및 리뷰 인계

`build_access_web.sh <exact HEAD SHA> /tmp/issue7-access-build-<suffix>`를 승인된 Docker wrapper로
실행합니다. Clean committed tree만 production runtime/runner target로 build하며 테스트/fixture
이미지는 배포하지 않습니다. Local archive/index/arm64 manifest/config/layer hash와 source label을
`access_image_audit.py`가 확인하고 images.json을 만듭니다. Build log/archives/metadata는 private로
보존합니다. 이 단계는 native process/auth/provider 실행이나 containerd import가 아닙니다.
Audit는 build workspace 소유 UID로 읽으며 source auth 파일은 mount하지 않습니다.

기존 runner-test image에 source readonly mount로 Docker 회귀를 수행할 수 있습니다:
`check_runner_companion.sh <immutable runner-test ID> <pinned public models.json> access`.
Unit-only는 마지막 인자 unit-only이며 native 성공을 대신하지 않습니다. Renderer 검증도
Docker pytest `test_access_web_prepare.py`로 수행합니다. 실제 source/계정 성공과 구분합니다.

## PM staged 운영

모든 private 입력/output는0700 directory/0600 file, umask077/set+x/non-TTY입니다. Owner는
완료된 선택 GitHub login exact-one lookup을 재사용하며 UUID 재조회나 promotion은 없습니다.

1. **Production import:** reviewed source SHA와 images.json/API·runner audit/archive SHA를 고정합니다.
   기존 승인된 k3s import 방식으로 해당 tar 두 개만 import하고 imported index→arm64 manifest→
   config chain이 local audit와 같음을 대조합니다. Docker ID를 무조건 index라고 가정하지
   않습니다. Import 후 exact reference의 CRI repoDigest/config와 실제 Pod imageID association을
   확인합니다. 원본 image/tag나 기존 web/PVC/Secret을 덮어쓰지 않습니다.
2. **Render/guard:** 기존 API70 nativeoff Deployment와 hosting-root-https-v1 ConfigMap의 metadata/
   spec snapshot, 완료된 trial-native/control PVC/PV UID/claimRef/Bound/node 근거를 재사용합니다.
   Token 없는 private `inputs.json={ownerId,stateId}`를 고정합니다. Renderer 호출은 Docker에서
   `python -B /checks/access_web_prepare.py <part> --images /inputs/images.json --snapshot
   /inputs/snapshot.json --inputs /inputs/inputs.json [--current /inputs/current.json]`입니다.
   API upgrade/enable/disable/rollback-upgrade는 exact UID/RV/spec JSON Patch를 emit합니다.
   Upgrade는 새 API image+기존 nativeoff, enable은 selected UID mapping을 적용합니다. 원래
   GitHub/session/TLS/HTTP/HTTPS/DB schema/role/data/기존 Secret 및 web image는 유지합니다.
3. **Fresh web state:** init-policy와 init Job은 기존 dedicated Retain PVC 두 개에 **새 subdirectory**
   `/native/access-web`, `/control/access-web`만 만들며 기존 populated path면 거절합니다.
   Native/vendor/auth를 실행하지 않는 source init입니다. Partial init이면 자동 재실행/기존 path
   삭제하지 않고 실패 위치 metadata를 review에 인계합니다. ServiceAccount token/권한 추가 없음.
4. **Private transport/mapping:** 기존 검토된 Secret 생성 방식으로 새 runner transport token을
   생성합니다. 동일 token은 새 API-input Secret의 runners.json 및 tokenFile에만 바인딩합니다.
   이는 API↔runner 인증용 random token이며 Codex credential이 아닙니다. Source access token을
   Secret에 넣지 않습니다. Renderer `api-input`, config, policy, runner, service를 dry-run/review
   한 뒤 적용합니다. 새 runner Recreate1, UID10001/state header, 독립 Service입니다. Readiness는
   owner/state health 응답만 확인하므로 token 미공급/만료에도 API가 상태와 보존된 history를
   조회할 수 있습니다. Cilium allowlist는 chatgpt.com443와 API callback/DNS만입니다. Auth refresh/
   api.openai.com/metrics 호스트를 새로 허용하지 않습니다. L3 FQDN policy는 URL/path 분리를
   증명하지 않으며 no-refresh는 ephemeral external auth/callback refusal/no retry로도 통제합니다.
5. **명시적 source 진단:** PM이 선택한 canonical file 하나의 경로/owner/mode/inode를 private로
   고정합니다. Same-UID reader Docker에 그 파일만 readonly `/selected/source.json`으로 bind하며
   host path symlink/canonical identity도 mount 전후 metadata로 확인합니다. Host credential 값을
   읽어 console로 확인하지 않습니다. `--network none/read-only/cap-drop/no-new-privileges`,
   `--user <선택 file 소유자인 현재 operator UID:GID>`, `--workdir /app`인 **새 production API image**에서
   `python -B -m ops.access_snapshot --source /selected/source.json --diagnose`를 실행합니다.
   출력은 eligibility/format/expiry 존재/claim match/signature·entitlement 미검증 boolean과 safe code만.
   실패한 다른 file/HOME/keyring/계정을 검색하거나 새 로그인으로 대체하지 않습니다.
6. **검토된 private 공급:** operator-owned0700 input directory를 readonly `/operator`로 mount합니다.
   그 안의 inputs JSON은 `ownerId,stateId,windowId,newWindow` 네 필드만입니다. 최초/new test 창은
   fresh window UUID+true, 같은 창 snapshot 재공급은 기존 window UUID+false입니다. 다음 두
   명령을 **직접 pipe**로 연결하고 pipefail을 적용합니다. Export stdout를 파일/tee/console/
   message에 넣지 않습니다. Envelope에는 access/account/exp/owner/state/window만 있습니다.

   ```text
   <same-UID readonly source Docker> python -B -m ops.access_snapshot --source /selected/source.json --inputs /operator/inputs.json --export
     | <verified k3s kubectl wrapper> -n codex-trial exec -i <exact selected Pod> -- python -B -m runner.access_supply /control/access-web/access.sock
   ```

   실제 공급은 `supply_access_web.sh`를 사용합니다. PM의 기존 승인된 Docker/kubectl wrapper를
   exported shell functions ACCESS_SUPPLY_DOCKER/ACCESS_SUPPLY_KUBECTL로 제공합니다.
   ACCESS_SUPPLY_IMAGE는 reviewed production API Docker ID, SOURCE는 선택 file, INPUT_DIR은
   inputs.json만 담은0700 directory, POD/POD_UID는 exact applied runner,
   RUNNER_REF는 images.json의 immutable production runner reference입니다. Script는
   **일회 source reader에 --log-driver=none을 강제**합니다. Pipe만으로 Docker json-file stdout
   logging이 꺼지는 것은 아닙니다. Daemon/global/원래 Pod logging 설정은 변경하지 않습니다.
   Supply script 전체 stdout(안전 receipt)/stderr도 private로 보존하고 partial failure는 unknown입니다.

   Exec는 기존 Kubernetes TLS/신뢰 context로 수행하며 대상 Pod UID/imageID/owner/state/해당
   Deployment를 전후 확인합니다. Stdin/exec session recording이나 payload 저장이 있는 경로는
   사용하지 않습니다. 새 global logging/RBAC/daemon 설정을 변경하지 않습니다. Ingress는 local Unix
   socket0600+private parent+SO_PEERCRED same UID이며 public HTTP 공급 endpoint가 없습니다.
   Safe accepted receipt만 출력합니다. Receipt timeout/끊김은 unknown으로 두고 health/ledger의
   비밀 아닌 기존 상태를 먼저 확인하며 자동 재공급/새 창 생성/turn 재전송하지 않습니다.
7. **Enable/live 확인:** reviewed API enable patch를 적용하고 본인 HTTPS GitHub UID에서 status,
   fixed gpt-6.1-sol의 두 성공 turn·research tool·같은 conversation follow-up을 확인합니다.
   실제 provider entitlement/routing 실패면 exact safe code를 기록하고 연결 완료로 보고하지
   않습니다. 202 뒤 만료/401/429도 원래 입력 보존·소비·no autoresend를 확인합니다. Native 결과
   불명 요청은 다시 보내지 않습니다. 총3 ledger 검증과 사용자가 쓸 다음 창을 구분합니다.
8. **재공급/재개:** Native active turn 또는 protocol lock 점유 중 공급은 거절합니다. 같은 owner/
   state/provider account만 받습니다. 정상 quiescent snapshot 재공급은 native 외부 login만
   갱신하고 bookmark/history/request dedup을 보존합니다. Process 재시작은 token을 잃어
   not_configured이며 운영자 명시적 같은 창 공급 또는 새 창으로만 재개합니다. 동일 session은
   기존 expectedThreadId로 resume하고 mismatch면 새 thread를 만들지 않습니다. 만료 창/소진
   창 재개는 **새 windowId**이며 old window replay는 거절합니다. Model block은 자동 해제하지 않습니다.
9. **Disable/recovery:** 먼저 reviewed API disable patch로 nativeoff/mapping off, 이후 web runner만
   replicas0로 정상 정리합니다. Runner lifecycle patch는 PM이 보존한 exact Deployment UID/RV/spec
   test와 `/spec/replicas` 교체만 사용하며 renderer runner-stop/runner-start에 보존된
   runner/runnerDryRun baseline과 fresh current GET을 전달합니다. Rollout status/기존 Pod 종료를
   확인합니다. 필요하면
   reviewed rollback-upgrade로 API70 image/spec까지 복귀합니다. Root HTTPS/web/DB/auth/research/
   원래 세션/cache는 유지합니다. Latest web history/broker/ledger/projection을 PVC에서 보존하고
   old cache/ledger snapshot을 덮어써 budget을 초기화하지 않습니다. Restart 후 재공급 시
   revoked/failed/unknown request를 자동 replay하지 않습니다. Source file/원래 cache copy-back 없음.

## Front 계약과 결과 구분

`/api/research/codex/status`에 optional accessExpiresAt(RFC3339|null, 실제 admission deadline)와
remainingAdmissions(0~3|null)을 추가했습니다. expired=auth_expired/codex_auth_expired,
external refresh/401=auth_revoked/codex_auth_revoked,
local quota=budget_exhausted/codex_budget_exhausted,
provider429/usage=provider_rate_limited/codex_rate_limited입니다. 확인된 pre-admission 거절은503,
202 후에는 polling failed입니다. Native userMessage 확인 여부에 따라 recorded/not_recorded를
유지하며 transport ambiguity는 기존 running/key 보존입니다. RetryAfter는 추정하지 않습니다.
재공급 시 unverified, 그 snapshot의 actual fixed-model completed turn 뒤 real입니다.

Source/unit/mock/native wire 검증, production build/import, 실제 공급/응답, 사용자 브라우저
성공을 각각 기록합니다. 첫 세 단계 통과만으로 마지막 성공이나 장기 refresh 완료를 주장하지 않습니다.
