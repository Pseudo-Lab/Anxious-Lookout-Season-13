# DNS contrast 운영 UID 보완 / 별도 fresh run 검토

Source 기능은 b57ad8d 그대로입니다. 실제 L4 realized gate/holder ownership emitter는 통과했지만
일반 사용자 wrapper 내부의 kubectl transport 권한 오류로 중단됐습니다. Review가 exact UID 정리와
baseline을 독립 검증했습니다. DNS 판정은 없고 기존 claim은 소비됐으며 L7 조건은 충족되지 않았습니다.
이 문서는 **새 호출 방식과 별도 fresh run 한 개의 검토안**이며 기존 승인으로 자동 재실행하지 않습니다.

## 일치시킬 UID와 권한

현재 target의 기존 kubectl은 관리자 root 권한을 사용합니다. 내부 source를 수정하지 않고 **wrapper
전체를 같은 root UID로 실행**합니다. Kubernetes diagnostic의 runAsUser10001은 그대로입니다.
기존 trusted absolute kubectl/target/context/transport를 유지합니다. Credentials 복사/출력, kubeconfig
수정/다른 context 선택, sudo -E/새 KUBECONFIG 공급은 하지 않습니다. 권한 실패는 생성 전 중단입니다.

PM은 아래 경로를 기존 검증값으로 치환합니다. 새 evidence는 처음부터 root-owned이며 기존 directory/
claim을 재사용하거나 chown하지 않습니다. Directory UID0/mode700 및 operator UID0를 기록합니다:

```bash
contrast_kubectl='<existing-trusted-absolute-path-ending-in-/kubectl>'
contrast_wrapper='<reviewed-source-root>/infra/hosting/m3/run_dns_contrast_exec.sh'
contrast_evidence=$(sudo -n /usr/bin/mktemp -d /tmp/issue7-dns-contrast-admin.XXXXXX)
sudo -n /usr/bin/stat -c '%u %a' "$contrast_evidence"
sudo -n /usr/bin/id -u
```

동일 UID/동일 trusted path의 **선행 read-only target 확인**은 기존 bounded capture(core0/outer
TERM10+KILL2/출력256KiB/private0600 원문 보존) 안에서 다음 고정 argv로 수행합니다:

```text
sudo -n <same-trusted-absolute-kubectl> --request-timeout=8s get --raw=/api/v1/namespaces/codex-trial/services
```

API ServiceList/v1/nonempty list RV/완결 pagination 및 known Service namespace/name/UID/item RV/selector를
기존 provenance와 대조합니다. 기존 API70/Runner/CNP/P1/CCNP/known DNS fresh baseline GET도 같은
관리자 invocation으로 수행합니다. 원문·명령/exit·시점/hash는 새 root evidence에 보존합니다. 같은 target/
권한 확인은 exec 허가나 DNS 성공의 사전 증명이 아닙니다. 실패하면 객체를 생성하지 않습니다.

## 입력과 전체 wrapper 호출

모든 language emit/gate는 Docker입니다. Root-owned metadata를 읽을 때 해당 Docker invocation은
--user0:0/파일별 readonly mount이며 출력 리다이렉션도 root-owned shell 안에서 수행합니다. 기존
network-none/RO/cap-drop 제약과 source/image를 유지합니다. Host Python/credential 변환은 쓰지 않습니다.
Holder manifest의 원문 command bytes를 보존하며 shell에서 재조립하지 않습니다.

이미 일반 사용자로 생성한 **새 Pod의 검증된 program 산출물**을 전달해야 할 경우에만 다음 제한 복사를
사용합니다. 입력은 공개 source+해당 fresh diagnostic metadata를 담은 emitter output입니다:

```bash
sudo -n /usr/bin/install -o root -g root -m 0600 -- '<fresh-emitted-program.py>' "$contrast_evidence/l4-program.py"
sudo -n /usr/bin/stat -c '%u %a %s' "$contrast_evidence/l4-program.py"
sudo -n /usr/bin/sha256sum "$contrast_evidence/l4-program.py"
```

UID0/mode600/<=65536bytes 및 producer bytes hash 일치를 확인합니다. Kubeconfig/credentials/원본
state/PVC/이전 consumed program·nonce는 복사하지 않습니다. 원본 파일의 권한을 넓히거나 소유권을
바꾸지 않습니다. Raw metadata/receipt/result도 새 run에서 수집합니다.

Fresh owner/realization/receipt gate 및 remote28초 cutoff 여유가 있을 때만 다음 전체 wrapper를 **한 번**
호출합니다. 새 prefix의 .claim/.stdout/.stderr/.exit는 모두 부재여야 합니다:

```bash
sudo -n /bin/bash "$contrast_wrapper" "$contrast_kubectl" \
  '<fresh-exact-owned-l4-Pod-name>' "$contrast_evidence/l4-program.py" "$contrast_evidence/l4-exec"
```

L7 조건 충족 때만 fresh L7 owner/program와 새 l7-exec prefix에 같은 방식을 적용합니다. Host/remote
claim은 실패·ACK 누락 뒤 소비 상태로 두고 무재exec합니다. 다시 권한 오류가 나도 nonce/셋업을 바꿔
자동 반복하거나 상한을 늘리지 않습니다. Owned exact UID cleanup 후 unknown/미완료를 인계합니다.

## 이번에 제안하는 새 실행의 경계

완성 문서/source 검토 후 review/PM이 **별도 fresh run 한 개**를 명시적으로 승인해야 합니다.
과거 /tmp/issue7-dns-contrast-20261011/의 evidence/claim/prefix/nonce/UID는 종료 증거로 보존합니다.
Fresh 두 고정 Job/CNP 이름 및 control app 잔여물0를 확인하며 잔여물 재사용/자동 삭제는 없습니다.

같은 fixed names를 부재 확인 뒤 **새 CNP/Job/Pod UID**, 새 nonce/root evidence/claims에 사용합니다.
새 run도 CNP 한 개/L4 최대1 exec, 같은 실행 context의 네 정상 reply+transport0+exact cleanup 때만
rules 한 경로 전환/L7 최대1 exec입니다. 최대 두 Pod/8 attempts, holder40/Job50/첫 network28/본체10/
end38은 동일합니다. 시간 부족은 정상적인 무질의 중단이며 관측/질의/권한 범위를 확대하지 않습니다.

모든 실패에서 L7 금지, owned Job exact UID Foreground 정리→owner/control/runner Pod0→owned CNP
정리/부재→baseline 후검증입니다. UID 미확정/교체/삭제 실패는 미완료입니다. 새 결과/정리는 별도
독립 검증 대상입니다. 이전 stdout0을 일반적인 무송신 증명 또는 Runner/CNP DNS 고장으로 보지 않습니다.
DNS/FQDN cache 즉시 원복/flush, 생산 격리 정책·Deployment 및 원본 credentials/state/PVC 변경은 없습니다.
DN12 PAUSED 및 기존 재공급/새 창/API enable/Native·auth·turn/최종 PR/public posting hold를 유지합니다.
