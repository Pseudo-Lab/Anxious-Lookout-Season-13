# 같은 fresh L7 endpoint의 query 전후 counter — 준비 / 별도 운영 검토

이번은 **새 control-only L7 CNP/holder 하나·단발4 helpers**입니다. L4를 반복하거나 생산 정책을
바꾸지 않습니다. 이전 admin contrast run 승인은 소비됐으며 실제 새 생성/GET/query는 완성 검토 후 PM이
담당합니다. Provider/callback TCP·TLS/HTTP/auth/turn은 추가하지 않습니다.

## 재사용 및 시간 예산

기존 policy_pair의 L7 spec만, holder l7, 전체 realized rows gate, metadata env2/소유 PID1 marker,
Service raw gate/root-owned0700·0600/전체 sudo wrapper/exact UID cleanup을 재사용합니다. 이름·image·
node·DNS·runAs10001·RO/no volume·Secret·token 및 DNS helper bytes는 그대로입니다.

기본 contrast 첫network28/end38 profile은 그대로 두며 counter-program만 **더 이른 admission18초/
본문 최대10초/종료28초**를 적용합니다. Holder40/Job50 및 기존 절대38초 상한은 늘리지 않습니다.
후표본에 최대10초 예산을 남기지만 완료 보장은 아닙니다. 준비가18초를 넘으면 query 없이 종료하고
after는 unknown입니다. Source/namespace/ownership 검증 지연도 이 예산에 포함하며 재시도하지 않습니다.

## 고정 native GET와 clock metadata

`access_dns_counter_capture.sh`는 stdin으로 known same-node agent에 전달하는 작은 고정 helper입니다.
호출은 각각 최대 한 번이며 내부 native 명령은 timeout TERM4+KILL1의 다음 argv 하나입니다:

```text
cilium-dbg endpoint get <fresh-numeric-CEP-id> -o json
```

Helper 자신의 `readlink /proc/self/ns/time` 및 `/proc/uptime`만 추가로 읽습니다. Agent 원본 프로세스의
cmdline/env/state나 native monitor/전체 metrics/log/packet은 읽지 않습니다. Time namespace와 GET
시작/종료의 BOOTTIME centisecond 상한을 stderr의 단일 JSON receipt에 남깁니다. stdout은 native 원문입니다.
Native stderr가 섞이거나 transport/child exit!=0/partial/empty/unsupported면 수집 실패입니다.

Linux uptime의 두 소수자리 floor에1centisecond를 더해 **보수적 upper bound**로 삼습니다. Lower bound는
upper-1입니다. 같은 node/agent/Pod binding 및 time namespace가 함께 같아야 PID1 startTicks에서
SC_CLK_TCK로 환산한 startNs/remote CLOCK_BOOTTIME과 비교할 수 있습니다. Node가 같다는 사실만
사용하거나 PM host clock으로 치환하지 않습니다. Unsupported precision/clock/ns는 unknown입니다.

Remote query 직전 자신의 time namespace를 before receipt와 비교하고 before end upper<=현재
BOOTTIME도 검사합니다. 다른 namespace/미래 before 표본은 claim/query 전에 거부됩니다.
CounterBudget은 remote body entered/finishedNs 및 startNs/afterCutoffNs를 private result에 기록합니다.

Root bounded capture에서의 fixed kubectl argv(기존 trusted absolute path/target/context)는 다음입니다.
Helper source는 review된 stdin bytes 그대로이며 현장에서 고치거나 shell로 command를 재조립하지 않습니다:

```text
sudo -n <trusted-kubectl> --request-timeout=8s -n kube-system exec -i <known-agent>
  -c cilium-agent -- sh -s -- before <numeric-id> - 0
sudo -n <trusted-kubectl> --request-timeout=8s -n kube-system exec -i <same-agent>
  -c cilium-agent -- sh -s -- after <same-id> <same-time-namespace> <after-cutoff-centiseconds>
```

after-cutoff-centiseconds는 result counterBudget.afterCutoffNs//10000000으로 Docker에서 계산합니다.
After helper는 시작 전에 namespace/시간 및4초 timeout+1초 kill의5초 예산을 검사해 부족하면 **GET 없이** 실패합니다. 시작했더라도
종료가 cutoff보다 늦거나 child 실패면 unknown이며 한 번 더 GET하지 않습니다. 이 helper는 새 범용
관측 도구가 아니며 정확한 endpoint JSON 한 번의 수집/clock receipt만 수행합니다.

## source와 strict numeric/schema

`access_dns_proxy_counter.py` bind는 같은 Job/Pod/CEP/agent/CNP·nonce/receipt/기존 scope를 묶습니다.
Pod containerID/restart0/startTicks, CEP/local endpoint id/IP/attachment/identity, agent containerID/image/
restart/startedAt, CNP UID/RV/spec hash, policy/proxy revision/allocated port를 전후 동일하게 확인합니다.
Agent 재시작/정책 재실현·revision 변화는 정상 delta로 처리하지 않습니다. After도 새 native JSON으로
기존 realized gate를 통과해야 하며 과거 scope를 복사하지 않습니다.

두 DNS egress53/same allocated-port entries는 transport 식별자가 없으므로 UDP/TCP로 이름을 붙이지
않습니다. 정확한 row fields/protocol/location/port/positive allocated port와 requests/responses 객체가
모두 필요합니다. Supported scalar received/forwarded/denied/error만 생략 시0입니다. Missing entry/
statistics/전체 요청·응답 객체/null/bool/float/string/음수/추가 field는 unknown입니다.

[Cilium1.20.2 API model](https://github.com/cilium/cilium/blob/v1.20.2/api/v1/models/message_forwarding_statistics.go):
개별 count는0..2^63-1. 두 entry 합계는0..2*(2^63-1)까지 **정확한 정수**로 계산합니다. 합계를 int64에
맞추거나 float로 변환하지 않습니다. Entry 순서가 달라도 결과는 같습니다.

총합 감소 또는 전후 두 vectors 사이에 가능한 componentwise nondecreasing bijection이 하나도 없으면
unknown입니다. 그러나 transport key가 없으므로 feasible pairing이 있어도 실제 특정 entry reset이
다른 entry 증가에 가려질 수 있습니다. `perEntryResetExcluded:false`를 항상 출력하며 고유 pairing도
실제 transport 매칭 증명으로 보지 않습니다. Agent/policy context를 고정해도 이 한계는 남습니다.

`compare`는 before 완료 upper<=remote entered, finished<=after begin lower, after end upper<start+38,
same time namespace 및18/28 profile을 확인합니다. Result는 full4 helper outcomes/transport0와 같은
phase/Job/Pod/nonce/CNP context가 필요합니다. Query 거부/ACK 실패/partial result/after timeout·누락/
endpoint 종료는 **unknown**이며 지원되는 빈 counter 객체0과 구분합니다. 공개 요약은 aggregate delta/
한계 flags만이고 private IDs/IP/원문은 출력하지 않습니다.

## PM 한 번의 절차

1. 완성 source/운영 승인 뒤 **새 run**의 root-private evidence/nonce/owned UID, fixed names/CNP/control
   잔여물0, production/API70/runner0/mapping disabled 및 policy/P1/CCNP/DNS/provenance를 확인합니다.
   생성은 새 CNP의 L7 spec 및 L7 holder 하나뿐이고 pair의 L4 산출물/transition은 실행하지 않습니다.
2. 단일 fresh owner Pod/CEP/agent/receipt/current CNP를 모읍니다. Numeric endpoint **before GET 한 번**으로
   native 원문/clock receipt를 보존하고 기존 L7 realized gate를 실행합니다. `bind`로 before binding을 생성합니다.
3. Docker `access_dns_contrast_exec.py counter-program`은 기존 command 입력 외 --counter-bound/
   --counter-clock/--counter-endpoint를 받아 before schema/clock/context를 검증하고 tighter program만 emit합니다.
   Fresh emitted file0600/hash와 root wrapper 단발 claim을 사용합니다.18초 admission 실패는 무재exec 중단입니다.
4. Transport0/full result일 때만 같은 numeric endpoint **after GET 한 번**을 수행합니다. Result의 own
   namespace/cutoff를 사용합니다. After fresh Pod/CEP/agent/CNP 및 raw native scope/binding을 확인합니다.
   원문 GET/exec start/end·command/exit/hash와 receipt/context를 보존합니다. 준비·capture·소유권 확인이
   holder 내에 끝나지 않으면 unknown입니다. Native after capture 자체는38초 안에 완료되어야 합니다.
   늦으면 재GET/query/시간 연장/다른 endpoint로 대체하지 않습니다. Offline sanitizer는 정리 뒤에도 실행 가능합니다.
5. `compare`는 before/after native·bindings·clocks·result/transport를 읽어 단일 요약 또는 unknown을 emit합니다.
   모든 outcome에서 owned Job exact UID Foreground 정리→owner/control/runner Pod0→owned CNP exact UID
   정리/부재→API/production/policy/Service/DNS 후검증을 수행합니다. UID 미확정/교체/삭제 실패는 미완료입니다.

All language emit/bind/compare/clock-cutoff 변환은 Docker입니다. Root UID 호출 방법은 기존
ACCESS-RUNNER-DNS-CONTRAST-OPERATOR.md를 적용하고 input/claims/UID를 과거 run에서 재사용하지 않습니다.

## counter가 말해주는 지점과 한계

[UpdateProxyStatistics](https://github.com/cilium/cilium/blob/v1.20.2/pkg/endpoint/endpoint.go#L1695-L1734)는
기존 redirect key를 찾아 notification 통계를 증가시킵니다. Key 부재면 갱신되지 않습니다.
[DNS handler](https://github.com/cilium/cilium/blob/v1.20.2/pkg/fqdn/messagehandler/message_handler.go#L168-L195)는
timeout/미식별 endpoint 등에 early return합니다. [ServeDNS](https://github.com/cilium/cilium/blob/v1.20.2/pkg/fqdn/dnsproxy/proxy.go#L1000-L1139)의
request notification은 upstream Exchange 전이고 response notification은 client WriteMsg 전입니다.

Request forwarded 증가이면 이 처리/허용 지점을 관측한 근거지만 upstream packet 송신 증명은 아닙니다.
Denied/error 증가이면 해당 처리 분기로 후보를 좁히고, response 증가+helper timeout이면 client 반환
구간을 우선 조사합니다. **delta0은 proxy 미수신/TPROXY 고장 확정이 아닙니다**. 비동기 갱신/upstream10초
처리가 helper 종료 뒤에도 남는 시점 한계도 있습니다. Counter/helper/packet 수를 동일시하지 않습니다.

이 한 지점 결과에 맞춰 최소 수정안을 정하고, 수정 후 original Runner app DNS와 그 다음 별도 검토된
anonymous provider 연결 검증으로 이어갑니다. 이번에는 관측 항목/정책/provider/auth를 늘리지 않습니다.
DNS/FQDN cache가 삭제로 즉시 원복됐다고 보지 않으며 cache flush도 없습니다. DN12 PAUSED 및 기존
원본 auth/state/PVC/window 비접근/재공급/API enable/Native·turn/최종 PR/public posting hold 유지.
