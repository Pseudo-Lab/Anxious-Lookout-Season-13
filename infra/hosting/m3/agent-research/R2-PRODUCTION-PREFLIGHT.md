# #97 r2 운영 HTTPS 경로 확인·배포 전제 (검토용)

이 문서는 실행 절차 초안이다. 운영에서 실행하지 않았으며 OCI runner→공개 HTTPS hairpin은 미검증이다. [D6](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/97#issuecomment-6919336768), [#62 D1](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/62#issuecomment-6860968588), [PM 보완](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/97#issuecomment-6920101832)에 따른다. 사용자 승인 뒤 PM이 실행 범위·실행자를 지정한다. 이 문서는 운영 실행·진단 Pod 생성·배포 승인이 아니다.

## 1. 승인할 작업을 구분한다

| 단계 | 작업 | 운영 변경/권한 |
| --- | --- | --- |
| A | 선택한 운영 context에서 API GET inventory | 지속 객체 변경 없음. Secret/kubeconfig/전체 env를 출력하지 않음 |
| B | 기존 idle runner Pod의 승인된 exec로 HTTPS health GET, 기존 Cilium 관측 | CONNECT/exec 권한과 VAP 제약 적용. 파일·설정 변경, native/model 실행 없음 |
| C | 기존 idle Pod가 없을 때 진단 Pod 후보 | **Pod 생성/삭제는 운영 변경**. 별도 manifest 검토와 사용자 승인이 필요하며 A/B 승인을 재사용하지 않음 |
| D | r2 image/config 활성화 | 별도 배포 승인. 직전 D1 재검사·008 호환 recovery gate 필수 |

운영 노드의 firewall/route/DNS/OCI 보안 규칙, Cilium 값·정책, TLS/Traefik, RBAC/VAP를 변경하지 않는다. Hubble을 켜거나 Cilium을 재시작하지 않는다. 정책이 달라 검사가 막히면 중단·PM 보고한다.

## 2. A — 읽기 inventory

운영자가 확인한 정확한 context를 명시한다. 아래 명령은 실행 예시이며 context/Pod 이름을 추측하거나 개발 context를 대입해 운영 성공이라고 하지 않는다. 결과는 로컬0600 증적 파일로 저장하고 필요한 비밀 없는 필드만 보고한다. 전체 Deployment/Pod JSON에는 민감한 env/인자가 있을 수 있어 그대로 게시하지 않는다. `kubectl auth whoami`를 쓰는 경우 GET이 아닌 SelfSubjectReview 요청임을 별도 기록한다. kubeconfig의 인증서·토큰을 출력하지 않는다.

```sh
kubectl --context "$R2_PROD_CONTEXT" --request-timeout=10s get namespace codex-runner -o json
kubectl --context "$R2_PROD_CONTEXT" --request-timeout=10s -n codex-runner get ciliumnetworkpolicies -o json
kubectl --context "$R2_PROD_CONTEXT" --request-timeout=10s get ciliumclusterwidenetworkpolicies -o json
kubectl --context "$R2_PROD_CONTEXT" --request-timeout=10s -n codex-runner get networkpolicies -o json
kubectl --context "$R2_PROD_CONTEXT" --request-timeout=10s get validatingadmissionpolicy runner-subresources -o json
kubectl --context "$R2_PROD_CONTEXT" --request-timeout=10s get validatingadmissionpolicybinding runner-subresources -o json
kubectl --context "$R2_PROD_CONTEXT" --request-timeout=10s -n m2-hosting get ingressroute hosting-root-https-v1 -o json
```

추가로 다음을 **필드 선택 GET**으로 수집한다: runner Pod 이름/UID/node/phase/deletionTimestamp/labels/container image, 해당 namespace labels, Cilium endpoint identity와 적용 policy revision, 실제 공개 origin과 APP_BASE_PATH, Traefik websecure Service의443 및 route/backend, API 공개8080과 내부8081 분리·transition flag의 비밀 없는 값. API env는 이미 알려진 origin/base/flag 이름만 선택하고 envFrom/설정 공급 위치의 차이도 확인한다. TLS 개인키/Secret 데이터는 읽지 않고 인증서 공개 부분은 B의 TLS handshake에서 확인한다. 현재 Pod에 진행 중인 사용자 턴이 없는지는 승인된 운영 상태 조회로 확인하고 불명확하면 exec하지 않는다.

### D1 필수 비교

실제 `codex-runner/runner-callback-draft.spec`를 [최종 manifest](../user-runner-e2e/40-runner-network.json)의 spec과 구조적으로 비교한다. metadata/resourceVersion 차이는 제외하지만 허용/거부/selector 내용은 제외하지 않는다. 다음 조건이 모두 필요하다.

- endpointSelector와 실제 source Pod/namespace labels가 일치한다. 최종형 egress는 broker8080, kube-dns TCP/UDP53, hosting-api8081, world TCP443뿐이다.
- 넓은 **egress allow `toEntities: all`**, 직접 API8080 전환형, host/remote-node/추가 포트 allow가 없어야 한다. **egressDeny all TCP6443**은 정상 최종형 규칙이므로 broad allow와 혼동하지 않는다.
- runner/trial·kube-apiserver/6443 deny 및 ingress 제한이 유지된다. KNP runner-default-deny도 확인한다.
- 같은 Pod에 적용되는 다른 CNP/CCNP/KNP와 namespace/Pod selector를 함께 평가한다. named CNP 하나의 일치만으로 additive allow가 없다고 하지 않는다. 권한 부족·알 수 없는 적용 정책이면 통과하지 않는다.
- 실제 Cilium endpoint가 그 정책 revision을 반영했는지 확인한다. manifest 존재만으로 datapath 적용을 주장하지 않는다.

차이가 있으면 **r2 network 활성화를 중단**한다. #62 전환을 이 절차에서 자동 실행하거나 transition manifest/render 전체를 적용하지 않는다. [기존 운영 지침](../user-runner-e2e/RUNNER-NETWORK.md#production)과 [parity](../../../k3s/PARITY.md)의 과거 기록은 현재 운영 실측을 대체하지 않는다.

## 3. VAP와 실행 자격

[runner-subresources 최종형](../user-runner-e2e/50-runner-subresources-vap.json)은 codex-runner의 exec/attach/portforward/proxy CONNECT와 ephemeralcontainers UPDATE를 `system:masters` 그룹에만 허용하며 failurePolicy=Fail, binding validationActions=Deny이다. 현재 policy/binding/namespace selector가 이와 같은지 확인한다.

일반 운영 계정의 exec가 거부되면 정상 경계다. 기존 관리 자격으로 수행하는 예외도 사용자 승인된 작업·실행자 범위 안에서만 사용한다. impersonation, 새 RBAC, policy 삭제/완화, ephemeral container/port-forward 우회는 하지 않는다. Pod 생성이 이 VAP에 잡히지 않는다는 사실은 생성 허가가 아니다. 승인된 자격이 없거나 exec가 거부되면 해당 단계는 미검증으로 보고한다.

## 4. B — 기존 idle runner에서 무모델 HTTPS health 확인

사용자 승인 뒤 현재 Pod UID/node/idle 여부를 다시 읽는다. 재시작/교체/활성 턴이 있으면 중단한다. 새 native turn, provider 호출, broker credential 조회, 사용자 연구 token 발급은 하지 않는다.

승인 요청에 실제 public HTTPS origin, basePath, 정확한 Pod/컨테이너/UID, 명령 원문과 시간 상한을 붙인다. 명령은 **컨테이너 안의** `python -B -c`와 표준 `ssl`/`http.client.HTTPSConnection`만 사용한다. host Python이나 테스트 스택은 사용하지 않는다. probe의 동작 계약은 다음과 같다.

1. 승인된 고정 origin을 HTTPS/443, userinfo/query/fragment 없음으로 검증한다. endpoint는 고정 `<basePath>/api/health`이다. 모델/사용자 입력 URL을 받지 않는다.
2. 시스템 CA 및 hostname/SNI 검증을 켠 기본 SSLContext를 쓴다. proxy env를 읽지 않고 redirect를 따라가지 않는다. `-k`, hosts/DNS override, direct IP/8080/8081 fallback, 임시 CA 설치가 없다.
3. timeout10초, 총 실행 상한20초로 한 번만 GET한다. Authorization/Cookie/Origin 사용자 자격·요청 body는 보내지 않고 응답 header/body 원문을 기록하지 않는다.
4. TLS 검증 성공 여부·peer IP/port·공개 인증서 SHA256·HTTP status·최대1KiB 응답의 `status == "ok"` 여부만 출력한다. 이 endpoint는 API DB/schema readiness를 확인하며200과 `{status: ok}`가 성공이다. redirect/401/404/503/TLS 실패/timeout은 성공으로 바꾸지 않는다.
5. 요청 전후 source Pod UID/node와 UTC 시간을 기록한다. 종료 후 남는 파일/프로세스가 없어야 한다. HTTP health 접근 자체의 정상 access/metric 흔적은 지속 객체 변경과 구분해 보고한다.

동일 시간대 **기존** Cilium agent의 endpoint 조회와 `cilium-dbg monitor --type drop --type policy-verdict`로 해당 source endpoint→실제 peer443의 identity/verdict를 묶는다. monitor도 exec이므로 승인 범위에 포함하고 최대20초·source endpoint/시간 필터로 제한하며 다른 사용자 traffic 원문을 수집하지 않는다. 이미 제공되는 Traefik route 정보/관측을 연결하되 logging을 켜거나 설정을 바꾸지 않는다. 관측 기능/권한이 없으면 TLS 성공과 world 분류 미검증을 나눠 보고한다. timeout만으로 Cilium deny라 하지 않는다.

**통과 증거:** 실제 runner Pod UID·정책 revision·peer443·Cilium world 허용·CA/SNI 검증·API health200이 한 요청에 연결돼야 한다. host curl, 일반 외부 HTTPS, dev synthetic TLS fixture는 이 증거가 아니다. Pod exec 성공은 Pod 경로 증거이며 r2 native sandbox 내부 network/file-root 증명은 별도 dev gate다. 같은 HTTPS 도메인이라도 실제 OCI public IP hairpin인지 peer IP/route 증거로 구분하며 split DNS/internal 경로를 hairpin이라고 부르지 않는다.

## 5. idle Pod가 없는 경우

runner를 깨우거나 사용자 admission/native turn을 만들지 않는다. A만 보고하고 B는 **source Pod 없음으로 미검증** 처리한다.

필요하면 별도 진단 Pod manifest를 검토용으로 제출한다. 이는 읽기 전용 작업이 아니다. 임의 managed runner/owner/state label, runner Service selector와의 충돌, state/control PVC, Secret/projected token, host network/PID/IPC, privilege/capability를 사용하지 않고 SA token automount=false·read-only root·non-root·capdrop으로 제한한다. 승인된 고정 이미지/짧은 수명·정확한 생성/삭제 UID·잔류0 확인을 제출한다. namespace와 labels에 적용되는 모든 정책의 실질 동등성을 확인해야 하며 다른 namespace/관리망 Pod 결과를 runner 증거로 대체하지 않는다. 이 계획과 사용자 승인이 없으면 생성하지 않는다.

## 6. D — network 활성화 직전 다시 실행할 필수 gate

1. 별도 승인된 r2 release의 source SHA/image manifest digest/설정 diff와 review 판정을 확인한다. 이미지 config ID를 manifest digest라 부르지 않는다.
2. **활성화 직전** A의 D1 spec/추가 적용 정책/endpoint revision/VAP를 fresh GET으로 다시 확인하고 UTC·UID/resourceVersion을 기록한다. 이전 확인 뒤 변경되면 재검토하며, 불일치 시 networkAccess=true 활성화를 중단한다. 배포 동안 변경 통제도 명시한다.
3. public route ROOT-HTTPS-P1, encoded/internal 차단, callback8081 분리, 원래 정책 상한 유지와 OCI 경로 증거를 확인한다. 실패 시 정책 확대나 내부 fallback 없이 network 비활성 유지·PM 보고한다. 별도 routing 대안이 필요하면 승인받을 새 설계로 제출한다.
4. **0008_version_authors 호환 recovery API+runner**의 정확한 source/image, 복구 명령·기존 HTTPS/env 보존, 사람 read/save 회귀 증거를 확보한다. 미호환 main/옛 이미지, schema downgrade/data 삭제, MCP candidate를 검증된 r2 recovery로 쓰지 않는다. 호환 recovery가 없으면 배포하지 않는다.
5. schema008과 기존 직접 저장/author/F1/F3/F4를 유지한다. profile network/config 변경·새 token 경계는 별도 승인된 구현 diff에만 포함한다. `render.sh` 전체 적용, CNP 전환, TLS 교체를 끼워 넣지 않는다.
6. 배포 후 probe/회귀도 승인된 범위만 수행한다. 이 절차는 모델 예산을 만들지 않으며 현재 실제 모델 승인0이다.

## 7. 보고 양식

실행 승인 링크·실행자·context 확인값, UTC 구간, source Pod UID/node/endpoint, 실제 정책 UID/resourceVersion/revision 및 D1 비교 결과, TLS peer/공개 인증서 fingerprint/status, Cilium identity/verdict, 실제 route/API 연결 근거, 미검증 항목을 #97에 남긴다. secret/전체 env/raw response는 붙이지 않는다. 실패는 DNS/TCP·정책 분류/TLS/HTTP 단계로 나누고 관측 없는 원인은 추정으로 표시한다. 정책 확대 없이 network 비활성 유지 또는 별도 승인할 routing/진단 대안을 PM inbox에 보고한다.
