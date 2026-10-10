# Existing M3 root HTTPS / native follow-up for issue7

검토 대상은 기존 `m2-hosting`의 root 서비스와 같은 `hosting` DB입니다.
새 trial DB/OAuth 앱/prefix/PC IP를 기본 입력으로 요구하지 않습니다. PM이
운영하고 back이 산출물을 작성하며 review가 source/절차/실제 결과를 구분해
확인합니다. 이 문서는 단계별 절차 검토용이며 빈 입력으로 apply 가능한
manifest가 아닙니다. Credential 공급/native 시작은 이번 준비에서 수행하지
않습니다. 공개 issue 게시 보류와 최종 PR 승인 조건은 그대로입니다.

## 이미 완료된 기준점과 재사용할 근거

`083b154aa5a473444ac0cb77a30ff292620d4164` 절차로 PM이 실제
before→0004→after→API→root web을 완료했고 reviewer가 운영 checkpoint를
독립 확인했습니다. Auth0001, 원래 DB/PVC/PG Pod/config/Secret identity를
보존하고 research0004를 추가했습니다. API native=false/owner empty/map
empty이며 runner는 없습니다. API b19와 root web edff의 descriptor/CRI/실제
Pod 연결까지 확인됐습니다. 이 성공을 OAuth/session/native 성공으로 확대하지
않습니다. 실제 archive 복원, PV 네 단계와 이미지 감사는 변경 없으면 반복하지
않고 운영 직전 현재 baseline/backup recency를 확인합니다.

기존 TLS source의 공개 metadata 검사에서는 SAN/key-match/현재 유효=true,
notAfter `2026-10-15T19:56:04Z`였습니다. 실제 serving handshake/trust와
갱신 이후 동일성이 확인된 것은 아닙니다. 이미 있는 GitHub client/secret/
transaction key/DB URL을 다시 요청하지 않습니다.

## 선택한 변경 방향과 코드 경계

기본 제안은 **기존 API/root web을 canonical HTTPS origin으로 단계 전환**하는
것입니다. 계정/roles/DB 원래 행을 보존하며 native=false로 HTTPS 로그인부터
검증합니다. 기존 origin의 session digest가 HTTPS origin에서 달라지므로 기존
HTTP 쿠키를 이식하지 않고 기존 GitHub 계정으로 재로그인합니다. 변경된 세션은
같은 account UUID를 가리켜야 합니다. HTTP origin에 남아 있는 browser pending
request를 새 origin으로 복사하거나 자동 재전송하지 않습니다. HTTPS 전환 전에
진행 중 쓰기/로그인 작업을 마무리하고 영향 시각을 PM이 기록합니다.

원래 HTTP **인증 동작까지 병행 유지해야 한다는 실제 요구가 있으면**, 동일
host/path의 두 API view를 즉석에서 추가하지 않습니다. 현재 cookie 이름/path는
scheme 간 분리되지 않고 세션은 origin-bound라 충돌/로그인 반복이 가능합니다.
별도 cookie namespace 또는 별도 검토 origin의 설계가 먼저 필요합니다. 이는
새 DB/OAuth 요구가 아니라 구체적인 병행 인증 구현 조건입니다.

현재 배포 b19 API는 non-loopback native gate에서 `/codex-trial`만 허용합니다.
이번 source는 root에만 별도 `CODEX_PERSONAL_ROOT_ENABLE=true`를 추가합니다.
기존 `APP_ENV=personal-test`, `CODEX_PERSONAL_ENABLE=true`, HTTPS/Secure,
exact `CODEX_PERSONAL_REMOTE_ORIGIN`, trusted proxy, personal-private 조건을
모두 유지합니다. 빈 base path만 추가 허용하며 다른 path/HTTP는 거절합니다.
설정 기본값은 off; prefix와 loopback의 기존 동작은 유지합니다. 기존 single-owner
mapping, account 권한, native grant/max3/model 검증은 변경하지 않습니다.

**배포된 b19 이미지에 이 변경이 들어 있다고 간주하면 안 됩니다.** Root gate
source 검토 후 새 API runtime image를 정확 SHA로 빌드/import/audit해야 합니다.
runner source는 변경하지 않았으므로 기존 pinned runner 이미지의 source tree
동일성/installed CLI SHA/provenance를 확인해 재사용할 수 있습니다. API source
SHA와 runner/web release SHA는 각각 기록합니다. 기존 image_audit.py의 b19
정책을 새 digest에 그대로 대입하거나 operator가 상수를 고쳐 통과시키지 않습니다.
새 API 이미지 정책/새 target 설정 emitter는 back의 후속 산출물과 독립 검토 대상입니다.

## P0 — PM이 지금 수집할 수 있는 정확한 metadata

이 단계는 token/cache 복사, 원본 session stop, native 시작 없이 합니다.
실제 값은 mode0700 private 디렉터리/mode0600 파일에만 둡니다. 공유 결과는
boolean/미충족 항목/시각/source SHA만 전달합니다.

| 입력 | 재사용 또는 필요한 좁은 확인 | 실패/미상 시 처리 |
|---|---|---|
| 기존 service baseline | 승인 rollout 결과, 현재 API/web/PG/PVC UID/image/Ready, 원래 config/Secret UID/RV, 적용 CNP/Traefik route | resource drift면 exact 변경을 재평가; DB 재초기화/Secret 교체 안 함 |
| HTTPS root route | 기존 host의 websecure 라우터 전체 match/priority, 다른 앱의 정확 path, HTTP redirect 유무, 실제 trusted Traefik peer 범위 | broad root route가 다른 HTTPS 앱을 가리지 않게 exact exclusions/priority 작성; 공유 controller/listener 수정 안 함 |
| TLS generation | 이미 선택된 source Secret generation/owner, SAN/key-match/CA trust/시험 종료까지 expiry, snapshot renewal 관리 | 원본 Secret/ACME/archive 변경 없음; target-local 새 generation만 검토 |
| 기존 GitHub callback | 기존 앱 등록이 정확 `<https-origin>/api/auth/github/callback`을 수용하는지 owner의 private 등록 확인 | secret/client 재수집 안 함. 등록 영향이 있으면 정확 변경/복귀 절차를 검토; 새 앱 자동 생성 안 함 |
| 플랫폼 owner | 기존 GitHub 로그인 identity의 account UUID와 필요한 기존 approved editor/admin 권한 | UUID/role 추측 또는 기존 account 임의 승격 안 함; 정상 기존 관리 절차로 확인 |
| 실제 사용 범위/접근 | 실제 이용자가 계정 owner의 private OSS client인지, 이미 존재하는 private 접근 수단의 exact enforcement와 직접 public 경로 차단 근거 | GitHub allowlist만으로 supported/private 분류 확정 안 함. PC IP 대신 이미 있는 수단부터 확인 |
| 선택된 server ChatGPT source | owner가 이미 선택한 file-cache의 format/version/store mode, provider binding, 해당 renewable grant의 소비자 및 writer 범위 | HOME/keyring/경로 자동 탐색·cache raw 읽기/출력 안 함. owner는 token 값이 아닌 적합성 결과만 공유 |
| grant 전체 수명 | 시험 동안뿐 아니라 clean 종료 이후 latest-cache 소유/소비 경로와 원본 host 연속성 | 같은 계정이라는 사실만으로 grant 독립성 추정 안 함; 아래 분기 적용 |
| pinned endpoint inventory | native0.160.1의 auth/refresh/inference 및 DNS/transport 실제 source 근거, exact destination names | credentialed runner를 켜서 추측하거나 wildcard egress로 관찰 안 함 |

원본 source private 경로/계정/provider ID/토큰/쿠키/client ID를 message나 GitHub
issue로 받지 않습니다. 기존 GitHub 로그인 metadata가 ChatGPT auth 증거는 아닙니다.

## P1 — HTTPS, native-disabled 운영 산출물/검증 순서

P0 route/callback/TLS metadata를 바탕으로 back이 정확 UID/RV 변경·복귀 patch,
target-local immutable config/TLS references, root route exclusions/priority,
현재 policy 적용성, 자원/교체 계획을 작성합니다. Review 후 PM이 실행합니다.

1. 운영 직전 readiness, 진행 중 작업, backup recency, source/Secret identity와
   원래 HTTP/다른 HTTPS 앱 baseline을 확인합니다. 원래 TLS source/renewal은
   그대로 둡니다. 새 target-local generation의 충돌/소유권을 확인합니다.
2. 이미 승인된 TLS metadata reader로 actual source generation을 재검증합니다.
   Exit0와 valid/SAN/key-match=true를 함께 요구합니다. Partial/timeout을 pass로
   보지 않습니다. CA/trust와 충분한 lifetime도 별도로 확인합니다.
3. 승인된 private snapshot을 `m2-hosting` 새 generation으로 공급합니다. Secret
   YAML/key를 shared stdout에 덤프하지 않습니다. Namespace 간 Secret reference
   또는 원본 Secret overwrite를 하지 않습니다.
4. Native=false/owner empty/map empty 상태로 HTTPS origin, Secure cookie,
   root base path, exact trusted proxy 범위 및 route를 staged dry-run/diff/apply
   합니다. Cookie path는 `/api`, callback은 `/api/auth/github/callback`입니다.
   직접 HTTP/위조 X-Forwarded-Proto는 session 발급/인증 경로가 될 수 없습니다.
5. Public edge에서 fixture와 `/api/internal`을 제외하고 encoded path ambiguity
   일곱 종류를 기존 검토 패턴으로 차단합니다. Query의 정상 OAuth encoding은
   유지합니다. 공유 Traefik entrypoint 옵션을 임의 변경하지 않습니다. 실제
   installed CRD/controller가 route-local middleware를 처리하는지 검증합니다.
6. Certificate CA/SAN/fingerprint/expiry/live serving, root/assets/version, 정상
   사용자 GitHub success/failure, Secure/path/HttpOnly/SameSite, Origin+CSRF,
   trusted/untrusted forwarding, 같은 account UUID/role 및 기존 자료 조회를
   확인합니다. Raw Location/code/cookie/account values는 private 기록에만 둡니다.
   GET auth/start는 DB 쓰기이므로 readonly 단계에서 호출하지 않습니다.
7. Native status는 계속 not_configured, runner/credential/provider startup0을
   확인합니다. HTTP endpoint에 authenticated native를 남겨 두지 않습니다.
   다른 앱 route와 원래 PG/PVC/Secret identity를 다시 확인합니다.

전역 origin 교체와 route 변경은 같은 검토 변경 집합입니다. 같은 image만 되돌려
origin/route 복귀가 됐다고 판정하지 않습니다. 실패/미상 시 native admission을
닫고 새 route를 비활성화한 뒤 exact 이전 origin/config/route의 검토된 순서로
복귀합니다. HTTPS에서 발급된 session은 origin이 다르므로 HTTP에서 재사용하지
않습니다. DB 행을 삭제/restore/downgrade해서 cookie를 복원하지 않습니다.

## P2 — 인증 source와 refresh ownership의 실행 가능한 분기

공식 [auth/headless 안내](https://learn.chatgpt.com/docs/auth)는 file/keyring
차이와 managed refresh를 설명합니다. 공식
[app-server 인증 경계](https://learn.chatgpt.com/docs/app-server#auth-endpoints)는
commercial/hosted 서비스에 legacy auth를 허용하지 않습니다. 기존 서버 사용
요청을 곧바로 unsupported라고 단정하지 않지만 실제 private OSS 사용 근거가
필요합니다. 공개 회원에게 계정 owner의 grant를 제공하는 구성이면 이 candidate를
활성화하지 않습니다. 새 SIWC UI/API key/다른 model을 임의 대체하지 않습니다.

공식 [session refresh 안내](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions#refreshing-tokens)는
동일 session의 refresh 직렬화와 최신 교체값 저장을 요구합니다. 새 토큰 client를
직접 구현하라는 근거가 아니며 native managed auth를 그대로 사용합니다.

| 선택된 기존 source의 실제 상태 | 현재 코드로 가능한 조치 | 추가 구현/운영 조건 |
|---|---|---|
| 동일 server 계정의 이미 존재하는 독립 grant, 해당 grant 외부 writer 없음 | 승인된 source를 private control로 공급하고 단일 native가 갱신; 최신 control이 그 grant의 authoritative store | grant 독립성/소비자 근거, 시험 후 latest-cache의 지속 소유자/수명. 새 로그인/grant 생성 안 함 |
| 동일 renewable grant가 원래 CLI/IDE/agent에도 사용됨 | 현재 구현으로 안전한 동시 활성화 불가 | owning client까지 참여하는 whole-lifetime serialization/latest-cache handoff 구현·리뷰 필요. 원본 수정/중단 금지와 충돌하는 정확 지점을 PM에게 특정 |
| source가 keyring-only/현재 schema와 다름 또는 writer 불명 | 공급/attach 이전 보류 | 선택 source/store의 좁은 호환 구현 또는 owner metadata 필요. 파일 복사로 grant 분리/hostGrantQuiescent=true 추정 안 함 |

현재 lease는 private control 내부만 잠그고 clean exit는 최신 cache를 그 control에
씁니다. **원래 host에는 돌려쓰지 않습니다.** Cleanup은 cache를 삭제하므로
그 전에 최신 credential을 필요로 하는 owner의 검토된 연속성 절차가 있어야
합니다. 같은 grant 사용에서 최신 host cache가 요구되면 이 문서는 임의 copy-back
명령을 제공하지 않습니다. Native startup 자체가 인증/refresh를 할 수 있으므로
source/ownership 조건은 첫 turn 이전이 아니라 첫 process 시작 이전에 충족합니다.

## P3 — 정확 root native 설정과 최대3 실제 dispatch

P1/P2 및 endpoint inventory가 해소된 후 back이 별도 정확 target emitter를
작성합니다. 기존 `infra/codex-trial/render.py`나 Compose new-DB bootstrap은
root에 현장 변환해서 쓰지 않습니다. 다음 계약을 그대로 적용합니다.

- API는 기존 DB/Secret을 사용하고 지정 UUID 하나만 runners.json에 매핑합니다.
  enable/root-enable/personal-test/private-scope/exact remote origin을 요구합니다.
  browser가 owner/model/runner/cache 경로를 선택하지 않습니다.
- Runner는 pinned0.160.1, executable SHA256
  `fbbaec80443919f86dd63648a0b62759cf6f1d0e09310602fde96885e0bceb3e`,
  모델 `gpt-6.1-sol`입니다. Account-specific native/control 저장소를 분리하고
  원래 host HOME/DB/Docker socket은 연결하지 않습니다. 하나의 process만 실행합니다.
- 이미 검증된 same-node codex-trial Retain PV는 배치 후보입니다. 사용하면
  PVC namespace는 그대로 두고 root API↔runner의 cross-namespace Cilium/Service
  적용성과 API callback `http://api.m2-hosting.svc.cluster.local:8080/api/internal/research/tools`
  를 exact 검토합니다. 이름은 현재 Service 확인 후 확정합니다. PVC를 이동하거나
  검증된 PV 시험을 반복하지 않습니다. 기존 namespace runner를 선택해도 새 native/
  control storage 적용성·capacity와 default-deny/추가 policy 합성은 따로 확인합니다.
- Credential-free/bootstrap runner egress deny를 실제 확인한 뒤만 approved exact
  provider DNS/TCP443와 API callback을 허용합니다. API는 runner/기존 DB/GitHub
  경로만 필요합니다. Runner에는 DB/ingress 접근을 주지 않습니다. Control loader는
  network deny, tokenless/non-root, 소유권10001/dir0700/file0600, 종료 확인을 요구합니다.
- PM private provisioning 후 network-none `python -m runner.auth --validate`를
  실행합니다. 이는 schema/binding만 검사하며 지원/권한/외부 writer를 증명하지
  않습니다. Grant≤1h/maxDispatches3와 기존 control ledger/tombstone은 유지합니다.

실행 승인/준비 확인 뒤 PM은 작은 합성 자료에 **전체 최대3 dispatch**를 사용합니다:
slot1 tool 저장/조회 및 실제 fixed-model 완료; slot2 같은 owner의 추가 권한/조회
검증; slot3 clean stop/restart 뒤 기존 thread 명시 follow-up/resume. 기존 multi-user
fixture visitor 검증은 재사용하며 실제 서버 grant를 다른 계정에 바인딩하지 않습니다.
실패/모호함/재시작 이후에도 같은 ledger를 소비하고 새 key로 자동 재시도하지
않습니다. Model catalog/config/status만으로 이용권을 pass 처리하지 않습니다.
[공식 모델 안내](https://learn.chatgpt.com/docs/models#gpt-61-sol)도 계정/client/
workspace별 가용성을 구분합니다. 실제 응답이 다른 model이면 quarantine/중단하고
model/API-key fallback을 하지 않습니다.

중단은 admission close→active work quiescence→owned runner clean stop→latest-cache
연속성 절차 확인→local cleanup/tombstone 보존 순서입니다. Unknown crash/lease/
refresh 상태에서 restore/ledger reset/native-home 교체로 재개하지 않습니다.
Runner/map/native enable을 먼저 제거하고 기존 HTTPS login/M3/data는 보존할 수
있습니다. TLS 문제는 root origin/config/route rollback과 별도로 처리합니다.

## 준비 결과의 정확한 한계

Source 변경은 root gate 하나이며 isolated Docker tests로 조건을 검사합니다.
실제 root HTTPS/private access, registered callback, selected file-cache/binding,
whole-lifetime refresh owner, exact provider inventory 및 fixed-model inference는
이번 source 검사로 확정되지 않습니다. P0 metadata 수집은 현재 가능한 작업이고,
P1/P3 exact manifest/이미지 정책은 실제 metadata를 반영한 별도 산출물입니다.
기존 사용자 승인을 반복 요청하는 대신 위 미상 항목의 **사실/owner 근거**만
PM private 입력으로 확보하고 source/절차 review를 계속 진행합니다.
