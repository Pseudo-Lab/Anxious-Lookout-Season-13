#!/bin/sh
# Mock harness for the #72 production step scripts. Docker only, never on a real node:
#   docker run --rm -v "$PWD/infra/hosting/m3:/m3:ro" -v "$PWD/backend:/backend:ro" alpine:3.20 sh /m3/codex-broker/prod/test_steps.sh
# Stubs replace kubectl (k3s) and systemctl; credential fixtures hold SECRET markers that must
# never reach any output.
set -u
set -a  # prefix assignments before t must reach the script under test
apk add -q python3 coreutils findutils procps grep sed diffutils >/dev/null 2>&1 || { echo 'apk failed'; exit 1; }
P=/m3/codex-broker/prod
B=/root/issue72/bin
IMG=runner@sha256:$(printf 'b%.0s' $(seq 64))
DIG=sha256:$(printf 'b%.0s' $(seq 64))
printf '#!/bin/sh\nexec python3 %s/mock/k3s.py "$@"\n' $P > /usr/local/bin/k3s
printf '#!/bin/sh\nexec python3 %s/mock/systemctl.py "$@"\n' $P > /usr/bin/systemctl
chmod 0755 /usr/local/bin/k3s /usr/bin/systemctl

build() {
  rm -rf /mock /root/issue72 /etc/systemd/system /usr/local/lib/anxious-lookout /etc/anxious-lookout /var/lib/anxious-lookout
  mkdir -p /mock /root/issue72/render $B /etc/systemd/system /usr/local/lib/anxious-lookout/issue16 \
           /etc/anxious-lookout/issue16/supply /var/lib/anxious-lookout/issue16-d4/supply/attempts
  echo 'loaded inactive enabled' > /mock/unit.issue16-supply.timer
  echo 'loaded inactive static' > /mock/unit.issue16-supply.service
  printf 'runner-a|Running|docker.io/library/%s\nrunner-66|Running|old@sha256:%s\nrunner-gone|Failed|old@sha256:c\n' \
    "$IMG" "$(printf 'c%.0s' $(seq 64))" > /mock/runners
  echo 'true|0' > /mock/role
  printf '[Timer]\nOnCalendar=*:0/5\n' > /etc/systemd/system/issue16-supply.timer
  printf '[Service]\nExecStart=/usr/local/lib/anxious-lookout/issue16/supply.sh\n' > /etc/systemd/system/issue16-supply.service
  printf '[Service]\nExecStart=/usr/local/lib/anxious-lookout/issue16/controller.sh\n' > /etc/systemd/system/issue16-controller.service
  printf '#!/bin/sh\nread_private database-url\n' > /usr/local/lib/anxious-lookout/issue16/supply.sh
  printf '#!/bin/sh\nexit 0\n' > /usr/local/lib/anxious-lookout/issue16/stop-supply.sh
  chmod 0644 /etc/systemd/system/issue16-supply.*; chmod 0700 /usr/local/lib/anxious-lookout/issue16/*.sh
  for n in database-url kube-token kubeconfig; do echo "SECRET-$n" > /etc/anxious-lookout/issue16/supply/$n; chmod 0600 /etc/anxious-lookout/issue16/supply/$n; done
  echo '{"owner":"00000000-0000-4000-8000-000000000048","state":"s","generation":1,"podUid":"p","snapshotId":"a1","result":"accepted"}' \
    > /var/lib/anxious-lookout/issue16-d4/supply/attempts/a1
  printf '{"image":"%s","accountId":"synthetic-account","storageClass":"local-path","accessMode":"ReadWriteOnce","egress":"world"}' "$IMG" > /mock/values.json
  ( cd /backend && python3 -B -m ops.broker_render --values /mock/values.json ) > /root/issue72/render/broker.json
  cp /m3/user-runner-e2e/40-runner-network.json /root/issue72/render/runner-cnp.json
  python3 -I - <<'PY'
import json
cnp = json.load(open('/root/issue72/render/runner-cnp.json'))
cnp['spec']['egress'] = cnp['spec']['egress'][1:]
json.dump(cnp, open('/mock/live-cnp.json', 'w'))
items = []
for kind in ('Role', 'RoleBinding', 'ClusterRole', 'ClusterRoleBinding'):
    meta = {'name': 'runner-pool-supply'}
    if not kind.startswith('Cluster'):
        meta['namespace'] = 'codex-runner'
    item = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': kind, 'metadata': meta}
    if kind.endswith('Binding'):
        item.update(subjects=[], roleRef={'apiGroup': 'rbac.authorization.k8s.io', 'kind': kind[:-7], 'name': 'runner-pool-supply'})
    else:
        item['rules'] = []
    items.append(item)
json.dump({'apiVersion': 'v1', 'kind': 'List', 'items': items}, open('/root/issue72/render/supply-rbac.json', 'w'))
PY
  for s in inventory apply-broker check-login retire-e1-disable retire-e2-rbac retire-e3-files retire-e4-final; do
    sed -e "s|@@BROKER_JSON_SHA256@@|$(sha256sum < /root/issue72/render/broker.json | cut -c1-64)|" \
        -e "s|@@RUNNER_CNP_JSON_SHA256@@|$(sha256sum < /root/issue72/render/runner-cnp.json | cut -c1-64)|" \
        -e "s|@@SUPPLY_RBAC_JSON_SHA256@@|$(sha256sum < /root/issue72/render/supply-rbac.json | cut -c1-64)|" \
        -e "s|@@RUNNER_IMAGE_REF@@|$IMG|" -e "s|@@RUNNER_IMAGE_ID@@|$DIG|" -e "s|@@EXEMPT_PODS@@|runner-66|" \
        -e "s|@@NOT_BEFORE_UTC@@|2020-01-01T00:00:00Z|" -e "s|@@SUPPLY_CRED_FILE_NAMES@@|database-url kube-token kubeconfig|" \
        -e "s|@@SUPPLY_DB_ROLE@@|runner_supply|" $P/$s.sh > $B/$s.sh
  done
}
after_e1() { echo 'loaded inactive disabled' > /mock/unit.issue16-supply.timer; echo 'loaded inactive static' > /mock/unit.issue16-supply.service; }
after_e2() { after_e1; touch /mock/rbac-emptied; }
after_e3() { after_e2; rm -f /etc/systemd/system/issue16-supply.* /usr/local/lib/anxious-lookout/issue16/supply.sh /usr/local/lib/anxious-lookout/issue16/stop-supply.sh
             echo 'not-found inactive -' > /mock/unit.issue16-supply.timer; echo 'not-found inactive -' > /mock/unit.issue16-supply.service; }

pass=0 fail=0
# t name want script [check] -- environment assignments come before t.
t() {
  name=$1 want=$2 script=$3 check=${4:-true}
  out=$(sh $B/$script.sh 2>&1); rc=$?
  ok=ok
  [ $rc = "$want" ] || ok=FAIL
  echo "$out" | grep -q SECRET && ok=FAIL
  echo "$out" | grep -q 'stub: unhandled' && ok=FAIL
  eval "$check" || ok=FAIL
  [ $ok = ok ] && pass=$((pass+1)) || { fail=$((fail+1)); echo "$out" | sed 's/^/    /'; }
  echo "$ok $script/$name rc=$rc want=$want | $(echo "$out" | tail -1)"
}
creds() { [ "$(find /etc/anxious-lookout/issue16/supply -type f 2>/dev/null | wc -l)" = "$1" ]; }

# F inventory (read-only).
build; t normal 0 inventory 'echo "$out" | grep -q "^owner66 3cb453bf" && echo "$out" | grep -q "supply.credentials f 600 root kube-token" \
  && echo "$out" | grep -q "^capacity.pods hard=3|used=2" && echo "$out" | grep -q "^supply.attempts result.accepted=1" \
  && echo "$out" | grep -q "^supply.attempts newest .*|00000000-0000-4000-8000-000000000048|accepted" \
  && echo "$out" | grep -q "^rbac.supply.clusterrolebinding runner-pool-supply|\[{"'
build; touch /mock/rbac-emptied; t emptied-rbac-is-present 0 inventory 'echo "$out" | grep -q "^rbac.supply.role runner-pool-supply|$"'
build; touch /mock/rbac-deleted /mock/sa-deleted; t absent-objects 0 inventory 'echo "$out" | grep -q "^sa.supply absent" && echo "$out" | grep -q "^rbac.supply.role absent"'
build; MOCK_PSQL_FAIL=1 t psql-fails 3 inventory 'echo "$out" | grep -q "^registry lookup_failed"'; unset MOCK_PSQL_FAIL
# Review round 1: every refused lookup must end in lookup_failed and rc 3, never in "absent" or a count.
for k in 'get role runner-pool-supply' 'get rolebinding runner-pool-supply' 'get clusterrole runner-pool-supply' 'get clusterrolebinding runner-pool-supply' 'serviceaccount runner-pool-supply' resourcequota 'cnp runner-callback-draft' cilium-config storageclass; do
  build; MOCK_KFAIL=$k t "refused:$k" 3 inventory '[ "$(echo "$out" | grep -c lookup_failed)" = 1 ] && ! echo "$out" | grep -q Forbidden'
done; unset MOCK_KFAIL
build; MOCK_KFAIL='get nodes' MOCK_KFAIL_PARTIAL='node1|True' t partial-then-refused 3 inventory 'echo "$out" | grep -q "^node lookup_failed" && ! echo "$out" | grep -q "^node node1"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL
build; echo 'not json' > /var/lib/anxious-lookout/issue16-d4/supply/attempts/a2; t attempts-unreadable-record 3 inventory 'echo "$out" | grep -q "^supply.attempts lookup_failed" && ! echo "$out" | grep -q "total="'
build; mkdir -p /stubs; printf '#!/bin/sh\necho 0\nexit 2\n' > /stubs/pgrep; chmod 0755 /stubs/pgrep
PATH=/stubs:$PATH t pgrep-error 3 inventory 'echo "$out" | grep -q "^supply.processes lookup_failed"'; rm -rf /stubs
build; mkdir -p /stubs; printf '#!/bin/sh\nfor l; do :; done\ncase "$l" in */supply.sh) exit 1;; esac\nexec /usr/bin/stat "$@"\n' > /stubs/stat; chmod 0755 /stubs/stat
PATH=/stubs:$PATH t stat-error 3 inventory 'echo "$out" | grep -q "lookup_failed$" && echo "$out" | grep -q "^file .*supply.sh lookup_failed"'; rm -rf /stubs
build; mkdir -p /stubs; printf '#!/bin/sh\ncase "$*" in *issue16/supply*) echo "f 600 root kube-token"; exit 1;; esac\nexec /usr/bin/find "$@"\n' > /stubs/find; chmod 0755 /stubs/find
PATH=/stubs:$PATH t credential-scan-partial 3 inventory 'echo "$out" | grep -q "^supply.credentials lookup_failed"'; rm -rf /stubs

# B3 apply-broker.
build; sed -i 's/^BROKER_SHA=.*/BROKER_SHA=@@X@@/' $B/apply-broker.sh; t placeholders 2 apply-broker '[ ! -e /mock/applied ]'
build; t normal 0 apply-broker '[ -f /root/issue72/evidence/b3/runner-cnp-before.json ] && grep -q CiliumNetworkPolicy/codex-runner/runner-callback-draft /mock/applied'
build; echo ' ' >> /root/issue72/render/broker.json; t hash-mismatch 3 apply-broker '[ ! -e /mock/applied ]'
build; IMG2=runner@sha256:$(printf 'd%.0s' $(seq 64)); sed -i "s|^RUNNER_IMAGE=.*|RUNNER_IMAGE=$IMG2|" $B/apply-broker.sh; t image-not-reviewed 3 apply-broker '[ ! -e /mock/applied ]'
build; python3 -I -c "
import json; p='/mock/live-cnp.json'; d=json.load(open(p)); d['spec']['egress'].append({'toEntities':['world']}); json.dump(d,open(p,'w'))"
t live-cnp-drifted 3 apply-broker '[ ! -e /mock/applied ]'
build; touch /mock/ns-exists; t namespace-exists 3 apply-broker '[ ! -e /mock/applied ]'
build; MOCK_KFAIL='get ns codex-broker' t namespace-lookup-refused 3 apply-broker '[ ! -e /mock/applied ] && echo "$out" | grep -q "namespace lookup failed"'; unset MOCK_KFAIL
build; MOCK_DIFF_STDERR=1 t diff-error-rc1 3 apply-broker '[ ! -e /mock/applied ]'; unset MOCK_DIFF_STDERR
build; MOCK_DIFF_ZERO=1 t diff-already-applied 3 apply-broker '[ ! -e /mock/applied ]'; unset MOCK_DIFF_ZERO
build; install -d -m 0700 /root/issue72/evidence/b3; echo x > /root/issue72/evidence/b3/runner-cnp-before.json; t evidence-present 3 apply-broker '[ ! -e /mock/applied ]'
build; MOCK_APPLY_FAIL=StatefulSet t apply-fails-at-sts 5 apply-broker '! grep -q runner-callback-draft /mock/applied && grep -q CiliumNetworkPolicy/codex-broker /mock/applied'; unset MOCK_APPLY_FAIL
build; MOCK_ROLLOUT_FAIL=1 t rollout-timeout 5 apply-broker '! grep -q runner-callback-draft /mock/applied'; unset MOCK_ROLLOUT_FAIL
build; MOCK_CILIUM_CHANGE=1 t cilium-changed 8 apply-broker 'echo "$out" | grep -q cilium-config-changed'; unset MOCK_CILIUM_CHANGE
build; MOCK_BROKER_RESTARTS=1 t broker-restarted 8 apply-broker 'echo "$out" | grep -q broker-pod'; unset MOCK_BROKER_RESTARTS

# C check-login (read-only).
build; t normal 0 check-login 'echo "$out" | grep -q "auth.json: 600 10001 1890" && ! echo "$out" | grep -q "Logged in"'
build; MOCK_LOGIN_RC=1 t not-logged-in 3 check-login; unset MOCK_LOGIN_RC
build; MOCK_AUTH_STAT='644 10001 1890' t auth-mode 3 check-login; unset MOCK_AUTH_STAT
build; MOCK_HOME_STAT='755 10001' t home-mode 3 check-login; unset MOCK_HOME_STAT

# E1.
build; t normal 0 retire-e1-disable 'grep -q disabled /mock/unit.issue16-supply.timer && echo "$out" | grep -q "exempt (decision 3): runner-66"'
build; sed -i "s|^EXEMPT_PODS=.*|EXEMPT_PODS='none'|" $B/retire-e1-disable.sh; t old-image-runner 3 retire-e1-disable 'grep -q enabled /mock/unit.issue16-supply.timer'
build; echo 'loaded active static' > /mock/unit.issue16-supply.service; t delivery-running 3 retire-e1-disable 'grep -q enabled /mock/unit.issue16-supply.timer'
build; MOCK_BROKER_READY=false t broker-not-ready 3 retire-e1-disable; unset MOCK_BROKER_READY
build; MOCK_DISABLE_FAIL=1 t disable-fails 5 retire-e1-disable; unset MOCK_DISABLE_FAIL
build; sh -c 'sleep 20; :' /usr/local/lib/anxious-lookout/issue16/supply.sh & sp=$!
t process-remains 8 retire-e1-disable 'echo "$out" | grep -q "postcheck failed: process"'; kill $sp 2>/dev/null
build; mkdir -p /stubs; printf '#!/bin/sh\nexit 2\n' > /stubs/pgrep; chmod 0755 /stubs/pgrep
PATH=/stubs:$PATH t pgrep-error 8 retire-e1-disable 'echo "$out" | grep -q "postcheck failed: process"'; rm -rf /stubs

# E2.
build; t e1-not-done 3 retire-e2-rbac '[ ! -e /mock/rbac-emptied ]'
build; after_e1; t normal 0 retire-e2-rbac '[ -f /root/issue72/evidence/e2/supply-rbac-before.json ] && grep -q pods /root/issue72/evidence/e2/supply-rbac-before.json'
build; after_e1; MOCK_DIFF_STDERR=1 t diff-error 3 retire-e2-rbac '[ ! -e /mock/rbac-emptied ]'; unset MOCK_DIFF_STDERR
build; after_e1; python3 -I -c "
import json; p='/root/issue72/render/supply-rbac.json'; d=json.load(open(p)); d['items'][0]['rules']=[{'verbs':['get']}]; json.dump(d,open(p,'w'))"
sed -i "s|^RBAC_SHA=.*|RBAC_SHA=$(sha256sum < /root/issue72/render/supply-rbac.json | cut -c1-64)|" $B/retire-e2-rbac.sh; t render-not-empty 3 retire-e2-rbac '[ ! -e /mock/rbac-emptied ]'
build; after_e1; MOCK_APPLY_FAIL=runner-pool-supply t apply-fails 5 retire-e2-rbac; unset MOCK_APPLY_FAIL

# E3.
build; after_e2; t normal 0 retire-e3-files '[ "$(grep -c . /root/issue72/evidence/e3/MANIFEST.sha256)" = 5 ] && [ ! -e /usr/local/lib/anxious-lookout/issue16/supply.sh ] && grep -q not-found /mock/unit.issue16-supply.timer && creds 3'
build; after_e1; t e2-not-done 3 retire-e3-files '[ -e /usr/local/lib/anxious-lookout/issue16/supply.sh ]'
build; after_e2; echo 'DSN=postgresql://x' >> /usr/local/lib/anxious-lookout/issue16/stop-supply.sh; t credential-marker 3 retire-e3-files '[ ! -e /root/issue72/evidence/e3 ]'
build; after_e2; printf 'ExecStart=/usr/local/lib/anxious-lookout/issue16/stop-supply.sh\n' > /etc/systemd/system/issue16-other.service; t other-unit-uses-script 3 retire-e3-files
build; after_e2; mkdir /etc/systemd/system/issue16-supply.service.d; t drop-in 3 retire-e3-files
build; after_e2; mkdir -p /stubs; printf '#!/bin/sh\ncase "$*" in *postgres*) exit 2;; esac\nexec /bin/grep "$@"\n' > /stubs/grep; chmod 0755 /stubs/grep
PATH=/stubs:$PATH t marker-scan-error 3 retire-e3-files '[ ! -e /root/issue72/evidence/e3 ]'; rm -rf /stubs
build; after_e2; mkdir -p /stubs; printf '#!/bin/sh\ncase "$*" in *supply.sh*) exit 2;; esac\nexec /bin/grep "$@"\n' > /stubs/grep; chmod 0755 /stubs/grep
PATH=/stubs:$PATH t unit-scan-error 3 retire-e3-files '[ ! -e /root/issue72/evidence/e3 ]'; rm -rf /stubs
build; after_e2; mkdir -p /root/issue72/evidence/e3; t evidence-dir-exists 3 retire-e3-files '[ -e /etc/systemd/system/issue16-supply.timer ]'
build; after_e2; mkdir -p /stubs; printf '#!/bin/sh\nfor l; do :; done\ncase "$l" in */stop-supply.sh) exit 1;; esac\nexec /bin/cp "$@"\n' > /stubs/cp; chmod 0755 /stubs/cp
PATH=/stubs:$PATH t evidence-copy-fails 6 retire-e3-files '[ -e /usr/local/lib/anxious-lookout/issue16/supply.sh ]'; rm -rf /stubs
build; after_e2; mkdir -p /stubs; printf '#!/bin/sh\ncase "$1" in */supply.sh) exit 1;; esac\nexec /usr/bin/unlink "$@"\n' > /stubs/unlink; chmod 0755 /stubs/unlink
PATH=/stubs:$PATH t unlink-fails-midway 7 retire-e3-files '[ -e /usr/local/lib/anxious-lookout/issue16/supply.sh ] && [ -f /root/issue72/evidence/e3/MANIFEST.sha256 ]'; rm -rf /stubs

# E4.
build; after_e3; t normal 0 retire-e4-final 'creds 0 && [ ! -e /etc/anxious-lookout/issue16/supply ] && [ -f /var/lib/anxious-lookout/issue16-d4/supply/attempts/a1 ] && [ -e /mock/sa-deleted ] && grep -q false /mock/role'
build; after_e3; sed -i 's|^NOT_BEFORE=.*|NOT_BEFORE=2099-01-01T00:00:00Z|' $B/retire-e4-final.sh; t too-early 3 retire-e4-final 'creds 3 && grep -q true /mock/role'
build; after_e2; t e3-not-done 3 retire-e4-final 'creds 3'
build; after_e3; echo SECRET-extra > /etc/anxious-lookout/issue16/supply/extra; t unexpected-credential-file 3 retire-e4-final 'creds 4'
build; after_e3; echo 'true|1' > /mock/role; t role-in-use 3 retire-e4-final 'creds 3'
build; after_e3; sed -i "s|^DB_ROLE=.*|DB_ROLE='x; drop'|" $B/retire-e4-final.sh; t role-not-identifier 3 retire-e4-final 'creds 3'
build; after_e3; MOCK_SA_DELETE_FAIL=1 t sa-delete-fails 5 retire-e4-final 'creds 3 && grep -q false /mock/role'; unset MOCK_SA_DELETE_FAIL
build; after_e3; mkdir -p /stubs; printf '#!/bin/sh\ncase "$*" in *issue16/supply*) printf "f database-url\\nf kube-token\\nf kubeconfig\\n"; exit 1;; esac\nexec /usr/bin/find "$@"\n' > /stubs/find; chmod 0755 /stubs/find
PATH=/stubs:$PATH t credential-scan-fails 3 retire-e4-final 'grep -q true /mock/role'; rm -rf /stubs
build; after_e3; MOCK_KFAIL='--ignore-not-found -o name' t sa-absence-unconfirmed 8 retire-e4-final 'echo "$out" | grep -q "postcheck failed: serviceaccount"'; unset MOCK_KFAIL

echo "passed $pass failed $fail"
[ $fail = 0 ]
