# guest, as root; no argument, no stdin. P-A r4 replacement, step 1 of 3: keeps the installed r3 bytes of
# pa-start.py and pa-observe.py under new names (<name>.r3-kept-<UTC>, one UTC stamp for both), so pa-install.sh r2
# (it never overwrites) can then create the r4 files under the stable names (step 2: pa-observe.py, step 3:
# pa-start.py). Nothing is deleted, overwritten or edited; no other file is touched.
# Refused before any change (rc 3 / 4): a file not at its r3 pin or not a root:root 0600 regular file with one link;
# an earlier *.r3-kept-* name; a pa-start-pas-* run (pas never ran on the r3 bytes, so they are not evidence of a run);
# the base lock (evidence/v6pre.lock, taken by every unit's main) not free. pa-start.py is moved first: a stop between
# the two moves leaves no entry point (a pa-start.py r4 would refuse the r3 observer by its pin anyway).
# Each move is `mv -n -T` (as pa-install.sh) and its result is verified, not assumed: the old name gone, the kept name
# at the r3 pin, root:root 0600, one link. rc: 0 both kept and verified; 2 no /root/native68; 3/4 refused before any
# change; 7 a move or a check after a move failed: the partial state is kept as it is, nothing retried, report it.
set -u
cd /root/native68 || exit 2
O3=4e8db8b9c67295c6a003083e810d7a9d83690cb8351116205e40989eb10e63c2
S3=fe1c398715760109a6fe12f07065a6e3a589593aec5b2cbfccae89742c16cccf
LOCK=/root/native68/evidence/v6pre.lock
U=$(date -u +%Y%m%dT%H%M%SZ) || { echo "no UTC stamp; nothing changed"; exit 3; }
KS=pa-start.py.r3-kept-$U
KO=pa-observe.py.r3-kept-$U
printf '%s  %s\n%s  %s\n' "$S3" pa-start.py "$O3" pa-observe.py | sha256sum -c -; r=$?
echo "r3 pin check rc=$r"
[ "$r" = 0 ] || { echo "pa-start.py / pa-observe.py not at the r3 pins (or unreadable); nothing changed"; exit 3; }
for f in pa-start.py pa-observe.py; do
  m=$(stat -c '%F|%u|%g|%a|%h' "$f"); r=$?
  echo "stat $f rc=$r: $m"
  [ "$r" = 0 ] && [ "$m" = "regular file|0|0|600|1" ] || { echo "$f is not a root:root 600 regular file with one link; nothing changed"; exit 3; }
done
l=$(ls -A /root/native68); r=$?
[ "$r" = 0 ] || { echo "listing failed; nothing changed"; exit 3; }
c=$(printf '%s\n' "$l" | grep -c '\.r3-kept-')
echo "earlier kept names: $c"
[ "$c" = 0 ] || { echo "a *.r3-kept-* name exists; nothing changed"; exit 4; }
e=$(ls -A /root/native68/evidence); r=$?
[ "$r" = 0 ] || { echo "evidence listing failed; nothing changed"; exit 3; }
c=$(printf '%s\n' "$e" | grep -c '^pa-start-pas-')
echo "pas runs: $c"
[ "$c" = 0 ] || { echo "a pa-start-pas run exists; nothing changed"; exit 4; }
[ -f "$LOCK" ] && [ ! -L "$LOCK" ] || { echo "no base lock file; nothing changed"; exit 3; }
command -v flock >/dev/null || { echo "no flock; nothing changed"; exit 3; }
exec 9<"$LOCK" ||{ echo "base lock not opened; nothing changed"; exit 3; }
flock -n 9 || { echo "the base lock is held (a unit is running); nothing changed"; exit 4; }
# Changes. After the first move every failure is rc 7 and the state is left as it is.
for pair in "pa-start.py $KS $S3" "pa-observe.py $KO $O3"; do
  set -- $pair
  mv -n -T "$1" "$2"; r=$?
  echo "keep $1 -> $2 mv rc=$r"
  if [ -e "$1" ] || [ -L "$1" ]; then echo "$1 still present: move not proved (rc 7)"; exit 7; fi
  printf '%s  %s\n' "$3" "$2" | sha256sum -c -; r=$?
  [ "$r" = 0 ] || { echo "$2 is not at the r3 pin after the move (rc 7)"; exit 7; }
  m=$(stat -c '%F|%u|%g|%a|%h' "$2"); r=$?
  echo "stat $2 rc=$r: $m"
  [ "$r" = 0 ] && [ "$m" = "regular file|0|0|600|1" ] || { echo "$2 is not a root:root 600 regular file with one link (rc 7)"; exit 7; }
done
l=$(ls -A /root/native68); r=$?
[ "$r" = 0 ] || { echo "listing after the moves failed (rc 7)"; exit 7; }
echo "names now:"; printf '%s\n' "$l" | grep '^pa-\(start\|observe\)\.py'
c=$(printf '%s\n' "$l" | grep -c '^pa-\(start\|observe\)\.py$')
[ "$c" = 0 ] || { echo "a stable name is present after the moves (rc 7)"; exit 7; }
echo "keep rc=0"
