# #7 Runner app selector 한 변수 비교

이 패키지는 별도 control Job 준비용입니다. **완성 패키지의 독립 검토 및 PM 운영 범위 확정 전에는
생성/질의하지 않습니다.** 기존 Deployment/Service/정책 객체는 수정하지 않지만 control Pod가
named Runner boundary 선택에서 벗어나므로 effective isolation/L7 적용은 달라질 수 있습니다.
DN12는 계속 PAUSED이며 node-wide 관측을 추가하지 않습니다.

비교 기준은 review가 검증한 `/tmp/issue7-runner-direct-20261011/render.json`과 해당 실제 Runner2948
결과입니다. source73cde8e, 문서2a3af39의 네 absolute UDP/TCP helper가 모두 timeout이었습니다.
이번 단일 control은 과거 결과와 시점 차이가 있으며 동시 paired 실험이 아닙니다.

## 고정 variant와 차이 검증

`access_runner_dns_control_prepare.py`는 현재 direct renderer를 호출하고 **fresh metadata로 만든
direct render 전체가 보관 source render와 일치해야** 진행합니다. JSON key 순서는 무관하지만
값/타입은 동일해야 합니다. live Job의 UID/defaulted 필드를 baseline으로 사용하지 않습니다.
그 뒤 딱 두 경로를 변경합니다:

- `metadata.name`: `access-runner-image-dns-v1` → `access-runner-selector-dns-v1`
- `spec.template.metadata.labels.app`: `access-web-runner` → `access-runner-dns-control`

다른 labels, namespace, retained node, DNS 설정, command bytes, security, 이미지 및 Job 제한은
같습니다. source915/Runner2948/imagePullPolicy Never, UID10001/RO/no env·envFrom·mount·PVC·Secret·token,
serviceAccount trial, 단일 container, backoff0/deadline30을 유지합니다. 기존 프로그램 그대로
두 이름/UDP·TCP 네 helper attempts/socket operation timeout2초/전체10초, libcQueries0입니다.
HTTP/TLS/Native/auth/상태 초기화는 실행하지 않습니다. attempts는 packet 수가 아닙니다.

Language toolchain은 Docker에서만 실행합니다. 고정 검증 image와 private readonly 파일 mount를 사용합니다:

```text
python -B /checks/access_runner_dns_control_prepare.py
  --snapshot /metadata/snapshot.json --inputs /metadata/inputs.json --images /metadata/images.json
  --baseline /metadata/direct-render.json --services /metadata/services.json
```

`services.json`은 **fresh/unfiltered 원본 API** 목록입니다. 고정 명령은
`<trusted-absolute-kubectl> --request-timeout=8s get --raw=/api/v1/namespaces/codex-trial/services`입니다.
`-o json`, resource 인자, label/field selector, limit/continue/resourceVersion/watch query를 붙이지 않습니다.
수집 세부 기준 및 선행 read-only 비교는 [Service 목록 수집 보완](ACCESS-RUNNER-SERVICE-LIST.md)을 따릅니다.
원문을 변경하지 않습니다. kind=ServiceList/apiVersion=v1/nonempty list RV를 수집 단계에서 확인합니다.
typed ServiceList의 nested items는 타입 필드 생략을 허용하되 명시한 kind/apiVersion 충돌은 거부합니다.
generic List는 여전히 개별 kind=Service/apiVersion=v1가 필요하고 빈 list RV는 계속 거부합니다.
renderer는 유효한 namespace/name/UID/selector/list RV를 요구하고 control의 모든 labels 및 예상되는
자동 `job-name`, `batch.kubernetes.io/job-name`(둘 다 고정 control Job 이름)로 selector를 대조합니다.
이 예상 labels는 gate 내부에만 추가하며 선언된 template는 변경하지 않습니다. 어떤 기존 Service라도
control을 선택하면 중단합니다. app 조건 이외 selector도 검사합니다. 생성 전 Job UID는 알 수 없으므로
`controller-uid`, `batch.kubernetes.io/controller-uid`를 포함한 Service selector는 다른 조건과 무관하게
미지원으로 거부합니다. template에 남은 UID labels 또는 다른 Job 이름 labels도 거부합니다.
비어 있지 않은 `metadata.continue`, 0이 아닌 `remainingItemCount` 및 잘못된 pagination 타입은
거부합니다. 누락된 pagination 필드 또는 빈 continue/정수0만 허용합니다. 부분 목록을 gate에 쓰지 않습니다.
selector가 없는 Service는 Pod를 선택하지 않습니다. 이 파일의 완전성/수집 시점과 기존 Runner label의
Service membership 비교는 PM이 기록합니다. 목록의 형식만으로 freshness/누락 없음이 증명되지는 않습니다.

## PM 운영 절차 — 아직 실행 승인 아님

1. private0700 evidence directory 및 기존 bounded kubectl 방식(core0/TERM10+KILL2/출력256KiB)을
   사용합니다. API70 UID/spec/Ready1/updated1, runner replicas0/runner selector Pod0, mapping disabled,
   기존 storage metadata를 확인합니다. window1 expired/requests0는 기존 보관 증거로만 취급하며
   새 원본 state 확인을 하지 않습니다. 원본 auth/state/PVC 내용은 읽거나
   공급하지 않습니다. fresh snapshot/inputs/images 및 보관 direct-render bytes/source/provenance를 보존합니다.
2. 현재 named Runner boundary/P1/CCNP의 UID/RV/spec, known DNS Service/EndpointSlice metadata 및
   기존 runner node/DNS/template를 보관 기준과 대조합니다. known DNS Service/EndpointSlice는 과거
   수집 provenance를 명시하고 fresh GET과 연결하며 direct run이 이를 새로 수집했다고 전제하지 않습니다.
   named boundary의 app selector 및
   default-deny true/true와 namespace-based CCNP 적용 조건이 같아야 합니다. **어떤 drift나 미지원도 중단**합니다.
   다른 label-based 정책/identity 선택까지 동일하다고 주장하지 않습니다. 새 policy relaxation/namespace/
   node/image 변경을 섞지 않습니다. 기존 global CCNP는 같은 namespace에 계속 적용될 수 있습니다.
3. pagination이 완결된 unfiltered Service 목록의 실제 selector에 baseline/control 전체 labels 및 각
   Job의 예상 자동 name labels를 대조하고 control membership0을 보존합니다. UID selector가 있으면
   새 UID 값을 예측해 허용하지 않고 중단합니다. `codex-trial/access-runner-selector-dns-v1` Job 부재, control selector
   `app=access-runner-dns-control` Pod0 및 runner selector Pod0를 **각각** 확인합니다. 같은 이름/label의
   잔여물이 있으면 재사용하거나 자동 삭제하지 않고 중단합니다. 컨트롤러 등 다른 metadata에서
   동일 control 이름/label 사용이 발견돼도 중단합니다.
4. 위 renderer를 Docker로 실행합니다. baseline/control의 기계적 diff가 위 두 경로만인지 저장합니다.
   server dry-run을 보존하고 실제 spec을 비교합니다. **server-defaulted template labels로 동일 전체
   Service 목록을 재대조**하고 예상 name labels/UID selector 거부 기준을 적용합니다. selector나 labels가
   바뀌면 생성 전에 다시 수집/대조하거나 중단합니다. 자동 UID/controller labels/ownerReferences 및
   Kubernetes defaulting은 lifecycle 차이로 별도 기록하고 arbitrary template 변경을 허용하지 않습니다.
   Pod 생성 시 추가된 Job controller labels는 원래 다른 labels 보존 검사를 대체하지 않습니다.
   이 재대조는 Docker에서 `services_exclude_control(services, defaulted_job['spec']['template']['metadata']['labels'])`
   로 실행할 수 있습니다. 실제 Pod labels도 같은 함수에 전달합니다. source emit은 template에 남은 UID
   labels를 거부하지만 재대조 함수는 실제 UID labels를 입력받으며 UID-based Service selector는 계속 거부합니다.
5. review 승인 후 PM이 **Job 한 번만 생성**하고 반환된 exact Job UID를 저장합니다. 해당 UID를 owner로
   가진 단일 Pod의 UID/imageID/command/labels/node/DNS/security/無 env·volume·token을 확인합니다.
   **actual Pod labels와 fresh 전체 Service selector도 재대조**합니다. 예상하지 못한 admission labels/
   selector 변화/선택이 있으면 unknown으로 인계하고 exact cleanup하며 재시도하지 않습니다. 실제 Pod
   확인은 생성 뒤이므로 이미 시작된 진단을 예방했다는 증명이 아닙니다. 생성 전 dry-run 대조와 보수적
   UID-selector 거부를 생략하지 않습니다. admission 또는 selector 변동 가능성을 판정할 수 없으면 생성하지 않습니다.
   OCI index2948→arm64 manifest b902→config2b017 및 CRI repoDigest/config id 연결로 provenance를
   검증합니다. 새 build/import/pull은 하지 않습니다. 실패/Pod replacement/restart/다중 Pod는 unknown이며
   재시도하지 않습니다. 고정 deadline 안에서 bounded safe JSON만 수집합니다. raw errors/packet/주소를 공개하지 않습니다.
6. 생성 이후 **모든 outcome에서** 저장된 exact Job UID precondition/Foreground 기존 deletion 절차를
   사용합니다. 같은 이름의 다른 UID는 삭제하지 않습니다. selector만으로 삭제하지 않습니다.
   exact Job UID 소유 Pod0(저장 owner UID 및 controller-uid selector로 확인), Job 부재,
   **control selector Pod0**, **runner selector Pod0**를 각각 기록합니다. cleanup 실패/UID mismatch는
   미완료로 인계하며 성공으로 판정하지 않습니다. 객체를 못 생성한 경우 이전 잔여물을 지우지 않습니다.
7. API/runner UID/spec/Ready/replicas/mapping, Service selector, known DNS metadata 및 정책/P1/CCNP
   UID/RV/spec을 후검증합니다. 변화가 있으면 별도 중단/조사 대상으로 인계합니다. baseline 복구를 위해
   임의 정책/Deployment를 수정하지 않습니다. 원본 storage 불변이나 모든 요청 무영향을 추정하지 않습니다.

## 결과 해석과 종료

Control reply는 app-selected policy/Cilium identity/L7/Service 선택 차이의 영향을 의심할 근거이며
특정 CNP 단독 원인 증명은 아닙니다. REFUSED/SERVFAIL/malformed는 정상 DNS 해석이 아닙니다.
Control도 timeout이면 named boundary 단독 가설의 근거는 약해지지만 그 영향 부재가 증명되지는
않습니다. global/other policy/common DNS 경로, 시간 차이와 복합 원인은 여전히 열려 있습니다.

Backend는 source/오프라인 회귀만 담당하며 실제 Job/query/cluster metadata 수집은 PM 담당입니다.
생산 정책 변경, DN12 재개, 재공급/새 창/API enable, Native/auth/turn 및 최종 PR/public posting은
자동 진행하지 않습니다. 결과/정리 증거는 별도 독립 검증을 받습니다.
