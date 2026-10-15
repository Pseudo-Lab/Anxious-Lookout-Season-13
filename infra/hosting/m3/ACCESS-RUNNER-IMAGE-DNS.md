# #7 실제 Runner image의 단일 DNS 점검

기존 D3 실제 Job `/tmp/issue7-access-diagnosis-20261011/network-job.actual.json`은 API597a image였습니다.
두 이름 dns timeout/TCP·TLS 미진행 결과는 실제 Runner-image 직접 DNS 증거가 아닙니다.
이번은 준비된 production Runner image로 Pod 하나를 만드는 최소 비교입니다. **실제 실행은
독립 review 승인 후 PM이 담당**합니다. DN12는 중지하며 packet/trace 관측을 포함하지 않습니다.

## 이미지와 프로그램

`/tmp/issue7-access-live-20261011/images.json`, runner.audit.json의 기준은 source
91577194a5a9343778b68fcaa258d5941f92aa5a입니다. Runner immutable reference:

```text
docker.io/library/anxious-s13-access-runner-9157719@sha256:2948da59599ba7842406d3ce49fccf716f47010fb713a43721c95eeb3f08d2e7
```

Audit의 index2948→arm64 manifest b90242d8…→config2b0170aa… 및 archive83e62ea9… chain과
node CRI/repoDigest/실제 Pod imageID를 기존 provenance 방식으로 대조합니다. Spec image 문자열만으로
검증하지 않습니다. 새 image build/import/pull 또는 fixture image로 대체하지 않습니다.
로컬 Docker inspect는 arm64/revision915 및 기본 CMD `uvicorn runner.app:create_app …`를 확인했습니다.

`access_runner_dns_prepare.py`는 기존 DNS helper를 그대로 조합하고 **Runner image의 command를
python -B -c 진단 프로그램으로 override**합니다. Runner app/Native/auth/broker/state 초기화를
시작하지 않습니다. 같은 image의 Python/libc metadata와 `/etc/resolv.conf`를 확인하지만 실제
Codex native resolver/인증 동작 전체를 검증하는 것은 아닙니다.

Configured resolver 첫 주소에 `chatgpt.com`, `api.m2-hosting.svc.cluster.local`의 absolute A를
UDP/TCP 각각 한 helper attempt씩, **총4회/socket operation timeout2초/프로그램 전체10초**로 요청합니다.
TCP connect/send/각 recv의 대기가 누적될 수 있으므로 helper 한 회의 wall-clock 상한을2초로
해석하지 않습니다. 전체10초 alarm이 프로그램의 누적 대기를 제한합니다. Libc metadata는
읽지만 libc resolution/search suffix/A·AAAA expansion query는 실행하지 않습니다. Public resolver/
다른 이름/다른 transport/fallback/retry/HTTP/TLS/Native/auth는 없습니다. helperAttempts는 실제
packet 수가 아닙니다. Reply status/rcode/answer-count 존재는 주소 해석이나 인증 성공 증명이 아닙니다.
주소/packet/answer record/error text를 출력하지 않고 runtime metadata·수/count·safe status만 남깁니다.

## 한 번의 PM 실행 절차

1. 기존 API70 UID/spec/Ready1/updated1, runner replicas0/Pod0, mapping disabled, P1/runner
   policy/CCNP UID/RV/spec 및 원래 storage/window 보존을 확인합니다. 기존 window1/requests0를
   재공급하거나 읽지 않습니다. Fresh snapshot의 actual runner image/nodeSelector/DNS spec를 대조합니다.
2. Private0700 directory에 fresh snapshot/existing operator identifier inputs/검증 images.json을
   준비하고 고정 로컬 Docker image로 아래 renderer를 실행합니다. Language toolchain은 Docker만 사용합니다.

```text
python -B /checks/access_runner_dns_prepare.py --snapshot /metadata/snapshot.json
  --inputs /metadata/inputs.json --images /metadata/images.json
```

3. Render는 Job `codex-trial/access-runner-image-dns-v1`, backoff0/deadline30/단일 diagnose container,
   위 Runner digest/imagePullPolicy Never/UID10001/RO/noPVC·Secret·env·envFrom·mount·SA token, serviceAccount trial입니다.
   기존 runner의 retained nodeSelector와 dnsPolicy/dnsConfig를 그대로 가져오며 ndots 변경은 없습니다.
   현재 runner template labels(app access-web-runner 포함)를 복사해 기존 policy를 선택합니다. Service selector에 포함될 수 있어
   점검 동안 API mapping disabled/runner0를 유지합니다. Diagnostic은8080 서버를 띄우지 않습니다.
4. 기존 trusted absolute kubectl/private/core0/TERM10+KILL2/출력256KiB 방식으로 server dry-run을
   보존합니다. Actual immutable image/runtime provenance와 security/command/DNS/node spec을 대조한 뒤
   **Job 한 번 생성**합니다. 새 controller Job UID의 단일 Pod 하나만 선택합니다. Pod UID/Job owner/
   imageID/command/env·volume 없음/node/current DNS spec을 확인하고 byte/source SHA를 보존합니다.
5. 고정30초 deadline 내 완료/exit와 bounded safe JSON 로그를 수집합니다. 실패/지연/미지원은unknown이며
   새로운 Job/다른 image/node/query/timeout으로 재시도하지 않습니다. 원문 describe/log를 console에
   공개하지 않습니다. 필요 private stderr도 기존 크기·시간 한도를 적용합니다.
6. 모든 outcome에서 저장된 **exact Job UID precondition/Foreground** 기존 deletion 방법으로 정리합니다.
   UID mismatch면 같은 이름의 다른 Job을 지우지 않습니다. Job/소유 Pod0·runner selector Pod0 및
   API/runner/DNS spec/정책/CCNP 후검증을 보존합니다. Cleanup 실패는 미완료로 인계하고 성공 판정을 하지 않습니다.

## 결과 해석

- Runner image에서도 absolute UDP/TCP timeout이면 같은 현재 policy/node/DNS 조건에서 재현된 증거입니다.
  Image 차이만의 원인이나 전역 DNS 원인을 확정하지 않으며 정책 해제/image·node 변경을 동시에 하지 않습니다.
- Reply가 있으면 프로토콜·rcode·answer 존재를 분리하고 당시 API-image 결과와 시점/환경 차이를 명시합니다.
  같은 시점 paired image 비교가 아니므로 image만의 효과로 단정하지 않습니다.
- UDP/TCP 중 한 쪽만 달라지면 그 차이의 최소 경로 비교만 다음 후보로 제시합니다. REFUSED/SERVFAIL/
  malformed/무응답은 정상 DNS 해석으로 처리하지 않습니다. Source helper의 reply 검증 한계는 기존 DN1과 같습니다.
- 이 결과는 actual Runner image의 제한 DNS 점검이며 stock Codex 인증/turn/웹 연결 성공이나 최종 PR 승인이 아닙니다.

Backend는 실제 cluster Job/query를 실행하지 않습니다. 서비스·정책·원본 auth/source/PVC/state/window,
재공급/새 창/API enable/실제 인증/turn/최종 PR/public posting hold는 유지합니다.
