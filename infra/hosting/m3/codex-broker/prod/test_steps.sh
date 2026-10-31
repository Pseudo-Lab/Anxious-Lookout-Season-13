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
  printf 'runner-a|Running|docker.io/library/%s\nrunner-3cb453bfd87040dcb7e13a58aa0cbaf2|Running|old@sha256:%s\nrunner-gone|Failed|old@sha256:c\n' \
    "$IMG" "$(printf 'c%.0s' $(seq 64))" > /mock/runners
  echo 'true|0' > /mock/role
  printf '[Timer]\nOnCalendar=*:0/5\n' > /etc/systemd/system/issue16-supply.timer
  printf '[Service]\nExecStart=/usr/local/lib/anxious-lookout/issue16/supply.sh\n' > /etc/systemd/system/issue16-supply.service
  printf '[Service]\nExecStart=/usr/local/lib/anxious-lookout/issue16/controller.sh\n' > /etc/systemd/system/issue16-controller.service
  printf '#!/bin/sh\nread_private database-url\n' > /usr/local/lib/anxious-lookout/issue16/supply.sh
  printf '#!/bin/sh\nexit 0\n' > /usr/local/lib/anxious-lookout/issue16/stop-supply.sh
  chmod 0644 /etc/systemd/system/issue16-supply.*; chmod 0700 /usr/local/lib/anxious-lookout/issue16/*.sh
  for n in ca.crt database-url inputs.json kube-token kubeconfig; do echo "SECRET-$n" > /etc/anxious-lookout/issue16/supply/$n; chmod 0600 /etc/anxious-lookout/issue16/supply/$n; done
  echo '{"owner":"00000000-0000-4000-8000-000000000048","state":"s","generation":1,"podUid":"p","snapshotId":"a1","result":"accepted"}' \
    > /var/lib/anxious-lookout/issue16-d4/supply/attempts/a1
  # Node-local inputs exactly as `git show <sha>:<path>` would place them; values.json is written on the node.
  printf '{"image":"%s","accountId":"synthetic-account","storageClass":"local-path","accessMode":"ReadWriteOnce","egress":"world"}' "$IMG" \
    > /root/issue72/render/values.json
  cp /backend/ops/broker_render.py /root/issue72/render/broker_render.py
  cp /m3/user-runner-e2e/40-runner-network.json /root/issue72/render/runner-cnp.json
  cp $P/supply-rbac.json /root/issue72/render/supply-rbac.json
  python3 -I - <<'PY'
import json
cnp = json.load(open('/root/issue72/render/runner-cnp.json'))
cnp['spec']['egress'] = cnp['spec']['egress'][1:]
json.dump(cnp, open('/mock/live-cnp.json', 'w'))
PY
  for s in inventory apply-broker check-login retire-e1-disable retire-e2-rbac retire-e3-files retire-e4-final; do
    sed -e "s|@@STORAGE_CLASS@@|local-path|" -e "s|@@ACCESS_MODE@@|ReadWriteOnce|" \
        -e "s|@@RUNNER_IMAGE_REF@@|$IMG|" -e "s|@@RUNNER_IMAGE_ID@@|$DIG|" $P/$s.sh > $B/$s.sh
  done
}
after_e1() { echo 'loaded inactive disabled' > /mock/unit.issue16-supply.timer; echo 'loaded inactive static' > /mock/unit.issue16-supply.service; }
after_e2() { after_e1; touch /mock/rbac-emptied; }
after_e3() { after_e2; rm -f /etc/systemd/system/issue16-supply.* /usr/local/lib/anxious-lookout/issue16/supply.sh /usr/local/lib/anxious-lookout/issue16/stop-supply.sh
             echo 'not-found inactive -' > /mock/unit.issue16-supply.timer; echo 'not-found inactive -' > /mock/unit.issue16-supply.service
             install -d -m 0700 /root/issue72/evidence/e1; echo $(( $(date -u +%s) - 90000 )) > /root/issue72/evidence/e1/stopped-at
             echo 100:150 > /root/issue72/evidence/e1/session; }

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

# The committed E2 input must equal what ops.runner_rollout renders for runner-pool-supply.
( cd /backend && python3 -B - <<'PY'
import json
from ops.runner_rollout import resources
v = json.load(open('/m3/multi-user-runners/rollout/values.example.json'))
want = [i for i in resources(v)['items'] if i['metadata']['name'] == 'runner-pool-supply']
assert json.load(open('/m3/codex-broker/prod/supply-rbac.json')) == {'apiVersion': 'v1', 'kind': 'List', 'items': want}
PY
) && { pass=$((pass+1)); echo 'ok supply-rbac.json equals the rollout render'; } || { fail=$((fail+1)); echo 'FAIL supply-rbac.json differs from the rollout render'; }

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
build; sed -i 's/^STORAGE_CLASS=.*/STORAGE_CLASS=@@X@@/' $B/apply-broker.sh; t placeholders 2 apply-broker '[ ! -e /mock/applied ]'
build; t normal 0 apply-broker '[ -f /root/issue72/evidence/b3/runner-cnp-before.json ] && grep -q CiliumNetworkPolicy/codex-runner/runner-callback-draft /mock/applied \
  && grep -q "\"storageClassName\": \"local-path\"" /root/issue72/evidence/b3/broker.json && ! echo "$out" | grep -q synthetic-account'
build; echo '#' >> /root/issue72/render/broker_render.py; t renderer-hash-mismatch 3 apply-broker '[ ! -e /mock/applied ]'
build; echo ' ' >> /root/issue72/render/runner-cnp.json; t runner-cnp-hash-mismatch 3 apply-broker '[ ! -e /mock/applied ]'
for v in '"storageClass":"local-path"/"storageClass":"openebs-lvm-ext4"' '"accessMode":"ReadWriteOnce"/"accessMode":"ReadWriteOncePod"' \
         '"egress":"world"/"egress":"fqdn"' '"accountId":"synthetic-account"/"accountId":"has space"' '"egress":"world"/"egress":"world","extra":1'; do
  build; sed -i "s/$v/" /root/issue72/render/values.json; t "values:${v%%/*}" 3 apply-broker '[ ! -e /mock/applied ] && echo "$out" | grep -q "values.json not as reviewed" && ! echo "$out" | grep -q "has space"'
done
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
build; MOCK_KFAIL='get pod codex-broker-0' MOCK_KFAIL_PARTIAL="Running|true|0|$IMG" t postcheck-pod-partial-then-fails 8 apply-broker 'echo "$out" | grep -q "postcheck failed: broker-pod"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL
build; MOCK_KFAIL='get cnp codex-broker' MOCK_KFAIL_PARTIAL='True' t postcheck-cnp-partial-then-fails 8 apply-broker 'echo "$out" | grep -q "postcheck failed: cnp-valid"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL

# C check-login (read-only).
build; t normal 0 check-login 'echo "$out" | grep -q "auth.json: 600 10001 1890" && ! echo "$out" | grep -q "Logged in"'
build; MOCK_LOGIN_RC=1 t not-logged-in 3 check-login; unset MOCK_LOGIN_RC
build; MOCK_AUTH_STAT='644 10001 1890' t auth-mode 3 check-login; unset MOCK_AUTH_STAT
build; MOCK_HOME_STAT='755 10001' t home-mode 3 check-login; unset MOCK_HOME_STAT
# Review C/E2/E3 round 1: correct-looking output followed by a failure status must not pass.
build; MOCK_KFAIL='stat -c %a %u %s %Y' MOCK_KFAIL_PARTIAL='600 10001 1890 4102444800' t auth-stat-partial-then-fails 3 check-login 'echo "$out" | grep -q "auth.json: lookup_failed"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL
build; MOCK_KFAIL='stat -c %a %u /data/codex' MOCK_KFAIL_PARTIAL='700 10001' t home-stat-partial-then-fails 3 check-login 'echo "$out" | grep -q "CODEX_HOME: lookup_failed"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL
build; MOCK_KFAIL='get pod codex-broker-0' MOCK_KFAIL_PARTIAL='Running|true|0' t pod-partial-then-fails 3 check-login 'echo "$out" | grep -q "broker pod: lookup_failed"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL
build; MOCK_AUTH_STAT='600 10001 0' t auth-size-zero 3 check-login; unset MOCK_AUTH_STAT
build; MOCK_AUTH_MTIME='12' t auth-mtime-malformed 3 check-login; unset MOCK_AUTH_MTIME

# E1.
build; t normal 0 retire-e1-disable 'grep -q disabled /mock/unit.issue16-supply.timer && echo "$out" | grep -q "exempt (decision 3): runner-3cb453bfd87040dcb7e13a58aa0cbaf2" \
  && grep -qE "^[0-9]{10}$" /root/issue72/evidence/e1/stopped-at && echo "$out" | grep -q "^stopped-at 20" \
  && [ "$(cat /root/issue72/evidence/e1/session)" = 100:200 ]'
build; MOCK_KFAIL='os.lstat' MOCK_KFAIL_PARTIAL='100:200' t not-logged-in-yet 3 retire-e1-disable 'grep -q enabled /mock/unit.issue16-supply.timer && [ ! -e /root/issue72/evidence/e1 ]'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL
build; sed -i "s|^EXEMPT_PODS=.*|EXEMPT_PODS='none'|" $B/retire-e1-disable.sh; t old-image-runner 3 retire-e1-disable 'grep -q enabled /mock/unit.issue16-supply.timer && echo "$out" | grep -q "runner-3cb453bfd87040dcb7e13a58aa0cbaf2"'
build; sed -i "s|^EXEMPT_PODS=.*|EXEMPT_PODS='runner-3cb453bf'|" $B/retire-e1-disable.sh; t partial-exemption-name 3 retire-e1-disable 'grep -q enabled /mock/unit.issue16-supply.timer'
build; install -d -m 0700 /root/issue72/evidence/e1; t e1-record-exists 3 retire-e1-disable 'grep -q enabled /mock/unit.issue16-supply.timer'
# Review round 1 on d23550c: one real 30 s deadline covers lookups too. Elapsed is the whole script, measured here.
t0() { s0=$(date +%s.%N); }
within() { awk -v a="$s0" -v b="$(date +%s.%N)" -v m="$1" 'BEGIN { d = b - a; printf "    elapsed %.2fs\n", d; exit !(d < m) }'; }
waited='grep -q enabled /mock/unit.issue16-supply.timer && [ ! -e /root/issue72/evidence/e1 ] && within 31'
build; echo 'loaded active static' > /mock/unit.issue16-supply.service; t0
t delivery-running 3 retire-e1-disable "$waited"' && echo "$out" | grep -q "at the 30s deadline (delivery in progress)"'
build; echo 'loaded active static' > /mock/unit.issue16-supply.service; t0
MOCK_SYSTEMCTL_DELAY=0.5 t slow-lookups-stay-in-bound 3 retire-e1-disable "$waited"; unset MOCK_SYSTEMCTL_DELAY
build; echo 'loaded active static' > /mock/unit.issue16-supply.service; t0
MOCK_SYSTEMCTL_DELAY=7 t lookup-cut-at-deadline 3 retire-e1-disable "$waited"; unset MOCK_SYSTEMCTL_DELAY
build; MOCK_KFAIL='get pod codex-broker-0' MOCK_KFAIL_PARTIAL='Running|true' t broker-pod-partial-then-fails 3 retire-e1-disable \
  'grep -q enabled /mock/unit.issue16-supply.timer && [ ! -e /root/issue72/evidence/e1 ] && echo "$out" | grep -q "broker pod lookup failed"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL
# Production cadence: a batch every ~15 s that fails after ~3 s. One that ends during the wait is waited out.
build; echo 'loaded activating static' > /mock/unit.issue16-supply.service; ( sleep 3; echo 'loaded failed static' > /mock/unit.issue16-supply.service ) &
t delivery-ends-during-wait 0 retire-e1-disable 'grep -q "inactive disabled" /mock/unit.issue16-supply.timer && [ -f /root/issue72/evidence/e1/stopped-at ]'; wait
# Production baseline (F before): the service's last batch failed, the timer is active.
build; echo 'loaded active enabled' > /mock/unit.issue16-supply.timer; echo 'loaded failed disabled' > /mock/unit.issue16-supply.service
t baseline-service-failed 0 retire-e1-disable 'grep -q "inactive disabled" /mock/unit.issue16-supply.timer'
build; MOCK_BINDING_IMAGE=runner@sha256:old t stopped-owner-binding-old 3 retire-e1-disable 'grep -q enabled /mock/unit.issue16-supply.timer && echo "$out" | grep -q "bindings still on an old runner image: runner-00000000000040008000000000000048"'; unset MOCK_BINDING_IMAGE
build; MOCK_PSQL_FAIL=1 t binding-lookup-fails 3 retire-e1-disable 'grep -q enabled /mock/unit.issue16-supply.timer'; unset MOCK_PSQL_FAIL
build; MOCK_BROKER_READY=false t broker-not-ready 3 retire-e1-disable; unset MOCK_BROKER_READY
build; MOCK_DISABLE_FAIL=1 t disable-fails 5 retire-e1-disable; unset MOCK_DISABLE_FAIL
build; sh -c 'sleep 20; :' /usr/local/lib/anxious-lookout/issue16/supply.sh & sp=$!
t process-remains 8 retire-e1-disable 'echo "$out" | grep -q "postcheck failed: process"'; kill $sp 2>/dev/null
build; mkdir -p /stubs; printf '#!/bin/sh\nexit 2\n' > /stubs/pgrep; chmod 0755 /stubs/pgrep
PATH=/stubs:$PATH t pgrep-error 8 retire-e1-disable 'echo "$out" | grep -q "postcheck failed: process"'; rm -rf /stubs
build; MOCK_SYSTEMCTL_PARTIAL=1 t systemctl-partial-then-fails 3 retire-e1-disable 'grep -q enabled /mock/unit.issue16-supply.timer && [ ! -e /root/issue72/evidence/e1 ]'; unset MOCK_SYSTEMCTL_PARTIAL

# E2.
build; t e1-not-done 3 retire-e2-rbac '[ ! -e /mock/rbac-emptied ]'
build; after_e1; t normal 0 retire-e2-rbac '[ -f /root/issue72/evidence/e2/supply-rbac-before.json ] && grep -q pods /root/issue72/evidence/e2/supply-rbac-before.json'
build; after_e1; MOCK_DIFF_STDERR=1 t diff-error 3 retire-e2-rbac '[ ! -e /mock/rbac-emptied ]'; unset MOCK_DIFF_STDERR
build; after_e1; python3 -I -c "
import json; p='/root/issue72/render/supply-rbac.json'; d=json.load(open(p)); d['items'][0]['rules']=[{'verbs':['get']}]; json.dump(d,open(p,'w'))"
sed -i "s|^RBAC_SHA=.*|RBAC_SHA=$(sha256sum < /root/issue72/render/supply-rbac.json | cut -c1-64)|" $B/retire-e2-rbac.sh; t render-not-empty 3 retire-e2-rbac '[ ! -e /mock/rbac-emptied ]'
build; after_e1; MOCK_APPLY_FAIL=runner-pool-supply t apply-fails 5 retire-e2-rbac; unset MOCK_APPLY_FAIL
build; after_e1; MOCK_SYSTEMCTL_PARTIAL=1 t systemctl-partial-then-fails 3 retire-e2-rbac '[ ! -e /mock/rbac-emptied ] && [ ! -e /mock/applied ] && [ ! -e /root/issue72/evidence/e2 ]'; unset MOCK_SYSTEMCTL_PARTIAL
build; after_e1; MOCK_CANI_NO_RC0=1 t can-i-no-with-rc0 8 retire-e2-rbac 'echo "$out" | grep -q "postcheck failed: can-i"'; unset MOCK_CANI_NO_RC0

# E3.
build; after_e2; t normal 0 retire-e3-files '[ "$(grep -c . /root/issue72/evidence/e3/MANIFEST.sha256)" = 5 ] && [ ! -e /usr/local/lib/anxious-lookout/issue16/supply.sh ] && grep -q not-found /mock/unit.issue16-supply.timer && creds 5'
build; after_e1; t e2-not-done 3 retire-e3-files '[ -e /usr/local/lib/anxious-lookout/issue16/supply.sh ]'
build; after_e2; echo 'DSN=postgresql://x' >> /usr/local/lib/anxious-lookout/issue16/stop-supply.sh; t credential-marker 3 retire-e3-files '[ ! -e /root/issue72/evidence/e3 ]'
build; after_e2; printf 'ExecStart=/usr/local/lib/anxious-lookout/issue16/stop-supply.sh\n' > /etc/systemd/system/issue16-other.service; t other-unit-uses-script 3 retire-e3-files
build; after_e2; mkdir /etc/systemd/system/issue16-supply.service.d; t drop-in 3 retire-e3-files
# Review round 1: consumers outside top-level unit files. Each must refuse with the four files and evidence untouched.
e3intact='[ -e /usr/local/lib/anxious-lookout/issue16/stop-supply.sh ] && [ -e /etc/systemd/system/issue16-supply.service ] && [ ! -e /root/issue72/evidence/e3 ]'
build; after_e2; mkdir /etc/systemd/system/issue16-controller.service.d
printf '[Service]\nExecStartPost=/usr/local/lib/anxious-lookout/issue16/stop-supply.sh\n' > /etc/systemd/system/issue16-controller.service.d/supply.conf
t other-units-drop-in 3 retire-e3-files "$e3intact && echo \"\$out\" | grep -q issue16-controller.service.d/supply.conf"
build; after_e2; mkdir -p /usr/lib/systemd/system; printf '[Service]\nExecStart=/usr/local/lib/anxious-lookout/issue16/supply.sh\n' > /usr/lib/systemd/system/vendor-x.service
t vendor-unit 3 retire-e3-files "$e3intact"; rm -rf /usr/lib/systemd/system
build; after_e2; mkdir -p /opt/units /etc/systemd/system/timers.target.wants; printf 'ExecStart=/usr/local/lib/anxious-lookout/issue16/stop-supply.sh\n' > /opt/units/linked.service
ln -s /opt/units/linked.service /etc/systemd/system/timers.target.wants/linked.service; t linked-unit 3 retire-e3-files "$e3intact"; rm -rf /opt/units
build; after_e2; printf '#!/bin/sh\n/usr/local/lib/anxious-lookout/issue16/stop-supply.sh\n' > /usr/local/lib/anxious-lookout/issue16/controller.sh
t script-calls-it 3 retire-e3-files "$e3intact"
build; after_e2; mkdir -p /etc/systemd/system/multi-user.target.wants; ln -s /nonexistent.service /etc/systemd/system/multi-user.target.wants/gone.service
t dangling-link-skipped 0 retire-e3-files '[ ! -e /usr/local/lib/anxious-lookout/issue16/supply.sh ]'
build; after_e2; MOCK_SYSTEMCTL_PARTIAL=1 t systemctl-partial-then-fails 3 retire-e3-files "$e3intact"; unset MOCK_SYSTEMCTL_PARTIAL
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
# The supply database-url is a copy of the observer login, which stays in use until #61: E4 never reaches the database.
nodb() { [ ! -e /mock/psql.log ]; }
build; after_e3; t normal 0 retire-e4-final 'creds 0 && nodb && [ ! -e /etc/anxious-lookout/issue16/supply ] && [ -f /var/lib/anxious-lookout/issue16-d4/supply/attempts/a1 ] && [ -e /mock/sa-deleted ]'
build; after_e3; echo 'true|3' > /mock/role; t observer-login-in-use-untouched 0 retire-e4-final 'creds 0 && nodb && grep -qx "true|3" /mock/role'
build; after_e3; date -u +%s > /root/issue72/evidence/e1/stopped-at; t too-early 3 retire-e4-final 'creds 5 && nodb && echo "$out" | grep -q "stable period not over"'
# Decision 2 as reviewed: a scheduled, successful refresh between E1 and now, in one unbroken chain of
# auth.json identities from E1's session record (100:150) to the current file (mock default 100:200).
ev() { printf '{"at": %s, "event": "refresh", "kind": "%s", "ok": %s, "before": "%s", "after": "%s"}\n' "$1" "$2" "$3" "$4" "$5"; }
now=$(date -u +%s)
refused='creds 5 && nodb && [ ! -e /mock/sa-deleted ] && echo "$out" | grep -q "no scheduled refresh in the same login session"'
build; after_e3; : > /mock/events; t no-events 3 retire-e4-final "$refused"
build; after_e3; ev $((now-3600)) forced true 100:150 100:200 > /mock/events; t forced-only 3 retire-e4-final "$refused"
build; after_e3; ev $((now-3600)) scheduled false 100:150 100:200 > /mock/events; t scheduled-failed-only 3 retire-e4-final "$refused"
build; after_e3; ev $((now-100000)) scheduled true 100:150 100:200 > /mock/events; t scheduled-before-e1-only 3 retire-e4-final "$refused"
# Review round 2: a success from the session, then a device login (no event) must refuse.
build; after_e3; MOCK_IDENT=300:400 t relogin-after-success 3 retire-e4-final "$refused"; unset MOCK_IDENT
build; after_e3; { ev $((now-7200)) scheduled true 100:150 100:200; ev $((now-1800)) scheduled true 300:350 300:400; } > /mock/events
MOCK_IDENT=300:400 t relogin-then-new-session-refresh 3 retire-e4-final "$refused"; unset MOCK_IDENT
build; after_e3; { ev $((now-3600)) forced true 100:150 100:170; ev $((now-1800)) scheduled true 100:170 100:200; } > /mock/events
t chain-through-forced 0 retire-e4-final 'creds 0 && echo "$out" | grep -q "same login session since E1"'
build; after_e3; st=$(cat /root/issue72/evidence/e1/stopped-at); { ev $st forced true 100:120 100:150; ev $((now-1800)) scheduled true 100:150 100:200; } > /mock/events
t event-in-stop-second-before-record 0 retire-e4-final 'creds 0'
build; after_e3; { ev $((now-3600)) scheduled true 100:150 100:200; ev $((now+3600)) forced true 100:200 100:200; } > /mock/events; t future-event 3 retire-e4-final "$refused"
build; after_e3; { ev $((now-3600)) scheduled true 100:150 100:200; echo '{"at": 1, "event": "refresh", "kind": "scheduled", "ok": true, "before": "1:1", "after": "1:1", "token": "SECRET-x"}'; } > /mock/events
t extra-field 3 retire-e4-final "$refused"
build; after_e3; { ev $((now-3600)) scheduled true 100:150 100:200; echo 'not json'; } > /mock/events; t malformed-line 3 retire-e4-final "$refused"
build; after_e3; MOCK_KFAIL='cat /data/broker-events.jsonl' MOCK_KFAIL_PARTIAL="$(ev $((now-3600)) scheduled true 100:150 100:200)" t events-partial-then-fails 3 retire-e4-final 'creds 5 && echo "$out" | grep -q "event lookup failed"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL
build; after_e3; MOCK_KFAIL='os.lstat' MOCK_KFAIL_PARTIAL='100:200' t identity-partial-then-fails 3 retire-e4-final 'creds 5 && echo "$out" | grep -q "identity lookup failed"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL
build; after_e3; rm /root/issue72/evidence/e1/session; t session-record-missing 3 retire-e4-final 'creds 5 && echo "$out" | grep -q "session record missing"'
build; after_e3; MOCK_SYSTEMCTL_PARTIAL=1 t systemctl-partial-then-fails 3 retire-e4-final 'creds 5 && nodb && [ ! -e /mock/sa-deleted ]'; unset MOCK_SYSTEMCTL_PARTIAL
build; after_e3; rm -rf /root/issue72/evidence/e1; t e1-record-missing 3 retire-e4-final 'creds 5'
build; after_e2; t e3-not-done 3 retire-e4-final 'creds 5'
build; after_e3; echo SECRET-extra > /etc/anxious-lookout/issue16/supply/extra; t unexpected-credential-file 3 retire-e4-final 'creds 6'
build; after_e3; MOCK_SA_DELETE_FAIL=1 t sa-delete-fails 5 retire-e4-final 'creds 5 && nodb'; unset MOCK_SA_DELETE_FAIL
build; after_e3; mkdir -p /stubs; printf '#!/bin/sh\ncase "$*" in *issue16/supply*) printf "f database-url\\nf kube-token\\nf kubeconfig\\n"; exit 1;; esac\nexec /usr/bin/find "$@"\n' > /stubs/find; chmod 0755 /stubs/find
PATH=/stubs:$PATH t credential-scan-fails 3 retire-e4-final 'nodb && [ ! -e /mock/sa-deleted ]'; rm -rf /stubs
build; after_e3; MOCK_KFAIL='--ignore-not-found -o name' t sa-absence-unconfirmed 8 retire-e4-final 'echo "$out" | grep -q "postcheck failed: serviceaccount"'; unset MOCK_KFAIL
# Review round 1 on d23550c: correct-looking stdout followed by a failure status must refuse with nothing changed.
build; after_e3; MOCK_KFAIL='get pod codex-broker-0' MOCK_KFAIL_PARTIAL='Running|true' t broker-pod-partial-then-fails 3 retire-e4-final \
  'creds 5 && nodb && [ ! -e /mock/sa-deleted ] && echo "$out" | grep -q "broker pod lookup failed"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL
build; after_e3; MOCK_KFAIL='get serviceaccount runner-pool-supply -o jsonpath' MOCK_KFAIL_PARTIAL='runner-pool-supply' t sa-partial-then-fails 3 retire-e4-final \
  'creds 5 && nodb && [ ! -e /mock/sa-deleted ] && echo "$out" | grep -q "ServiceAccount lookup failed"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL

echo "passed $pass failed $fail"
[ $fail = 0 ]
