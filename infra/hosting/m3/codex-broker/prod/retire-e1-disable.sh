#!/bin/sh
# #72 E1: stop host supply (timer and service). Reversible: files and RBAC stay until E2/E3.
# Run as root, once, after the D canary was accepted and review cleared this SHA-256 with the @@ values.
# rc: 0 disabled and verified; 2 placeholders left; 3 precheck refused, nothing changed;
#     5 systemctl failed (report, do not rerun); 8 changed but a postcheck failed.
set -u
RUNNER_IMAGE_ID=@@RUNNER_IMAGE_ID@@   # containerd digest of the e9337f7 runner image, sha256:<64 hex>
# #72 decision 3 (option c): the #66 owner 3cb453bf-d870-40dc-b7e1-3a58aa0cbaf2 keeps its issue28 Pod and binding;
# its binding moves in the #66 runbook before that owner's next entry. runner-<owner hex>, or "none".
EXEMPT_PODS='runner-3cb453bfd87040dcb7e13a58aa0cbaf2'
K='/usr/local/bin/k3s kubectl'
UNITS='issue16-supply.timer issue16-supply.service'
SUPPLY=/usr/local/lib/anxious-lookout/issue16/supply.sh
EV=/root/issue72/evidence/e1
case "$RUNNER_IMAGE_ID$EXEMPT_PODS" in *@@*) echo 'placeholders not filled'; exit 2;; esac
refuse() { echo "precheck: $1; nothing changed"; exit 3; }
echo "$RUNNER_IMAGE_ID" | grep -qE '^sha256:[0-9a-f]{64}$' || refuse 'RUNNER_IMAGE_ID is not a digest'
[ ! -e "$EV" ] || refuse "$EV already exists"

# 1. Broker serving, and no Running user runner left on an image without the broker client.
p=$(timeout 30 $K -n codex-broker get pod codex-broker-0 -o jsonpath='{.status.phase}|{.status.containerStatuses[0].ready}' 2>/dev/null)
[ "$p" = 'Running|true' ] || refuse 'broker pod not ready'
pods=$(timeout 30 $K -n codex-runner get pods -l anxious.io/managed=user-runner \
  -o jsonpath='{range .items[*]}{.metadata.name}|{.status.phase}|{.status.containerStatuses[?(@.name=="runner")].imageID}{"\n"}{end}' 2>/dev/null) \
  || refuse 'runner pod lookup failed'
old=
for l in $pods; do
  name=${l%%|*}; rest=${l#*|}; phase=${rest%%|*}; image=${rest#*|}
  [ "$phase" = Running ] || continue
  case "$image" in "$RUNNER_IMAGE_ID"|*"@$RUNNER_IMAGE_ID") continue;; esac
  case " $EXEMPT_PODS " in *" $name "*) echo "exempt (decision 3): $name"; continue;; esac
  old="$old $name"
done
[ -z "$old" ] || refuse "Running runners on an old image:$old"
# Stopped owners too: a binding still pinning an old runner image would start a Pod with the broker
# spec but no broker client at its next entry, with no host supply left. Pod name = runner-<owner hex>.
b=$(timeout 30 $K -n m2-hosting exec postgres-0 -- psql -U postgres -d hosting -At -v ON_ERROR_STOP=1 -c \
  "select 'runner-'||replace(owner_id::text,'-','')||'|'||coalesce(plan::jsonb->>'image','-') from runner_bindings.bindings order by 1" 2>/dev/null) \
  || refuse 'binding lookup failed'
oldb=
for l in $b; do
  name=${l%%|*}; image=${l#*|}
  case "$image" in *"@$RUNNER_IMAGE_ID") continue;; esac
  case " $EXEMPT_PODS " in *" $name "*) continue;; esac
  oldb="$oldb $name"
done
[ -z "$oldb" ] || refuse "bindings still on an old runner image:$oldb"

# 2. Units loaded, no delivery in progress; record the unit-file state for the rollback.
before=
for u in $UNITS; do
  v=$(timeout 10 systemctl show -p LoadState -p ActiveState -p UnitFileState "$u" 2>/dev/null) || refuse "systemctl show $u failed"
  load=$(echo "$v" | sed -n 's/^LoadState=//p'); act=$(echo "$v" | sed -n 's/^ActiveState=//p'); file=$(echo "$v" | sed -n 's/^UnitFileState=//p')
  [ "$load" = loaded ] && [ -n "$act" ] || refuse "$u LoadState=$load ActiveState=$act"
  [ "$u" = issue16-supply.service ] && case "$act" in active|activating|deactivating|reloading) refuse "$u ActiveState=$act (delivery in progress)";; esac
  before="$before $u=$file"
done
echo "precheck ok: broker ready, no old-image runner, units loaded and idle; before:$before"

# 3. Disable and stop.
rc=0
timeout 60 systemctl disable --now $UNITS > /dev/null 2>&1 || { echo 'systemctl disable --now failed'; rc=5; }
if [ $rc = 0 ]; then
  bad=
  # Decision 2: E4's 24 h stable period and its refresh check count from this moment.
  at=$(date -u +%s)
  { install -d -m 0700 "$EV" && ( umask 077; printf '%s\n' "$at" > "$EV/stopped-at" ) && [ "$(cat "$EV/stopped-at")" = "$at" ]; } \
    && echo "stopped-at $(date -u -d "@$at" +%Y-%m-%dT%H:%M:%SZ)" || bad="$bad stopped-at"
  for u in $UNITS; do
    # Status first, then the named properties: stdout of a failed systemctl is never trusted.
    v=$(timeout 10 systemctl show -p ActiveState -p UnitFileState "$u" 2>/dev/null) || { bad="$bad $u"; continue; }
    v="$(echo "$v" | sed -n 's/^ActiveState=//p') $(echo "$v" | sed -n 's/^UnitFileState=//p')"
    echo "$u: $v"
    case "$v" in 'inactive disabled'|'failed disabled'|'inactive static'|'failed static') ;; *) bad="$bad $u";; esac
  done
  pgrep -f "$SUPPLY" > /dev/null 2>&1; s=$?  # 1 = none; 0 = still running; >=2 = lookup error
  [ $s = 1 ] || bad="$bad process"
  [ -z "$bad" ] && rc=0 || { echo "postcheck failed:$bad"; rc=8; }
fi
echo "rollback: for each unit recorded as enabled above: systemctl enable <unit>; then systemctl start issue16-supply.timer (keep $EV as the record)"
echo "result rc=$rc"
exit $rc
