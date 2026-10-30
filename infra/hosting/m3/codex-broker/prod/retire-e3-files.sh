#!/bin/sh
# #72 E3: remove the supply unit files and scripts after copying them to verified evidence.
# Reversible by reinstalling from the evidence copy. No credential file is touched (that is E4).
# rc: 0 removed and verified; 3 precheck refused, nothing changed; 6 evidence failed, nothing removed;
#     7 a removal failed midway (stop, report); 8 removed but a postcheck failed.
set -u
K='/usr/local/bin/k3s kubectl'
EV=/root/issue72/evidence/e3
FILES='/etc/systemd/system/issue16-supply.timer
/etc/systemd/system/issue16-supply.service
/usr/local/lib/anxious-lookout/issue16/supply.sh
/usr/local/lib/anxious-lookout/issue16/stop-supply.sh'
refuse() { echo "precheck: $1; nothing changed"; exit 3; }

# 1. Exactly these regular root-owned files, no drop-ins, no credential-looking content.
for f in $FILES; do
  [ -f "$f" ] && [ ! -L "$f" ] && [ "$(stat -c %U "$f")" = root ] || refuse "$f is not a root-owned regular file"
done
for d in /etc/systemd/system/issue16-supply.timer.d /etc/systemd/system/issue16-supply.service.d; do
  [ ! -e "$d" ] || refuse "unexpected drop-in $d"
done
grep -lE 'postgres(ql)?://|eyJhbGci|BEGIN [A-Z ]*(KEY|CERTIFICATE)|password=' $FILES > /dev/null 2>&1 && refuse 'credential marker in a file'
# No other unit may still call the scripts being removed.
others=$(grep -lE 'supply\.sh' /etc/systemd/system/*.service /etc/systemd/system/*.timer 2>/dev/null \
  | grep -vxE '/etc/systemd/system/issue16-supply\.(service|timer)')
[ -z "$others" ] || refuse "other units reference the supply scripts: $(echo $others)"
# 2. E1 and E2 done.
for u in issue16-supply.timer issue16-supply.service; do
  v=$(timeout 10 systemctl show -p ActiveState -p UnitFileState --value "$u" 2>/dev/null | tr '\n' ' ')
  case "$v" in 'inactive disabled '|'failed disabled '|'inactive static '|'failed static ') ;; *) refuse "E1 not complete: $u $v";; esac
done
for o in 'role -n codex-runner' clusterrole; do
  r=$(timeout 30 $K get $o runner-pool-supply -o jsonpath='{.rules}' 2>/dev/null) || refuse "E2 lookup failed ($o)"
  case "$r" in ''|'[]') ;; *) refuse "E2 not complete ($o has rules)";; esac
done
[ ! -e "$EV" ] || refuse "$EV already exists"
echo 'precheck ok: four files, no drop-ins, no markers, E1 and E2 complete'

# 3. Evidence: copy, compare, manifest with mode/owner, verify. Any failure stops before removal.
install -d -m 0700 "$EV" || { echo 'evidence directory failed; nothing removed'; exit 6; }
: > "$EV/MODES" && chmod 0600 "$EV/MODES" || { echo 'evidence MODES failed; nothing removed'; exit 6; }
for f in $FILES; do
  b=$(basename "$f")
  cp -p "$f" "$EV/$b" && cmp -s "$f" "$EV/$b" && stat -c '%a %U %G %n' "$f" >> "$EV/MODES" \
    || { echo "evidence copy failed at $b; nothing removed"; exit 6; }
done
( cd "$EV" && sha256sum issue16-supply.timer issue16-supply.service supply.sh stop-supply.sh MODES > MANIFEST.sha256 ) \
  && [ "$(grep -c . "$EV/MANIFEST.sha256")" = 5 ] && ( cd "$EV" && sha256sum -c --quiet MANIFEST.sha256 ) \
  || { echo 'evidence manifest failed; nothing removed'; exit 6; }
echo 'evidence ok: 4 files + MODES copied and checksum-verified'

# 4. Remove, literal paths, stop at the first failure.
rc=0
for f in $FILES; do unlink "$f" || { echo "unlink failed at $f"; rc=7; break; }; done
[ $rc = 0 ] && { timeout 60 systemctl daemon-reload || { echo 'daemon-reload failed'; rc=7; }; }
if [ $rc = 0 ]; then
  bad=
  for f in $FILES; do [ ! -e "$f" ] && [ ! -L "$f" ] || bad="$bad $f"; done
  for u in issue16-supply.timer issue16-supply.service; do
    [ "$(timeout 10 systemctl show -p LoadState --value "$u" 2>/dev/null)" = not-found ] || bad="$bad $u"
  done
  ( cd "$EV" && sha256sum -c --quiet MANIFEST.sha256 ) || bad="$bad manifest"
  [ -z "$bad" ] && echo 'removal ok' || { echo "postcheck failed:$bad"; rc=8; }
fi
echo "rollback: reinstall each file from $EV with the mode/owner in $EV/MODES (install -m <mode> -o root -g root), systemctl daemon-reload, then E1's rollback"
echo "result rc=$rc"
exit $rc
