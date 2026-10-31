#!/bin/sh
# #72 E4: POINT OF NO RETURN. Delete the supply ServiceAccount and remove the supply credential directory
# without copying it. Going back means new supply credentials as a new reviewed change.
# Keeps /var/lib/anxious-lookout/issue16-d4/supply/attempts.
# No database step: the supply database-url is a copy of the observer DB login (rollout README before #60,
# HOST-SUPPLY.md), and the observer worker keeps using that login until #61. E4 removes the copy only and
# never connects to the database.
# Run as root, once, after review cleared this SHA-256.
# Decision 2: at least 24 h since E1 stopped host supply, and a scheduled broker refresh in the same login
# session since then (step 1).
# rc: 0 done and verified; 3 precheck refused, nothing changed;
#     5 a step failed midway (stop, report; earlier steps stay done); 8 done but a postcheck failed.
set -u
STABLE_SECONDS=86400                                          # decision 2
CRED_ENTRIES='ca.crt database-url inputs.json kube-token kubeconfig'  # F inventory 2026-10-30T15:26:55Z
K='/usr/local/bin/k3s kubectl'
D=/etc/anxious-lookout/issue16/supply
A=/var/lib/anxious-lookout/issue16-d4/supply/attempts
E1=/root/issue72/evidence/e1/stopped-at
refuse() { echo "precheck: $1; nothing changed"; exit 3; }
for n in $CRED_ENTRIES; do echo "$n" | grep -qE '^[A-Za-z0-9._-]+$' || refuse 'bad credential file name'; done

# 1. Stable period, and a scheduled refresh in the same login session since E1.
#    The broker appends one line per refresh it drives to /data/broker-events.jsonl
#    ({"at","event":"refresh","kind","ok","before","after"}; backend/broker/credentials.py), where before/after
#    are auth.json's file identity '<inode>:<mtime ns>'. E1 recorded the identity at the stop. The events after E1
#    must form one unbroken chain from that identity to the current one: a device login replaces auth.json without
#    an event, so it breaks the chain and E4 refuses, whatever was recorded before it. At least one link must be
#    kind "scheduled" with ok true ("forced" follows a runner 401). Malformed lines or future times refuse.
[ -f "$E1" ] && [ ! -L "$E1" ] || refuse 'E1 record missing'
stopped=$(cat "$E1" 2>/dev/null) && echo "$stopped" | grep -qE '^[0-9]{10}$' || refuse 'E1 record unreadable'
S=$(dirname "$E1")/session
[ -f "$S" ] && [ ! -L "$S" ] || refuse 'E1 session record missing'
session=$(cat "$S" 2>/dev/null) && echo "$session" | grep -qxE '[0-9]+:[0-9]+' || refuse 'E1 session record unreadable'
now=$(date -u +%s)
[ "$stopped" -le "$now" ] || refuse 'E1 record is in the future'
[ "$now" -ge $((stopped + STABLE_SECONDS)) ] || refuse "stable period not over (E1 at $(date -u -d "@$stopped" +%Y-%m-%dT%H:%M:%SZ), 24 h)"
ev=$(timeout 30 $K -n codex-broker exec codex-broker-0 -- cat /data/broker-events.jsonl 2>/dev/null) || refuse 'broker event lookup failed'
current=$(timeout 30 $K -n codex-broker exec codex-broker-0 -- python3 -I -c \
  'import os; s = os.lstat("/data/codex/auth.json"); print("%d:%d" % (s.st_ino, s.st_mtime_ns))' 2>/dev/null) \
  && echo "$current" | grep -qxE '[0-9]+:[0-9]+' || refuse 'broker login identity lookup failed'
seen=$(printf '%s\n' "$ev" | python3 -I -c '
import json, re, sys
stopped, now, chain, current = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4]
ident = re.compile(r"\d+:\d+|-")
links = []
for line in sys.stdin.read().splitlines():
    if not line:
        continue
    e = json.loads(line)
    assert set(e) == {"at", "event", "kind", "ok", "before", "after"} and e["event"] == "refresh"
    assert type(e["at"]) is int and e["kind"] in ("scheduled", "forced") and type(e["ok"]) is bool
    assert ident.fullmatch(e["before"]) and ident.fullmatch(e["after"])
    assert e["at"] <= now + 60  # no future events
    if e["at"] >= stopped:
        links.append(e)
# Events in the stop second that already ended at the recorded identity happened before E1 read it.
while links and links[0]["after"] == chain and links[0]["before"] != chain:
    links.pop(0)
seen = []
for e in links:
    assert e["before"] == chain  # a break: auth.json was replaced by something other than a broker refresh
    chain = e["after"]
    if e["kind"] == "scheduled" and e["ok"]:
        seen.append(e["at"])
assert chain == current and seen  # unbroken to the current file, with a scheduled refresh in it
print(max(seen))
' "$stopped" "$now" "$session" "$current" 2>/dev/null) \
  || refuse 'no scheduled refresh in the same login session since E1 (a login, a gap or a malformed event record)'
echo "scheduled refresh recorded at $(date -u -d "@$seen" +%Y-%m-%dT%H:%M:%SZ), same login session since E1"
# 2. E3 done, broker serving.
for u in issue16-supply.timer issue16-supply.service; do
  v=$(timeout 10 systemctl show -p LoadState "$u" 2>/dev/null) || refuse "systemctl show $u failed"
  [ "$v" = LoadState=not-found ] || refuse "E3 not complete: $u"
done
p=$(timeout 30 $K -n codex-broker get pod codex-broker-0 -o jsonpath='{.status.phase}|{.status.containerStatuses[0].ready}' 2>/dev/null)
[ "$p" = 'Running|true' ] || refuse 'broker pod not ready'
# 3. The credential directory holds exactly the expected regular files (names only, never contents).
[ -d "$D" ] && [ ! -L "$D" ] || refuse "$D is not a directory"
have=$(find "$D" -mindepth 1 -printf '%y %f\n' 2>/dev/null) || refuse 'credential directory lookup failed'
have=$(printf '%s\n' "$have" | LC_ALL=C sort)
want=$(for n in $CRED_ENTRIES; do echo "f $n"; done | LC_ALL=C sort)
[ "$have" = "$want" ] || refuse 'credential directory is not exactly the expected files'
[ -d "$A" ] || refuse 'attempts evidence directory missing'
# 4. ServiceAccount present.
s=$(timeout 30 $K -n codex-runner get serviceaccount runner-pool-supply -o jsonpath='{.metadata.name}' 2>/dev/null)
[ "$s" = runner-pool-supply ] || refuse 'supply ServiceAccount lookup failed or absent'
echo "precheck ok: stable period over, E3 complete, broker ready, credential directory exact"

# 5. No return from here. Stop at the first failure.
rc=0
timeout 30 $K -n codex-runner delete serviceaccount runner-pool-supply > /dev/null 2>&1 || { echo 'serviceaccount delete failed'; rc=5; }
if [ $rc = 0 ]; then
  for n in $CRED_ENTRIES; do unlink "$D/$n" || { echo "unlink failed at $n"; rc=5; break; }; done
  [ $rc = 0 ] && { rmdir "$D" || { echo 'rmdir failed'; rc=5; }; }
fi
if [ $rc = 0 ]; then
  bad=
  # Absence must be confirmed: --ignore-not-found gives rc 0 and empty output only when it is gone.
  g=$(timeout 30 $K -n codex-runner get serviceaccount runner-pool-supply --ignore-not-found -o name 2>/dev/null) && [ -z "$g" ] || bad="$bad serviceaccount"
  [ ! -e "$D" ] || bad="$bad directory"
  [ -d "$A" ] || bad="$bad attempts"
  [ -z "$bad" ] && echo 'retirement ok' || { echo "postcheck failed:$bad"; rc=8; }
fi
echo 'rollback: none in place; new supply credentials would be a new reviewed change (the observer DB login was not touched)'
echo "result rc=$rc"
exit $rc
