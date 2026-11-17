# #97 r3 운영 무모델 배포 절차 — D7 / D9

[D7](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/97#issuecomment-6934762282)은 공유 Traefik websecure의 다른 Host 접근 가능성을 수용하고, 정확한 Traefik Pod/단일 targetPort egress를 추가한다. [운영 입력 확정](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/97#issuecomment-6934879391)은 운영에서 수행한다. 운영 inventory가 없다는 이유로 소스 제출을 막지 않는다. [D9](https://github.com/Pseudo-Lab/Anxious-Lookout-Season-13/issues/97#issuecomment-6935053511)에 따라 dev HTTPS fixture B와 실제 모델 턴 gate는 취소했다. 플랫폼 Codex의 첫 실제 사용은 사용자가 운영에서 직접 한다.

이 문서는 실행 승인이 아니다. 사용자 승인 때 context·실행자·대상 UID·명령·시간 상한·복구 대상을 지정한다. GET inventory, 정책 create/delete, Pod create/delete, exec, 이미지/config 활성화는 각각 범위에 포함해야 한다. 모델/provider/실제 turn grant는 이 무모델 절차에서 사용하지 않는다. 정책·권한·노드 설정 확대가 필요하면 중단하고 사용자 결정을 받는다.

## 1. 운영 inventory와 복구 준비

정확한 운영 context와 실제 origin/basePath를 확인한다. 공개 origin이 IP literal이면 그대로 유지한다. DNS origin 전환, TLS 우회, API8080 직접 연결은 하지 않는다. 아래 변수는 실측 값으로 채운다. 개인 디렉터리0700/파일0600에 보관하고 전체 env·Secret·kubeconfig·응답 원문을 게시하지 않는다.

```sh
kubectl --context "$R3_CONTEXT" --request-timeout=10s -n codex-runner get ciliumnetworkpolicies,networkpolicies -o json
kubectl --context "$R3_CONTEXT" --request-timeout=10s get ciliumclusterwidenetworkpolicies -o json
kubectl --context "$R3_CONTEXT" --request-timeout=10s get validatingadmissionpolicy runner-subresources -o json
kubectl --context "$R3_CONTEXT" --request-timeout=10s get validatingadmissionpolicybinding runner-subresources -o json
kubectl --context "$R3_CONTEXT" --request-timeout=10s get namespaces --show-labels
kubectl --context "$R3_CONTEXT" --request-timeout=10s -n "$R3_TRAEFIK_NS" get service "$R3_TRAEFIK_SERVICE" -o json > "$R3_INPUTS/service.json"
kubectl --context "$R3_CONTEXT" --request-timeout=10s -n "$R3_TRAEFIK_NS" get pods -l "$R3_TRAEFIK_SELECTOR" -o json > "$R3_INPUTS/pods.json"
kubectl --context "$R3_CONTEXT" --request-timeout=10s -n "$R3_TRAEFIK_NS" get endpointslices -l "kubernetes.io/service-name=$R3_TRAEFIK_SERVICE" -o json > "$R3_INPUTS/slices.json"
kubectl --context "$R3_CONTEXT" --request-timeout=10s get ingress,ingressroute,middleware,ingressroutetcp,ingressrouteudp,traefikservice,tlsoption,tlsstore -A -o json
```

CRD 부재/권한 부족이면 사용 중인 provider를 식별하고 미확인으로 보고한다. Kubernetes Ingress/CRD뿐 아니라 활성 Gateway API·file/internal provider, entrypoint 기본 middleware, forwardedHeaders/proxyProtocol trust, TCP/TLS passthrough, middleware chain, Service/상위 ACL까지 운영에서 확인한다. Traefik 시작 설정에는 비밀 값이 있을 수 있으므로 필요한 필드만 추출한다.

[소스 inventory](ROUTE-INVENTORY-D7.md)의 hosting·Android·reports·trial을 시작점으로 모든 공유 리스너의 Host/path/entrypoint→middleware chain→Service→숫자 targetPort와 접근 제한을 표로 만든다. **인터넷에서는 거부되지만 새 runner 경로에서는 허용되는 private Host/IP 제한/상위 ACL이 발견되면 중단·보고한다.** 미확인 provider/보호를 통과로 처리하지 않는다. 다른 앱의 제한은 변경하지 않는다.

실제 `codex-runner/runner-callback-draft.spec`를 [D1 최종형](../user-runner-e2e/40-runner-network.json)과 구조적으로 비교한다. broker8080·DNS53·hosting-api8081·world443와 기존 ingress/runner/trial/6443 deny를 유지한다. `toEntities: all` egress allow, 직접 API8080, host/remote-node, 추가 포트는 불가하다. egressDeny all TCP6443은 정상이다. 추가 적용 CNP/CCNP/KNP와 namespace/Pod selector, endpoint policy revision까지 확인한다. named 정책 하나만 비교하지 않는다. 불일치하면 #62 전환을 이 절차에 끼워 넣지 않고 중단한다.

최종 소스 SHA·arm64 API/runner manifest digest·빌드 인자·설정 diff·기존 상태를 기록한다. DB는 최종 reviewed schema와 호환해야 한다. PAT0009 배포 후008-only fallback은 복구 후보가 아니다. [B3](DEV-VM.md#b3--recovery-certification-before-final-pr-approval)의 최종 호환 이미지 무모델 boot/owner read/human-save 인증이 선행되어야 한다. migration/backup은 별도 승인 순서로 수행하며 startup migration·downgrade·기록 삭제를 하지 않는다.

## 2. 한 규칙 렌더·검토·적용

`values.json`은 정확히 namespace, service, selector(실제 Service selector 전체), targetPort(정수)다. 443이 named `websecure`이면 실제 선택 Pod의 containerPort/Traefik args와 EndpointSlice까지 따라가 숫자를 확정한다. 443/8443을 추측하지 않는다. hostNetwork, 불일치/준비되지 않은 endpoint, 중간 갱신이면 렌더가 거부한다.

검토한 runtime 이미지의 ops 명령을 Docker에서 실행한다. 입력은 read-only, 출력만 개인 새 디렉터리에 쓴다. 전용 입력/출력은 실행자의 소유 UID/GID와0700/0600을 유지한다. 아래 실행 UID/GID는 그 소유자와 같아야 하며 runtime 기본 UID10001을 그대로 사용하거나 모드를 넓히지 않는다.

```sh
docker run --rm --user "$(id -u):$(id -g)" --network none --read-only --tmpfs /tmp \
  -v "$R3_INPUTS:/inputs:ro" -v "$R3_OUTPUT:/output" \
  --entrypoint python "$R3_RUNTIME_DIGEST" -B -m ops.research_traefik \
  --values /inputs/values.json --service-snapshot /inputs/service.json \
  --pods-snapshot /inputs/pods.json --slices-snapshot /inputs/slices.json --out /output/d7
```

출력 `egress.json`은 `codex-runner/runner-research-traefik-v1` 하나다. 기존 D1은 수정하지 않고 `managed=user-runner`에서 **실제 namespace+정확한 Pod selector, TCP targetPort 하나**만 추가한다. ingress/다른 namespace 전체/host/remote-node/API8080을 추가하지 않는다. `proof.json`에 Service UID/RV·Pod UID·입력 hash가 남는다. 적용 직전 fresh snapshot으로 다시 렌더하고 diff가 있으면 재검토한다. 기존 같은 이름의 객체가 있으면 덮어쓰지 말고 소유권부터 확인한다.

사용자 승인 범위에서 검토 출력만 create한다. `render.sh` 전체 적용, CNP 전환형, TLS 교체는 하지 않는다.

```sh
kubectl --context "$R3_CONTEXT" --request-timeout=10s create -f "$R3_OUTPUT/d7/egress.json"
```

UID/RV와 실제 endpoint 적용 revision을 기록한다. 대상 Traefik ingress도 기존 정책에서 허용되는지 확인한다. 새 ingress allow가 필요하면 중단한다. runner 빌드의 `RESEARCH_TRAEFIK_NAMESPACE`/`RESEARCH_TRAEFIK_SERVICE`는 위 Service와 일치해야 한다. root CLI는 이미지 빌드 때 고정된 `<service>.<namespace>.svc.cluster.local:443`으로만 TCP 연결하고 원래 공개 origin의 TLS 검증 이름과 HTTP Host를 유지한다. IP SAN 검증도 유지하며 fallback은 없다. selector/targetPort는 정책 렌더 입력이고 사용자/모델 입력은 아니다.

## 3. arm64 커널 격리 확인

x86 dev 통과는 arm64 인증이 아니다. 사용자 승인된 빈 측정 Pod에서 검토 runner arm64 이미지·Codex0.160.1·실제 커널로 `ops.research_dev_probe inode`를 실행한다. `ops.native_measure.measurement_pod`의 runner 동일 UID10001/resources/capdrop/NNP/seccomp/namespace와 새 emptyDir만 사용한다. env/Secret/PVC/SA token/host namespace/실제 grant는 없다. purpose-only label·짧은 activeDeadlineSeconds·명확한 UID 기반 삭제를 포함한 **정확한 manifest를 review 후 생성 승인**받는다. managed/owner/state/generation label을 임의로 붙이지 않는다. 이 Pod는 파일 격리용이며 runner egress 증거를 대신하지 않는다.

```sh
kubectl --context "$R3_CONTEXT" --request-timeout=80s -n codex-runner \
  exec "$R3_APPROVED_MEASUREMENT_POD" -c runner -- python -B -m ops.research_dev_probe inode
```

오래된 sandbox/path/FD가 교체 전 inode만 읽고 새 sandbox만 새 inode를 읽는지, parent/sibling/private control/proc/FD canary가 차단되는지 고정 boolean을 확인한다. 모든 체크와 fresh_sandbox_new_inode가 true여야 한다. UID/커널/arch/pin/seccomp/SELinux/caps/NNP를 함께 기록한다. namespace EPERM/timeout은 미통과이며 권한을 높이지 않는다. 이는 직접 sandbox 무모델 검사이고 실제 app-server/provider/대화 성공 증거가 아니다. 정확한 UID로 삭제하고 잔류 파일/Pod가 없음을 확인한다.

## 4. HTTPS와 잘못된 자격

별도 승인된 **기존 idle runner**의 UID/node/idle을 실행 전후 확인한다. VAP/binding의 system:masters CONNECT 제한을 유지한다. 거부되면 impersonation/새 RBAC/ephemeral container/port-forward로 우회하지 않는다. idle Pod가 없으면 미검증으로 보고하며 사용자 턴을 만들어 깨우지 않는다.

```sh
kubectl --context "$R3_CONTEXT" --request-timeout=25s -n codex-runner \
  exec -i "$R3_APPROVED_IDLE_POD" -c runner -- python -I - \
  --origin "$R3_ORIGIN" --base-path "$R3_BASE" \
  --traefik-namespace "$R3_TRAEFIK_NS" --traefik-service "$R3_TRAEFIK_SERVICE" \
  < backend/ops/research_https_preflight.py
```

검토 SHA/hash의 소스만 stdin으로 공급한다. platform CA·원래 origin TLS 이름/IP 검증·원래 HTTP Host, Service443 TCP pin, 한 번의 health GET,10초 socket/20초 전체 상한이다. proxy/CA/keylog env·redirect·retry·자격·응답 원문 출력은 없다. TLS/인증서 hash/peer/status200 및 ready=true를 기록한다. peer는 Service DNAT 관측 위치에 따라 달라질 수 있으므로 Service/EndpointSlice/기존 Cilium endpoint 관측과 연결한다. world/hairpin 통과를 요구하거나 주장하지 않는다.

잘못된 purpose/missing/expired/revoked/foreign/read-only 자격은 먼저 Docker/local DB의 review gate에서 확인한다. 운영에서는 승인된 무자격/고정 가짜 PAT·turn 문자열만 사용하여 private agent401 및 broker/internal/runner 경계 거부를 확인한다. 실제 내부 자격을 sandbox에 넣는 positive control은 없다. exact 요청·대상·예상 응답은 최종 PAT 계약에 맞춰 review가 실행 전에 확정한다. 이 확인을 위해 정상 PAT/turn을 발급하지 않는다. 존재하지 않는 경로404를 인증 거부 증거로 대신하지 않는다.

## 5. 읽기 진단과 롤백

실패 단계에 맞춰 기존 관측만 사용한다. 다음 GET은 원인 확인용이며 exec/monitor는 별도 승인된 대상·20초 상한·endpoint/시간 필터가 필요하다. 로그에 자격/다른 사용자 traffic가 포함될 수 있어 필요한 안전한 필드만 남긴다.

```sh
kubectl --context "$R3_CONTEXT" --request-timeout=10s -n "$R3_TRAEFIK_NS" get service "$R3_TRAEFIK_SERVICE" -o json
kubectl --context "$R3_CONTEXT" --request-timeout=10s -n "$R3_TRAEFIK_NS" get endpointslices -l "kubernetes.io/service-name=$R3_TRAEFIK_SERVICE" -o json
kubectl --context "$R3_CONTEXT" --request-timeout=10s -n codex-runner get ciliumendpoints -o json
kubectl --context "$R3_CONTEXT" --request-timeout=10s -n codex-runner get ciliumnetworkpolicies,networkpolicies -o json
kubectl --context "$R3_CONTEXT" --request-timeout=10s -n "$R3_TRAEFIK_NS" get ciliumnetworkpolicies,networkpolicies -o json
kubectl --context "$R3_CONTEXT" --request-timeout=10s -n m2-hosting get endpointslices -l kubernetes.io/service-name=api -o json
```

- DNS 오류: Service FQDN/namespace와 기존 DNS53 정책·endpoint를 비교한다. hosts/노드 DNS를 바꾸지 않는다.
- TCP timeout/refused: Service443→실제 targetPort/ready endpoint/Pod label, source egress와 대상 ingress revision을 확인한다. 기존 `cilium-dbg endpoint get <id>`/bounded `monitor --type drop --type policy-verdict`가 허용되면 source/destination identity와 verdict를 연결한다. timeout만으로 정책 deny라 단정하지 않는다.
- TLS 오류: 기존 인증서의 공개 SAN/유효기간과 원래 origin/IP identity를 확인한다. `-k`, 임시 CA, origin 변경으로 통과시키지 않는다.
- HTTP404/redirect: 실제 Host/path/entrypoint/middleware/backend를 확인한다.401은 자격 경계,503은 API readiness/schema/DB를 분리해 확인한다. 새 포트나 직접 API fallback을 열지 않는다.

통과 뒤 활성화도 별도 사용자 승인이다. 직전 D1+추가 적용 정책·revision·route/입력 UID/RV·복구 인증을 재확인하고 승인한 이미지/config diff만 배포한다. 실패 시 research flags(`RESEARCH_AGENT_API_ENABLE`, `CODEX_RUNNER_RESEARCH_API_ENABLE`, `RUNNER_RESEARCH_API_ENABLE`)를 승인된 원래 공급 위치에서 off하고, 추가한 `runner-research-traefik-v1`만 저장 UID 전제조건 DELETE로 제거한다. 다른 객체가 되었으면 중단한다. 임시 Pod를 UID로 삭제하고 원래 이미지/config·origin/routes를 복구한 뒤 human read/save·기존 이력·잔류0을 확인한다. schema/data/author는 되돌리거나 지우지 않는다.

보고는 승인 링크·실행자/context·UTC·SHA/manifest·정책/Pod UID/revision·route inventory·arm64 체크·TLS/HTTP·거부 결과·복구·미검증 항목을 #97에 남긴다. 기능 flag off와 추가 egress 제거 이후에도 기존 D1 world443은 그대로이며 D7 롤백을 전체 인터넷 차단이라고 부르지 않는다.
