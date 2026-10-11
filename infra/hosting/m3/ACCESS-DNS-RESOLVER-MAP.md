# #7 DN8 제안: Pod의 실제 resolver와 알려진 DNS 주소의 일치 여부

DN7 actual/독립 검증은 종료/context/pin/정리 통과,6초 구간의 proxy events0와 DNS 전체
timeout입니다. Empty는 proxy 미도달/정상/차단 부재 증거가 아닙니다. 같은 구독/Job/type/
관측창을 자동 반복하지 않습니다. 이 단계는 **다른 입력 사실**, 진단 Pod가 실제 읽는
resolver 주소와 알려진 kube-dns Service·CoreDNS Pod의 일치 여부를 처음 대조합니다.

## 판별할 가설

- H1: 실제 primary resolver가 named kube-dns Service의 ClusterIP인가?
  Service match=true면 resolver의 단순 주소 불일치 가설을 좁힙니다. Service routing/proxy/
  응답 경로 또는 DNS 정상은 증명하지 않습니다.
- H2: primary가 Service와 다르지만 known CoreDNS PodIP와 직접 일치하는가?
  직접 Pod 주소 사용을 구별하며 이를 오류/원인으로 단정하지 않습니다.
- H3: 둘 다 아니고 link-local 또는 loopback resolver인가?
  Node-local/custom resolver 가능성을 다음 한정 metadata 대조 대상으로 분리합니다.
  Scope classification만으로 node-local 기능 설치나 explicit deny matching을 증명하지 않습니다.
- Secondary resolver나 address-family 차이도 별도 bool로 남겨 primary와 혼동하지 않습니다.

[Kubernetes Pod DNS 설명](https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/) 및
[DNS troubleshooting](https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/)은
Pod의 DNS 설정과 `/etc/resolv.conf` 대조를 안내합니다. Node/kubelet/namespace 전체 설정을
읽거나 다른 Pod를 exec하는 대신 credential-free diagnostic의 이 파일 하나만 읽습니다.

## 구현과 범위

`access_diagnose_prepare.py resolver-map --dns-inputs <private JSON>`은 기존 API597a image/
same node/app label/UID10001/read-only/cap-drop/noPVC·Secret·env·SA token을 유지한 새 Job
한 개만 emit합니다. Deadline30초/backoff0이며 network query나 startup/sleep window가 없습니다.
Runner0/Pod0를 renderer도 요구합니다. DNS policy/config override는 설정하지 않습니다.
기존 state/network/dns/dns-ndots1/dns-observed program과 emitted 기본 동작은 유지합니다.

Private dns-inputs는 **named kube-system/kube-dns Service 하나**와 **이미 알려진 same-node
CoreDNS Pod 하나**의 fresh GET 객체를 service/pod 키로 묶습니다. Render 전에 UID/RV,
ClusterIP type/TCPUDP53, selector→known Pod labels, Running/Ready, hostNetwork=false/same node,
유효 ClusterIPs/PodIPs를 검사합니다. Namespace/name/Ready/selector/주소 schema가 다르면
unknown으로 중단하며 전체 Service/Pod list나 다른 CoreDNS를 선택하지 않습니다.

Container command에는 검증된 IP lists만 data로 넣고 raw metadata나 credential을 넣지
않습니다. Job manifest 자체는 internal 주소를 포함하므로 private0700 파일로 보존하며
message/console에 출력하지 않습니다. Program은 `/etc/resolv.conf`를 O_RDONLY/NOFOLLOW로
한 번 열어 regular file/64KiB·256lines 한도 안에서 nameserver 값만 비교합니다. Search domain/
raw IP/파일 원문은 출력·보존하지 않습니다. Symlink/권한/없음/비지원 파일 또는 malformed,
nameserver 없음/8개 초과는 unknown JSON/exit1입니다. 다른 path를 탐색하지 않습니다.

Safe JSON은 resolverCount, primaryMatchesDnsService/primaryMatchesKnownCoreDnsPod,
allResolversMatchDnsService/primaryFamilyMatchesDnsService 및 loopback/link_local/other
classification, networkRequested=false/dnsCause=unknown만 반환합니다. IPv4/IPv6와 secondary
차이를 구별하지만 실제 libc fallback 동작을 증명하지 않습니다. Socket/DNS/TCP/TLS/HTTP/
proxy/event subscription/Native/app startup/관측 인프라 활성화·변경은 없습니다.

## PM 실행 순서: 별도 DN8 승인 후

1. API70 UID/full spec/Ready1/updated1, runner UID/spec/replicas0/Pod0, P1/runner policy/CCNP
   baseline을 대조합니다. 기존 Service selector/API mapping disabled 주의 조건을 유지합니다.
   Known Service와 CoreDNS name/UID를 이전 지정 metadata와 결합한 fresh GET을 얻습니다.
   새 Pod/Service 교체/selector drift이면 중단하고 다른 대상을 자동 선택하지 않습니다.
2. Docker로 private dns-inputs/snapshot/inputs를 넣어 resolver-map을 emit합니다. Source SHA/
   program bytes/prefix addresses/provenance는 private로 보존합니다. 이미지는 기존597a digest
   그대로입니다. Server dry-run/defaulted Pod DNS spec을 기록한 뒤 새 Job 한 개를 create합니다.
3. Exact Job UID/controller owner의 Pod를 GET합니다. 이 Job은 빠르게 완료될 수 있으므로
   Running window/로그 도착을 기다리지 않고 동일 Job의 completed Pod도 소유관계와 spec을
   대조합니다. Node/imageID/noVolumes·env·token/UID10001 및 dnsPolicy/dnsConfig가 dry-run과
   일치하고 DN7 diagnostic의 DNS spec과 동일한지 확인합니다. DNS spec/admission drift면
   결과를 해당 원래 DNS 경로의 증거로 채택하지 않습니다.
4. Deadline30초 안의 completion/Pod exit/stdout safe JSON을 보존합니다. Result/schema 실패는
   unknown이며 원문 resolv.conf/다른 file/원래 runner를 추가 exec하지 않습니다. 관측/질의나
   같은 Job 재생성 없이 중단합니다. Known Service/CoreDNS UID/RV/주소/Ready도 fresh 후대조해
   expected 입력이 바뀌면 해당 비교는unknown입니다.
5. 모든 outcome의 exact Job UID precondition/Foreground 삭제, 소유 Pod0/runner selector Pod0,
   API/runner/P1/runner policy/CCNP baseline 후검증을 보존합니다. 새 정책/Deployment를 만들지 않습니다.

## 결과 뒤 최소 다음 분기

Resolver 불일치가 실제 확인돼야 node-local/custom target의 알려진 설정 대조나 diagnostic-only
resolver contrast를 별도 설계할 근거가 생깁니다. Service match=true면 다음 별도 검토는
configured Service와 already-known CoreDNS Pod의 경로 차이를 구별하는 제한된 contrast일 수
있습니다. 현재 패키지는 새 DNS target 질의/dnsConfig 변경 또는 그 contrast를 실행하지 않습니다.
어느 결과도 global deny/host/CIDR/whitelist/ndots를 완화하는 근거로 자동 채택하지 않습니다.

원래 source/PVC/state/window/runner와 인증을 보존합니다. 재공급/새창/Native/API enable/turn,
실제 웹 연결/최종 PR 성공은 미확인·hold를 유지합니다. Backend는 실제 GET/Job/OS file/구독을
실행하지 않았으며 Docker fixture와 emit 구현만 준비합니다. 내부 inbox watcher는 시작하지 않습니다.
