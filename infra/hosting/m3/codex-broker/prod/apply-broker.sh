#!/bin/sh
# #72 B3: first apply of the codex-broker objects and the runner CNP broker rule.
# Run as root on the node, once, after review cleared this script's SHA-256 with the @@ values filled in.
# The broker objects are rendered here, on the node, by the pinned renderer, so the workspace id in
# values.json never leaves the node. Inputs in /root/issue72/render/ (root-owned regular files):
#   broker_render.py  git show <sha>:backend/ops/broker_render.py           (pinned below)
#   runner-cnp.json   git show <sha>:infra/hosting/m3/user-runner-e2e/40-runner-network.json (pinned)
#   values.json       written on the node; every field except accountId must equal the constants
# rc: 0 applied and verified; 2 placeholders left; 3 precheck refused, nothing applied;
#     5 an apply failed midway (stop, keep state, report); 8 applied but a postcheck failed.
set -u
RENDERER_SHA=5a0bae425e0eb06c43fecd69cec51759d54e91b841a184293735bb4ceb24459f  # backend/ops/broker_render.py since d1428e1
CNP_SHA=c969169d995b97b1cae5a36eb32e92fa7d53ce698b69292b3157cf210c5bb5ec       # 40-runner-network.json since d1428e1
RUNNER_IMAGE=@@RUNNER_IMAGE_REF@@   # <name>@sha256:<64 hex>, the imported e9337f7 runner image
STORAGE_CLASS=local-path            # decision 1: stock class (Delete); production has no CSI class
ACCESS_MODE=ReadWriteOnce           # decision 1: ReadWriteOncePod needs CSI; replicas 1 keeps the single writer
K='/usr/local/bin/k3s kubectl'
R=/root/issue72/render
EV=/root/issue72/evidence/b3
case "$RUNNER_IMAGE$STORAGE_CLASS$ACCESS_MODE" in *@@*) echo 'placeholders not filled'; exit 2;; esac

tmp=$(mktemp -d /run/issue72-b3.XXXXXX) || { echo 'precheck: no work directory'; exit 3; }
trap 'rm -rf "$tmp"' EXIT
refuse() { echo "precheck: $1; nothing applied"; exit 3; }

# 1. Pinned, root-owned regular files.
for f in broker_render.py runner-cnp.json values.json; do
  [ -f "$R/$f" ] && [ ! -L "$R/$f" ] && [ "$(stat -c %U "$R/$f")" = root ] || refuse "$f is not a root-owned regular file"
done
printf '%s  %s\n%s  %s\n' "$RENDERER_SHA" "$R/broker_render.py" "$CNP_SHA" "$R/runner-cnp.json" > "$tmp/sums"
sha256sum -c --quiet "$tmp/sums" >/dev/null 2>&1 || refuse 'renderer or runner CNP hash mismatch'

# 2. values.json: the reviewed image, storage and world egress; accountId only by its format (never printed).
python3 -I - "$R/values.json" "$RUNNER_IMAGE" "$STORAGE_CLASS" "$ACCESS_MODE" <<'PY' 2>/dev/null || refuse 'values.json not as reviewed'
import json, re, sys
path, image, storage, mode = sys.argv[1:]
v = json.load(open(path))
assert set(v) == {'image', 'accountId', 'storageClass', 'accessMode', 'egress'}
assert v['image'] == image and v['storageClass'] == storage and v['accessMode'] == mode and v['egress'] == 'world'
assert isinstance(v['accountId'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', v['accountId'])
PY
python3 -I "$R/broker_render.py" --values "$R/values.json" > "$tmp/broker.json" 2>/dev/null || refuse 'render refused'

# 3. Shape: exactly the nine broker objects, the reviewed image, one replica, no mock override;
#    the runner CNP must equal the live one plus only the codex-broker egress rule.
timeout 30 $K -n codex-runner get cnp runner-callback-draft -o json > "$tmp/live-cnp.json" 2>/dev/null || refuse 'live runner CNP lookup failed'
python3 -I - "$tmp/broker.json" "$R/runner-cnp.json" "$tmp/live-cnp.json" "$RUNNER_IMAGE" "$tmp" <<'PY' 2>/dev/null || refuse 'render shape not as reviewed'
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

# 4. Fresh install only, Cilium snapshot, and diff gates. kubectl diff also exits 1 on most errors,
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

# 5. Evidence, no clobber: the rendered broker objects (postcheck and rollback) and the live runner CNP.
[ ! -e "$EV" ] || refuse "$EV already exists"
install -d -m 0700 "$EV" || refuse 'evidence directory failed'
for f in broker.json runner-cnp-before.json; do
  cp "$tmp/$f" "$EV/$f" && chmod 0600 "$EV/$f" && cmp -s "$tmp/$f" "$EV/$f" || refuse "evidence copy of $f failed"
done
echo 'precheck ok: renderer pinned, values as reviewed, render shaped, live runner CNP matches, namespace absent, diffs pending'

# 6. Apply in order; stop at the first failure.
rc=0
for f in "$tmp"/0*.json "$R/runner-cnp.json"; do
  timeout 60 $K apply -f "$f" > /dev/null 2>&1 || { echo "apply failed at $(basename "$f"); earlier objects stay applied"; rc=5; break; }
  [ "$f" = "$tmp/09.json" ] && { timeout 300 $K -n codex-broker rollout status statefulset/codex-broker --timeout=300s > /dev/null 2>&1 \
    || { echo 'apply: broker StatefulSet not ready within 300 s'; rc=5; break; }; }
  rc=0
done
if [ "$rc" = 0 ]; then
  echo 'apply ok'
  # 7. Postcheck.
  bad=
  [ "$(diffrc "$EV/broker.json")" = 0 ] || bad="$bad broker-diff"
  [ "$(diffrc "$R/runner-cnp.json")" = 0 ] || bad="$bad runner-cnp-diff"
  # A failed lookup's stdout is discarded, never matched.
  p=$(timeout 30 $K -n codex-broker get pod codex-broker-0 -o jsonpath='{.status.phase}|{.status.containerStatuses[0].ready}|{.status.containerStatuses[0].restartCount}|{.status.containerStatuses[0].imageID}' 2>/dev/null) \
    || p=lookup-failed
  case "$p" in Running\|true\|0\|*) ;; *) bad="$bad broker-pod";; esac
  v=$(timeout 30 $K -n codex-broker get cnp codex-broker -o jsonpath='{.status.conditions[?(@.type=="Valid")].status}' 2>/dev/null) \
    || v=lookup-failed
  [ "$v" = True ] || bad="$bad cnp-valid"
  [ "$(cil)" = "$cilium_before" ] || bad="$bad cilium-config-changed"
  echo "broker pod: $p"
  [ -z "$bad" ] || { echo "postcheck failed:$bad"; rc=8; }
fi
echo "rollback: $K -n codex-broker scale statefulset codex-broker --replicas=0; $K delete -f $EV/broker.json; $K apply -f $EV/runner-cnp-before.json"
echo "result rc=$rc"
exit $rc
