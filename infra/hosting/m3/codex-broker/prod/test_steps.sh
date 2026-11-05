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
printf '#!/bin/sh\nexec python3 %s/mock/docker.py "$@"\n' $P > /usr/bin/docker
chmod 0755 /usr/local/bin/k3s /usr/bin/systemctl /usr/bin/docker
# B5/D: A's images (synthetic) and the production baselines from the node reads.
REL=$(printf 'f%.0s' $(seq 40))
APIIMG=docker.io/library/anxious-s13-issue72-runtime@sha256:$(printf 'a%.0s' $(seq 64))
OLDAPI=docker.io/library/anxious-s13-issue36-runtime@sha256:7998b73da0f267e59b91da36a52ba571992365fd0f4056b59ce9640295978e67
OLDRUNNER=docker.io/library/anxious-s13-issue36-runner@sha256:0efaa9f741e04f1fc9d92544932139d39834de4c483ba1e73d3ec8aed3373f01
OLDEXEC=docker.io/library/anxious-s13-issue23-shell-executor@sha256:bae153febf222fbe2919cc50afd7c1564730b77bd0a45fbb036f567e5035d958
WID=sha256:$(printf '9%.0s' $(seq 64))
OLDWID=sha256:1c3ce2683832a187ef207abe4daa7989589a996cde8214ce374237fa94ea81e6
EXEC=docker.io/library/anxious-s13-issue72-shell-executor@sha256:$(printf 'd%.0s' $(seq 64))
OWN=00000000-0000-4000-8000-000000000048

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
  # B5/D fixtures: stopped workers on the old worker image, the live api Deployment, a stopped v2 owner.
  for r in controller observer; do
    echo 'loaded inactive enabled' > /mock/unit.issue16-$r.timer; echo 'loaded inactive static' > /mock/unit.issue16-$r.service
    printf '#!/bin/sh\nset -eu\nexec docker run --rm --pull=never --network=host --entrypoint=python %s /rollout/worker.py %s\n' \
      "$OLDWID" $r > /usr/local/lib/anxious-lookout/issue16/$r.sh
    chmod 0700 /usr/local/lib/anxious-lookout/issue16/$r.sh
  done
  printf '%s %s\n%s d890447f385b38614a4dfdef752647b65e678e5d\n' "$WID" "$REL" "$OLDWID" > /mock/docker-images
  printf '%s\n%s\n%s\n' "$APIIMG" "$IMG" "$EXEC" > /mock/ctr-images
  # B5b fixture: the controller inputs as the node has them (#72 window record), on the pre-#72 images.
  mkdir -p /etc/anxious-lookout/issue16/controller
  printf '{\n  "api": "https://127.0.0.1:6443",\n  "namespace": "codex-runner",\n  "namespaceUid": "415c3924-0000-4000-8000-000000000001",\n  "podCIDR": "10.42.0.0/16",\n  "caFile": "/private/ca.crt",\n  "kubeTokenFile": "/private/kube-token",\n  "hostRoot": "/var/lib/anxious-lookout/user-runners",\n  "node": "anxious-lookout",\n  "providerAccountId": "synthetic-account",\n  "image": "%s",\n  "callback": "http://api.m2-hosting.svc.cluster.local:8080/api/internal/research/tools",\n  "executor": {\n    "image": "%s",\n    "wallSeconds": 30,\n    "outputBytes": 65536\n  }\n}\n' \
    "$OLDRUNNER" "$OLDEXEC" > /etc/anxious-lookout/issue16/controller/inputs.json
  chmod 0600 /etc/anxious-lookout/issue16/controller/inputs.json
  printf '{"metadata":{"name":"api","uid":"dep-uid","resourceVersion":"41"},"spec":{"replicas":1,"template":{"spec":{"containers":[{"name":"api","image":"%s"}],"initContainers":[{"name":"runner-pool-input","image":"%s"}]}}}}' \
    "$OLDAPI" "$OLDAPI" > /mock/api-deployment.json
  install -d -m 0700 /root/issue72/d /root/issue72/d/private
  ( umask 077; echo 'SECRET-dsn' > /root/issue72/d/private/admin-dsn )
  echo '11111111-0000-4000-8000-000000000048|idle_stopped|3|7|-' > /mock/d-registry
  printf '{"version":2,"image":"docker.io/library/anxious-s13-issue28-runner@sha256:%s","executor":{"image":"docker.io/library/anxious-s13-issue23-shell-executor@sha256:%s","wallSeconds":30,"outputBytes":65536},"identity":{"owner":"%s"}}' \
    "$(printf 'c%.0s' $(seq 64))" "$(printf 'e%.0s' $(seq 64))" "$OWN" > /mock/d-plan.json
  for s in inventory apply-broker check-login retire-e1-disable retire-e2-rbac retire-e3-files retire-e4-final b5-release b5b-worker-inputs d-binding-move; do
    sed -e "s|@@STORAGE_CLASS@@|local-path|" -e "s|@@ACCESS_MODE@@|ReadWriteOnce|" \
        -e "s|@@RUNNER_IMAGE_REF@@|$IMG|" -e "s|@@RUNNER_IMAGE_ID@@|$DIG|" -e "s|@@RELEASE_SHA@@|$REL|" \
        -e "s|@@API_IMAGE_REF@@|$APIIMG|" -e "s|@@PROVISIONER_IMAGE_ID@@|$WID|" -e "s|@@EXECUTOR_IMAGE_REF@@|$EXEC|" $P/$s.sh > $B/$s.sh
  done
}
# B5's window: only the exempt #66 Pod and a Failed one remain.
b5_window() { printf 'runner-3cb453bfd87040dcb7e13a58aa0cbaf2|Running|old@sha256:%s\nrunner-gone|Failed|old@sha256:c\n' "$(printf 'c%.0s' $(seq 64))" > /mock/runners; }
# D's precondition is a completed B5: run the real script, which must end rc 0.
after_b5() { b5_window; sh $B/b5-release.sh > /dev/null 2>&1 || { fail=$((fail+1)); echo 'FAIL after_b5: B5 did not complete'; }; }
after_e1() { echo 'loaded inactive disabled' > /mock/unit.issue16-supply.timer; echo 'loaded inactive static' > /mock/unit.issue16-supply.service; }
after_e2() { after_e1; touch /mock/rbac-emptied; }
after_e3() { after_e2; rm -f /etc/systemd/system/issue16-supply.* /usr/local/lib/anxious-lookout/issue16/supply.sh /usr/local/lib/anxious-lookout/issue16/stop-supply.sh
             echo 'not-found inactive -' > /mock/unit.issue16-supply.timer; echo 'not-found inactive -' > /mock/unit.issue16-supply.service
             install -d -m 0700 /root/issue72/evidence/e1; echo $(( $(date -u +%s) - 90000 )) > /root/issue72/evidence/e1/stopped-at
             echo 100:150 > /root/issue72/evidence/e1/session; }

pass=0 fail=0
# t name want script [check [step...]] -- environment assignments come before t (or inside a step).
# Each step is a shell fragment with $S = the script under test (default 'sh $S'); rc and $out come from the
# last step, and no step's output may carry a SECRET marker or reach an unhandled stub.
t() {
  name=$1 want=$2 script=$3 check=${4:-true}
  [ $# -ge 4 ] && shift 4 || shift $#
  S=$B/$script.sh
  [ $# -gt 0 ] || set -- 'sh $S'
  all=
  for step; do out=$(eval "$step" 2>&1); rc=$?; all="$all$out"; done
  ok=ok
  [ $rc = "$want" ] || ok=FAIL
  echo "$all" | grep -q SECRET && ok=FAIL
  echo "$all" | grep -q 'stub: unhandled' && ok=FAIL
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
build; touch /mock/rbac-emptied; t emptied-rbac-is-present 0 inventory 'echo "$out" | grep -q "^rbac.supply.role runner-pool-supply|null$"'
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
# #74: the default fixture is production's 2700 (setgid inherited from the fsGroup'd volume root); plain 0700 passes too.
build; MOCK_HOME_STAT='700 10001' t home-mode-plain 0 check-login; unset MOCK_HOME_STAT
build; MOCK_HOME_STAT='2700 10002' t home-owner 3 check-login; unset MOCK_HOME_STAT
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

# B5 b5-release.
L=/usr/local/lib/anxious-lookout/issue16
old_api() { grep -q issue36 /mock/api-deployment.json; }
untouched="old_api && grep -q $OLDWID $L/controller.sh && grep -q $OLDWID $L/observer.sh && [ ! -e /root/issue72/evidence/b5 ]"
build; b5_window; t normal 0 b5-release "! old_api && [ \"\$(grep -o 'issue72-runtime' /mock/api-deployment.json | wc -l)\" = 2 ] \
  && grep -q $WID $L/controller.sh && grep -q $WID $L/observer.sh && ! grep -q $OLDWID $L/controller.sh \
  && [ \"\$(stat -c %a $L/controller.sh)\" = 700 ] && [ -f /root/issue72/evidence/b5/controller.sh ] && grep -q $OLDWID /root/issue72/evidence/b5/observer.sh \
  && (cd /root/issue72/evidence/b5 && sha256sum -c --quiet MANIFEST.sha256) && grep -q 'MODES' /root/issue72/evidence/b5/MANIFEST.sha256 \
  && [ \"\$(grep -c . /root/issue72/evidence/b5/MODES)\" = 2 ] && [ -f /root/issue72/evidence/b5/done ] && echo \"\$out\" | grep -q 'exempt (decision 3): runner-3cb453bfd87040dcb7e13a58aa0cbaf2'"
build; b5_window; sed -i 's/^RELEASE_SHA=.*/RELEASE_SHA=@@X@@/' $B/b5-release.sh; t placeholders 2 b5-release "$untouched"
build; b5_window; echo 'loaded active static' > /mock/unit.issue16-controller.service; t controller-running 3 b5-release "$untouched"
build; b5_window; echo 'loaded active enabled' > /mock/unit.issue16-observer.timer; t observer-timer-running 3 b5-release "$untouched"
build; b5_window; MOCK_SYSTEMCTL_PARTIAL=1 t systemctl-partial-then-fails 3 b5-release "$untouched"; unset MOCK_SYSTEMCTL_PARTIAL
build; t live-runner-pod-59-gate 3 b5-release "$untouched && echo \"\$out\" | grep -q 'runner Pods still live (#59 gate): runner-a'"
build; b5_window; MOCK_KFAIL='status.phase!=Failed' t runner-pod-lookup-fails 3 b5-release "$untouched"; unset MOCK_KFAIL
build; b5_window; sed -i "/^$WID /d" /mock/docker-images; t worker-image-missing 3 b5-release "$untouched"
build; b5_window; sed -i "s|^$WID .*|$WID 0000000000000000000000000000000000000000|" /mock/docker-images; t worker-image-other-release 3 b5-release "$untouched"
build; b5_window; : > /mock/ctr-images; t api-image-not-imported 3 b5-release "$untouched"
build; b5_window; sed -i 's/"name":"api","image"/"name":"api","image":"x","imageX"/' /mock/api-deployment.json; t api-deployment-image-differs 3 b5-release "grep -q $OLDWID $L/controller.sh"
build; b5_window; sed -i 's/}\],"initContainers"/},{"name":"sidecar","image":"y"}],"initContainers"/' /mock/api-deployment.json; t api-deployment-extra-container 3 b5-release "$untouched"
build; b5_window; MOCK_KFAIL='get deployment api -o json' MOCK_KFAIL_PARTIAL="$(cat /mock/api-deployment.json)" t deployment-partial-then-fails 3 b5-release "$untouched"; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL
build; b5_window; echo "# $OLDWID" >> $L/observer.sh; t script-names-old-twice 3 b5-release "old_api && grep -q $OLDWID $L/controller.sh"
build; b5_window; install -d -m 0700 /root/issue72/evidence/b5; t evidence-exists 3 b5-release "old_api && grep -q $OLDWID $L/controller.sh"
# Review early findings on 27be753: an evidence copy must equal its source (content, mode, owner) and the manifest
# must verify before any change; otherwise rc 6 with scripts and api untouched.
unchanged6="old_api && grep -q $OLDWID $L/controller.sh && grep -q $OLDWID $L/observer.sh && [ ! -e /root/issue72/evidence/b5/done ]"
build; b5_window; mkdir -p /stubs; printf '#!/bin/sh\n/bin/cp "$@" || exit $?\nfor l; do :; done\ncase "$l" in */evidence/b5/controller.sh) : > "$l";; esac\n' > /stubs/cp; chmod 0755 /stubs/cp
PATH=/stubs:$PATH t evidence-copy-truncated 6 b5-release "$unchanged6 && echo \"\$out\" | grep -q 'evidence copy failed at controller.sh'"; rm -rf /stubs
build; b5_window; mkdir -p /stubs; printf '#!/bin/sh\n/bin/cp "$@" || exit $?\nfor l; do :; done\ncase "$l" in */evidence/b5/observer.sh) chmod 0644 "$l";; esac\n' > /stubs/cp; chmod 0755 /stubs/cp
PATH=/stubs:$PATH t evidence-copy-wrong-mode 6 b5-release "$unchanged6"; rm -rf /stubs
build; b5_window; mkdir -p /stubs; printf '#!/bin/sh\ncase "$1" in -c) exit 1;; esac\nexec /usr/bin/sha256sum "$@"\n' > /stubs/sha256sum; chmod 0755 /stubs/sha256sum
PATH=/stubs:$PATH t evidence-manifest-unverified 6 b5-release "$unchanged6 && echo \"\$out\" | grep -q 'evidence manifest failed'"; rm -rf /stubs
build; b5_window; MOCK_PATCH_FAIL=1 t patch-fails 5 b5-release "old_api && grep -q $WID $L/controller.sh && echo \"\$out\" | grep -q 'rollback:'"; unset MOCK_PATCH_FAIL
build; b5_window; MOCK_PATCH_RV_BUMP=1 t deployment-changed-after-read 5 b5-release 'old_api && echo "$out" | grep -q "api deployment patch failed"'; unset MOCK_PATCH_RV_BUMP
build; b5_window; MOCK_ROLLOUT_FAIL=1 t rollout-fails 5 b5-release; unset MOCK_ROLLOUT_FAIL
build; b5_window; MOCK_API_NOT_READY=1 t api-pod-not-ready 8 b5-release 'echo "$out" | grep -q "postcheck failed: api-pods"'; unset MOCK_API_NOT_READY
# #74 (production 2026-10-31 09:30Z): the previous ReplicaSet's Pod is still listed as Running/ready on the old image,
# deletionTimestamp set, when rollout status returns. It is terminating, not a failed release.
build; b5_window; MOCK_API_TERMINATING=1 t api-old-pod-terminating 0 b5-release '[ -f /root/issue72/evidence/b5/done ] && echo "$out" | grep -q "terminating: api-0"'; unset MOCK_API_TERMINATING
build; b5_window; MOCK_API_TERMINATING=1 MOCK_API_NOT_READY=1 t api-new-pod-not-ready-old-terminating 8 b5-release 'echo "$out" | grep -q "postcheck failed: api-pods"'; unset MOCK_API_TERMINATING MOCK_API_NOT_READY
build; b5_window; MOCK_API_RELEASE=$(printf '0%.0s' $(seq 40)) t api-other-release 8 b5-release 'echo "$out" | grep -q "postcheck failed: api-release"'; unset MOCK_API_RELEASE
build; b5_window; MOCK_KFAIL='app=hosting-api' MOCK_KFAIL_PARTIAL="api-1|Running|true|$APIIMG" t api-pods-partial-then-fails 8 b5-release 'echo "$out" | grep -q "postcheck failed: api-pods"'; unset MOCK_KFAIL MOCK_KFAIL_PARTIAL

# B5b b5b-worker-inputs (#74): the controller's first-binding images.
CI=/etc/anxious-lookout/issue16/controller/inputs.json
ci_old() { grep -q "$OLDRUNNER" $CI && grep -q "$OLDEXEC" $CI && [ ! -e /root/issue72/evidence/b5b ]; }
ci_new() { python3 -I -c '
import json, sys
v = json.load(open(sys.argv[1])); assert v["image"] == sys.argv[2] and v["executor"]["image"] == sys.argv[3]
assert v["executor"]["wallSeconds"] == 30 and v["callback"].startswith("http://api.") and v["providerAccountId"] == "synthetic-account"' $CI "$IMG" "$EXEC"; }
build; t normal 0 b5b-worker-inputs "ci_new && [ \"\$(stat -c %a $CI)\" = 600 ] && grep -q $OLDRUNNER /root/issue72/evidence/b5b/inputs.json \
  && (cd /root/issue72/evidence/b5b && sha256sum -c --quiet MANIFEST.sha256) && [ \"\$(grep -c . /root/issue72/evidence/b5b/MODES)\" = 1 ] && [ ! -e $CI.b5b ]"
build; chmod 0640 $CI; t normal-keeps-mode 0 b5b-worker-inputs "ci_new && [ \"\$(stat -c %a $CI)\" = 640 ]"
build; sed -i 's/^RUNNER_IMAGE=.*/RUNNER_IMAGE=@@X@@/' $B/b5b-worker-inputs.sh; t placeholders 2 b5b-worker-inputs ci_old
build; echo 'loaded active static' > /mock/unit.issue16-controller.service; t controller-running 3 b5b-worker-inputs ci_old
build; echo 'loaded active enabled' > /mock/unit.issue16-observer.timer; t observer-timer-running 3 b5b-worker-inputs ci_old
build; MOCK_SYSTEMCTL_PARTIAL=1 t systemctl-partial-then-fails 3 b5b-worker-inputs ci_old; unset MOCK_SYSTEMCTL_PARTIAL
build; echo "$APIIMG" > /mock/ctr-images; t images-not-imported 3 b5b-worker-inputs "ci_old && echo \"\$out\" | grep -q 'image not in containerd'"
build; sed -i "s|$OLDRUNNER|docker.io/library/other@sha256:$(printf '1%.0s' $(seq 64))|" $CI; t baseline-differs 3 b5b-worker-inputs '[ ! -e /root/issue72/evidence/b5b ]'
build; sed -i "s|\"callback\"|\"note\": \"$OLDEXEC\", \"callback\"|" $CI; t old-image-named-twice 3 b5b-worker-inputs ci_old
build; sed -i "s|$OLDRUNNER|$IMG|" $CI; t already-moved 3 b5b-worker-inputs '[ ! -e /root/issue72/evidence/b5b ]'
build; mv $CI $CI.real; ln -s $CI.real $CI; t symlink 3 b5b-worker-inputs '[ ! -e /root/issue72/evidence/b5b ]'
build; install -d -m 0700 /root/issue72/evidence/b5b; t evidence-exists 3 b5b-worker-inputs "grep -q $OLDRUNNER $CI"
build; mkdir -p /stubs; printf '#!/bin/sh\n/bin/cp "$@" || exit $?\nfor l; do :; done\ncase "$l" in */evidence/b5b/inputs.json) : > "$l";; esac\n' > /stubs/cp; chmod 0755 /stubs/cp
PATH=/stubs:$PATH t evidence-copy-truncated 6 b5b-worker-inputs "grep -q $OLDRUNNER $CI && echo \"\$out\" | grep -q 'evidence copy failed'"; rm -rf /stubs
build; mkdir -p /stubs; printf '#!/bin/sh\ncase "$*" in *.b5b*) exit 1;; esac\nexec /bin/chmod "$@"\n' > /stubs/chmod; chmod 0755 /stubs/chmod
PATH=/stubs:$PATH t update-fails-midway 5 b5b-worker-inputs "grep -q $OLDRUNNER $CI && [ ! -e $CI.b5b ] && echo \"\$out\" | grep -q 'rollback:'"; rm -rf /stubs

# D d-binding-move.
OD=/root/issue72/d/$OWN
on_old() { grep -q issue28-runner /mock/d-plan.json && grep -q issue23-shell-executor /mock/d-plan.json; }
R="sh \$S report $OWN"; A="sh \$S apply $OWN"; refusedD="on_old && [ ! -e $OD ]"
build; after_b5; t report 0 d-binding-move "on_old && [ -f $OD/report/binding-image.json ] && [ \"\$(stat -c %a $OD/inputs/binding-image.json)\" = 600 ] \
  && grep -q '\"executorImage\": \"$EXEC\"' $OD/inputs/binding-image.json && echo \"\$out\" | grep -q 'oldPlanSha256'" "$R"
build; after_b5; t report-then-apply 0 d-binding-move "grep -q issue72-shell-executor /mock/d-plan.json && grep -q '\"image\": \"$IMG\"' /mock/d-plan.json && [ -f $OD/applied ] \
  && grep -q wallSeconds /mock/d-plan.json" "$R" "$A"
build; after_b5; t apply-without-report 3 d-binding-move on_old "$A"
build; after_b5; t apply-twice 3 d-binding-move "echo \"\$out\" | grep -q 'already applied'" "$R" "$A" "$A"
build; after_b5; t exempt-owner 3 d-binding-move "on_old && [ ! -e /root/issue72/d/3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2 ]" 'sh $S report 3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2'
build; b5_window; t b5-not-done 3 d-binding-move "$refusedD && echo \"\$out\" | grep -q 'B5 not completed'" "$R"
# Review early findings on 27be753: a B5 that stopped midway or failed its postcheck leaves no completion record,
# and a completed B5 whose live state was changed back is refused too. No report, no plan change.
B5="sh $B/b5-release.sh"
build; b5_window; t b5-patch-failed 3 d-binding-move "$refusedD && grep -q $WID $L/controller.sh && echo \"\$out\" | grep -q 'B5 not completed'" "MOCK_PATCH_FAIL=1 $B5" "$R"
build; b5_window; t b5-rollout-failed 3 d-binding-move "$refusedD && echo \"\$out\" | grep -q 'B5 not completed'" "MOCK_ROLLOUT_FAIL=1 $B5" "$R"
build; b5_window; t b5-postcheck-failed 3 d-binding-move "$refusedD && echo \"\$out\" | grep -q 'B5 not completed'" "MOCK_API_NOT_READY=1 $B5" "$R"
build; after_b5; sed -i 's/issue72-runtime/issue36-runtime/' /mock/api-deployment.json
t api-reverted-after-b5 3 d-binding-move "$refusedD && echo \"\$out\" | grep -q 'api deployment is not on the new image'" "$R"
build; after_b5; sed -i "s|$WID|$OLDWID|" $L/observer.sh; t script-reverted-after-b5 3 d-binding-move "$refusedD" "$R"
build; after_b5; t api-other-release 3 d-binding-move "$refusedD && echo \"\$out\" | grep -q 'not the reviewed release'" "MOCK_API_RELEASE=$(printf '0%.0s' $(seq 40)) $R"
build; after_b5; t deployment-partial-then-fails 3 d-binding-move "$refusedD && echo \"\$out\" | grep -q 'api deployment lookup failed'" \
  "MOCK_KFAIL='get deployment api -o jsonpath' MOCK_KFAIL_PARTIAL='$APIIMG|$APIIMG' $R"
build; after_b5; echo 'loaded activating static' > /mock/unit.issue16-controller.service; t controller-running 3 d-binding-move "$refusedD" "$R"
build; after_b5; echo "runner-$(echo $OWN | tr -d -)" > /mock/d-pods; t pod-exists 3 d-binding-move "$refusedD" "$R"
build; after_b5; rm /root/issue72/d/private/admin-dsn; t dsn-missing 3 d-binding-move "$refusedD" "$R"
build; after_b5; chmod 0644 /root/issue72/d/private/admin-dsn; t dsn-mode-0644 3 d-binding-move "$refusedD" "$R"
build; after_b5; ln /root/issue72/d/private/admin-dsn /root/issue72/d/dsn-link; t dsn-hard-linked 3 d-binding-move "$refusedD" "$R"
build; after_b5; echo '11111111-0000-4000-8000-000000000048|runner_unreachable|3|7|22222222-0000-4000-8000-000000000001' > /mock/d-registry
t not-idle-stopped 3 d-binding-move "$refusedD" "$R"
build; after_b5; t registry-partial-then-fails 3 d-binding-move "$refusedD" \
  "MOCK_KFAIL='from runner_runtime.registry' MOCK_KFAIL_PARTIAL='11111111-0000-4000-8000-000000000048|idle_stopped|3|7|-' $R"
build; after_b5; sed -i 's/"version":2,//' /mock/d-plan.json; t v1-plan 3 d-binding-move "$refusedD" "$R"
build; after_b5; sed -i "s|docker.io/library/anxious-s13-issue28-runner@sha256:[c]*|$IMG|; s|docker.io/library/anxious-s13-issue23-shell-executor@sha256:[e]*|$EXEC|" /mock/d-plan.json
t already-moved 3 d-binding-move "[ ! -e $OD ]" "$R"
build; after_b5; t tool-refuses-report 3 d-binding-move "on_old && [ ! -e $OD/report/binding-image.json ]" "MOCK_TOOL_FAIL=1 $R"
build; after_b5; t fence-moved-after-report 3 d-binding-move "on_old && echo \"\$out\" | grep -q 'registry fence or images changed'" "$R" \
  "sed -i 's/|7|/|8|/' /mock/d-registry" "$A"
build; after_b5; t ambiguous-apply 5 d-binding-move "[ ! -e $OD/applied ] && echo \"\$out\" | grep -q 'never repeat'" "$R" "MOCK_TOOL_AMBIGUOUS=1 $A"
build; after_b5; t postcheck-plan-lookup-fails 8 d-binding-move "echo \"\$out\" | grep -q 'postcheck failed: plan-images'" "$R" \
  "MOCK_KFAIL='plan::jsonb-' MOCK_KFAIL_PARTIAL='$IMG|$EXEC' $A"
build; after_b5; t close 0 d-binding-move "[ ! -e /root/issue72/d/private/admin-dsn ] && [ -d /root/issue72/d/private ]" 'sh $S close'
build; after_b5; rm /root/issue72/d/private/admin-dsn; t close-when-absent 0 d-binding-move "echo \"\$out\" | grep -q 'already absent'" 'sh $S close'

echo "passed $pass failed $fail"
[ $fail = 0 ]
