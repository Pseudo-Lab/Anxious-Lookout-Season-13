# #7 DN5B 결과 이후: credential-free DNS drop event의 제한 관측 제안

DN5B actual 및 독립 검증은 single-ID map 조회 성공, fresh pin/time/cleanup 유효,
관찰 시점 CoreDNS53/TCP·UDP allow와 userspace/kernel proxy port 일치를 확인했습니다.
그 시점의 DNS kernel 규칙 누락, numeric CLI 조회 실패, CoreDNS label/identity 불일치
가설은 좁혀졌습니다. 전체 시간의 policy 수렴이나 실제 proxy 왕복을 증명하지 않습니다.
두 대상 DNS는 계속 timeout이고 모든 counters는 unavailable `-`입니다. Counters를0으로
해석하거나 node-protection deny 존재만으로 원인을 확정/완화하지 않습니다.

다음은 같은 구성 snapshot을 반복하는 대신 새 credential-free diagnostic **한 개의
drop 사건**을 관측하는 후보입니다. 아직 event subscription을 실행하거나 내부 inbox
Monitor/inotify watcher를 시작하지 않았습니다. 운영 실행은 PM과 별도 review 범위입니다.

## 실제 지원 근거와 제한

보존 DN5A status에서 nodeMonitor metadata가 있고 Hubble Disabled입니다. 이는 기존
node event 기능의 보고이며 실제 subscription 접속 가능 증명은 아닙니다.
[공식1.20.2 CLI](https://docs.cilium.io/en/stable/cmdref/cilium-dbg_monitor/)는
`cilium-dbg monitor --type drop --related-to <local endpoint ID> --json --numeric`을 지원합니다.
설치 binary/help와 단발 실행은 별도로 확인해야 합니다. Hubble 설치/활성화는 필요 없습니다.

[v1.20.2 formatter](https://github.com/cilium/cilium/blob/v1.20.2/pkg/monitor/format/format.go)는
CLI process 안에서 event type와 local source/destination endpoint를 먼저 비교한 뒤
일치하는 notification을 출력합니다. **서버에서 port53로만 보내는 filter는 아닙니다.**
CLI는 node stream을 받아 client에서 걸러내므로 이 한계와 bounded raw 처리/보존 범위를
후속 review에 드러내야 합니다. Node 전체 event를 저장하는 collector로 사용하지 않습니다.

[drop JSON 구현](https://github.com/cilium/cilium/blob/v1.20.2/pkg/monitor/datapath_drop.go)은
reason/local endpoint/identity와 summary를 출력합니다. 후속 처리기는 exact diagnostic
IP 및 TCP/UDP53를 검사하고 reason/location/identity 같은 최소 필드만 private 보존해야 합니다.
Summary의 layer 문자열 전체를 그대로 저장/공유하지 않습니다. CLI의 unknown/decode/lost
라인은 선택 target에 결합된 JSON이 아니므로 저장하지 않고 전체 결과를 unknown으로 처리합니다.

**Policy-verdict를 함께 요청하지 않습니다.**
[동일 버전 구현](https://github.com/cilium/cilium/blob/v1.20.2/pkg/monitor/datapath_policy.go)은
policy-verdict의 --json에도 text DumpInfo를 사용하며 GetDst가 remote security identity를
uint16으로 변환합니다. Related-to 숫자가 다른 workload의 security identity와 우연히
겹치는 것을 target endpoint 증거로 오인할 수 있습니다. Drop의 GetDst는 실제 local
destination endpoint입니다. Drop-only와 exact IP/53 후검증을 함께 사용합니다.

## DN6A: 이번에 검토 요청하는 capability만

API70/runner0/Pod0/P1/runner policy/CCNP 및 기존 same-node agent UID/container/image/
containerStatuses 전후 baseline 연속성을 대조합니다. 같은 agent/container에서 아래 두
명령만 각 non-TTY/외부 TERM10초+KILL2초/private 출력 상한으로 실행합니다:

```text
cilium-dbg monitor --help
timeout --help
```

첫 명령은 event subscription을 시작하지 않는 help만입니다. Drop/json/numeric/related-to
옵션을 확인합니다. 둘째는 기존 remote timeout binary가 INT/kill-after 및 process 종료
제한을 지원하는지 확인합니다. 실제 설치가 GNU-compatible하지 않거나 없으면 unknown으로
중단하며 도구 설치/copy/daemon 권한 변경이나 host timeout만으로 대체하지 않습니다.
Help 성공은 node stream/schema/필터 효과/종료 검증의 성공 증명이 아닙니다.

이 단계는 새 Job/endpoint 조회/packet/event subscription/상시 watcher를 포함하지 않습니다.
기존 status/config/정책 snapshot exec도 추가하지 않습니다. 설치 capability와 lifetime
제한의 실제 근거가 생기면 아래 DN6B를 별도 구현·검토하며 이번 승인으로 실행하지 않습니다.

## DN6B 후보: 짧은 단발 drop 관측과 별도 검토 조건

- 기존 dns-observed source/image의 단발 fresh Job/owner Pod/CEP/agent pin을 사용합니다.
  Credential/PVC/Secret/SA token 없이 원래 두 DNS 이름/목적지/정책을 유지합니다.
  삭제 endpoint ID를 live로 재사용하지 않습니다. 새 listener나 관측 설정을 만들지 않습니다.
- Remote timeout으로 CLI 자체를 INT6초/KILL1초 이내 종료하고 local wrapper도10초/KILL2초로
  제한하는 후보입니다. Exact 종료/재연결 여부를 검사할 절차를 설치 timeout help와
  CLI source에 맞춰 고정해야 합니다. Source의 EOF retry delay12초보다 짧은 window이며
  foreground만 사용합니다. Serving agent/CoreDNS/API를 signal 대상으로 삼지 않습니다.
- Fresh local endpoint ID/type=drop/numeric/JSON만 CLI에서 선택합니다. Snapshot 준비 대신
  관측 구간과 DNS 실행 구간이 겹치는 실제 시각을 기록합니다.6초는 모든 질의/왕복을
  덮는 구간이 아니므로 사건 부재를 통과나 원인 부재로 해석하지 않습니다.
- Node stream/CLI client-side filter라는 한계를 감추지 않습니다. 별도 제한 처리기는 stdout를
  memory에서 bounded line/byte/record 수로 읽고, 실제 drop JSON의 endpoint와 exact IP 및
  양방향53/TCPUDP만 받아 최소 private metadata로 변환해야 합니다. Raw layer string/payload/
  unknown event bytes/node-wide data를 파일/console에 보존하지 않습니다. EOF/lost/잘못된
  schema/foreign/malformed/limit/종료 실패는 전체 unknown입니다. 구현과 반증 테스트가 필요합니다.
- 조회 전후 fresh pin/Pod Running과 agent 연속성이 확인돼야 합니다. Job60초 cutoff/정확한
  child 종료/Job UID Foreground cleanup/소유 Pod0/baseline 증거를 함께 보존해야 합니다.
  Capability/schema 실패 시 scope/type/port를 넓히거나 새 Job으로 반복하지 않습니다.

유효 DNS drop의 reason/location/identity가 확보되면 그 제한된 흐름의 실패 경로를 좁힙니다.
PolicyDenied 하나가 named CCNP attribution을 직접 제공한다고 가정하지 않습니다. 원인을
matching rule과 연결한 뒤에만 수정안을 따로 검토합니다. 원래 runner/state/source/window,
정책/전역 proxy 및 재공급/새 창/Native/API enable/turn·최종 PR hold를 유지합니다.
