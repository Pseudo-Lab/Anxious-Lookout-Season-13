# #7 DN9 제안: configured kube-dns Service와 known CoreDNS backend의 최소 contrast

DN8 actual/독립 검증은 one primary resolver가 known kube-dns ClusterIP와 일치함을 확인했고
단순 resolver 주소 mismatch 가설을 좁혔습니다. Service routing/proxy/CoreDNS 왕복은unknown입니다.
DN9은 그 Service와 **그 Service의 Ready EndpointSlice에 등록된 already-known same-node
CoreDNS Pod**의 경로를 비교하는 별도 review 대상입니다. DN8 승인으로 추가 질의를 실행하지 않습니다.

## 판별할 가설과 최소 차이

동일 credential-free Pod·node·정책·DNS 설정에서 기존 허용 이름 `chatgpt.com`의 absolute A
질의만 사용합니다. 대상 주소 차이만 두고 고정 순서로 다음 **네 번**을 수행합니다:

1. Configured Service UDP53.
2. Configured Service TCP53.
3. Known CoreDNS Pod UDP53.
4. Known CoreDNS Pod TCP53.

Callback/relative libc/search expansion/AAAA/새 hostname/public resolver/다른 Pod를 추가하지 않습니다.
DNS policy/config/ndots/Deployment/CNP/CCNP 변경이나 drop/L7 구독도 없습니다. Direct Pod 주소는
진단 contrast에만 사용하며 실제 runner의 nameserver나 제품 설정에 hardcode하지 않습니다.

Service timeout/known Pod reply는 해당 표본의 **Service 경로 차이**를 좁힙니다. VIP/backend
map 오류를 즉시 확정하지 않습니다. 둘 다 timeout이면 Service-only 장애를 주장할 수 없고,
둘 다 reply면 기존 timeout의 원인·지속 정상·Native 연결 성공은 여전히unknown입니다.
Reply RCode/answer-present는 기존 bounded wire helper의 ID/질문/응답 header 대조 결과이며
실제 client libc 수신이나 유효 주소로의 HTTPS 연결 증명은 아닙니다. REFUSED/SERVFAIL 등도
단순 reply와 구분합니다. 고정 순서·cache/시각 차이의 한계를 보존하고 자동 역순/retry하지 않습니다.

## 지정 입력의 provenance와 Ready backend gate

Private dns-inputs에는 fresh named kube-system/kube-dns Service와 기존 known CoreDNS Pod,
DN1 `dns-endpoints.json`에 저장된 **known EndpointSlice name/UID 한 개**를 service/pod/slice로
묶습니다. Whole namespace list나 다른 backend 선택은 없습니다.

기존 resolver-map의 Service UID/RV/type/53TCPUDP/selector↔Pod labels/RunningReady/
hostNetworkfalse/same-node/address guard를 재사용합니다. 추가로 Slice UID/RV/Service label/
controller owner Service UID/name, IPv4·IPv6 addressType 및TCPUDP53를 검사합니다.
Known Pod의 UID/name/namespace를 targetRef로 가진 endpoint가 정확히 한 개여야 하고,
node/ready=true/terminating=false 및 family의 PodIP가 address와 일치해야 합니다. 그 family의
ServiceIP/PodIP도 각각 한 개이며 서로 달라야 합니다. 다른 family/endpoint fallback은 없습니다.

Source 근거는 [Kubernetes EndpointSlice](https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/)
및 [Service](https://kubernetes.io/docs/concepts/services-networking/service/)의 backend association입니다.
Metadata gate는 현재 Kubernetes endpoint 구성을 확인하는 것이지 실제 Cilium Service datapath
map/packet 전달을 증명하지 않습니다. PM은 renderer가 보관하지 않는 **이전 name/UID와의
연속성**을 반드시 대조해야 합니다. 객체 교체면 새 대상을 찾지 않고 unknown으로 중단합니다.

## 구현/시간/출력 제한

`access_diagnose_prepare.py dns-path-contrast --dns-inputs ...`는 기존 API597a/same node/
app label/UID10001/RO/cap-drop/noPVC·Secret·env·SA token, deadline30/backoff0를 유지합니다.
기존 modes와 `access_dns_diagnose.py` wire client는 변경하지 않습니다. Renderer는 trusted
repo의 wire 함수와 resolver file 함수 정의만 deterministic하게 결합하고 **기존 diagnostic
main을 실행하지 않습니다**. Operator 입력은 검증된 address lists만 data로 삽입합니다.
Manifest/program의 internal addresses는 private0700 파일로만 보존합니다.

Runtime에서 fixed readonly `/etc/resolv.conf`를 재비교해 resolverCount1 및 configured Service
일치가 유지된 뒤만 질의합니다. Drift면 query 전에 중단합니다. 새 libc/resolver selection을
추정하거나 dnsConfig를 덮어쓰지 않습니다. Query helper는 기존 timeout2초/port53/packet4096
한도를 유지합니다. Program 전체 SIGALRM12초를 별도 BaseException으로 처리하여 shared
query의 Exception handler가 deadline을 삼키지 못합니다. Partial result는 폐기하고 unknown/
exit1로 중단합니다. Job30초보다 앞서 종료하도록 하되 Kubernetes scheduling은 별도입니다.

Safe output는 configuredService/knownCoreDnsPod aliases 각각 UDP/TCP status(reply/timeout/failed),
rcode 및 answersPresent bool, queryCount4/networkRequested=true/dnsCause=unknown입니다.
IP/name/packet/answer 원문·exception은 출력하지 않습니다. 실패 schema/도중 deadline은 전체
unknown이며 실제 발신 질의 수를4라고 주장하지 않습니다. Source/PVC/state/HTTP/TLS/auth/
Native/model/broker를 읽거나 시작하지 않습니다.

## PM 절차: DN9 별도 승인 뒤

1. API70/runner0/Pod0/P1/runner policy/CCNP baseline 및 API mapping disabled/기존 Service selector
   조건을 확인합니다. Known Service/CoreDNS는 DN8, known Slice는 DN1 기록의 name·UID와
   fresh GET을 결합합니다. Slice 조건 변화/교체/권한 실패면 unknown이며 다른 Slice list/Pod 선택 없음.
2. Docker로 private snapshot/inputs/dns-inputs를 render하고 Source SHA/program bytes/image597a,
   operator prefix 및 provenance를 보존합니다. Server dryrun/defaulted Pod DNS spec 후 새 Job 하나만 create.
3. Exact Job UID/controller owner Pod의 node/imageID/command/UID10001/RO/no volume/env/token 및
   dnsPolicy/dnsConfig를 dryrun/DN8·DN7과 대조합니다. Spec drift면 원래 경로의 contrast로 채택하지 않습니다.
4. Deadline30초 내 completion/Pod exit/safe result를 보존합니다. Running window/사전 로그를 기다리지
   않고 completed Pod의 owner/spec도 검증합니다. Queries는 최대 네 번/12초이며 수동·자동 retry 없음.
5. Known Service/Pod/Slice UID/RV/spec/status/address/Ready를 후대조해 변화면 결과unknown.
   정상/실패/deadline 모두 exact Job UID Foreground 삭제/소유 Pod0·runner Pod0와 baseline을 후검증합니다.

서비스 경로 차이가 실제 생기면 그 named Service의 kernel routing metadata를 한정해
확인하는 후보를 따로 고정할 수 있습니다. 둘 다 실패하면 proxy/upstream/return 등 공통
경로를 좁힐 다음 근거가 필요합니다. 이번 결과를 전역 deny/host/CIDR/whitelist/ndots 완화나
CoreDNS restart의 자동 근거로 사용하지 않습니다. 원래 runner/source/PVC/state/window와
재공급/새창/Native/API enable/turn·실제 웹 Codex 성공/최종 PR hold를 유지합니다.
