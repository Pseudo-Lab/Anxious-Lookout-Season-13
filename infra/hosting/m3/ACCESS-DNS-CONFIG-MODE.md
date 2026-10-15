# #7 DN11A 준비 패키지: runtime proxy 전달 방식 네 항목

DN10 actual과 독립 검증은 선택 allocation의 agent-owned IPv4 TCP listener1/UDP bound1을
확인했습니다. 실제 query 전달/upstream/return과 DNS 원인은 unknown입니다. 다음 전달 경로
읽기 범위를 고르기 위해 같은 agent의 runtime configuration 네 항목만 분류합니다.
이 문서는 **준비 패키지**이며 actual help/config 실행은 별도 reviewer 승인 후 PM이 담당합니다.
새 Job/query/holder/listener 반복·event 구독·config/정책 변경·설치/restart는 없습니다.

## Serializer 근거와 exact argv

[Cilium1.20.2 GET handler](https://github.com/cilium/cilium/blob/v1.20.2/daemon/restapi/config.go)의
getConfigHandler는 option.Config의 exported field 이름/값으로 DaemonConfigurationMap을
생성합니다. [DaemonConfig 선언](https://github.com/cilium/cilium/blob/v1.20.2/pkg/option/config.go)의
네 필드는 bool입니다. [모델](https://github.com/cilium/cilium/blob/v1.20.2/api/v1/models/daemon_configuration_status.go)은
이 map을 JSON으로 직렬화하고 [CLI](https://github.com/cilium/cilium/blob/v1.20.2/cilium-dbg/cmd/config.go)의
JSON+read-only 출력은 map 자체입니다. Fixture는 다음 **top-level Go field key**를 사용합니다.
CLI flag/default/ConfigMap 이름으로 JSON key/value를 추정하지 않습니다.

| JSON key | Type | 용도 |
| --- | --- | --- |
| EnableIPv4 | boolean | 관찰했던 IPv4 경로의 설정 |
| EnableBPFTProxy | boolean | BPF socket assignment 후보 |
| InstallIptRules | boolean | iptables 설치 후보 |
| EnableEndpointRoutes | boolean | 후속 local delivery/return 설계 조건 |

중복 key는 모든 JSON object에서 거절합니다. 누락/null/string/숫자/NaN/partial/추가 JSON/지원하지
않는 별칭 혼입은 네 항목 모두 unknown입니다. False는 실제 boolean false인 경우에만 반환합니다.
관련 없는 항목은 읽기 memory에서 버리고 보존하지 않습니다.

`access_config_remote.sh`는 정확히 mode와32자리 소문자 hex nonce 두 argv만 받습니다.
각 모드의 실제 CLI argv는 다음과 같으며 caller가 option을 덧붙일 수 없습니다.

```text
help: cilium-dbg config --help
read: cilium-dbg config --read-only -o json
```

CLI 소스는 positional options 없음/num-pages=0일 때 ConfigGet 후 출력/return하며 ConfigPatch
경로로 가지 않습니다. --read-only라는 flag 이름만을 안전 근거로 삼지 않습니다. Extra assignments/
num-pages/--all/추가 argv는 helper가 CLI 호출 전에 exit2로 거절합니다.

Help에서 usage/--read-only/-o --output만 검증합니다. Help는 버전 증명이 아닙니다.
DN5A/DN6A의 이미 확보된1.20.2 version 증거와 DN10 동일 imageID/UID/containerStatuses 연속성을
PM이 결합합니다. 새로운 version 명령을 추가하지 않습니다. 이 연결이 불가능하면 실행하지 않습니다.

## stdout/stderr 비보존과 종료 경계

`capture_access_config.sh`는 PM이 지정하는 trusted **absolute** Docker/kubectl binaries만
사용합니다. --log-driver=none/network none/read-only/cap-drop ALL/no-new-privileges Docker
sanitizer에 raw stdout와 raw stderr를 **별도0600 named FIFO**로 전달합니다. FIFO 경로는
파일시스템에 있지만 내용은 kernel pipe buffer이며 regular-file raw dump가 아닙니다.
Tee/console/Docker log/remote 파일 copy는 없습니다. 전체 read-only configuration은 네 key만
서버에서 필터된 응답이 아니며 **전체 JSON과 arbitrary stderr가 일시적으로 sanitizer memory를
통과하는 새 metadata scope**입니다. 원본 credential/source/PVC/env/cmdline/FD content를 읽지 않습니다.

Raw stderr의 첫 줄만 `DN11:<nonce>:<exact child4 JSON>` control로 허용합니다. Nonce는 호출
correlation이며 인증 토큰이 아닙니다. Mode/nonce 검증 뒤 helper 자체 stat와 comm=cilium-agent,
parent=group>1/live group signal0 gate를 통과한 후 marker를 발행하고 exec CLI합니다.
Exec는 helper의 PID/startTicks/group을 유지합니다. 임의 CLI stderr를 marker로 찾거나 scan하지
않습니다. 첫 marker 뒤 stderr가 하나라도 있으면 결과unknown입니다. Marker는 private receipt의
child4만 남기며 나머지 stderr는 원문/hash/reason을 보존하지 않습니다.

Reader는 합계256KiB **미만**, 단일 marker512bytes, monotonic12초 deadline입니다.
Invalid/partial JSON·한도·timeout·nonzero/미완료 transport는 전체unknown이며 partial options를
남기지 않습니다. Host dummy FIFO writer는 reader startup EOF 오판을 막고 producer 종료 뒤 닫습니다.
Reader-ready/transport exit/summary/receipt/nonce만 regular files이며 raw 문자열을 담지 않습니다.
Exit file은 stdout/stderr writer 종료 후 atomic rename으로 전달합니다. 한도 때문에 reader가 먼저
닫혀도 remote timeout은 유지됩니다. FIFO를 새 파이프/파일/더 큰 한도로 대체하지 않습니다.

Remote exact exec는 `timeout --signal=TERM --kill-after=1s 6s sh -s -- <mode> <nonce>`이며
host kubectl TERM10/KILL2, core0입니다. Reader Docker client는 startup 대기20초 및 local
TERM35/KILL2 제한이 있고 reader 자체는12초입니다. 이 제한의 존재만으로 remote 종료를
추정하지 않습니다. Marker의 정확한4 값에 대해 기존 `access_drop_remote.sh check`를 같은
agent/container에 stdin으로 한 번 전달해 childGone=true/groupGone=true와 proof exit0을
요구합니다. Serving process/다른 PID/group을 signal하지 않습니다. 실제 KILL fallback은 미검증입니다.

## PM 실행 순서와 evidence

1. Review 승인 후 private0700 input/output 및 source SHA/image를 준비합니다. API70 same UID/
   spec/Ready1/updated1, runner0/Pod0, P1/runner policy/CCNP UID/RV/spec, DN10 same-agent
   UID/spec/container/imageID/containerStatuses/hostNetworktrue/hostPIDfalse/sharePIDfalse를 확인합니다.
   Agent version은 기존1.20.2 증거에 연결합니다. Drift/unsupported면 시작하지 않습니다.
2. Help input directory에는 fresh `before.json`과 DN10 `diag.agent.after.json`에서 가져온
   `anchor.json` 두 private metadata 파일만 둡니다. Read input에는 아래에서 검증한
   `capability.json` 한 파일만 더 허용합니다. 다른 metadata/source/credential mount는
   없습니다. Capture는 before와 anchor의 UID/name/ns/spec/containerStatuses를 hash 결합하고
   phase Running/containers Ready/running 및 정확한 cilium-agent container를 요구합니다.
   이 anchor는 현재 상태 대체값이 아니라 연속성 검증 기준입니다.
3. 다음 환경변수를 PM trusted wrapper에서 지정하고 `bash capture_access_config.sh`를 help
   mode로 한 번 실행합니다. Output directory는 fresh empty/owner0700입니다.

```text
DN11_DOCKER=<trusted absolute Docker binary>
DN11_KUBECTL=<trusted absolute kubectl binary>
DN11_IMAGE=<reviewed immutable local image sha256 ID>
DN11_SOURCE_DIR=<reviewed absolute infra/hosting/m3 directory>
DN11_INPUT_DIR=<private dir containing before.json, anchor.json; read mode adds capability.json>
DN11_OUTPUT_DIR=<fresh private empty help directory>
DN11_AGENT=<exact pinned agent name>
DN11_MODE=help
```

4. `receipt.json`의 child4를 proof에 결합하고 fresh `after.json`을 GET합니다. 다음 offline
   Docker CLI로 capability 결과를 private 보관합니다(예시 path는 operator metadata입니다).

```text
python -B /checks/access_config_capture.py --before /inputs/before.json --anchor /inputs/anchor.json
  --after /metadata/after.json --receipt /result/receipt.json --proof /metadata/proof.json
  --transport-exit-code <preserved exit> --proof-exit-code <preserved proof exit>
```

   실행은 Docker 안에서만 합니다. helpSupported/contextMatched=true, versionVerifiedByHelp=false와
   contextTag가 capability의 제한 결과입니다. Exit/proof/context/capability 실패면 read로 진행하지 않습니다.
5. Read는 새 fresh before와 같은 anchor, 별도 fresh output directory, DN11_MODE=read로
   단발 실행합니다. 검증된 help final JSON을 input의 `capability.json`에 둡니다. Reader는
   capability/context가 일치해야만 ready를 발행하므로 gate 실패면 remote CLI를 시작하지 않습니다.
   Help nonce/marker/proof를 read에 재사용하지 않습니다. 새 종료 proof와
   after metadata 뒤 같은 finalizer에 `--capability <help final JSON>`을 추가합니다.
   Capability contextTag도 current agent와 일치해야 합니다. Reader receipt는 미채택 결과이며
   finalizer0/transport0/proof0/context 일치 뒤만 options를 채택합니다.
6. Help/read 시작·종료시각, 각 exit/source SHA/Docker image, safe receipt/summary/final,
   exact proof 및 fresh metadata를 private 보관합니다. FIFO는 각 종료에서 제거합니다.
   API/runner/Pod0/정책/CCNP/agent를 후검증합니다. 어떤 실패든 추가 exec/retry/설정 변경 없이
   unknown으로 인계합니다. 새 Job이 없으므로 Job cleanup은 발생하지 않습니다.

## 결과별 다음 후보 (실행 승인 아님)

EnableIPv4=false/unsupported는 unknown 분기입니다. BPF TPROXY=true는 endpoint에 적용된
datapath program/config와 socket assignment의 연결을 다음 제한 읽기 후보로 삼습니다.
BPF=false·InstallIptRules=true는 DNS 전용 TPROXY rule/mark/local delivery route 정합성이
후보입니다. 그 밖은 unknown입니다. EnableEndpointRoutes는 그 설계 조건으로 보관합니다.
[iptables 구현](https://github.com/cilium/cilium/blob/v1.20.2/pkg/datapath/iptables/iptables.go)은 BPF
socket assignment 때 per-proxy rule을 생략하므로 rule absence를 즉시 장애로 보지 않습니다.
[proxy datapath](https://github.com/cilium/cilium/blob/v1.20.2/bpf/lib/proxy.h)의 분기도 설정값 의미의 근거이며
실제 packet redirect/upstream/return 성공 증명은 아닙니다. TrafficConclusion은 항상unknown입니다.

Backend는 actual config/help/cluster 작업을 수행하지 않았습니다. 원래 source/PVC/state/window,
재공급/새 창/API enable/실제 인증/turn/최종 PR 및 public issue posting hold를 유지합니다.
