# #7 DN7 제안: fresh diagnostic에 결합된 DNS proxy Request/Response 단발 관측

DN6B actual/독립 검증은 probe/observe 종료 proof, pin, cleanup 통과 및 matching drop0을
확인했습니다. Backend는 보존 observe stderr에서 native startup/수신 loop interrupt 종료
메시지 존재와 socket failure 메시지 부재를 bool로 대조했습니다. 이는 CLI loop의 제한된
실행 근거이며 drop JSON/filter 실증이나 DNS 통과 증명은 아닙니다.6초 밖/다른 proxy 경로의
차단은 배제되지 않습니다. 같은 drop 구독/Job/window를 자동 반복하지 않습니다.

## 별도 review 요청 범위와 근거

DN7은 새로운 **DNS proxy metadata** 관측 방식이며 DN6B의 drop-only 승인을 재사용해
실행하지 않습니다. 기존 같은6초/한 fresh credential-free Job을 유지하며 type=l7에서
해당 endpoint에 결합된 DNS 두 exact 이름의 Request/Response만 처리합니다. 프로그램/
정책/hostname/port를 늘리지 않고 Hubble/새 server/관측 기능을 활성화하지 않습니다.

설치 DN6A monitor help의 l7/json/related-to 옵션을 근거로 하고,
[v1.20.2 logrecord 구현](https://github.com/cilium/cilium/blob/v1.20.2/pkg/monitor/logrecord.go)은
DNS JSON과 source/destination **local endpoint ID**를 제공합니다. Policy-verdict의 remote
security identity alias 문제와 구분하며 policy-verdict/drop/trace/capture/all을 함께 요청하지 않습니다.
[DNS record 정의](https://github.com/cilium/cilium/blob/v1.20.2/pkg/proxy/accesslog/record.go)의
Query/QTypes/RCode/ObservationSource를 검사합니다. 실제 설치 DNS JSON schema 성공은 미검증입니다.

**중요 한계:** CLI의 LogRecordNotifyVerbose JSON은 endpoint labels/identities와 DNS 항목을
보내지만 IP·port·transport·kernel timestamp·DNS transaction ID를 포함하지 않습니다.
따라서 이 방식은 DNS packet의 exact IP/53 tuple 검증을 주장하지 않습니다. Fresh Pod/CEP/
agent/local ID/CNI/IP pin과 별도로 security identity를 전후 대조해 **client endpoint context**를
묶습니다. Physical proxy↔CoreDNS 왕복/특정 DNS request-response pair 또는 client 수신은
이 JSON만으로 증명할 수 없습니다. Client-side node stream 필터라는 기존 한계도 유지합니다.

## 구현

기존 remote helper에 `observe-dns` mode만 추가했습니다. 정확한8 argv는 다음입니다:

```text
cilium-dbg monitor --type l7 --related-to <validated fresh local ID> --json --numeric
```

기존 drop/probe/check 경로의 동작과 lifetime은 유지합니다. SIGINT6초/KILL1초와 host10초/
KILL2초, owned child4값 proof와 fresh pin, total256KiB 미만/line16KiB/256records/12초 reader
상한을 재사용합니다. Remote timeout 제품/실제 KILL fallback 성공은 추정하지 않습니다.

Sanitizer의 DNS mode는 type=logRecord/l7Proto=dns/Egress/Request·Response/known verdict,
local src/dst ID 중 정확히 하나의 fresh target, 그 쪽 security identity와 namespace/app labels를
검사합니다. Security identity는 endpoint-get JSON에서 읽으며 local ID로 쓰지 않습니다.
DNS.ObservationSource=proxy, Query는 기존 두 exact 이름 또는 단일 trailing-dot 형태,
QTypes는 A/AAAA만 받습니다. RCode는 source serializer의 integer0 생략 규칙을 반영하며
response의 숫자/answer-present bool만 남깁니다. HTTP/generic L7/다른 query/standalone proxy/
미지원 schema/foreign/혼합/lost/limit이면 prior events까지 폐기합니다.

Source CLI JSON에는 DNS answers/CNAMEs와 endpoint labels가 있을 수 있으므로 stdout는
파일/tee/console/Docker log로 남기지 않고 bounded Docker reader에만 연결합니다. 선택된
이벤트에서도 query 원문/answer IP/CNAME/labels/원문 JSON은 보존하지 않습니다. Receipt에는
target alias(provider/callback), Request·Response, verdict, QTypes, response RCode·hasAnswers,
**수신시각**만 남습니다. Child proof/tag 및 private security identity는 fresh 결합용 metadata입니다.
Response에는 qTypes가 없거나 origin이 미지원이면 추정으로 채우지 않고 unknown입니다.

## PM 실행 순서: 별도 승인 후만

1. 기존 DN6B와 같은 API70/runner0/Pod0/P1/runner policy/CCNP/agent UID/container/image/
   containerStatuses baseline을 확인합니다. 새 도구 SHA와 Docker/image/manifest를 Job 전에
   준비하고 remote `probe`/정확한 check proof/transport0/finalizer 성공을 선행합니다.
   Probe는 무해한 child이며 event 구독을 하지 않습니다.
2. 기존 dns-observed renderer/program/API597a를 byte 대조한 한 Job만 server dry-run/create.
   8초 내 단일 controller owner Pod 선택과 fresh pin, 단일 endpoint-get JSON을 준비합니다.
   API mapping disabled/noPVC·Secret·env·SA token/same node/UID10001/60초/backoff0를 유지합니다.
3. Observe 전에 endpoint-get JSON의 local ID/CNI/IP와 pin을 대조하고 status.identity.id가
   유효한 security identity인지 확인합니다. Reader --endpoint 입력은 이 private JSON입니다.
   입력 schema가 맞지 않으면 client를 시작하지 않습니다. Buffered DNS JSON을 기다리지 않습니다.
4. Job startTime15초 내 `observe-dns` remote helper를 **한 번** 시작하고 raw stdout를
   `access_drop_capture.py --mode observe-dns --pin ... --endpoint ... --receipt <새 private>`
   Docker stdin으로만 연결합니다. 기존 pipefail/PIPESTATUS, bounded private stderr, log-driver=none을
   유지합니다. Start/end/Job 경과 및 kernel timestamp와 구분된 수신 시각을 보존합니다.
5. 정확한 observe child4값 check proof 후 fresh Pod/CEP/agent GET과 기존 finalizer를 실행해
   Job 경과40초 내 완료합니다. New mode의 finalizer는 after CEP security identity도 일치해야
   합니다. Proof 재사용/transport nonzero/Pod 완료·교체/UID·CNI·IP·identity drift면 unknown입니다.
6. DNS 완료 JSON/log 및 exact UID Foreground cleanup/소유 Pod0·runner Pod0/전후 baseline을
   보존합니다. 실패 시 구독/Job/type/관측창을 반복·확대하지 않습니다. 기존 libc relative query가
   search suffix를 만들면 그것은 exact 이름 scope 밖이므로 해당 receipt를unknown으로 폐기하며
   suffix를 whitelist에 추가하지 않습니다.

## 결과 해석

- Request event는 해당 client context의 DNS proxy 처리 근거입니다. Forwarded verdict를
  CoreDNS packet 도달 증명으로 바꾸지 않습니다.
- Response의rcode/hasAnswers/verdict는 proxy의 응답 처리 근거입니다. Transaction ID가 없으므로
  request-response pair를 만들어내거나 원래 DNS client가 수신했다고 주장하지 않습니다.
- Denied와 Error를 구분해 그 제한된 proxy 경로의 실패 근거를 인계합니다. Named CCNP attribution은
  별도이며 전역 deny를 추정으로 완화하지 않습니다.
- Empty/시간 밖/불완전 결과는unknown입니다. Event 부재가 proxy 미도달/차단 부재의 증명이 아닙니다.

Public stdout는 dnsRequests/dnsResponses/dnsProxyDenied/dnsProxyErrors counts와
dnsCause/noEventConclusion=unknown만 출력합니다. 후속 실제 증거가 생긴 뒤만 최소 수정안을
따로 검토합니다. 원래 runner/state/source/PVC/window/정책/global proxy 및 재공급/새창/
Native/API enable/turn·실제 인증/최종 PR hold는 유지합니다.
