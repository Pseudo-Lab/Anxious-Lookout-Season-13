# 진단 전용 DNS L4→L7 비교 — 완성 source/운영 절차 재검토 대상

b57ad8d의 제한 운영 승인을 받은 첫 실행은 L4 실현 후 host transport 권한 오류로 중단·정리됐으며
review도 이를 독립 검증했습니다. **새 실행 승인은 별도**입니다. Production boundary/P1/CCNP/Deployment를 바꾸지 않습니다. 기존 실제 control의 네
reply와 원래 app의 네 timeout은 출발점이며, 이 패키지는 default-deny와 DNS L7을 분리합니다.
API P1은 두 요소를 함께 바꾼 사례입니다. DNS L7 제거를 production 처방으로 확대하지 않습니다.

L4 DNS 판정은 없고 기존 claim은 소비됐으며 L7는 미진행입니다. 다음 호출 권한과 명시적 fresh run
범위는 [운영 UID 보완](ACCESS-RUNNER-DNS-CONTRAST-OPERATOR.md)을 재검토한 뒤 적용합니다.

## 고정 산출물

- access_dns_contrast_prepare.py pair: fresh production 전체 spec을 source와 대조합니다. L7 진단
  CNP는 이름 access-runner-dns-contrast-v1/namespace codex-trial, selector app=access-runner-dns-control입니다.
  Production 대비 spec 차이는 app 한 값입니다. L4는 이 L7 spec의 `/spec/egress/1/toPorts/0/rules`만 없습니다.
  default-deny true/true, callback8080, CoreDNS endpoints/TCP·UDP53 및 chatgpt.com toFQDNs443는 같습니다.
- transition: 같은 진단 CNP UID/RV/full L4 spec의 JSON Patch test 후 위 rules 한 경로를 add합니다.
  네 정상 L4 reply, transport exit0, 해당 L4 Job/Pod/nonce/CNP context 및 cleanup receipt가 필요합니다.
  L4 Job 부재/owner·control·runner Pod0 전에는 L7 전환하지 않습니다.
- access_dns_contrast_exec.py holder: 이름 access-runner-dns-l4-v1 또는 access-runner-dns-l7-v1,
  Runner2948/Never/단일 diagnose/UID10001/RO/no volume·envFrom·Secret·token/backoff0/Job50입니다.
  같은 retained node/DNS/다른 labels를 유지하고 network-free PID1 holder를 process 시작 기준40초로 제한합니다.
- **PM 명시적 metadata env 예외**: DNS_CONTRAST_POD_UID=metadata.uid 및
  DNS_CONTRAST_POD_NAME=metadata.name, apiVersion v1 fieldRef 두 항목만 허용합니다. 양 단계 동일하며
  credentials/임의 value/원본 프로세스 env를 읽지 않습니다. 기존 no-env 설명의 유일한 예외입니다.
- Service raw 목록 gate는 실제 phase Job 이름의 두 자동 name labels까지 반영합니다. UID selector는
  여전히 거부합니다. `services_exclude_control(services, labels, JOBS[phase])`로 defaulted/actual labels도 재대조합니다.

기존 direct render가 보관 `/tmp/issue7-runner-direct-20261011/render.json`과 전체 동일해야 합니다.
Holder 차이는 Job 이름/app, deadline30→50, command→동일 holder source+단계별 고정 nonce 및 metadata
env 두 항목입니다. L4/L7 holder 사이 차이는 Job 이름/nonce와 lifecycle UID/자동 labels뿐입니다.
기존 DNS helper 및 runner_image_dns 함수 정의는 bytes 그대로 재사용하며 stock Native/app factory는 시작하지 않습니다.

## 실현 gate — Ready/revision만으로 허용하지 않음

보관 native witness는 `/tmp/issue7-dns-pin-20261011/diag.response.json`입니다. 이는 과거 production
정책의 직렬화/규칙 reference이며 새 endpoint 실현 증거가 아닙니다. current production spec/UID 및
CCNP UID/RV/spec과 연결한 뒤 fresh selected endpoint와 비교합니다.

고정된 최소 조회는 다음과 같습니다. 모두 기존 trusted absolute kubectl/동일 target, core0,
TERM10+KILL2/출력256KiB/private 원문 보존을 적용합니다. native monitor/list-all/packet capture는 없습니다.

```text
kubectl -n codex-trial get pod <exact-owned-Pod-name> -o json
kubectl -n codex-trial get ciliumendpoint <exact-owned-Pod-name> -o json
kubectl -n kube-system get pod <known-same-node-Cilium-agent> -o json
kubectl -n kube-system exec <same-agent> -c cilium-agent -- cilium-dbg endpoint get <fresh-numeric-CEP-id> -o json
kubectl -n codex-trial get ciliumnetworkpolicy access-runner-dns-contrast-v1 -o json
```

생산 CNP/CCNP/P1/known DNS/Service의 fresh GET은 기존 baseline 절차에 포함합니다. Native argv의 endpoint
ID는 fresh CEP local id이며 identity ID/pod-name으로 대체하지 않습니다. 기존 확인된 CLI capability를
유지하고 missing/unsupported 형식에 명령을 바꾸거나 시간을 늘리지 않습니다.

access_dns_contrast_scope.py는 fresh Pod/CEP/agent 및 attachment/IP/local endpoint를 pin/verify하고
desired(spec)/realized **전체 L4 rows**를 reference와 대조합니다. CNP 이름/UID를 새 diagnostic으로
치환한 기대값만 허용하고 production/global UID·clone full spec을 연결합니다. ingress 한 row,
egress 다섯 rows(known CCNP deny ANY0, callback8080, DNS TCP53/UDP53, provider443), 실제 derived-from
labels 및 serialized selectors를 검사합니다. 추가 정책/merged refs/광범위 L4 우회/다른 DNS names는
거부합니다. 서로 같은 양의 desired/realized revision과 policy-enabled=both도 필요합니다.

L4: DNS selector 값 null, DNS proxy statistics 없음. L7: 같은 selector의 정확한 두 DNS matchNames/
listenerPriority121, TCP/UDP 두 realized rows, 동일 positive allocated DNS proxy port 및 proxy revision ACK.
단순 Ready/revision/stats port만으로 L7를 허용하지 않습니다. 설치 형식이 이 witness와 다르거나 새로운
정책을 식별할 수 없으면 **무질의 중단**합니다. 이 제한된 metadata gate는 실제 packet/프록시 통과 증명이 아닙니다.

## remote 소유권/단발 실행/cutoff

Holder receipt는 자신의 Downward UID/name, nonce, PID1 startTicks/startNs/CMD hash를 private JSON으로
출력합니다. Own diagnostic PID1의 `/proc/1/stat`, `/proc/1/cmdline`만 읽고 다른 프로세스는 읽지 않습니다.
exec emitter는 exact Job owner/Pod UID·Running/restart0/container ID·Runner repoDigest/command/env/
security/node/DNS 및 실현 scope 연결을 검사합니다. 재실행 때도 같은 fresh Pod/container여야 합니다.

Remote는 **실제 자기 env UID/name**, PID1 startTicks 및 전체 고정 holder command/nonce/hash를
송신 직전에 다시 비교합니다. PM의 시간 계산만 믿지 않습니다. CLOCK_BOOTTIME 기준 PID1 시작 후
**28초 미만**에 첫 network operation/DNS send를 시작해야 하며, DNS 본체의 timer는 최대10초이고
모든 subsequent connect/send 및 종료를 **38초 미만**으로 제한합니다. Holder40/Job50 안에2초 여유를
남깁니다. UDP connect만으로 첫 DNS send cutoff를 소비하지 않습니다. 남은 상한 부족/재시작/교체/
nonce 오류/metadata 누락은 query 없이 거부하며 미지원 또는 중간 deadline은 unknown입니다.
Socket operation timeout2초/두 이름 UDP·TCP 각 한 helper/각 단계 총4회/libc0은 동일합니다.

실행 중 긴 OS deschedule 등으로 검사와 syscall 사이 시간이 흐를 가능성까지 원자적으로 보장하는 것은
아닙니다. post deadline/transport 실패는 unknown이며 이미 송신한 요청을 취소했다고 주장하지 않습니다.
상한을 자동 확대하거나 ACK 실패를 no-query로 해석하지 않습니다.

Remote는 UID10001 소유 **해당 diagnostic의 runtime /dev/shm**에 nonce별 O_EXCL/NOFOLLOW/0600
단발 marker를 만듭니다. 이 자체 metadata marker만 쓰며 mounted volume/PVC/원본 state/IPC listener는
없습니다. Transport/ACK 실패 뒤에도 소비한 marker는 제거하지 않아 재실행이 거부됩니다. /dev/shm이
없거나 writable이 아니면 무질의 중단하고 다른 저장소로 대체하지 않습니다.

`run_dns_contrast_exec.sh`는 PM-only wrapper입니다. 다음의 program은 Docker emitter `program` 산출물이며
host에서 실행하거나 shell eval하지 않습니다. private0700/소유UID/파일0600/source hash를 확인합니다.
Wrapper 전체의 operator UID와 kubectl 관리자 권한, input/output 소유 UID가 같아야 합니다. 기존 target이
root-owned k3s 설정을 사용하는 경우 **wrapper 전체를 sudo로 호출**하고 root-owned private inputs/output을
사용합니다. 다른 kubectl 명령에서 sudo를 썼다는 사실이 wrapper 내부에 권한을 전달하지 않습니다.

```text
bash run_dns_contrast_exec.sh <trusted-absolute-kubectl>
  <exact-owned-phase-Pod-name> <private-emitted-program.py> <private-new-phase-output-prefix>
```

argv는 codex-trial/exact Pod/container diagnose/python -B -c로 고정합니다. Host 단발 claim과
stdout/stderr/exit 파일을 exclusive 생성하고 TERM12+KILL1/각 출력<=128KiB로 제한합니다. 기존 prefix/
symlink/실패 claim을 재사용하지 않습니다. Remote marker와 함께 단계별 exec 최대1회를 강제합니다.
exit!=0/ACK 누락/partial JSON/timeouts는 unknown이며 **한 번 더 exec하지 않습니다**. 성공 출력에도
phase/Job/Pod/nonce/CNP UID·RV/revision/startTicks context가 붙어 다른 실행 결과를 L4 허가로 재사용할 수 없습니다.

## PM 단계/실패 정리

1. 완성 패키지 review 뒤 fresh API70 Ready1/runner0/mapping disabled, 모든 baseline 및 원본 비접근을
   확인합니다. CNP/name/두 Job/control app Pod/controller 잔여물0 gate; 있으면 자동 삭제/재사용하지 않습니다.
   private source/reference hashes 및 raw Service 완결성·known DNS provenance/immutable Runner OCI·CRI를 보존합니다.
2. Docker pair와 두 holder(각 nonce를 한 번 생성)를 emit합니다. source 대비 selector/rules 차이와 holder
   차이를 기계적으로 저장합니다. server dry-run 및 defaulted labels Service gate를 확인합니다.
3. L4 CNP 한 번 생성해 반환 UID를 owned record에 저장합니다. L4 Job 한 번 생성/UID 저장 후 단일 Pod
   선택, actual labels+fresh raw Service gate/holder receipt/CEP/native rows를 수집합니다. 소유권·실현 gate를
   Docker에서 통과한 경우에만 program emit/단발 wrapper exec.28초 cutoff를 넘으면 단계 중단합니다.
4. 모든 L4 outcome에서 exact Job UID Foreground 정리와 Job 부재/owner·control·runner 각각 Pod0를
   확인합니다. 네 정상 reply/rcode0/answers true+transport0 및 같은 실행 context가 아니면 L7 없이 CNP 정리합니다.
5. 정리가 증명된 L4만 transition emitter로 current CNP UID/RV/full spec test patch를 생성하고 dry-run/patch
   한 번 수행합니다. 새/동시 CNP는 만들지 않습니다. L7 Job은 독립 fresh UID/Pod로 같은 gate/단발 exec를
   적용하며 policy realization 및 native allocated port를 단계별 보존합니다. 최대 두 Pod/8 attempts입니다.
6. 어느 단계라도 create/exec/patch/metadata drift/중간 종료 실패 시 추가 query 없이 소유 Job부터 정리하고
   control Pod0 뒤 소유 CNP를 정리합니다. deletion emitter는 고정 진단 names 및 owned UID만 허용하는
   Foreground DeleteOptions를 만듭니다. UID mismatch는 새 객체를 지우지 않고 미완료로 인계합니다.
   생성 응답을 잃어 UID를 소유 record로 확정하지 못하면 추정 삭제하지 않습니다. 기존 정책/Deployment 수정으로
   복구하지 않습니다. 정확한 UID 확인/정리가 불가능한 실패는 **미완료**이며 성공 cleanup을 주장하지 않습니다.
7. Job/CNP 부재 및 각 owner/control/runner Pod0, API Ready1/runner0/mapping disabled와 production
   policy/P1/CCNP UID/RV/spec/Service/known DNS 후검증을 보존하고 실제 결과를 독립 review에 인계합니다.

Cleanup receipt는 `{job:{kind,namespace,name,uid},podUid,nonce,jobAbsent:true,ownerPodCount:0,
controlPodCount:0,runnerPodCount:0}`이며 저장 exact deletion/Job GET/세 Pod lists에서 작성합니다.
counts 자체가 live 증명은 아니므로 원문/명령/exit/시점/UID 연결을 함께 보존합니다. Pair/transition/deletion/
holder/scope/program 모든 language 실행은 Docker이며 CLI는 --help의 고정 입력을 사용합니다.

## 해석/잔여 효과

L4 실패는 L7 없이도 재현된 증거이므로 L7를 진행하지 않습니다. L4 정상→L7 실패는 DNS L7 적용/
redirect 경로를 우선 조사할 근거이며 특정 proxy/TPROXY 결함의 확정은 아닙니다. 둘 다 정상이어도
생산 app의 다른 selection/identity/시간 차이가 남습니다. REFUSED/SERVFAIL/partial/unsupported는
정상 해석/인증으로 간주하지 않습니다. reply/answersPresent는 주소 검증이나 Native/웹 turn 성공이 아닙니다.

DNS 응답은 agent DNS/FQDN cache에 학습될 수 있습니다. Pod/CNP 삭제가 학습 상태를 즉시 원복하지
않으며 cache flush를 추가하지 않습니다. 서비스 모든 요청 무영향/원본 storage 불변을 추정하지 않습니다.
DN12 PAUSED와 원본 auth/state/PVC/window 비접근, 재공급/새 창/API enable/Native·auth·turn/최종 PR/
public posting hold는 계속 유지합니다.
