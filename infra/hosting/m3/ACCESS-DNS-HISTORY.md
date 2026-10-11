# #7 DN4B: 기존 node-local UDS의 DN3 역사 DNS verdict 단발 조회

DN4A 실제·독립 검증은 named node-protection이 diagnostic을 선택하고 CoreDNS는
제외하며, ingressDeny 없이 egress explicit deny가 있음을 확인했습니다. Hubble1.20.2
help는 필요한 옵션을 지원합니다. Listener 활성·접속/이력 존재는 아직 unknown입니다.
이 계획은 정책 변경, 새 Job/DNS 요청, 원래 runner/인증/window 조작을 포함하지 않습니다.

## 대상과 local UDS 근거

지정 DN3 증거 `/tmp/issue7-dns-pin-20261011/`의 diag.pin.json, diag.pod.json,
job.actual.json, diag.agent.json만 입력합니다. 이미 독립 검증된 pin과 Job owner UID,
namespace/name/IP/node/agent UID를 다시 대조합니다. 저장 Job의 시작~완료 구간
`2026-10-11T11:28:21Z`~`2026-10-11T11:29:11Z`만 사용합니다. 삭제된 numeric endpoint를
다시 live 조회하거나 새 Pod에 재사용하지 않습니다. Job/Pod 원본 증거의 무결성은 PM이
확인해야 하며 helper는 API GET을 대신하지 않습니다.
Docker UID는 metadata 파일의 실제 소유 UID/GID에 맞추고 권한을 넓히지 않습니다.

[Cilium1.20.2 troubleshooting](https://docs.cilium.io/en/stable/operations/troubleshooting/)
은 node-local `unix:///var/run/cilium/hubble.sock` 사용을 설명합니다. 이는 후보 경로의
근거이며 실제 설치에 그 socket/listener가 있다는 증명은 아닙니다. PM은 fresh baseline과
DN4A의 agent UID/container/image/containerStatuses 연속성을 확인한 뒤 같은 container에서
다음 **고정 경로의 socket type만** bounded exec로 검사합니다:

```sh
sh -c 'test ! -L /var/run/cilium/hubble.sock && test -S /var/run/cilium/hubble.sock'
```

exit0도 listener 활성 증명은 아닙니다. 아래 단일 실제 접속의 exit/result로 판별합니다.
Socket 부재·권한/CLI/schema 실패면 unknown으로 중단합니다. 다른 socket 탐색, localhost4245,
Relay/port-forward/TLS credential, Hubble enable/install/config/restart로 대체하지 않습니다.
Status/peers/topology 조회는 하지 않습니다.

## Offline scope 생성과 정확한 raw filter 대조

`access_dns_history.py`는 live 호출 없이 private plan0600 O_EXCL/NOFOLLOW를 생성하고,
stdout는 `historicalScopeCreated`만 반환합니다. Metadata와 출력 파일을 명시적으로 mount한
network-none/read-only Docker에서 실행합니다. 원래 source/credential/PVC를 mount하지 않습니다.

```text
access_dns_history.py --plan <새 private plan.json>
  --pin <diag.pin.json> --pod <diag.pod.json>
  --job <job.actual.json> --agent <diag.agent.json>
```

Plan.argv는 server를 위 UDS로 고정하고 dial timeout5s, since/until, JSON, field-mask를
명시합니다. Field-mask는 time/node/IP/endpoint name·identity/ports/event/verdict/drop reason/
denying policy name뿐이며 labels/payload/L7/HTTP/summary/extensions를 요청하지 않습니다.
Policy attribution은 name/kind/namespace를 요청해 동일 이름의 namespace 정책과 구분합니다.
`--last`와 시간 범위를 섞지 않으며 follow/all을 사용하지 않습니다.

두 raw allowlist 각각은 다음 AND 조건입니다:

- outbound: exact diagnostic source IP + namespace/name prefix + TCP/UDP destination53
  + event type drop(1)/policy-verdict(5).
- inbound: exact diagnostic destination IP + namespace/name prefix + TCP/UDP source53
  + 동일 두 event type.

값은 저장 pin에서 가져옵니다. Pod 필터는 CLI의 prefix semantics이므로 exact IP를 함께
묶고 응답의 전체 name을 후검증합니다. 각 allowlist 항목 사이 OR 외에 느슨한 항목을
추가하지 않습니다. Schema와 event 상수 근거는
[v1.20.2 FlowFilter](https://github.com/cilium/cilium/blob/v1.20.2/api/v1/flow/flow.proto) 및
[event types](https://github.com/cilium/cilium/blob/v1.20.2/pkg/monitor/api/types.go)입니다.

같은 선택 agent에서 Plan.argv에 `--print-raw-filters`만 추가해 단발 bounded exec합니다.
이 옵션은 DN4A 실제 help대로 request 없이 종료합니다. Private YAML 출력/exit를 보존하고
Docker `access_dns_history.py --plan ... --raw-filters ...`로 정확히 두 filter가 plan과
일치하는지 검사합니다. 지원한 제한 YAML 형태가 아니거나 exit!=0이면 중단합니다.
Flag를 일반 --pod/--port로 바꾸거나 field-mask 제거/출력 확대로 우회하지 않습니다.

## 단일 flow 요청과 출력 상한

Raw filter 검증 후 같은 agent에서 Plan.argv **그대로 한 번** 실행합니다. PM의 trusted
kubectl absolute path와 container name은 새 GET metadata로 대조하고, argv는 shell array로
읽으며 eval/string command 조합을 사용하지 않습니다. Non-TTY, 외부 TERM10초/KILL2초,
출력 파일별256KiB 상한을 적용합니다. Private0700 새 디렉터리, umask077, stdout/stderr/exit
별도 파일을 보존합니다. 다음은 기존 trusted wrapper 안의 제한 패턴입니다:

```sh
mapfile -d '' -t dn4_args < <(jq -j '.argv[] | ., "\u0000"' "$DN4_PLAN")
if (
  set -o posix
  ulimit -c 0
  ulimit -f 512
  exec timeout --signal=TERM --kill-after=2s 10s \
    "$DN4_KUBECTL" -n kube-system exec "$DN4_AGENT" -c "$DN4_CONTAINER" -- "${dn4_args[@]}"
) > "$DN4_OUT/flows.jsonl" 2> "$DN4_OUT/flows.stderr"; then
  dn4_exit=0
else
  dn4_exit=$?
fi
printf '%s\n' "$dn4_exit" > "$DN4_OUT/flows.exit"
```

POSIX ulimit-f512는512-byte blocks, 파일별262144 bytes입니다. 상한 도달/timeout/nonzero는
불완전 결과로 취급하며 stdout가 한도와 같아도 채택하지 않습니다. Signal은 이 단발
kubectl helper에만 적용하며 serving agent/CoreDNS/API를 kill하지 않습니다. Flow 요청은
follow=false/고정 until이므로 지속 collector를 두지 않습니다. Raw preview/console 출력은 없습니다.

## 결과 후검증과 판단

Docker `access_dns_history.py --plan ... --response <flows.jsonl> --exit-code <보존 exit>`를
실행합니다. 256KiB 미만/최대256 records, 정확한 node(default cluster prefix 또는 node명),
Job 시간, 전체 diagnostic name/IP, 양방향port53/TCP·UDP 및 event1/5를 검증합니다.
Empty/lost/malformed/foreign/L7/시간 불일치/한도초과/nonzero는 전체 unknown으로 거절합니다.
모든 정상 반환도 dnsCause=unknown을 유지하며 private identity를 출력하지 않고 matching/
dropped/policy-denied/named-node-policy-denied counts만 반환합니다. 추출한 record는 DNS
payload나 해당 요청의 hostname을 포함하지 않으므로 DNS 요청별 인과관계를 과장하지 않습니다.

Matching port53 drop와 node-protection policy attribution이 실제로 함께 있으면 해당
흐름의 차단 증거로 review에 전달합니다. Attribution이 없으면 전역 정책 원인으로
확정하지 않습니다. Audit는 실제 drop과 구분합니다. Port 변환/SNAT/metadata 누락/이력
만료 때문에 record가 없을 수 있으므로 proxy의 모든 왕복 정상이나 deny 부재를 뜻하지 않습니다.
새 Job/다른port/더 넓은 trace로 자동 재시도하지 않습니다.

전후 API70 UID/full spec/Ready1/updated1, runner UID/spec/replicas0/Pod0, P1/runner policy,
named CCNP UID/RV/spec 및 agent UID/container/image/containerStatuses를 대조합니다. 변경이면
결과 채택을 중단합니다. 이번 읽기에 Job cleanup은 필요 없고 기존 DN3 exact 삭제 증거를
유지합니다. 정책/Deployment/PVC/source/window 변경 및 실제 인증/최종 PR 승인은 없습니다.
