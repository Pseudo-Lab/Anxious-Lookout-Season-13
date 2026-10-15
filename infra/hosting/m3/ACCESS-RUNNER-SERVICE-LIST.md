# Selector control Service 목록 수집 보완 — 실행 전 재검토

실제 `/tmp/issue7-selector-control-20261011/services.before.json`은 kubectl generic List이며
metadata.resourceVersion이 빈 문자열입니다. 정상 Service 한 개의 item RV는 list snapshot RV의
대체물이 아닙니다. 기존 gate의 refusal은 유지하며 SC-R1/R2는 종료 상태입니다. 아직 원본 API 응답은
확인하지 않았으므로 빈 RV가 실제 서버에서 왔다고도, 실제 client가 삭제했다고도 확정하지 않습니다.

## 근거와 제안

[kubectl get 소스](https://raw.githubusercontent.com/kubernetes/kubectl/master/pkg/cmd/get/get.go)의
printGeneric은 flattened items로 새 corev1.List/빈 ListMeta를 만들 수 있습니다. 출력 List에는 그
resourceVersion만 전달합니다. 이는 관측된 빈 RV를 설명하는 후보이며 설치 client build의 실행 경로
검증은 아닙니다. `--raw`는 generic printer 대신 RawGet을 호출합니다.
[RawGet 구현](https://raw.githubusercontent.com/kubernetes/kubectl/v0.34.3/pkg/rawhttp/raw.go)은 지정
RequestURI의 GET response stream을 출력합니다.

[Kubernetes API 문서](https://kubernetes.io/docs/reference/using-api/api-concepts/#semantics-for-get-and-list)는
RV/limit 미지정 list를 Most Recent로 정의합니다. 단일 무필터 API 응답의 list RV/완결 pagination을
검사하는 방식으로 수집합니다. 단순히 nonempty RV가 있다는 사실만으로 파일 freshness를 증명하지 않습니다.
[v1 ServiceList 스키마](https://raw.githubusercontent.com/kubernetes/api/v0.34.3/core/v1/types.go)의 items는
[]Service입니다. parser는 이 typed 문맥에서만 nested kind/apiVersion 생략을 지원하며 명시한 충돌은 거부합니다.

## 별도 승인 후 PM의 좁은 read-only 확인

기존 trusted absolute kubectl/target kubeconfig/context를 사용하고 core0, outer TERM10+KILL2,
출력256KiB, private0700 directory/파일0600 기준을 적용합니다. source credential을 출력하거나 읽지
않으며 기존 kubeconfig transport 사용만 유지합니다. 각 명령/exit/start-end 시점/원문 bytes hash와
stderr를 private evidence로 보존합니다. 크기 초과/timeout/exit!=0/JSON 오류는 중단하며 자동 재시도하지 않습니다.

1. `<trusted-absolute-kubectl> version --client -o json` — client metadata만 수집합니다.
2. `<trusted-absolute-kubectl> --request-timeout=8s -n codex-trial get services -o json`
   — 기존 CLI aggregate 표현의 fresh read-only 비교입니다. 조회 명령에 필터를 붙이지 않습니다.
3. `<trusted-absolute-kubectl> --request-timeout=8s get --raw=/api/v1/namespaces/codex-trial/services`
   — 같은 namespace의 전체 Service 원본 API 목록입니다. query/출력 변환/다른 리소스를 추가하지 않습니다.

이 세 명령은 **수집 보완에 대한 독립 검토 뒤 PM만 실행**합니다. Backend는 실행하지 않습니다.
원출력은 cli-services.json/api-services.json으로 각각 보존하고 손으로 metadata를 채우거나 병합하지 않습니다.
API가 kind=ServiceList/apiVersion=v1/nonempty string list RV, continue 누락/빈 문자열,
remainingItemCount 누락/정수0일 때만 완결 후보입니다. 오류 Status/빈 RV/잘못된 pagination/부분 응답은
거부하며 다른 페이지 수집이나 임의 pagination 제거를 하지 않습니다. 이 좁은 namespace 목록이 크기
한도를 넘거나 부분 응답이면 별도 수집안으로 인계합니다.

양쪽 items의 namespace/name/UID/item RV/selector를 기계적으로 비교하고 read-only Docker에서 API
원문을 renderer 입력으로 사용합니다. 비교 구간의 item drift/서로 다른 목록은 중단합니다. API RV가
존재하고 CLI list RV가 비어 있는 관측은 두 표현의 차이이며 item RV가 list RV와 같아야 한다는 뜻은
아닙니다. API도 빈 RV이면 이 제안으로 운영 gate를 통과할 수 없습니다. fresh API 실제 증거가 없으므로
fixture 성공을 실제 호환성/완전성 검증으로 확대하지 않습니다.

## 이후 control preflight

성공한 API 원문만 services 입력에 사용합니다. 기존 generic services.before.json은 실패 증거로
남깁니다. 새로운 raw 응답의 수집 시점/정확한 고정 URI/exit/bytes/pagination을 기록하고, 완성 패키지
승인 뒤 fresh baseline부터 기존 control 절차를 다시 진행합니다. server-defaulted/actual labels 재대조와
후검증의 Service 목록도 이 고정 raw 수집 방식을 사용합니다. selector 목록은 시간에 따라 바뀔 수 있으므로
생성 직전/후 확인과 원문 보존을 생략하지 않습니다. 새 수집 중단 뒤 Job/create/query를 이어가지 않습니다.

source render 두 경로 차이, Runner2948/program/네 bounded attempts/전체10초 및 exact UID cleanup은
그대로입니다. 실제 Job/query/정책 변경 없이 준비한 보완이며 DN12 PAUSED 및 기존 전체 hold를 유지합니다.
