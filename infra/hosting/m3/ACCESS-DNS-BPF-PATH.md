# #7 DN4B 중단 뒤: 지정 endpoint의 kernel policy map 읽기 제안

DN4B fixed socket gate exit1과 전후 baseline 보존은 독립 검증됐습니다. Flow 단계는
미실행이며 DNS 원인은 unknown입니다. 실패한 socket 검사를 반복하거나 다른 Hubble
주소를 찾지 않습니다. 추가로 지정 보존 증거 `/tmp/issue7-dns-proxy-20261011/status.json`의
`hubble.state`가 **Disabled**이고 nodeMonitor metadata가 존재함을 확인했습니다.
이는 이전 DN3 시점 agent 보고이며 DN4B socket 실패만으로 얻은 결론이나 현재 활성
상태 증명은 아닙니다. 보존된 DN4A agent spec의 command/args에는 enable-hubble/
hubble-socket-path flags가 없었습니다. Flag 부재를 비활성 증거로 쓰지 않습니다.

## 선택한 후속 방법과 실제 지원 근거

Hubble을 활성화하는 대신 diagnostic endpoint **한 개**의 BPF policy map을 읽어
userspace realized policy와 kernel에 설치된 identity/port/deny/proxy 항목을 대조합니다.
상시 observer, 내부 inbox Monitor/inotify watcher, packet collector를 시작하지 않습니다.

[Cilium1.20.2 CLI 문서](https://docs.cilium.io/en/stable/cmdref/cilium-dbg_bpf_policy_get/)와
[동일 버전 구현](https://github.com/cilium/cilium/blob/v1.20.2/cilium-dbg/cmd/bpf_policy_get.go)은
`bpf policy get <endpoint ID> --numeric`가 지정 map을 읽고 ID의 label resolution을
생략하며 numeric table에 deny/direction/identity/port/proxy와 가능한 counters를 표시함을
보여줍니다. `--all`은 모든 map을 읽으므로 사용하지 않습니다. 출력의 port/proxy는 CLI가
host byte order로 변환하므로 raw network-order integer를 직접53과 비교하지 않습니다.
이는 설치된 CLI의 실제 성공 증명은 아니므로 아래 capability gate를 둡니다.

## DN5A: 새 Job 없는 capability 확인

PM이 API70 UID/full spec/Ready1/updated1, runner UID/spec/replicas0/Pod0, P1/runner policy,
named CCNP 및 기존 same-node agent UID/container/image/containerStatuses를 대조합니다.
그 agent에서 다음 두 명령만 각각 non-TTY/외부10초/private 출력으로 실행합니다:

```text
cilium-dbg status --timeout 5s -o json
cilium-dbg bpf policy get --help
```

첫 결과는 현재 agent의 Hubble state를 safe state로만 기록합니다. Hubble socket 접속이나
기능 활성화를 하지 않습니다. 두 번째는 request 없는 help로 installed numeric/endpoint
map read surface를 확인합니다. Version/agent 교체 또는 CLI 미지원이면 중단합니다.
Status의 unknown/없음은 Disabled로 바꾸어 해석하지 않습니다. Help만으로 map/counter
접근 가능성을 확정하지 않습니다. Root privilege 실패 시 agent 권한을 바꾸지 않습니다.

DN5A는 이번 리뷰에서 요청하는 실행 범위입니다. 아래 DN5B는 이 결과와 설치 help를
반영해 별도 검토할 고정된 후속안이며 DN5A 승인으로 실행하지 않습니다.

## DN5B 후보: fresh diagnostic 한 개 / 단일 map 조회

설치 capability가 확인된 뒤 기존 승인 source의 `dns-observed` renderer와 API597a image를
그대로 사용하는 새 Job 한 개를 별도 검토합니다. 기존60초/backoff0/8초+DNS+20초 window,
noPVC/noSecret/noenv/noSA token/UID10001/same node를 유지하며 program byte를 대조합니다.
API mapping disabled 및 동일 Service selector의 기존 주의 조건을 확인합니다.

Server dry-run/create 후 exact Job UID의 새 소유 Pod를 선택합니다. 새 Pod/CEP/agent GET과
`access_endpoint_pin.py`로 UID/node/local status.id/CNI/IP를 pin하고, 같은 agent의 단일
`endpoint get <validated fresh ID> -o json`으로 현재 realized revision/identity/proxy를 보존합니다.
DNS 결과 후 관찰 window에서 같은 agent에 다음을 **한 번** 실행합니다:

```text
cilium-dbg bpf policy get <validated fresh local endpoint ID> --numeric
```

직후 fresh Pod/CEP/agent를 다시 GET해 pin 동일성을 확인합니다. Pod가 완료/삭제/교체되거나
CEP/local ID/CNI/IP가 바뀌면 map 결과를 채택하지 않습니다. Endpoint get 응답은 기존
helper로 CNI/IP 일치까지 검사합니다. Deleted DN3 numeric ID/CoreDNS/host endpoint의
map을 대신 조회하지 않습니다. Same-name map path를 직접 열거나 bpftool/all list로 우회하지 않습니다.

기존 DN4B의 non-TTY/TERM10초+KILL2초/파일별256KiB/core-disabled/private0700 출력 제한을
적용합니다. exit0/완전한 지원 table만 private 대조합니다. Unknown schema/권한/빈 table/
출력한도/timeout이면 unknown으로 중단하며 같은 exec나 새 Job을 재시도하지 않습니다.

Kernel table에서 egress CoreDNS identity의 TCP/UDP53 allow와 proxy port 존재 및 userspace
realized 항목 일치, host/remote-node 계열 deny/ANY scope를 대조합니다. Identity·proxy port·
topology 원문은 공유하지 않고 일치 여부만 전달합니다. Counter가 unavailable/sentinel이면
없는 것으로 집계하지 않습니다. 값이 있어도 한 번의 snapshot 누적 counter는 해당 DNS
질의나 특정 policy 원인의 인과 증명이 아닙니다. 정책 통계 reset/flush/write를 하지 않습니다.

Userspace와 kernel 불일치가 확인되면 그 경로의 근거를 인계합니다. 일치하더라도 실제
packet 경로 정상이나 DNS 원인 해소를 뜻하지 않습니다. BPF snapshot은 flow/verdict를
대신하지 않으며 node-protection 완화, host 전체 허용 또는 전역 proxy 수정 근거가 아닙니다.

Job completion/log 보존 후 exact UID precondition/Foreground 삭제와 소유 Pod0를 확인하고
API/runner/정책/CCNP/agent baseline을 후검증합니다. 원래 source/PVC/state/window/runner는
보존하며 재공급/새 창/Native/API enable/turn·실제 인증/최종 PR hold를 유지합니다.
