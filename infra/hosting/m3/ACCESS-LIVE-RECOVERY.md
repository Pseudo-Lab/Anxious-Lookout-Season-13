# #7 실패 후 읽기 전용 진단과 최소 정책 수정

기준: PM 실제 실패/rollback 및 review 독립 확인. API70 exact baseline ready1/updated1,
runner0/Pod0, enable/turn0, source stamp 불변입니다. Supply 한 번 accepted=false/unavailable는
window/ledger/projection 미설치를 뜻하지 않습니다. PVC를 지우거나 fresh init/새 창/재공급/
vendor startup을 먼저 실행하지 않습니다. 이 패키지는 독립 review 뒤 PM이 수행합니다.

## 이미 확인한 범위와 결함

70→915의 DB/readiness/settings/probe/requirements bytes는 같습니다. Startup-only 로그는
DB readiness 실패나 Native 실패 단계를 알려주지 않습니다. Broker/projection 존재만으로
provider 인증 성공을 주장하지 않습니다.

확인한 **보조 API link 정책 설계 결함**은 hosting-api 전체에 default-deny와 runner DNS
한 이름의 L7 규칙을 적용한 점입니다. 기존 API DB/DNS/GitHub 정책을 이 보조 link가 완전히
대체할 수 없습니다. 실제 readiness의 원인은 아직 미확정입니다. 기존 pool/conntrack와 새
Pod 초기 연결의 차이가 설명 후보일 뿐 실제 deny/DNS 결과로 확인해야 합니다.
[Cilium default enforcement](https://docs.cilium.io/en/stable/security/policy/intro/)와
[L7 DNS](https://docs.cilium.io/en/stable/security/policy/layer7/)의 제한을 근거로 API link만
additive default-deny=false/DNS L7 없음으로 수정합니다. Runner의 DNS/FQDN 정책은 그대로이며
익명 network 결과 없이 호스트/와일드카드/권한을 넓히지 않습니다.

## D1: stopped web state, vendor/원본 credential 접근 없음

Private 새 snapshot은 restored API70/config와 sealed native/control PVC/PV metadata,
fresh stopped `access-web-runner-v1` GET을 `runner`, 해당 selector Pod count0을 `runnerPodCount`로
담습니다. 기존 private owner/state inputs와 최초 window inputs를 재사용합니다. Source file과
transport token을 snapshot에 넣지 않습니다.

Docker에서 `access_diagnose_prepare.py state --snapshot ... --inputs ... --window-inputs ...`
emit합니다. 기존 production API597a image만 사용합니다. Server dry-run과 exact image/node/
PVC UIDs/runner0/Pod0를 review·PM이 확인한 뒤 deny-all CNP를 먼저 적용하고 Job을 적용합니다.
별도 namespace/RBAC/service/새 claim을 만들지 않습니다.

Job은 UID10001/capdrop/read-only-root/automountToken=false, timeout30s/backoff0입니다.
두 기존 claim을 **readOnly**로 mount하며 fsGroup/chmod/chown이 없습니다. Python stdlib가
`/native/access-web/state-binding.json`, `/control/access-web/access-ledger.json`만 bounded/
NOFOLLOW 읽습니다. Projection/broker/auth.json는 존재 여부만 보고 **내용을 읽지 않습니다**.
출력은 binding/window 일치 boolean, ledgerInstalled, window/lifetime counts, deadline/time-limit
passed, safe block reason/model block 및 artifact 존재뿐입니다. UUID/account/path/token/MACkey/
원래 source 내용은 출력하지 않습니다. JSON/logs를 private로 보존하고 exact Job UID로 제거해
RWO reader를 해제합니다. 기존 PVC/state/다른 workload는 삭제하지 않습니다.

판정:
- ledger 없음: install persistence 미관찰. Receipt만으로 memory 효과까지 단정하지 않습니다.
- ledger 있음: install이 persisted됐다는 근거입니다. Projection 있으면 attach 단계 진행
  후보이며 Native init/login 성공 증거는 아닙니다.
- requests>0: 이미 reservation이 있으므로 그 요청을 재전송하지 않습니다. Broker 내용 조사나
  자동 reset은 이번 진단 범위 밖입니다.
- selectedWindowMatches=false/owner-state 불일치/unsafe 파일: 중단하며 다른 state를 채택하지 않습니다.
- Pod0이므로 snapshot memory는 유지되지 않습니다. deadline/time limit이 지나면 기존 창의
  resupply는 불가합니다. 새 창은 원인·보존 상태 대조 후 **별도 명시적 PM 재개 단계**이며
  진단 Job/도구가 자동 생성하거나 ledger를 초기화하지 않습니다.

## D2: 기존 API70의 readonly DB 단계

검토된 `access_api_program.py`를 Docker에서 실행해 **공개 program 소스만** private file로
emit합니다. 기존 API70의 Ready Pod UID/RS/Deployment/image를 전후 고정하고 승인된 wrapper로
`kubectl -n m2-hosting exec -i <exact API70 Pod> -- python -B - < api-diagnostic-program.py`
실행합니다. 기존 API spec/image/volume/probe/role을 변경하거나 helper를 Pod에 설치하지 않습니다.

이 새 helper는 readiness와 같은 **기존 DATABASE_URL 설정/DB credential만** memory에서
사용합니다. GitHub secret/session key/원본 Codex source/cache는 읽지 않습니다. DB DNS3s,
resolved 첫 address TCP3s, 연결 후 SET TRANSACTION READ ONLY와 auth schema/version/계정 table
LIMIT0만 실행합니다. DSN/hostname/password/exception text/row/계정 ID는 출력하지 않습니다.
출력은 dns/tcp/connection/schemaContract/accountTableAccessible boolean/enum입니다.
정상 readiness의 기존 pool과 달리 새 연결 진단이며, 성공도 실패 당시 새 Pod를 소급 증명하지
않습니다. 연결/DNS 실패는 policy/current route 근거와 대조하며 migration/credential 수정을
자동 해결책으로 삼지 않습니다.

## D3: runner network only, 인증/Native 없음

`access_diagnose_prepare.py network`는 PVC/Secret/env input을 **하나도** mount하지 않는
단발 Job입니다. 기존 runner network selector app=access-web-runner, same sealed node,
API597a image/UID10001/timeout30s/backoff0로 현재 동일 CNP 경로만 사용합니다. Native Deployment는
0으로 유지하고 API mapping도 disabled입니다. Diagnostic Pod가 같은 Service selector에 잠깐
포함되므로 **API enable 금지**, 진단 후 exact Job UID로 삭제/Pod0 확인합니다.

Anonymous 대상 두 개만: chatgpt.com443의 DNS/첫 resolved address TCP/TLS handshake와
api.m2-hosting.svc.cluster.local8080의 DNS/TCP. HTTP/Authorization/모델/account RPC/source/token
이 없습니다. TLS 검증은 Python/OS trust 기준이며 실제 pinned Native/provider entitlement
증명이 아닙니다. JSON은 dns/tcp/tls enum뿐이며 IP/인증서/hostname/error text를 출력하지 않습니다.
실패 결과만으로 전체 address 경로를 불가능으로 단정하지 않습니다. CNP/current DNS policy
metadata와 필요한 비밀 아닌 deny 증거가 있어야 최소 수정 범위를 정합니다.

## P1: API 보조 link만 guarded 수정

Fresh GET `m2-hosting/hosting-access-web-link-v1`를 private current JSON으로 보존합니다.
Docker `access_link_patch.py --current ... --inputs <existing owner/state> --images <915 images>`가
old approved source의 **전체 spec**/정확한 name·namespace·UID/RV를 검사한 JSON Patch만 emit합니다.
변경은 enableDefaultDeny ingress/egress false, link의 DNS rule 제거이며 callback runner8080
allow는 유지합니다. 다른 CNP/기존 API base 정책/runner 정책/PVC/config/Secret/role은 변경하지 않습니다.
Server dry-run/기존 base DNS/DB/GitHub 허용/실제 Cilium 해당 필드 지원을 확인한 뒤 PM apply합니다.
기존 policy가 예상과 다르면 자동 overwrite하지 않습니다. 적용 후 fresh DNS/DB readiness와
기존 trusted HTTPS를 확인하고, 실제 효과를 review에 인계합니다. Runtime915 production images는
backend bytes 무변경이므로 rebuild/import하지 않습니다.

그 뒤에도 Native 실패는 현재 로그로 단계 확정되지 않았습니다. D1/D3 결과가 선행이며,
vendor 재시작/재공급을 무조건 반복하지 않습니다. 필요하다면 별도 검토된 code-only startup
phase/filtered error 진단을 추가하고 기존 state를 보존한 다음 PM의 명시적 재개로 검증합니다.
Actual 웹 성공/entitlement/#7 완료·장기 refresh·최종 PR·공개 게시 hold는 유지합니다.
