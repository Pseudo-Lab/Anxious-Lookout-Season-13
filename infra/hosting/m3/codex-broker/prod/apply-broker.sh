#!/bin/sh
# #72 B3: first apply of the codex-broker objects and the runner CNP broker rule, from reviewed renders.
# Run as root, once, after review cleared this script's SHA-256 and the @@ values below were filled in.
# Inputs: /root/issue72/render/broker.json (ops.broker_render output) and runner-cnp.json
# (infra/hosting/m3/user-runner-e2e/40-runner-network.json as reviewed). Both hash-pinned.
# rc: 0 applied and verified; 2 placeholders left; 3 precheck refused, nothing applied;
#     5 an apply failed midway (stop, keep state, report); 8 applied but a postcheck failed.
set -u
BROKER_SHA=@@BROKER_JSON_SHA256@@
CNP_SHA=@@RUNNER_CNP_JSON_SHA256@@
RUNNER_IMAGE=@@RUNNER_IMAGE_REF@@
K='/usr/local/bin/k3s kubectl'
R=/root/issue72/render
EV=/root/issue72/evidence/b3
case "$BROKER_SHA$CNP_SHA$RUNNER_IMAGE" in *@@*) echo 'placeholders not filled'; exit 2;; esac

tmp=$(mktemp -d /run/issue72-b3.XXXXXX) || { echo 'precheck: no work directory'; exit 3; }
trap 'rm -rf "$tmp"' EXIT
refuse() { echo "precheck: $1; nothing applied"; exit 3; }

# 1. Pinned, root-owned regular files.
for f in broker.json runner-cnp.json; do
  [ -f "$R/$f" ] && [ ! -L "$R/$f" ] && [ "$(stat -c %U "$R/$f")" = root ] || refuse "$f is not a root-owned regular file"
done
printf '%s  %s\n%s  %s\n' "$BROKER_SHA" "$R/broker.json" "$CNP_SHA" "$R/runner-cnp.json" > "$tmp/sums"
sha256sum -c --quiet "$tmp/sums" >/dev/null 2>&1 || refuse 'render hash mismatch'

# 2. Shape: exactly the nine broker objects, the reviewed image, one replica, no mock override;
#    the runner CNP must equal the live one plus only the codex-broker egress rule.
timeout 30 $K -n codex-runner get cnp runner-callback-draft -o json > "$tmp/live-cnp.json" 2>/dev/null || refuse 'live runner CNP lookup failed'
python3 -I - "$R/broker.json" "$R/runner-cnp.json" "$tmp/live-cnp.json" "$RUNNER_IMAGE" "$tmp" <<'PY' || refuse 'render shape not as reviewed'
import json, sys
broker, cnp, live, image, out = sys.argv[1:]
items = json.load(open(broker))['items']
order = [('Namespace', 'codex-broker'), ('ServiceAccount', 'codex-broker'),
         ('ClusterRole', 'codex-broker-tokenreview'), ('ClusterRoleBinding', 'codex-broker-tokenreview'),
         ('Role', 'codex-broker-runner-pods'), ('RoleBinding', 'codex-broker-runner-pods'),
         ('CiliumNetworkPolicy', 'codex-broker'), ('Service', 'codex-broker'), ('StatefulSet', 'codex-broker')]
found = {(i['kind'], i['metadata']['name']): i for i in items}
assert len(items) == len(order) and set(found) == set(order)
sts = found['StatefulSet', 'codex-broker']['spec']
c, = sts['template']['spec']['containers']
assert sts['replicas'] == 1 and c['image'] == image
assert not any(e['name'] == 'CODEX_REFRESH_TOKEN_URL_OVERRIDE' for e in c['env'])
for n, key in enumerate(order, 1):  # Apply order: the broker CNP before the Service and the workload.
    json.dump(found[key], open(f'{out}/{n:02d}.json', 'w'))
# Objects outside the absent namespace can be server-diffed before it exists.
json.dump({'apiVersion': 'v1', 'kind': 'List', 'items': [found[k] for k in order
           if k[0] == 'Namespace' or found[k]['metadata'].get('namespace') != 'codex-broker']}, open(f'{out}/outside.json', 'w'))
want, have = json.load(open(cnp)), json.load(open(live))
assert want['kind'] == 'CiliumNetworkPolicy' and want['metadata'] == {'name': 'runner-callback-draft', 'namespace': 'codex-runner'}
rule = {'toEndpoints': [{'matchLabels': {'k8s:io.kubernetes.pod.namespace': 'codex-broker', 'k8s:app': 'codex-broker'}}],
        'toPorts': [{'ports': [{'port': '8080', 'protocol': 'TCP'}]}]}
spec = dict(want['spec']); assert spec['egress'][0] == rule
spec['egress'] = spec['egress'][1:]
assert spec == have['spec']
# Rollback copy: the live object without server fields, so a plain apply restores it.
json.dump({'apiVersion': have['apiVersion'], 'kind': have['kind'],
           'metadata': {'name': 'runner-callback-draft', 'namespace': 'codex-runner'}, 'spec': have['spec']},
          open(f'{out}/runner-cnp-before.json', 'w'))
PY

# 3. Fresh install only, Cilium snapshot, and diff gates. kubectl diff also exits 1 on most errors,
#    so a gate needs both the rc and an empty stderr. Objects inside the absent namespace cannot be
#    server-diffed yet; the absent namespace proves they are new.
n=$(timeout 30 $K get ns codex-broker --ignore-not-found -o name 2>/dev/null) || refuse 'namespace lookup failed'
[ -z "$n" ] || refuse 'namespace codex-broker exists'
cil() { j=$(timeout 30 $K -n kube-system get configmap cilium-config -o json 2>/dev/null) || return 1
  printf '%s' "$j" | python3 -I -c 'import hashlib,json,sys; print(hashlib.sha256(json.dumps(json.load(sys.stdin)["data"],sort_keys=True).encode()).hexdigest())'; }
cilium_before=$(cil) && [ -n "$cilium_before" ] || refuse 'cilium-config lookup failed'
diffrc() { timeout 60 $K diff -f "$1" > /dev/null 2> "$tmp/diff.err"; d=$?; [ -s "$tmp/diff.err" ] && d=err; echo $d; }
d=$(diffrc "$tmp/outside.json"); [ "$d" = 1 ] || refuse "broker diff outside the namespace: $d (want 1)"
d=$(diffrc "$R/runner-cnp.json"); [ "$d" = 1 ] || refuse "runner CNP diff: $d (want 1)"

# 4. Evidence: the rollback copy of the live runner CNP, no clobber.
install -d -m 0700 "$EV" && [ ! -e "$EV/runner-cnp-before.json" ] && cp "$tmp/runner-cnp-before.json" "$EV/" \
  && chmod 0600 "$EV/runner-cnp-before.json" && cmp -s "$tmp/runner-cnp-before.json" "$EV/runner-cnp-before.json" \
  || refuse 'evidence copy failed or already present'
echo 'precheck ok: renders pinned and shaped, live runner CNP matches, namespace absent, diffs pending'

# 5. Apply in order; stop at the first failure.
rc=0
for f in "$tmp"/0*.json "$R/runner-cnp.json"; do
  timeout 60 $K apply -f "$f" > /dev/null 2>&1 || { echo "apply failed at $(basename "$f"); earlier objects stay applied"; rc=5; break; }
  [ "$f" = "$tmp/09.json" ] && { timeout 300 $K -n codex-broker rollout status statefulset/codex-broker --timeout=300s > /dev/null 2>&1 \
    || { echo 'apply: broker StatefulSet not ready within 300 s'; rc=5; break; }; }
  rc=0
done
if [ "$rc" = 0 ]; then
  echo 'apply ok'
  # 6. Postcheck.
  bad=
  [ "$(diffrc "$R/broker.json")" = 0 ] || bad="$bad broker-diff"
  [ "$(diffrc "$R/runner-cnp.json")" = 0 ] || bad="$bad runner-cnp-diff"
  p=$(timeout 30 $K -n codex-broker get pod codex-broker-0 -o jsonpath='{.status.phase}|{.status.containerStatuses[0].ready}|{.status.containerStatuses[0].restartCount}|{.status.containerStatuses[0].imageID}' 2>/dev/null)
  case "$p" in Running\|true\|0\|*) ;; *) bad="$bad broker-pod";; esac
  v=$(timeout 30 $K -n codex-broker get cnp codex-broker -o jsonpath='{.status.conditions[?(@.type=="Valid")].status}' 2>/dev/null)
  [ "$v" = True ] || bad="$bad cnp-valid"
  [ "$(cil)" = "$cilium_before" ] || bad="$bad cilium-config-changed"
  echo "broker pod: $p"
  [ -z "$bad" ] || { echo "postcheck failed:$bad"; rc=8; }
fi
echo "rollback: $K -n codex-broker scale statefulset codex-broker --replicas=0; $K delete -f $R/broker.json; $K apply -f $EV/runner-cnp-before.json"
echo "result rc=$rc"
exit $rc
