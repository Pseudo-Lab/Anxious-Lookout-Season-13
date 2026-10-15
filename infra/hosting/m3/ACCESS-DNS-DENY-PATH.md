# #7 DN3 numeric 성공 뒤: deny 적용 범위와 기존 관측 기능 확인

## 확보된 증거와 아직 모르는 것

`f86a23c` 실제 증거 `/tmp/issue7-dns-pin-20261011/`는 diagnostic/CoreDNS의
fresh pin 및 CLI numeric 조회 성공, DNS TCP/UDP53 exact 이름/selector와 revision12,
allocated proxy port를 확인했습니다. 두 대상 DNS 질의는 계속 timeout입니다.
Proxy requests/responses 빈 객체는 특정 질의의 도달/차단 증거가 아닙니다.

Backend의 저장 응답 대조에서 CoreDNS security identity는 diagnostic의
denied-egress-identities에 없었습니다. CoreDNS의 denied-ingress-identities에도
diagnostic identity가 없습니다. Allowed-egress-identities는 null이므로 허용/미허용
판정에 사용하지 않습니다. Selector 기반 port53 realized 규칙은 별도 증거입니다.
이 대조는 저장 시점의 직접 identity 목록에 한정하며 node/proxy 왕복 경로는 판정하지 않습니다.

`anxious-lookout-node-protection` 유래 port0/ANY deny와 host/remote-node 계열 selector는
추가 확인 대상입니다. [Cilium1.20.2 deny 문서](https://docs.cilium.io/en/stable/security/policy/deny/)
상 explicit deny는 allow보다 우선하므로 additive DNS allow를 더 넣는 방식으로
matching deny를 해결할 수 없습니다. 그러나 현재 자료는 DNS 패킷의 실제 identity와
deny verdict를 연결하지 못합니다. Global deny를 timeout 원인으로 확정하지 않습니다.

## DN4A: PM 실행을 요청하는 최소 읽기 범위

새 Job/질의를 반복하기 전에 다음 읽기 범위만 검토합니다. 모든 명령은 기존 trusted
kubectl 경로, non-TTY, 외부 timeout10초, private0700 디렉터리의 출력/exit 보존입니다.
Backend는 실제 cluster 명령을 실행하지 않습니다.

1. API70 동일 UID/full spec/Ready1, runner 동일 UID/spec/replicas0/Pod0,
   recovered P1 및 access-web-boundary-v1 baseline을 대조합니다.
2. Named 객체 하나만 GET합니다:
   `kubectl get ciliumclusterwidenetworkpolicy anxious-lookout-node-protection -o json`.
   UID/RV/spec/specs를 private 보존하고 endpointSelector와 nodeSelector를 구분합니다.
   Diagnostic/CoreDNS labels에 어떤 rule이 선택되는지, ingressDeny/egressDeny의
   host/remote-node/kube-apiserver 및 CIDR 제한이 어떤 방향에 있는지 대조합니다.
   모든 정책 list, 다른 CCNP, raw topology 공유, patch/apply는 포함하지 않습니다.
3. DN3에서 선택한 same-node agent를 새 GET해 기존 UID/container/image와 대조합니다.
   Agent 교체면 중단합니다. 그 agent에서 `hubble version` 및
   `hubble observe --help`만 각각 bounded exec로 보존합니다. Flow 요청을 하지 않습니다.
   출력에서 local UDS server 선택, since/until, raw allowlist, print-raw-filters,
   JSON output, 필요한 source/destination IP·pod·port filter 지원을 확인합니다.
   설치 CLI 지원을 문서 예시로 대신하지 않습니다.
4. 후검증으로 API/runner/policy baseline 보존을 확인합니다. 객체 부재/권한 거절/
   CLI 부재·미지원이면 해당 safe failure와 unknown만 전달합니다. Agent restart,
   Hubble enable/config 변경, Relay/port-forward/새 server 설치로 우회하지 않습니다.

Hubble은 [공식 local 관측 설명](https://docs.cilium.io/en/stable/observability/hubble/)에서
기존 agent의 local Unix socket을 통한 node 범위 조회를 지원합니다. 현재 저장 ConfigMap의
enable-hubble/socket 항목은 없으므로 실제 활성 여부를 추정하지 않습니다.
이 패키지는 capability 확인까지이며 Hubble status/flow 연결 명령도 아직 실행하지 않습니다.

## DN4B: DN4A 결과를 바탕으로 고정할 단일 flow 조회

Capability가 확인된 뒤 별도 review로 정확한 CLI/filter를 고정합니다. 우선 기존 DN3의
저장 Pod UID/name/IP/node와 실제 시작~완료 시간에 한정한 historical 조회를 검토합니다.
삭제된 numeric endpoint를 live target으로 재사용하지 않습니다. Raw filter에 pod name
prefix만 두지 않고 exact IP와 namespace/name을 함께 묶으며, 조회된 record도 정확한
이름/IP/node/time으로 후검증합니다.

필터는 diagnostic에서 나간 TCP/UDP destination53 또는 diagnostic으로 돌아온
TCP/UDP source53의 두 방향만 허용합니다. 두 allowlist 항목 각각에 동일한 대상 조건을
넣습니다. 별도의 느슨한 필터를 OR로 추가하지 않습니다. 설치 CLI의
print-raw-filters 출력으로 범위를 검증한 뒤 local node의 기존 socket에 단발 요청합니다.
Follow/all/trace/capture/L7 DNS payload/HTTP는 제외하고 drop/policy-verdict만 검토합니다.
외부10초 timeout과 private 출력 크기 상한을 고정하며 lost/truncated/empty/schema 미지원은
unknown입니다. 보존 기간 만료를 정상 통과로 해석하지 않습니다.

이력 부족 시 새 dns-observed Job을 만드는 것은 별도 후속 실행 계획으로 검토합니다.
그때만 fresh Job owner Pod UID/node/CEP/agent pin 전후 검증과 단발 관측 창을 연결합니다.
현재 패키지는 추가 Job이나 Cilium monitor 프로세스, inbox watcher를 시작하지 않습니다.

## 수정안 결정 기준

- 해당 DNS flow에서 node-protection 유래 policy-denied와 matching identity/rule이
  확인되면 그 rule의 의도와 최소 예외 범위를 별도 설계합니다. Global deny 삭제나
  namespace 전체 제외, host 전체/다른 목적지/port 허용은 자동 후보로 적용하지 않습니다.
- DNS flow의 다른 drop reason이면 해당 경로만 인계합니다. Node deny의 존재만으로
  수정하지 않습니다. Port53 관측만으로 proxy의 모든 host 왕복을 확인했다고 하지 않습니다.
- 특정 deny가 확인되지 않거나 event가 없으면 원인 unknown을 유지합니다. Wildcard DNS,
  public resolver, ndots 변경, global proxy option/daemon restart를 해결책으로 적용하지 않습니다.

원래 runner/credential/PVC/state/window는 보존합니다. 재공급/새 창/Native/API enable/turn,
실제 인증 성공과 최종 PR 승인은 이 후속 범위에 포함하지 않습니다.
