#!/bin/sh
# #72 E2: empty the runner-pool-supply Role/ClusterRole and their bindings. Reversible from evidence.
# Input: /root/issue72/render/supply-rbac.json = git show <sha>:infra/hosting/m3/codex-broker/prod/supply-rbac.json,
# the four runner-pool-supply objects exactly as ops.runner_rollout renders them (rules and subjects empty;
# test_steps.sh compares the file with the renderer). Hash-pinned.
# rc: 0 applied and verified; 2 placeholders left; 3 precheck refused, nothing changed;
#     5 apply failed (report, do not rerun); 8 applied but a postcheck failed.
set -u
RBAC_SHA=65e0ed7ea394e16706aeec9dba3486e45608caf3d100d11946b96a5375123779
K='/usr/local/bin/k3s kubectl'
F=/root/issue72/render/supply-rbac.json
EV=/root/issue72/evidence/e2
SA=system:serviceaccount:codex-runner:runner-pool-supply
case "$RBAC_SHA" in *@@*) echo 'placeholders not filled'; exit 2;; esac
tmp=$(mktemp -d /run/issue72-e2.XXXXXX) || { echo 'precheck: no work directory'; exit 3; }
trap 'rm -rf "$tmp"' EXIT
refuse() { echo "precheck: $1; nothing changed"; exit 3; }

[ -f "$F" ] && [ ! -L "$F" ] && [ "$(stat -c %U "$F")" = root ] || refuse 'render is not a root-owned regular file'
echo "$RBAC_SHA  $F" | sha256sum -c --quiet - > /dev/null 2>&1 || refuse 'render hash mismatch'
python3 -I - "$F" <<'PY' || refuse 'render is not exactly the four emptied runner-pool-supply objects'
import json, sys
items = json.load(open(sys.argv[1]))['items']
kinds = sorted((i['kind'], i['metadata']['name'], i['metadata'].get('namespace')) for i in items)
assert kinds == sorted([('Role', 'runner-pool-supply', 'codex-runner'), ('RoleBinding', 'runner-pool-supply', 'codex-runner'),
                        ('ClusterRole', 'runner-pool-supply', None), ('ClusterRoleBinding', 'runner-pool-supply', None)])
for i in items:
    assert i.get('rules', []) == [] and i.get('subjects', []) == []
PY

# E1 must be done: both units disabled and not running.
for u in issue16-supply.timer issue16-supply.service; do
  v=$(timeout 10 systemctl show -p ActiveState -p UnitFileState --value "$u" 2>/dev/null | tr '\n' ' ')
  case "$v" in 'inactive disabled '|'failed disabled '|'inactive static '|'failed static ') ;; *) refuse "E1 not complete: $u $v";; esac
done
timeout 60 $K diff -f "$F" > /dev/null 2> "$tmp/diff.err"; d=$?; [ -s "$tmp/diff.err" ] && d=err
[ "$d" = 1 ] || refuse "diff: $d (want 1)"

# Rollback copies of the four live objects, without server fields. No Secret is involved.
timeout 30 $K get -n codex-runner role/runner-pool-supply rolebinding/runner-pool-supply \
  clusterrole/runner-pool-supply clusterrolebinding/runner-pool-supply -o json > "$tmp/live.json" 2>/dev/null || refuse 'live lookup failed'
python3 -I - "$tmp/live.json" "$tmp/before.json" <<'PY' || refuse 'live objects not as expected'
import json, sys
items = json.load(open(sys.argv[1]))['items']
assert len(items) == 4
def keep(i):
    out = {k: v for k, v in i.items() if k in ('apiVersion', 'kind', 'rules', 'subjects', 'roleRef')}
    out['metadata'] = {k: v for k, v in i['metadata'].items() if k in ('name', 'namespace', 'labels')}
    return out
json.dump({'apiVersion': 'v1', 'kind': 'List', 'items': [keep(i) for i in items]}, open(sys.argv[2], 'w'))
PY
install -d -m 0700 "$EV" && [ ! -e "$EV/supply-rbac-before.json" ] && cp "$tmp/before.json" "$EV/supply-rbac-before.json" \
  && chmod 0600 "$EV/supply-rbac-before.json" && cmp -s "$tmp/before.json" "$EV/supply-rbac-before.json" \
  || refuse 'evidence copy failed or already present'
echo 'precheck ok: render pinned and empty, E1 complete, diff pending, rollback copy kept'

timeout 60 $K apply -f "$F" > /dev/null 2>&1 && rc=0 || { echo 'apply failed'; rc=5; }
if [ $rc = 0 ]; then
  bad=
  timeout 60 $K diff -f "$F" > /dev/null 2>&1 || bad="$bad diff"
  for check in 'get pods -n codex-runner' 'create pods/exec -n codex-runner' 'list pods -n codex-runner'; do
    a=$(timeout 30 $K auth can-i $check --as="$SA" 2>/dev/null); echo "can-i $check: $a"
    [ "$a" = no ] || bad="$bad can-i"
  done
  [ -z "$bad" ] || { echo "postcheck failed:$bad"; rc=8; }
fi
echo "rollback: $K apply -f $EV/supply-rbac-before.json"
echo "result rc=$rc"
exit $rc
