#!/bin/sh
# #72 E4: POINT OF NO RETURN. Disable the supply database login, delete the supply ServiceAccount and
# remove the supply credential directory without copying it. Going back means new supply credentials
# as a new reviewed change. Keeps /var/lib/anxious-lookout/issue16-d4/supply/attempts.
# Run as root, once, after the stable period (#72 decision 2) and review cleared this SHA-256 with the @@ values.
# rc: 0 done and verified; 2 placeholders left; 3 precheck refused, nothing changed;
#     5 a step failed midway (stop, report; earlier steps stay done); 8 done but a postcheck failed.
set -u
NOT_BEFORE=@@NOT_BEFORE_UTC@@            # e.g. 2026-11-02T00:00:00Z, from decision 2
CRED_ENTRIES='@@SUPPLY_CRED_FILE_NAMES@@' # exact file names in the directory, from the F inventory
DB_ROLE=@@SUPPLY_DB_ROLE@@               # the supply login role (an identifier, not a credential)
K='/usr/local/bin/k3s kubectl'
D=/etc/anxious-lookout/issue16/supply
A=/var/lib/anxious-lookout/issue16-d4/supply/attempts
case "$NOT_BEFORE$CRED_ENTRIES$DB_ROLE" in *@@*) echo 'placeholders not filled'; exit 2;; esac
refuse() { echo "precheck: $1; nothing changed"; exit 3; }
q() { timeout 30 $K -n m2-hosting exec postgres-0 -- psql -U postgres -d hosting -At -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null; }
echo "$DB_ROLE" | grep -qE '^[a-z_][a-z0-9_]{0,62}$' || refuse 'DB_ROLE is not a plain identifier'
for n in $CRED_ENTRIES; do echo "$n" | grep -qE '^[A-Za-z0-9._-]+$' || refuse 'bad credential file name'; done

# 1. Time gate.
nb=$(date -u -d "$NOT_BEFORE" +%s 2>/dev/null) || refuse 'NOT_BEFORE unreadable'
[ "$(date -u +%s)" -ge "$nb" ] || refuse "stable period not over (not before $NOT_BEFORE)"
# 2. E3 done, broker serving.
for u in issue16-supply.timer issue16-supply.service; do
  [ "$(timeout 10 systemctl show -p LoadState --value "$u" 2>/dev/null)" = not-found ] || refuse "E3 not complete: $u"
done
p=$(timeout 30 $K -n codex-broker get pod codex-broker-0 -o jsonpath='{.status.phase}|{.status.containerStatuses[0].ready}' 2>/dev/null)
[ "$p" = 'Running|true' ] || refuse 'broker pod not ready'
# 3. The credential directory holds exactly the expected regular files (names only, never contents).
[ -d "$D" ] && [ ! -L "$D" ] || refuse "$D is not a directory"
have=$(find "$D" -mindepth 1 -printf '%y %f\n' 2>/dev/null | LC_ALL=C sort) || refuse 'credential directory lookup failed'
want=$(for n in $CRED_ENTRIES; do echo "f $n"; done | LC_ALL=C sort)
[ "$have" = "$want" ] || refuse 'credential directory is not exactly the expected files'
[ -d "$A" ] || refuse 'attempts evidence directory missing'
# 4. ServiceAccount present; role exists, can log in, and has no session.
s=$(timeout 30 $K -n codex-runner get serviceaccount runner-pool-supply -o jsonpath='{.metadata.name}' 2>/dev/null)
[ "$s" = runner-pool-supply ] || refuse 'supply ServiceAccount lookup failed or absent'
r=$(q "select rolcanlogin::text||'|'||(select count(*) from pg_stat_activity where usename='$DB_ROLE') from pg_roles where rolname='$DB_ROLE'")
[ "$r" = 'true|0' ] || refuse "database role not as expected (${r:-absent})"
echo "precheck ok: stable period over, E3 complete, broker ready, credential directory exact, role idle"

# 5. No return from here. Stop at the first failure.
rc=0
q "alter role \"$DB_ROLE\" nologin" > /dev/null || { echo 'alter role nologin failed'; rc=5; }
[ $rc = 0 ] && { timeout 30 $K -n codex-runner delete serviceaccount runner-pool-supply > /dev/null 2>&1 || { echo 'serviceaccount delete failed'; rc=5; }; }
if [ $rc = 0 ]; then
  for n in $CRED_ENTRIES; do unlink "$D/$n" || { echo "unlink failed at $n"; rc=5; break; }; done
  [ $rc = 0 ] && { rmdir "$D" || { echo 'rmdir failed'; rc=5; }; }
fi
if [ $rc = 0 ]; then
  bad=
  [ "$(q "select rolcanlogin::text from pg_roles where rolname='$DB_ROLE'")" = false ] || bad="$bad role"
  timeout 30 $K -n codex-runner get serviceaccount runner-pool-supply > /dev/null 2>&1 && bad="$bad serviceaccount"
  [ ! -e "$D" ] || bad="$bad directory"
  [ -d "$A" ] || bad="$bad attempts"
  [ -z "$bad" ] && echo 'retirement ok' || { echo "postcheck failed:$bad"; rc=8; }
fi
echo 'rollback: none in place; new supply credentials would be a new reviewed change (the role can be re-enabled with LOGIN)'
echo "result rc=$rc"
exit $rc
