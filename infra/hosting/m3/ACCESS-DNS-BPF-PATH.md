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

DN5A 실제 `/tmp/issue7-dn5a-20261011/`는 두 명령 exit0, 현재 Cilium Ok/Hubble Disabled 및
numeric 옵션을 확인했습니다. Help usage에는 positional ID가 표시되지 않습니다. 따라서
single ID 문법은 위1.20.2 구현의 requireEndpointID/listMap(args[0])에 근거한 후보이며 실제
설치 성공은 아직 미검증입니다. 첫 단일 호출 실패 시 --all/다른 문법으로 재시도하지 않습니다.
아래 DN5B는 별도 검토 요청 범위이며 DN5A 승인으로 실행하지 않습니다.

## DN5B: fresh diagnostic 한 개 / 단일 map 조회

설치 capability가 확인된 뒤 기존 승인 source의 `dns-observed` renderer와 API597a image를
그대로 사용하는 새 Job 한 개를 별도 검토합니다. 기존60초/backoff0/8초+DNS+20초 window,
noPVC/noSecret/noenv/noSA token/UID10001/same node를 유지하며 program byte를 대조합니다.
API mapping disabled 및 동일 Service selector의 기존 주의 조건을 확인합니다.

Job 생성 전에 renderer/helper와 Docker image를 준비합니다. 새 private0700 디렉터리에
baseline/manifest/helper SHA를 보존하고, 이미 알려진 same-node CoreDNS Pod와 같은 이름 CEP
각각 한 개를 GET해 owner UID/ready/identity labels를 대조합니다. Security identity는 private
comparison에만 사용하고 local map ID로 쓰지 않습니다. 이 metadata 대조에 다른 endpoint
exec나 CoreDNS map 조회는 필요 없습니다.

시간 순서는 다음과 같습니다. API/정책 조회 등 Job 전에 할 수 있는 작업을 관찰창 안으로
미루지 않습니다. 아래 metadata GET도 bounded하며 같은 endpoint exec를 반복하지 않습니다.

1. Server dry-run/create 후 exact Job UID를 기록합니다. 같은 Job의 selector Pod가 아직
   없으면 새 Job을 만들지 말고 제한된 시작 대기 내 그 Job 소유 Pod 하나를 GET합니다.
   Owner UID/name/controller, Running, container startedAt/node를 확인합니다. 시작 대기8초
   내 대상이 없거나 여러 Pod면 중단/정리합니다.
2. Job startTime을 기준으로 경과 시간을 기록합니다. Fresh Pod/CEP/agent GET 및 Docker
   `access_endpoint_pin.py --pod <before> --cep <before> --agent <before> --pin <새 pin>`을
   완료합니다. 모든 입력/출력은 private이고 pin 기존 파일 overwrite는 허용하지 않습니다.
3. 경과15초 이내에 같은 agent의 단일 `endpoint get <validated fresh ID> -o json`을
   시작합니다. 지원 JSON과 pin의 local ID/CNI/IP를 대조합니다. Metadata/Docker 준비가
   늦어 이 시작 한도를 놓치면 새로운 Job/더 긴 window로 재시도하지 않습니다.
4. 경과28초 이내에 아래 BPF map 조회를 **한 번** 시작합니다. 직전 Pod가 Running이고
   fresh CEP/local ID와 선택 agent의 연속성이 유지되는지도 확인합니다.

```text
cilium-dbg bpf policy get <validated fresh local endpoint ID> --numeric
```

5. 조회 직후 fresh Pod/CEP/agent를 다시 GET하고 Docker
   `access_endpoint_pin.py --pod <after> --cep <after> --agent <after> --pin <before pin>
   --response <보존 endpoint-get JSON>`으로 검증합니다. Job 경과48초 이내에 후검증을
   마쳐야 하며 늦으면 map 결과를 채택하지 않습니다. 이는60초 active deadline보다 앞선
   operator cutoff입니다. 실제 Pod lifetime을48초 이상으로 보장하는 것은 아닙니다.

**DNS JSON 수신을 기다리지 않고 Running Pod metadata로 진행합니다.** 기존 stdout는
buffered이므로 JSON이 보일 때까지 기다리면 관찰창을 놓칠 수 있습니다. Map snapshot이
질의 전/중/후 어느 시점인지 시간을 기록하며 질의 완료 뒤 counter라고 가정하지 않습니다.
DNS JSON은 Job 완료 후 보존합니다. 두 exec는 각각 TERM10초/KILL2초이므로 최대12초,
map을28초 내 시작하면40초 내 끝나고 후검증에8초를 배정합니다. Metadata/검증은 실제
시간을 측정하며 지연 시 cutoff에서 unknown으로 중단합니다. Pod가 완료/삭제/교체되거나
CEP/local ID/CNI/IP가 바뀌면 map 결과를 채택하지 않습니다. Endpoint get 응답은 기존
helper로 CNI/IP 일치까지 검사합니다. Deleted DN3 numeric ID/CoreDNS/host endpoint의
map을 대신 조회하지 않습니다. Same-name map path를 직접 열거나 bpftool/all list로 우회하지 않습니다.

Validated pin.endpointId가 integer1..65535임을 확인한 뒤 decimal string만 shell array에
넣습니다. `cilium-dbg`, `bpf`, `policy`, `get`, `<decimal ID>`, `--numeric` 여섯 argv만
사용하며 --all/output-format/label resolution flag를 추가하지 않습니다. Map output 자체는
local endpoint ID를 포함하지 않으므로 호출 argv/시각과 조회 전후 pin 증거를 함께 보존합니다.

기존 DN4B의 non-TTY/TERM10초+KILL2초/파일별256KiB/core-disabled/private0700 출력 제한을
적용합니다. exit0/완전한 지원 table만 private 대조합니다. Unknown schema/권한/빈 table/
출력한도/timeout이면 unknown으로 중단하며 같은 exec나 새 Job을 재시도하지 않습니다.
서로 다른 호출의 exit/stdout/stderr를 분리합니다. 기존 제한 wrapper의 POSIX ulimit-f512,
core0 및 trusted absolute kubectl 경로를 그대로 사용하며 Serving agent/DNS/API를 kill하지 않습니다.

Kernel table에서 egress CoreDNS identity의 TCP/UDP53 allow와 proxy port 존재 및 userspace
realized 항목 일치, host/remote-node 계열 deny/ANY scope를 대조합니다. Identity·proxy port·
topology 원문은 공유하지 않고 일치 여부만 전달합니다. Counter가 unavailable/sentinel이면
없는 것으로 집계하지 않습니다. 값이 있어도 한 번의 snapshot 누적 counter는 해당 DNS
질의나 특정 policy 원인의 인과 증명이 아닙니다. 정책 통계 reset/flush/write를 하지 않습니다.
Header는 POLICY/DIRECTION/IDENTITY/PORT/PROTO/PROXY PORT/AUTH TYPE/BYTES/PACKETS/
PREFIX/COOKIE입니다. 숫자 identity와 host-order port/range/protocol을 private로 대조하고,
BYTES/PACKETS의 `-`는 unavailable입니다. 지원하지 않는 table/header/row는 추정 parser로
처리하지 않습니다. Cookie를 policy name으로 간주하지 않습니다. CoreDNS metadata도
후검증해 UID/identity가 달라졌으면 그 identity 비교는 unknown으로 남깁니다.

Userspace와 kernel 불일치가 확인되면 그 경로의 근거를 인계합니다. 일치하더라도 실제
packet 경로 정상이나 DNS 원인 해소를 뜻하지 않습니다. BPF snapshot은 flow/verdict를
대신하지 않으며 node-protection 완화, host 전체 허용 또는 전역 proxy 수정 근거가 아닙니다.

정상/실패/cutoff/Pod 완료 어느 경우든 Job은 기존60초 deadline 이상 연장하지 않습니다.
Job completion 또는 실패 상태/log를 bounded 보존한 후 exact UID precondition/Foreground
삭제와 해당 Job 소유 Pod0 및 runner selector Pod0를 확인합니다. DNS timeout이어도
정리하며 stdout 부족/조회 실패를 이유로 새 Job을 만들지 않습니다. 그 뒤
API/runner/정책/CCNP/agent baseline을 후검증합니다. 원래 source/PVC/state/window/runner는
보존하며 재공급/새 창/Native/API enable/turn·실제 인증/최종 PR hold를 유지합니다.
