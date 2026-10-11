# #7 DN6B: owned child 종료 검증 후 단발 diagnostic DNS drop 관측

DN6A 실제·독립 검증은 설치 monitor의 drop/json/numeric/related-to 및 timeout의
signal/kill-after 옵션 표면만 확인했습니다. 제품/버전 또는 실제 child 종료/stream/schema
성공을 추정하지 않습니다. 이 패키지는 별도 review 대상이며 backend는 실제 agent exec,
event subscription, Job 생성 또는 inbox watcher를 시작하지 않습니다.

## 구현과 데이터 경계

- `access_drop_remote.sh`: PM이 같은 선택 agent/container의 `sh -s -- <mode> ...`에 stdin으로
  전달합니다. Remote 파일/프로그램 copy, 설치, daemon/config 변경 없이 foreground helper
  한 개만 실행합니다. Remote core dump는 끄고 --foreground/--preserve-status는 쓰지 않습니다.
- `probe`: 원격 timeout의 INT1초/KILL1초 안에서 무해한 shell+sleep child를 종료합니다.
  Shell은 INT에 자기 sleep만 TERM/wait하여 reaping하고 종료합니다. Monitor/네트워크는 없습니다.
- `observe <ID>`: 원격 timeout INT6초/KILL1초 안에서 `exec cilium-dbg monitor --type drop
  --related-to <fresh decimal local ID> --json --numeric`만 실행합니다.
- `check <pid> <parentPid> <group> <startTicks>`: 자기 helper가 발행한 PID/ticks를 한정해
  `/proc/<pid>/stat`의 동일 process 여부 및 signal0로 그 owned group의 존재만 검사합니다.
  Process list/다른 cmdline/serving process를 읽거나 signal로 중단하지 않습니다.
- `access_drop_capture.py`: Docker stdin만 읽고 raw stream/layer strings/unknown bytes를
  파일·stdout에 쓰지 않습니다. Exact endpoint, diagnostic IP, TCP·UDP 양방향53를 검사한
  drop만 reason category/hash·numeric identities·file/line·수신시각의 private receipt로 만듭니다.
  Reason 원문이나 DNS query/payload, peer IP, full summary는 보존하지 않습니다.

CLI 필터는 **client-side**이며 node stream이 server에서 port53로 제한되는 것은 아닙니다.
이 한계는 [이전 설계](ACCESS-DNS-DROP-PATH.md)의 version source 근거와 동일합니다.
Policy-verdict/trace/capture/L7/all을 추가하지 않습니다. Selected drop JSON의 `source`/`dstID`
중 하나가 fresh local ID여야 하고, summary.l3/l4는 exact IP와 source53 또는 destination53를
만족해야 합니다. Overlay/혼합 protocol/foreign/미지원 schema는 전체 unknown입니다.

Sanitizer는 총256KiB 미만, line16KiB, 전체256 records, stdin12초 alarm으로 제한합니다.
Lost/unknown/plaintext/malformed/한도/truncated/유효 event 뒤 foreign 혼입이면 이미 얻은
events도 모두 버립니다. Control PID/ticks만 종료 검증에 필요한 범위로 남깁니다.
Receipt는0600 O_EXCL/NOFOLLOW이며 overwrite/symlink를 거절합니다. Docker stdout는
완료 boolean 또는 안전한 counts뿐입니다. `--log-driver=none`을 사용합니다.

## 1. Job 이전: 무해한 종료 probe와 exact proof

Fresh API70 UID/full spec/Ready1/updated1, runner UID/spec/replicas0/Pod0, P1/runner policy,
CCNP UID/RV/spec 및 DN6A same-node agent UID/container/image/containerStatuses를 대조합니다.
도구 SHA/Docker/helper/renderer와 private0700 출력 디렉터리는 Job 전에 준비합니다.

Trusted absolute kubectl 경로로 같은 agent/container에서 remote script를 `probe` mode로
한 번 실행합니다. Non-TTY/host TERM10초+KILL2초/파일별256KiB/core0 wrapper는 유지합니다.
**stdout는 파일로 redirection/tee하지 않고** network-none/read-only Docker의 sanitizer로
직접 pipe합니다. Remote script는 stdin으로 전달하고 Docker stdin에는 stdout stream만 들어갑니다.
Pin/receipt mount와 실행 UID는 metadata 소유 UID/GID에 맞추며 source/credential/PVC는 mount하지 않습니다.

```text
remote: sh -s -- probe < access_drop_remote.sh
reader: access_drop_capture.py --mode probe --receipt <새 probe-receipt.json>
```

Probe 시작 control은 timeout의 child PID, parent/group, startTicks를 기록합니다. Child는
group=parent timeout PID임과 자기 live group에 대한 signal0 성공을 확인한 뒤만 control을
발행합니다. Group 분리/검사 미지원이면 client를 시작하지 않습니다. Probe control end의
timeoutExit=124와 정상 transport/helper exit가 있어야 합니다. Help만으로 이를 대신하지 않습니다.

Receipt.child의 정확한4 숫자만 같은 agent의 `check` mode에 전달합니다. Private proof의
PID/parent/group/startTicks가 receipt와 일치하고 childGone=true/groupGone=true/exit0이어야 합니다.
PID가 재사용됐으면 old startTicks와 구별합니다. Same PID/ticks가 살아있거나 group 존재면
중단하며 serving agent를 kill하거나 다른 PGID로 대체하지 않습니다.

```text
remote: sh -s -- check <pid> <parentPid> <group> <startTicks> < access_drop_remote.sh
final: access_drop_capture.py --receipt <probe-receipt> --proof <probe-proof>
       --transport-exit-code <probe kubectl exit>
```

`terminationProbePassed=true` 뒤만 Job 단계로 진행합니다. 이는 실제 INT 전달/cooperative
child reaping/group 종료 검증이며 **remote KILL fallback 검증은 아닙니다**. Docker fixture의
KILL 시험을 설치 환경의 성공으로 대체하지 않습니다. 실제 observe가137이면 종료 proof를
다시 요구하며 살아있는 group/zombie/접근 불확실성은 unknown입니다.

## 2. Fresh Job 한 개와6초 drop window

기존 dns-observed renderer/program/API597a image를 그대로 사용합니다. Same node,
noPVC/Secret/env/SA token/UID10001/60초/backoff0/8초+DNS+20초를 byte 대조합니다.
API mapping disabled 및 기존 Service selector 주의 조건을 확인합니다. Server dry-run 후
새 Job 한 개를 생성하고 controller owner UID의 단일 Running Pod를 선택합니다.

8초 내 시작 대기에서 대상이 없거나 여러 Pod면 unknown/정리합니다. 같은 Job을 재생성하지
않습니다. Fresh Pod/CEP/agent GET과 기존 `access_endpoint_pin.py`로 UID/node/local status.id/
CNI/IP를 pin하고, 같은 agent의 단일 endpoint-get JSON을 보존합니다. DNS 로그는 buffered이므로
수신을 기다리지 않습니다. 과거 삭제 endpoint ID나 security identity를 local ID로 쓰지 않습니다.

Job startTime 기준15초 이내에 observe helper를 시작합니다. Remote INT6초+KILL1초이며
host10초+KILL2초를 함께 제한합니다. Collector는 foreground helper의 자식 한 개이고
기존 node stream만 읽습니다. Stderr는 packet stdout과 분리된 bounded private CLI/transport
진단으로 보존하며 공개하거나 raw unknown stdout와 섞지 않습니다. 두 pipeline exit와 각
단계 시작/종료/Job 경과 시각, 실제8 argv의 endpoint ID binding 및 remote script SHA를 보존합니다.

```text
remote: sh -s -- observe <validated decimal ID> < access_drop_remote.sh
reader: access_drop_capture.py --mode observe --pin <fresh pin>
        --receipt <새 observe-receipt.json>
```

Raw stdout를 파일/console/Docker log에 남기지 않습니다. `set -o pipefail`과 Bash PIPESTATUS로
kubectl/reader 각각 exit를 보존합니다. Reader가 한도/오류로 먼저 끝나도 remote timer는 그대로
유지됩니다. Empty는 통과 증거가 아닙니다. Control start/end는 stream order를 표시하며
receipt.receivedAt는 **수신시각**이지 kernel event 발생 timestamp가 아닙니다.
관측 command 시각과 container startedAt/8초 leading delay/DNS 완료 결과를 함께 기록합니다.
6초는 전체 DNS 질의를 덮지 않습니다. Exact DNS drop 자체가 관측돼야 해당 실패 사건의
근거로 삼을 수 있으며 시간 구간만으로 질의 발생/통과를 추정하지 않습니다.

## 3. Child 종료와 fresh pin 후검증 뒤만 결과 채택

Remote control end 수신/정상 transport 뒤 정확한 observe child4 숫자의 check proof를
같은 agent에서 한 번 얻습니다. Control end가 없으면 helper 시작 후8초가 지난 뒤만
check하고 결과는 incomplete/unknown으로 유지합니다. Child 식별자도 없으면 원격 timer의
중단 보장을 추정하지 않고 unknown으로 인계합니다. Group 존재 시 결과를 채택하지 않습니다.
Check는 signal0만이며 serving process/다른 group을 kill하지 않습니다.

Fresh Pod/CEP/agent를 다시 GET하고 Job 경과40초 이내에 후검증을 완료합니다. Pod가 먼저
종료/삭제/교체되거나 CEP/agent/CNI/IP drift가 있으면 전체 unknown입니다. Receipt/proof와
해당 pin, fresh after metadata, endpoint-get JSON 및 kubectl exit0를 Docker finalizer에 넣습니다:

```text
access_drop_capture.py --receipt <observe-receipt> --proof <observe-proof>
  --transport-exit-code <observe kubectl exit> --pin <before pin>
  --pod <after pod> --cep <after cep> --agent <after agent> --endpoint <endpoint-get JSON>
```

Finalizer는 proof를 receipt.child에 정확히 결합하고 기존 pin verify를 실행합니다. Probe의
다른 PID proof를 observe에 재사용할 수 없습니다. 정상 출력은 matchingDnsDrops/
policyDeniedDrops뿐이며 dnsCause/noDropConclusion은unknown입니다. Other drop reason은 raw
문자열 대신 digest로 남기므로 version source의 공개 reason 목록과 offline 대조할 수 있습니다.
PolicyDenied/denylist 사건도 named CCNP attribution 자체가 아닙니다. Matching rule/identity/
location을 검토한 뒤만 수정안을 따로 제안합니다.

모든 정상/실패/cutoff 후 Job completion 또는 실패/log를 bounded 보존하고 exact UID
Foreground 삭제/소유 Pod0·runner selector Pod0 및 API/runner/정책/CCNP/agent baseline을
후검증합니다. Persistent client/Job/watcher를 남기지 않습니다. 종료 증명이 실패하면
새 Job/구독/타입/port/관측창을 넓히지 않고 해당 소유 child 증거와 blocker를 PM에 전달합니다.
원래 source/PVC/state/window/runner 및 정책/global proxy는 변경하지 않습니다. 재공급/새 창/
Native/API enable/turn·실제 인증/최종 PR hold는 유지합니다.
