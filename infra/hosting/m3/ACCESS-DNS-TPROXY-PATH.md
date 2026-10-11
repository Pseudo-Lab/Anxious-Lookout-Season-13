# #7 DN11B 준비: DNS TPROXY rule/mark와 한 번의 local FIB lookup

DN11A actual/독립 검증은 EnableIPv4=true, EnableBPFTProxy=false, InstallIptRules=true,
EnableEndpointRoutes=false 및 iptables_tproxy 후보를 확인했습니다. Config는 실제 전달/응답
성공 증명이 아닙니다. 이 패키지는 다음 정적 정합성만 구분하며 **실제 실행은 별도 review
승인 후 PM 범위**입니다. 새 Job/query/holder/listener/event/config/help 반복은 없습니다.

## 공개 source와 최소 명령 범위

[Cilium1.20.2 iptables 구현](https://github.com/cilium/cilium/blob/v1.20.2/pkg/datapath/iptables/iptables.go)은
DNS proxy 이름의 comment를 붙인 TCP/UDP TPROXY rule을 CILIUM_PRE_mangle에 설치합니다.
Target은127.0.0.1과 proxy port입니다. Match mark는 HostToNetwork16(port)<<16 OR0x200,
TPROXY mark는0x200입니다. [mark 상수](https://github.com/cilium/cilium/blob/v1.20.2/pkg/datapath/linux/linux_defaults/mark.go)
및 [proxy routes](https://github.com/cilium/cilium/blob/v1.20.2/pkg/proxy/routes.go)/
[routing defaults](https://github.com/cilium/cilium/blob/v1.20.2/pkg/datapath/linux/linux_defaults/linux_defaults.go)는
to-proxy mark0x200/mask0xf00, priority9와 table2004의 local0.0.0.0/0→lo를 정의합니다.

처음 계획의 RPDB/route show는 **실행 후보에서 제거**했습니다. Priority selector는 ip client가
내부 IPv4 rule dump를 필터하기 때문입니다. 아래의 route get은 전체 rule/table dump가 아닙니다.
Exact CLI는 다음 세 모드뿐이며 source helper는 mode/nonce 두 argv 외 입력을 거절합니다.

```text
capability: uname -m
            iptables --version
            ip -Version
dns-rules:  iptables -w 2 -t mangle -S CILIUM_PRE_mangle
proxy-route: ip -j -4 route get fibmatch 198.18.0.1 mark 0x200
```

[iproute2 get 구현](https://github.com/iproute2/iproute2/blob/v6.1.0/ip/iproute.c)은 RTM_GETROUTE
request에 dst/mark와 RTM_F_FIB_MATCH를 넣어 netlink 응답을 출력합니다. **DNS 질의/외부 TCP·UDP 연결/
network packet 송신이 없는 단일 kernel routing lookup**입니다. Local netlink socket/message는
metadata 요청에 사용됩니다. Benchmark IPv4는 고정 lookup 입력일
뿐 실제 provider/Service/CoreDNS 주소가 아닙니다. fibmatch는 원래 FIB route를 요청하므로
local/default/table2004/lo 정합성을 판별하는 후보이며 설치/kernel 지원은 실제 호출 전 미검증입니다.
미지원/nonzero/권한 실패면unknown이고 show/dump/일반 get/다른 주소로 재시도하지 않습니다.

Capability는 x86_64 또는 aarch64의 little-endian과 iptables1.8.x(legacy/nf_tables),
iproute2 6.x(x>=1)의 알려진 출력 가족만 인정합니다. CLI 이름/버전만으로 read 성공을
보장하지 않습니다. 다른 architecture/version/schema는unknown입니다. 새 config/help/version
Cilium 조회를 추가하지 않고 기존 DN11A의1.20.2 및 DN10 anchor 연속성을 유지합니다.

## 데이터/port 시점과 판정

- DNS chain 조회는 **그 지정 chain 전체**를 stdout/FIFO→sanitizer memory에서 받습니다.
  Non-DNS rule도 transient memory에 들어오며 -S header/basic framing만 확인한 뒤 버립니다.
  전체 mangle/filter/nat table, 다른 chain, iptables-save/all/전체 route/rule 출력은 없습니다.
- Exact `cilium: TPROXY to host cilium-dns-egress proxy` comment의 row만 완전한 option grammar,
  TCP/UDP/mark/mask/IPv4 target/decimal port/TPROXY rewrite를 검증합니다. Chain256lines/
  line4096bytes, malformed/잘림/중복 field/미지원 row/comment/partial line은 전체unknown입니다.
- **Port는 이번 fresh chain 응답의 각 DNS row에 나온 값**입니다. TCP/UDP 각 하나,
  common port 및 같은 row port의 network-byte-order mark/localhost/rewrite 정합성만 비교합니다.
  DN10의 옛 allocation/FD/socket/삭제 endpoint를 조회 입력이나 현재 port 증거로 사용하지 않습니다.
  새 holder/endpoint/socket이 없으므로 현재 endpoint allocation 또는 현재 listener와의 연계는
  결과가 모두 일치해도unknown입니다. Port/주소/mark/rule/comment 원문은 receipt에 남기지 않습니다.
- FIB JSON은 단일 simple route의 알려진 key/type만 허용합니다. Local/default/table2004/lo가
  모두 맞아야 supported_match입니다. Numeric 또는 decimal-string table2004를 지원합니다.
  지원 route type은 local/unicast뿐입니다. 위 iproute2 print_route source는 details 없는 기본
  RTN_UNICAST type을 생략하므로 type key 부재는 unicast로 처리하고 지원된 mismatch로 구분합니다.
  명시적 빈/미지원 type 문자열 및 잘못된 type은 전체unknown입니다. 명시적 local만 match 후보입니다.
  Missing table/multiple routes/duplicate key/cache/encap/multipath/알 수 없는 selector/type는
  unknown입니다. 기본 table 이름 추정/다른 출력 방식 fallback은 하지 않습니다.

| 판정 | 조건 | 수정 검토로 이어지는 의미 |
| --- | --- | --- |
| supported_match | 완전한 지원 출력과 exact proof/context 뒤 DNS pair/mark/target 및 해당 FIB 결과 일치 | 관찰 시점 이 좁은 정적 불일치 가설만 줄임; feeder/실제 skb mark/endpoint/upstream/return은unknown |
| absent | 정상 exit/완전한 지원 chain header이며 DNS TCP/UDP row 둘 다 없음 | 현재 DNS 전용 rule 생성/복구 경로의 별도 최소 조사 근거; 과거 query 원인/현 listener 부재나 자동 rule 추가 근거 아님 |
| mismatch | 지원된 정상 출력에서 pair 누락/중복/port/mark/target/rewrite 또는 FIB 결과 불일치 | 해당 component의 새 증거를 바탕으로 수정안을 따로 review; rebind/restart/전역 deny 완화 자동 수행 없음 |
| unknown | capability/schema/한도/transport/proof/context 실패 또는 일부 단계 미채택 | 부재/정상/원인을 판정하지 않고 결과 인계; 범위/한도/명령/주소 확장이나 retry 없음 |

FIB lookup은 source/iif/protocol/port를 지정하지 않은 **이 하나의 mark/destination lookup**입니다.
Priority9 실제 존재, 모든 목적지, DNS packet/veth의 실제 route, PREROUTING feeder나 route 우선순위
전체를 입증하지 않습니다. 여러 읽기는 atomic snapshot이 아닙니다. Same-agent context는
UID/spec/image/container 연속성이지 kernel rule/route의 원자성 보장이 아닙니다.

## 기존 bounded transport 재사용

`capture_access_tproxy.sh`는 DN11A와 같은 trusted absolute binaries/immutable local Docker
image, private owner0700 input/output, raw stdout/stderr 별도0600 FIFO와 --log-driver=none/
network-none/read-only/cap-drop/no-new-privileges reader를 사용합니다. Raw file/tee/console/
Docker log/remote 파일 copy/도구 설치 없음입니다. 전체 지정 chain 응답 및 arbitrary stderr가
memory를 통과하는 범위를 숨기지 않습니다. Receipt에는 safe counts/booleans/status 및
exact child4/contextTag만 남깁니다. 임의 stderr는unknown이며 첫 nonce-bound512byte marker만
control로 허용합니다. Capability/각 read는 새 nonce와 child/proof를 사용합니다.

DN11A `read_channels()`의 합계256KiB 미만, marker reserve1024bytes, monotonic12초,
partial/invalid/nonzero/deadline 거절을 재사용합니다. Remote TERM6/KILL1·host10/KILL2,
reader-ready startup20초·Docker client TERM35/KILL2, core0, FIFO 삭제를 유지합니다.
정상/실패 모두 가능한 marker의 exact4 값만 기존 check helper로 확인합니다. Proof0 및
childGone/groupGone, fresh before/after/anchor 일치 뒤만 해당 결과를 채택합니다.
Serving process/다른 PID/group을 읽거나 signal하지 않습니다. 실제 KILL fallback은 미검증입니다.

## PM 실행 순서와 evidence

1. Reviewer 승인 후 API70 UID/spec/Ready1/updated1, runner0/Pod0, P1/runner policy/CCNP
   UID/RV/spec 및 DN10/검증 DN11A same-agent UID/spec/imageID/restart/Ready/running/version
   연결을 fresh 대조합니다. 바뀌면 시작하지 않습니다. Config/help를 반복하지 않습니다.
2. 각 호출 input은 fresh `before.json`, DN10 `diag.agent.after.json`과 동일한 `anchor.json`,
   **검증된 DN11A `read.final.json`**에서 가져온 `classification.json`입니다. Capability 이후
   read input에만 검증된 DN11B `capability.json`을 추가합니다. 그 밖 파일은 mount하지 않습니다.
   Classification four bool은 정확한 type/value를 검사하며 current before와 anchor의 tag를
   대조합니다. DN11A final에는 tag가 없으므로 그것의 실제 proof/context 연결은 PM·Reviewer의
   이미 검증된 지정 evidence 인계에 근거합니다. File 이름만으로 현재 config를 추정하지 않습니다.
3. DN11A와 같은 DN11_DOCKER/KUBECTL/IMAGE/SOURCE_DIR/INPUT_DIR/OUTPUT_DIR/AGENT 환경과
   `DN11_MODE=capability`로 **bash capture_access_tproxy.sh**를 한 번 실행합니다. Before/name/
   anchor/classification 검증 뒤만 reader-ready를 발행합니다. Raw 내용을 보관하지 않습니다.
4. 새 child4 exact check proof/exit0와 fresh after를 얻습니다. Offline Docker에서 다음
   finalizer를 실행해 capability 결과를 private 보관합니다. Remote exec/check/각 stderr는
   기존 bounded/private/no-console 절차를 따릅니다.

```text
python -B /checks/access_tproxy_capture.py --before /inputs/before.json --anchor /inputs/anchor.json
  --classification /inputs/classification.json --receipt /result/receipt.json
  --after /metadata/after.json --proof /metadata/proof.json
  --transport-exit-code <preserved exit> --proof-exit-code <preserved proof exit>
```

5. Capability가 같은 context에서 supported일 때만 별도 fresh input/output/nonce로
   `DN11_MODE=dns-rules`, 그 단계 성공 뒤 `DN11_MODE=proxy-route`를 각 한 번 실행합니다.
   Read의 reader-ready도 capability/context 일치를 요구합니다. 각 단계에 새 proof 및 fresh
   after finalizer를 적용하고 `--capability <검증된 DN11B capability JSON>`을 추가합니다.
   Absence/mismatch는 지원된 metadata 결과이므로 다음 한정 lookup까지 허용하며 어떤 단계의
   unknown/transport/schema/proof/context 실패면 후속 실행을 중단합니다.
6. 두 accepted final이 모두 있어야 다음 offline Docker 조합으로 전체 정적 판정을 얻습니다.
   각 결과의 contextTag 및 지원된 safe schema를 다시 검사합니다. Partial result만으로 전체
   정합성을 채택하지 않습니다. 한 단계 실패면 전체unknown으로 인계합니다.

```text
python -B /checks/access_tproxy_capture.py --before /metadata/fresh-agent.json --anchor /inputs/anchor.json
  --classification /inputs/classification.json --capability /metadata/capability.json
  --rules-final /metadata/rules.final.json --route-final /metadata/route.final.json
```

7. Source SHA/고정 image/단계 시각/exit/safe receipt/final/exact proof/private metadata와 FIFO
   제거를 보존합니다. API/runner0/Pod0/정책/CCNP/agent 후검증을 인계합니다. 새 Job이 없으며
   원래 source/PVC/state/window/재공급/새 창/API enable/실제 인증/turn/최종 PR/public posting hold를
   유지합니다. Backend는 actual capability/iptables/ip/cluster command를 실행하지 않았습니다.
