# #7 DN10 제안: endpoint의 할당 DNS port와 agent-owned listener metadata

DN9 actual/독립 검증은 Service와 known Pod의 UDP/TCP 네 attempt가 모두 timeout임을
확인했습니다. Service-only 차이는 관측되지 않았고 공통 경로는unknown입니다. 같은 질의/
구독을 반복하지 않고 다음 **별도 가설**을 판별합니다: Fresh endpoint에 할당된 DNS proxy
port에 실제 TCP listener/UDP bound socket이 존재하고 해당 same-node Cilium agent가 소유하는가?

할당/정책 revision은 실제 listener 존재와 다릅니다. [Cilium1.20.2 DNS proxy 구현](https://github.com/cilium/cilium/blob/v1.20.2/pkg/fqdn/dnsproxy/proxy.go)
은 agent 내 DNS proxy와 TCP/UDP IPv4/IPv6 server 및 BindPort를 정의합니다. 실제 설치는
metadata로 따로 확인합니다. Listener 존재도 packet 전달/IPv4 dual-stack 동작/upstream/
return/DNS 정상 증명이 아니며 absence를 과거 DN9 timeout의 원인으로 바로 단정하지 않습니다.

## 새 관측 범위와 원문 처리

- `dns-proxy-listener` renderer는 기존 API597a/same node/app/UID10001/RO/noPVC·Secret·env·
  SA token/runner0/Pod0를 유지한 idle Job 하나입니다.40초만 sleep하며 시작/완료 boolean을
  flush하고 deadline50/backoff0입니다. **DNS/socket/query/libc/event/Native 호출이 없습니다.**
  Holder는 기존 app policy의 endpoint/port 할당을 관찰하는 동안 유지하는 용도입니다.
- 확장 pin helper는 이 holder의 exact 이름 prefix만 기존 app 조건 아래 추가 허용합니다.
  다른 namespace/app/Job wildcard를 허용하지 않으며 새 Pod owner UID/CEP/node/agent/CNI/IP를
  조회 전후에 기존과 같이 결합합니다.
- `access_proxy_listener_scope.py`는 endpoint-get JSON의 desired/realized/proxy revision 일치,
  egress53 DNS statistics 두 항목의 **단일 공통 allocated port**를 검사합니다. Port/revision이
  비지원/누락/분리/범위 밖이면 unknown이며 다른 port를 추정하지 않습니다. Scope0600는
  O_EXCL/NOFOLLOW이고 raw port/IDs는 stdout에 없습니다.
  설치1.20.2의 desired revision JSON 경로는 **status.policy.spec.policy-revision**입니다.
  실제 DN5B 저장 JSON과 offline parser를 대조했으며 policy.desired fallback은 사용하지 않습니다.
- **추가 infrastructure metadata read scope:** 같은 선택 agent/container에서 `/proc/1/comm`,
  `/proc/1/fd` symlink metadata와 `/proc/net/tcp`, udp, tcp6, udp6를 읽습니다. Agent hostNetwork=true,
  hostPID=false/shareProcessNamespace=false 및 comm=cilium-agent일 때만 동작합니다.
  다른 PID/process list/env/cmdline/credential/file content를 읽지 않습니다. HostPID/shared namespace/
  comm 불일치면 다른 PID를 찾지 않고 중단합니다.
- Shell helper는 namespace socket tables와 **선택 agent의 FD symlink targets를 memory에서
  스캔**합니다. Kernel이 한 port의 rows만 보내는 API가 아닙니다. 이 경계를 감추지 않습니다.
  Rows/addresses/ports/inodes/FD targets/파일 경로를 stdout·파일·console에 보존하지 않고 지정
  port의 TCP LISTEN(state0A), UDP unconnected bound(state07), agent inode ownership 및 wildcard
  binding counts만 반환합니다. FD content는 열지 않습니다.

## 실행 gate와 한도

1. 기존 API70/runner0/Pod0/P1/runner policy/CCNP 및 DN9 same-node agent UID/container/image/
   containerStatuses 연속성을 대조합니다. 저장 DN6A agent는 hostNetworktrue/hostPIDfalse/
   shareProcessNamespacefalse였으며 이는 현재 상태 증명이 아니므로 **Job 전 fresh spec**을
   재확인합니다. 새로운 scope/조건 미지원이면 Job을 만들지 않습니다.
2. Docker/private manifest/serverdryrun 뒤 holder Job 한 개를 생성합니다. Exact controller owner의
   단일 Running Pod를8초 내 선택하고 node/image/security/DNS spec을 dryrun/DN9·DN8과 대조합니다.
   로그의 holding=true를 endpoint ready로 해석하지 않습니다. Fresh Pod/CEP/agent pin과 같은
   agent의 단일 endpoint-get JSON으로 current allocated port scope를 생성합니다.
3. Job startTime15초 내 같은 agent/container에서 reviewed shell source를 stdin으로 한 번 실행합니다:
   `timeout --signal=TERM --kill-after=1s 6s sh -s -- <scope의 canonical decimal port> < access_proxy_listener.sh`.
   --foreground/preserve-status 없이 별도 timeout group 아래에서만 검사합니다. Helper는 자신의
   PID/parent/group/startTicks를 /proc/$$/stat로 읽어 group=parent와 live signal0를 확인하고
   **private stderr의 listenerChild JSON 한 줄**로 발행합니다. 이것은 serving agent PID가 아닙니다.
   원격 파일 copy/설치/권한 변경/관측 설정/agent restart는 없습니다. readlink 부재/권한/unknown
   proc schema/파일 부재는unknown입니다. 다른 파일/PID/ss/netstat/bpftool로 대체하지 않습니다.
4. Non-TTY/remote TERM6초+KILL1초/host TERM10초+KILL2초/private0700/core0/파일별256KiB wrapper를 유지합니다. Helper는
   agent FD 최대4096개, table별 최대4096 rows이며 한도/누락/형식 실패면 partial 결과 없이unknown/
   exit1입니다. UDP connect-to-peer/TCP established rows는 listener로 집계하지 않습니다.
   Symlink targets는 inode 비교용 metadata일 뿐 source 내용 접근이 아닙니다.
   Header 전체와 모든 row를 port filtering **전에** 검사합니다. IPv4/IPv6 주소·port·state·
   queue·timer·숫자·pointer 및 TCP17-field/UDP13-field tail을 검증하며 TCP TIME_WAIT/open-request의
   inode0인12-field 형태만 별도 허용합니다. 빈/잘린/미지원 row, 중복 header, newline 없는 마지막
   줄도 전체unknown입니다. Timer hex는8~16자리, pointer는8/16자리만 지원합니다.
   형태 기준은 [Linux TCP proc formatter](https://github.com/torvalds/linux/blob/v6.1/net/ipv4/tcp_ipv4.c)
   와 [UDP proc formatter](https://github.com/torvalds/linux/blob/v6.1/net/ipv4/udp.c)이며 설치 환경의
   다른 형태를 추정하여 정상 zero로 처리하지 않습니다.
5. Metadata 직후 stderr에서 발행한 **정확한4 숫자**를 기존 access_drop_remote.sh check mode에
   전달해 private proof를 얻습니다. Source를 복사/설치하지 않고 sh stdin으로 전달합니다.
   PID/startTicks/group에 결합된 childGone=true/groupGone=true/exit0 뒤만 결과를 채택합니다.
   Marker 없음/nonzero/unknown/live group은unknown이며 다른 PID/group/serving process를 signal하지 않습니다.
   정상적으로 종료된 helper의 proof도 요구하며 timeout 옵션 지원을 실제 종료 증명으로 대체하지 않습니다.
   그 뒤 **두 번째 endpoint-get**(동일 fresh local ID)과 fresh Pod/CEP/agent GET으로
   allocation/revision/UID/CNI/IP가 전후 같은지 재검증합니다. 이 endpoint reread는 selected-port
   binding 후검증이며 반복 DNS/config 조사나 metadata helper 재실행이 아닙니다.
   Scope/result/child/proof/transport0/fresh after 자료를 Docker scope finalizer로 대조합니다.
   Job 경과36초 이내 완료하고 먼저 Pod 종료/교체/agent container drift면 전체unknown입니다.
6. Holder40초 종료/Job 상태/log를 bounded 보존하고 모든 outcome exact UID Foreground 삭제/
   소유 Pod0·runner Pod0 및 API/runner/정책/CCNP/agent baseline을 후검증합니다.50초 deadline은
   연장하지 않습니다. 같은 holder/port/helper를 다시 실행하지 않습니다.

Scope CLI: before는 기존 pin/Pod/CEP/agent/endpoint JSON과 `--scope <새 private file>`로 생성합니다.
After는 같은 pin/새 metadata/**after endpoint JSON**와 `--scope <before scope> --result <safe shell JSON>
--child <listenerChild 한 JSON> --proof <정확한 check proof> --transport-exit-code <보존 exit>`를 넣습니다.
PID/port/inode/주소 원문은 공개하지 않습니다. Marker/proof parse 오류나 시간 제한 실패는 결과unknown입니다.

## 결과 해석과 다음 경계

모든 정상 결과는 selectedPortContextMatches/agentProcessMatched와 각 family/protocol의
selectedBoundSockets/ownedByAgent/wildcardBinds 및 dnsCause/trafficConclusion=unknown입니다.
다른 process가 같은 port를 소유하는 경우 bound와 owned를 구분합니다. Kernel/FD 읽기는
atomic snapshot이 아니므로 race/닫힘/권한/한도/unknown을 missing listener로 바꾸지 않습니다.
IPv6 socket의 V6ONLY/IPv4 acceptance 옵션은 읽지 않으므로 IPv4 table absence만으로 IPv4
listener 부재를 확정하지 않습니다. Wildcard bind도 접근·redirect·return 정상 증명이 아닙니다.

Owned listener가 있어도 기존 packets가 도달/forward/return했다고 하지 않습니다. 없는 경우도
과거 query 시점의 absence나 named policy 원인으로 단정하지 않고 해당 allocation/listener
경로의 다음 좁힌 확인 후보를 따로 검토합니다. Port reset/rebind, global proxy/host/CIDR/deny/
whitelist 변경은 이 패키지에 포함하지 않습니다. 원래 source/PVC/state/window/runner 및
재공급/새창/Native/API enable/turn·웹 Codex 성공/최종 PR hold는 유지합니다.

Backend는 actual Job/agent `/proc`/FD/socket/event/원본 source 접근을 실행하지 않았습니다.
Synthetic proc/FD fixture와 emit/scope validation만 Docker에서 검증합니다. Inbox watcher는 시작하지 않습니다.
