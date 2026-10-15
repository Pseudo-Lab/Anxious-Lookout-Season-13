# #7 DNS-only 구분 진단: 허용 대상 유지

실제 D1: installed 만료 window1, reservation0, binding/window match, projection 존재,
broker/auth 없음. D2/P1 뒤 API70 새 DB DNS/TCP/readonly schema 정상과 HTTPS 정상 확인.
D3 runner의 두 목적지는 DNS timeout이며 TCP/TLS에 도달하지 못했습니다. 이 상태를
유효 snapshot이나 재공급 가능 상태로 확장하지 않습니다. 기존 창/ledger/journal은 보존합니다.

## 후보와 현재 확인되지 않은 원인

기존 DNS L7 허용은 chatgpt.com, api.m2-hosting.svc.cluster.local 두 exact name입니다.
[Cilium DNS search/refused 설명](https://docs.cilium.io/en/stable/security/policy/layer7/#alpine-musl-deployments-and-dns-refused)은
search-suffix query 거절이 resolver의 다음 absolute query를 막을 수 있고 pod ndots 조절로
피할 수 있음을 설명합니다. 이는 후보이며 현재 cluster의 DNS proxy/endpoint selector/
UDP·TCP/실제 resolver 상태가 확인된 것은 아닙니다. wildcard/다른 resolver/새 FQDN/TLS target/
global Cilium 옵션 변경을 추측으로 적용하지 않습니다.

## DN1: 같은 정책의 configured-resolver absolute 질문

승인 요청 source `access_dns_diagnose.py`, renderer
`access_diagnose_prepare.py dns --snapshot <fresh restored metadata> --inputs <existing owner/state>`.
기존 API597a image/same retained node/UID10001/timeout30s/backoff0, same runner selector이며
PVC/Secret/env는 없습니다. Original runner는0/Pod0, API mapping disabled를 유지합니다.
Pod가 같은 Service selector에 포함될 수 있어 진단 중 API enable 금지, exact Job/Pod UID로
삭제 및 Pod0 확인은 기존 절차와 같습니다.

직접 UDP/TCP probe는 `/etc/resolv.conf`의 첫 **현재 configured resolver**만 사용합니다.
Libc 비교는 OS 설정에 따라 다른 configured resolver/search suffix/A·AAAA를 사용할 수 있습니다.
직접 만든 두 absolute A 질문과 OS name resolution 비교를 구분하며 새 public resolver나
policy 허용을 추가하지 않습니다. IP/검색suffix를 출력하지 않고 resolver 수/search 수/ndots만
출력합니다. Source credential/토큰/원래 세션/
native/HTTP/auth/provider model에는 접근하지 않습니다.

두 existing name의 absolute A DNS 질문을 UDP와 TCP53로 각각 보냅니다. 질문을 추가 suffix로
직접 확장하거나 외부 DNS를 쓰지 않습니다. Reply는 configured peer/transaction ID/question/
size를 검사하고 status/rcode/answer 존재만 내보냅니다. IP/packet/record/host/error text는
출력하지 않습니다. 이어 libc의 absolute trailing-dot와 기존 상대 name 결과를 각각2s로
구분합니다. 일반 DNS cache/FQDN cache 관찰 부작용은 있지만 policy/인증/state 쓰기는 없습니다.

## DN2: diagnostic Pod만 ndots1 대조

DN1의 해당 대상에서 **absoluteLibc=resolved AND relativeLibc=failed 또는 timeout**이
확인됐을 때만 `dns-ndots1` 단발 Job을 review 범위로 실행합니다. Direct status=reply/
rcode0/answersPresent만으로 DN2에 진입하지 않습니다. Direct probe는 answer section을
해석하지 않으므로 주소 해석 성공을 보증하지 않으며 REFUSED/SERVFAIL/무응답도 성공이 아닙니다.
Same code/image/selector/
permissions/no state 조건이며 **diagnostic Pod의 dnsConfig ndots1 한 필드만** 다릅니다.
원래 runner Deployment, DNS policy, 허용 destinations, cluster proxy 설정은 바꾸지 않습니다.
기존 만료창과 Original credential source를 다시 읽지 않습니다.

## 결과별 최소 수정 판단과 필요한 metadata

- Absolute와 relative 모두 실패/timeout: ndots가 원인이라고 확정하지 않습니다. 같은 node의
  cluster DNS Service/EndpointSlice 및 CoreDNS label·ready/hostNetwork metadata,
  해당 runner-selector CNP/NetworkPolicy, Cilium DNS proxy 관련 **설정 key만**을 PM이 기존
  GET으로 private 대조합니다. Secret/config 전체 내용·원본 process trace는 필요 없습니다.
- Absolute rcode5/REFUSED: 허용된 이름 자체의 effective policy/identity/proxy 거절 여부를
  좁혀야 합니다. 기존 named 정책/설치 metadata와 대조하기 전 allow wildcard를 만들지 않습니다.
- UDP 실패/TCP 성공 또는 반대: protocol/해당 CNP path의 최소 차이를 확인하며53 외 포트를
  열거나 public resolver로 우회하지 않습니다.
- Absolute 성공, relative 실패, DN2에서 relative 성공: **이 결과가 확인될 때만** 원래
  stopped runner template의 ndots1 수정 패키지를 별도로 만들 수 있습니다. FQDN/destinations/
  API recovered link policy는 유지합니다. 이 문서는 원래 Deployment patch 승인이 아닙니다.
- DNS 성공은 TLS/실제 Native/auth entitlement/웹 성공 증명이 아닙니다. 필요 후속 TLS는
  이미 승인된 D3 no-auth 범위로 판단하며, token/new window/Native restart는 별도 재개 단계입니다.

Metadata GET 대상도 namespace/name/node를 기록된 객체로 한정합니다. Cilium version/config
및 resolver equality가 필요하면 실제 이름을 기존 설치 기록에서 선택하며 cluster-wide
config/credentials 탐색이나 상시 monitor를 시작하지 않습니다. 현재 원인을 위한 new collector는
이 단발 DNS-only source로 한정합니다. API D2/P0/P1/원본 로그인 조사는 반복하지 않습니다.
