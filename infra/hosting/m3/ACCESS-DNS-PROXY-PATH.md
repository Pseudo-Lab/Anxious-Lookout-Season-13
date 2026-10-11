# #7 absolute DNS timeout 다음: 동일 node endpoint/proxy 상태 대조

DN1 실제·독립 확인은 두 대상의 absolute UDP/TCP/libc와 relative libc 모두 timeout입니다.
DN2는 미실행이며 조건 미충족입니다. CoreDNS endpoint Ready/UDP·TCP53, hostNetwork=false/
kube-dns label, proxy config true는 경로 통과 증명이 아닙니다. 기존 API70/recovered P1/
runner0/Pod0/mapping disabled/expired installed window1/reservation0을 유지합니다.

## DN3: 정책·허용 대상 그대로, endpoint 관찰 창만 추가

새 renderer mode `dns-observed`는 승인된 DN1 code/image/node/selector/권한/두 질문을
그대로 사용합니다. DNS 실행 전8s, 안전 JSON 출력 후20s만 머물러 metadata 대조 시간을
만들며 Job deadline60s/backoff0입니다. PVC/Secret/env/dnsConfig/ndots/policy 변경이 없습니다.
상시 watcher/monitor/packet capture/Hubble 전체 trace를 시작하지 않습니다.

Docker render와 server dry-run 뒤 PM이 exact Job UID를 기록합니다. API mapping disabled,
same Service selector caveat 및 완료/실패 시 exact Foreground Job 삭제/Pod0 확인은 유지합니다.
목적은 같은 timeout 질문만 반복하는 것이 아니라 **동일 Pod endpoint의 실제 realized
policy/proxy 상태와 질의 시점을 연결하는 것**입니다. Runtime Deployment는0으로 둡니다.

## 제한된 metadata 명령

Known node는 이미 sealed PV/Job 기록의 한 node입니다. 그 node의 기존 Cilium agent Pod를
설치 기록/해당 label·node GET으로 선택하고 UID/container/image/version을 기록합니다.
Known CoreDNS Pod는 이미 저장된 kube-dns endpoint의 한 Ready Pod입니다. 다른 node/agent/
endpoint를 대신 선택하거나 새 component/restart/config 변경하지 않습니다.

1. Public/recorded K8s metadata: exact Job의 소유 Pod GET(UID/node/time/labels),
   `codex-trial`의 **그 Pod 이름 하나**의 CiliumEndpoint GET. 정책 Namespace/name/UID/RV는
   현재 access-web-boundary-v1 및 recovered hosting-access-web-link-v1만 대조합니다.
2. 같은 node agent의 `cilium-dbg status --timeout 5s -o json`, **verbose/all-* 없음**.
   Existing proxy health/redirect aggregate/revision 관련 항목만 해석하고 private 원문을 보존합니다.
3. 설치1.20.2의 `pod-name:` prefix는 실제 exit1로 미지원 확인됐습니다. 아래 fresh pin 절차로
   얻은 **local numeric endpoint ID** 하나에 대해 같은 agent의
   `cilium-dbg endpoint get <validated local ID> -o json`만 사용합니다.
   Endpoint state/identity labels/desired·realized policy revision, egress53 TCP/UDP의
   proxy redirect/L7 DNS와 derived-from access-web-boundary를 대조합니다.
4. Known CoreDNS도 fresh Pod/CEP/동일 node agent를 따로 pin한 **local numeric ID** 하나로
   `cilium-dbg endpoint get <validated CoreDNS local ID> -o json`을 사용합니다.
   Ready/identity의 namespace·kube-dns labels와 source 정책의 toEndpoints selector를 대조합니다.

모든 exec는 기존 trusted wrapper/non-TTY/외부 timeout10s/private0700 출력으로 제한합니다.
CLI/version/schema가 달라 명령이나 field가 없으면 unknown을 기록하며 endpoint list 전체/
identity dump/all redirects/로그 전체/새 auth·debug server로 대체하지 않습니다. 지연 때 같은
exec를 반복하지 않고 해당 helper 종료만 확인합니다. API·agent·DNS serving process를 kill하지
않습니다. Raw 출력의 topology/identity를 메시지에 그대로 복사하지 않고 bool/state/revision
일치 여부 및 safe failure 종류만 공유합니다. Credential/PVC 원문·원래 process에는 접근하지 않습니다.

명령 근거: [endpoint get](https://docs.cilium.io/en/stable/cmdref/cilium-dbg_endpoint_get/),
[status](https://docs.cilium.io/en/stable/cmdref/cilium-dbg_status/). 문서의 현재 CLI surface와
설치된 버전의 지원 여부는 별도로 대조하며 latest 문서를 실제 상태 증거로 사용하지 않습니다.

## Numeric identifier 보완: fresh pin과 조회 전후 동일성

이전 DN3 Pod/endpoint는 삭제됐으므로 저장 numeric ID를 다음 조회에 재사용하지 않습니다.
`dns-observed` 동일 단발 Job의 새 exact 소유 Pod를 대상으로 기존 baseline/server dry-run/
UID/정리 전제를 그대로 적용합니다. Fresh Pod GET, **같은 이름** CiliumEndpoint GET,
same node의 기존 selected agent Pod GET을 operator private 파일로 보존합니다.

Docker `access_endpoint_pin.py --pod ... --cep ... --agent ... --pin <새 private pin file>`은
Pod UID와 CEP ownerReference, namespace/name, Running/ready, same node/agent UID,
CEP.status.id(1..65535인 integer)를 검사합니다. Security identity인 status.identity.id를
local endpoint ID로 사용하지 않습니다. 실제 CEP external-identifiers는 cni-attachment-id만
있었으므로 k8s-pod-name 필드를 가정하지 않습니다. CNI attachment와 Pod IP↔CEP addressing을
private pin에 묶습니다. Pin file은0600 O_EXCL/NOFOLLOW 생성이며 IDs/IP/CNI는 출력하지 않습니다.

같은 agent에 validated decimal local ID만 넣어 **단 한 번** bounded endpoint get을 수행합니다.
직후 fresh Pod/CEP/agent를 다시 GET하고
`access_endpoint_pin.py --pod <after> --cep <after> --agent <after> --pin <before pin> --response <CLI JSON>`
으로 대조합니다. 동일 Pod/CEP/agent UID/node/local ID/CNI/IP와 CLI response의 local ID/CNI/
addressing을 확인한 뒤만 해당 realized policy를 해석합니다. CLI의 optional k8s-pod-name이
있다면 일치도 확인합니다. CLI/schema 미지원, Pod 완료/삭제/replaced, endpoint/CNI/IP drift면
unknown으로 중단하며 다른 번호/prefix/list-all로 대체하지 않습니다. 같은 절차를 이미 알려진
same-node CoreDNS Pod 하나에 별도 적용합니다. CoreDNS가 다른 node면 이 agent로 조회하지 않습니다.

정상 반환의 policy.realized 및 revision/egress53 DNS redirect/derived-from 항목을 private로
대조하며 field가 없으면 unknown입니다. Identifier 호환성 해결은 DNS 정상이나 원인 해소
증명이 아닙니다. Metadata는 safe state/일치 여부만 공유합니다. Snapshot/window/PVC/source/
정책/목적지/port/global proxy/원래 runner는 변경하지 않습니다.

## 결과에 따른 최소 변경 경계

- Endpoint not-ready/identity 또는 realized revision 미수렴: 그 경로의 controller/policy
  상태를 좁힙니다. DNS whitelist/ndots 원인이나 credential 오류로 확정하지 않습니다.
- CoreDNS identity/selector 불일치: 관찰된 정확한 endpoint identity와 기존 의도를 비교한
  selector-only 후보를 별도 emit/review할 수 있습니다. wildcard/CIDR/public DNS 우회는 없습니다.
- Realized egress53에 DNS redirect 없음 또는 proxy health failure: 현재 node의 L7 proxy
  경로 문제 근거로 인계합니다. Global proxy option/daemon restart를 자동 해결책으로 실행하지 않습니다.
- Metadata가 정상인데 질문은 timeout: metadata만으로 datapath 정상/원인 없음이라 하지
  않습니다. 다음 단계는 해당 **자격 증명 없는 diagnostic Pod**의 port53 drop/verdict만을
  한정하는 별도 관측 범위를 검토하며 broad packet/HTTP/원래 세션 trace는 허용하지 않습니다.

이 패키지는 추가 읽기 전용 경로 대조이며 실제 정책/hostname/port 확대나 원래 Deployment
변경을 포함하지 않습니다. 원인 근거가 생긴 뒤 최소 수정안을 따로 고정합니다. 재공급/
새 창/Native/API enable/turn/실제 성공·최종 PR도 별도이며, 새 collector를 무한 반복하지 않습니다.
