# #7 현재 작업 인증을 유지하는 UID별 웹 연결 실행 경로

계획/의사결정 자료입니다. 실제 credential 접근·공급, 원래 세션 전환,
새 authority/bridge 시작, image import/배포를 실행하는 지시가 아닙니다.

## 결론과 현재 근거

companion과 tool wire 문제는 해결됐지만 **현재 embedded 작업 세션을 무변경으로
유지하면서 지속적인 웹 인증/refresh 소유자를 추가하는 경로는 입증되지 않았습니다.**
기존 runner의 companion package/host flag 수정과 실제 Native mock 회귀는 승인됐습니다.
이것이 기존 인증원의 refresh 소유권이나 공유 authority 전환까지 해결하지는 않습니다.

2026-10-10에 확보한 metadata에서 선택한 PM/back/review0.160.1 작업 세션은
각자 embedded 연결을 사용했습니다. 관찰된 socketpair는 각 PID 내부였으며,
별도0.162.1 daemon은 이 세션의 인증 소유자로 확인되지 않았습니다. PATH launcher
0.162.0도 선택한 실행 파일과 다릅니다. 이들을 재사용하거나 교체 대상으로 자동 선택하지
않습니다. metadata의 기본 HOME/config/file 존재는 실제 사용 중인 credential store와
현재 grant/모든 writer를 확정하지 않습니다. 다음 운영 직전에는 좁은 범위로 다시 확인해야 합니다.

pinned source의 AuthManager cache/refresh lock은 manager 단위입니다. 바깥 flock을
잡거나 같은 auth.json을 다른 Pod에 복사해도 현재 세션의 refresh가 그 lock에 참여하지
않습니다. PersonalAuth의 exclusive-managed-native/hostGrantQuiescent 선언을 현재
공유 작업 세션에 사실처럼 적용할 수 없습니다. 기존 cache-copy runner를 현재 인증으로
켜는 것이 이 계획의 기본 경로가 아닙니다.

이미 확정된 사실은 재사용합니다: 기존 HTTPS/API70 nativeoff/runner Pod0, 선택한
GitHub login의 exact-one eligible platform UID lookup, stage1/backup/PV 검증,
stock stdio/Unix transport·정상 tool 왕복, raw peer foreign thread/event 노출,
companion source 및 고정 모델/max3/중복/권한 mock 회귀. Lookup/backup/companion 원인
시험을 반복하지 않습니다. 실제 provider entitlement/원래 source 적용성은 아직 별도입니다.

## 1. 현재 세션 무변경으로 가능한 범위

- 기존 웹의 GitHub 로그인/연구 자료 기능과 현재 작업 세션을 유지하고 Codex native
  연결은 비활성으로 둡니다. 이미 얻은 소스/transport 증거로 구현·운영 계획을 준비할 수 있습니다.
- 새 persistent server를 같은 cache에 연결하거나 copied renewable cache를 웹 runner에
  주는 작업은 단일 refresh 소유자를 만들지 못합니다. 기존0.162.1 daemon의 존재나
  account/read 한 번의 성공도 이 문제를 해결하지 않습니다.
- 이전 frozen access-only 제안은 추가 구현/실제 조건 확인이 필요한 짧은 제한시험입니다.
  현재 배포된 대체 경로나 지속 연결 완료가 아니며, 이번 계획에서 자동 선택하지 않습니다.

따라서 현재 세션에 아무 변화도 허용하지 않는 선택은 지속 연결을 보류하는 선택입니다.
별도 로그인/API key/다른 모델을 조용히 추가해 이 제한을 우회하지 않습니다.

## 2. 지속 연결에 필요한 구조와 영향

추천 방향은 **선택한 현재 grant를 사용하는 persistent stock authority 한 개**와,
그 authority에 정상 lifecycle로 연결하는 원래 client 및 소유 웹 adapter입니다.
원래 grant를 여러 renewable native 인스턴스에 복제하지 않습니다.

| 구성 | 책임/경계 | 현재 상태 |
| --- | --- | --- |
| 선택한 source host의 persistent authority | 같은 native0.160.1/companion, 확인된 원래 credential store의 유일한 정상 refresh writer; client 종료와 별도 수명 | 미구축/실제 source 미확정 |
| 원래 작업 TUI | 작업 종료/정상 resume 시 explicit remote 접속; worktree/role 지침/원래 thread 보존 | 현재 embedded, hot 전환 불가 |
| 신뢰된 host coordinator/bridge | 원래 auth 변경과 웹 admission 직렬화, selected-account 조건, 보호된 stock socket; 웹에 raw endpoint 노출 금지 | 미구현 |
| UID별 웹 adapter | 기존 HTTP/UID-state/session-request 계약, private owned-thread mapping/ledger/projection, 허용 RPC/event/tool 응답만 전달 | 기존 isolated adapter 재사용 부분 있음; remote mode 미구현 |
| 기존 API/웹 | 서버가 선택한 platform UID mapping 및 기존 role/session/toolToken 권한 | 기존 계약 유지 |

권장 authority는 현재 source host/확인된 store 소유자에게 둡니다. 현재 확인된 UID1000
자료는 준비 근거이며 실제 host/store identity를 preflight 없이 가정하지 않습니다.
웹 Pod에 원래 HOME/auth.json/daemon socket 전체를 mount하지 않습니다. Pod의 UID10001과
host UID1000/socket600 차이를 chmod 확대나 host root 접근으로 해결하지 않습니다.
보호된 host bridge가 필요한 접속을 맡고 owner별 인증 transport를 제공합니다. 노드/주소/
TLS/허용 peer/network policy는 운영 패키지에서 고정하며 새 public native listener는 만들지 않습니다.

원래 client는 원래 thread를 정상 종료/재연결 때 resume해야 합니다. 원래 history와
역할별 cwd/config/MCP·tool 기능, external agents watcher 전달 경로도 유지되어야 합니다.
현재 client를 강제 종료하거나 살아 있는 embedded manager를 새 server로 hot 이동하는
지원 경로는 입증되지 않았습니다. 원래 정상 종료가 없으면 전환하지 않습니다.

새 client 전용 HOME/config에는 authority 접속을 명시하고 독립 cached login/자동 daemon
시작이 다시 같은 grant의 writer가 되지 않도록 합니다. 현재 원래 store가 file/ephemeral/
외부 host token 등 무엇인지 먼저 확인해야 합니다. **메모리 인증만 있고 같은 버전의
지원되는 안전한 이전 방법이 없으면 이 경로를 성립했다고 하지 않습니다.** 다른 HOME/
keyring을 탐색하거나 별도 login/캐시 복사/fork로 대체하지 않고 제한을 보고합니다.

웹 thread는 fixed gpt-6.1-sol/readonly cwd/허용 research tool/금지 기능을 가져야 합니다.
원래 TUI의 정상 작업 기능을 글로벌 flag로 꺼서 웹을 제한하면 안 됩니다. 기존 성공
fixture는 globally restricted server였습니다. 공유 server에서의 **thread별 제한과 원래
thread 기능 유지**는 아직 검증되지 않은 연결 전 필수 gate입니다.

또한 웹 RPC whitelist/UID→thread 소유 관계와 event 필터가 원래 thread/read/list/name/
auth/config/process 접근을 차단해야 합니다. Foreign event는 model/error/tool 처리 전에
버리고, 기존 turn/call/epoch 검사를 유지합니다. Closing 웹 adapter는 자기 연결/소유
turn만 정리하고 authority나 원래 client를 종료하지 않습니다.

account/read→turn/start 사이의 stock expected-owner 조건은 입증되지 않았습니다.
숫자 auth-generation 기능 자체를 새 요구로 추가하지 않습니다. 필요한 조건은 **다른
계정 권한으로 dispatch하지 않고 기존 인증을 손상하지 않는 것**입니다. 모든 정상 auth
writer를 확인·단일화하고 원래 login/logout/account-routing/config 변경도 coordinator를
통과시키며, 웹 active turn 동안 변경을 보류/거절한 뒤 재검증해야 합니다. 바깥 writer나
직접 auth 변경이 남아 있으면 gateway mutex/알림 관찰만으로 닫힌 경계라고 하지 않습니다.
원래 신뢰된 operator의 캐시 직접 변경도 그 운영 절차 밖에서 허용하지 않는 계약이 필요합니다.

처음에는 이미 lookup한 **선택 UID 하나만** 이 source에 매핑하고 미매핑 UID는 거절합니다.
다른 platform UID에게 현재 grant를 공통 pool로 주지 않습니다. 추가 UID에는 그 사용자의
source/consent/writer 조건을 별도로 충족해야 합니다. Pod UID는 계속 보조 metadata입니다.

## 3. 연결 전에 닫을 gate / 연결 뒤 확인

| 연결 전: 실제 공급·dispatch 전에 충족 | 연결 뒤: 실제 결과로 확인 |
| --- | --- |
| 선택한 실제 store/grant/source holder·platform UID의 private binding, 모든 writer·정상 lifecycle 전환 가능성 | 정확한 선택 계정/account routing 및 gpt-6.1-sol의 실제 권한; mock을 entitlement로 대체하지 않음 |
| thread별 웹 제한/원래 기능 보존 및 coordinator의 auth-change/admission 경계 | 실제 두 명시적 성공 turn, research tool 결과·history 및 같은 conversation follow-up |
| UID/RPC/event ownership, late/duplicate tool correlation, private bookmark/ledger/projection/permission revocation | 진행 중 tool의 disconnect/late response/원래 client 동시 작업에서 foreign 효과0 |
| 실제 target/source SHA·production image·helper provenance, private bridge peer/TLS/CNI, state/backup/recovery 절차 | adapter 재시작 후 동일 owner/state/thread 및 durable budget; ambiguous request 재전송0 |
| 단계별 apply/abort/rollback과 원래 client가 다시 열 수 있는 recovery 경로; 사용자 전환 선택 | 원래 client 정상 종료 뒤 authority 지속/정상 refresh 및 복구 후 원래 작업 계속 가능 |

연결 전 gate는 구현/정적 확인 및 기존 근거 재사용으로 먼저 좁힙니다. 필요한 새로운
최소 검증 단위는 PM/review가 이 계획을 선택·범위화한 뒤 진행합니다. 지금 추가 fixture
프로젝트나 실제 auth RPC를 자동 시작하지 않습니다. 실제 공급·provider 확인·원래
client 재연결은 별도 검토된 PM 운영 단계입니다. 실패하면 웹 admission을 닫습니다.

## 4. 최소 구현과 운영 순서, 이미지/복구

1. **계획 선택과 준비:** PM/review가 source topology와 연결 전 gate를 검토합니다.
   필요한 read-only preflight를 정확한 source/대상에 한정해 모든 writer/store/client
   resume 조건을 확인합니다. Existing owner lookup 결과는 private handoff로 재사용합니다.
2. **최소 소스:** 기존 adapter interface/ledger/binding/projection을 사용하는 remote mode,
   보호된 host bridge/coordinator, 원래 client remote 설정과 thread별 제한을 구현합니다.
   Remote mode는 PersonalAuth.attach/cache copy/Native child spawn을 사용하지 않습니다.
   Account/profile change의 interlock이 성립하지 않으면 actual 연결을 열지 않습니다.
3. **production package review:** 새 전체 소스 SHA와 대상/허용 경로를 고정합니다.
   Backend/API는 backend/Dockerfile runtime, 실제 runner는 runner target로 각각 build합니다.
   현재 승인된 package core SHA는1949f45e02f0e3d7eecfd07eb6c44150e1f7e126입니다.
   Remote/coordinator 구현은 아직 없으므로 **지속 연결 deployment SHA는 미정**이며
   구현 후 새 독립 승인 SHA로 바인딩해야 합니다. Host authority는 동일 native/helper
   pin과 owner/store/접속 권한을 갖춘 별도 reviewed service 패키지입니다.
4. **정상 전환 운영:** 사용자가 허용한 정상 작업 종료/재연결 시점에 웹 admission을
   계속 닫은 채 PM이 상태·history/control metadata를 보존합니다. 마지막 legacy writer가
   끝난 것을 확인한 뒤 authority를 시작하고 원래 client를 remote로 정상 resume합니다.
   이때 source 공급/접근 및 provider 접촉도 승인된 절차에 한정합니다. 새 reader/writer를
   먼저 병렬 시작하거나 원래 세션 종료를 강제하지 않습니다.
5. **웹 연결 운영:** 원래 작업 복구를 먼저 확인한 뒤 선택 UID 하나의 새 adapter/bridge
   및 private mapping을 단계적으로 적용하고 read-only auth 확인을 거칩니다. 실제 turn
   admission은 ownership/source 조건이 안정된 뒤에만 엽니다. 기존 총3/두 성공 turn·실패/
   unknown 소비/no autoresend 제한을 보존합니다.
6. **검증/복구:** 연결 뒤 gate를 PM 실행·review 독립 확인으로 닫습니다. 이상 시 먼저
   웹 admission/매핑을 비활성화하고 소유 작업만 정리합니다. 무응답 요청은 소비/불명 상태로
   남기며 자동 재전송하지 않습니다. 원래 작업과 authority를 웹 rollback에 묶어 kill하지 않습니다.

기존 e1cd49…는 **runner-test** 이미지, afba3cf…는 **companion fixture** 이미지입니다.
둘 모두 배포 이미지로 취급하지 않습니다. 새 runner/API image는 production target, 정확한
최종 source SHA/builtAt/index/arch manifest/config/archive 및 import provenance를 별도
패키지로 고정해야 합니다. 테스트 통과나 소스 승인이 운영 apply 승인은 아닙니다.

웹 rollback 기준은 현재 API70 nativeoff와 선택 UID 매핑 비활성/runner0 상태입니다.
현재 API70의 image/index·실제 spec은 보존된 stage1 evidence에서 그대로 재사용하고
운영 직전 GET/dry-run으로 확인합니다. 오래된8fa nativeoff rollback을 현재 API70
baseline과 혼동하지 않습니다. DB/auth/research를 이전 snapshot으로 덮어쓰지 않습니다.

authority/client 전환의 recovery는 웹 image rollback과 다릅니다. 최신 native history/
control 및 **최신 회전된 credential store**를 보존하고 모든 writer 상태를 확인해 원래
client가 정상 접속하도록 합니다. 이전 auth.json backup으로 회전된 credential을 되돌리거나
원래 cache에 private export를 copy-back하지 않습니다. 원래 cache/source를 이전해야 하는
경우 그 방식·정상 client recovery도 사용자가 허용한 운영 패키지에 포함해야 합니다.
원래 로그인 보존이 보장되지 않으면 그 단계 전에 중단하고 실제 제한을 보고합니다.

## 5. 사용자에게 필요한 결정

선택할 구현 세부사항은 back이 맡습니다. 사용자에게 필요한 것은 **현재 인증을 쓰는
전체 작업 세션의 정상 lifecycle 전환 및 그 동안의 auth 변경 순서 통제 허용 여부**입니다.

| 선택 | 결과와 영향 |
| --- | --- |
| 추천: 현재 작업을 정상 마친 뒤, 같은 grant를 쓰는 client들을 한 persistent authority로 정상 재연결하도록 허용 | 별도 login/API key/model 변경 없이 지속 연결을 목표로 진행할 수 있음. 전환/재연결 창과 auth 변경 시 웹 admission 중지 계약이 필요하며 위 미완료 gate가 먼저 충족되어야 함 |
| 현재 embedded 세션/인증 소유 구조를 계속 무변경으로 유지 | 원래 작업은 유지. 현재 증거로 지속 웹 연결을 안전하게 완료할 수 없어 nativeoff 상태를 유지하고 연결을 보류 |

이는 지금 세션을 종료하라는 요청이나 live 승인 추정이 아닙니다. PM은 검토된 target/
resume/recovery/영향 패키지를 사용자에게 제시하고 정상 전환 시점을 받아야 합니다.
현재 source가 지원되는 방식으로 이전 불가능하다는 결론이면 제한과 선택지를 다시
명시하고 사용자 선택 없이 새 login/key/version/fork를 시작하지 않습니다.

근거: 기존 SHARED-SESSION-AUTH.md / SHARED-AUTH-OPTIONS.md, 승인된
SHARED-EVENT-FIXTURE.md 및 companion/actual-runner 검증 artifacts. 계획 작성 중
새 fixture/원래 runtime/actual auth/provider/deployment 작업은 시작하지 않았습니다.
