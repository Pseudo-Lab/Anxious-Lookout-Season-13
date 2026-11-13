# guest, as root; no argument, no stdin. r2 (design (b) r1 §6): P-A r5 replacement, step 1 of 3: keeps the installed
# r4 bytes of pa-start.py and pa-observe.py under new names (<name>.r4-kept-<UTC>, one UTC stamp for both), so
# pa-install.sh r3 (it never overwrites) can then create the r5 files under the stable names (step 2: pa-observe.py,
# step 3: pa-start.py). Nothing is deleted, overwritten or edited; no other file is touched (the r3-kept files stay).
# Refused before any change (rc 3 / 4): a file not at its r4 pin or not a root:root 0600 regular file with one link;
# an earlier *.r4-kept-* name; the pas runs not exactly the failed run pa-start-pas-20261111T123436Z with its exit.json
# at its pin (the r4 bytes ran once, in that run, which stays as it is); a pa-start-pas2-* run; the base lock
# (evidence/v6pre.lock, taken by every unit's main) not free. pa-start.py is moved first: a stop between the two moves
# leaves no entry point (a pa-start.py r5 would refuse the r4 observer by its pin anyway).
# Each move is `mv -n -T` (as pa-install.sh) and its result is verified, not assumed: the old name gone, the kept name
# at the r4 pin, root:root 0600, one link. rc: 0 both kept and verified; 2 no /root/native68; 3/4 refused before any
# change; 7 a move or a check after a move failed: the partial state is kept as it is, nothing retried, report it.
set -u
cd /root/native68 || exit 2
O3=f00e8ff3be4a23600c1221589549aee93dc4641021f67f9ab254c0c07bd98f3d
S3=35401391d18976dd0c16ac0b1ce509952f0819e891d79b49f8fbb12dc7929c87
FAILED=pa-start-pas-20261111T123436Z
FAILED_EXIT=72dfc1a79b30556923f7f2dc9f636a85c7299cbf021130e8aecb070260399910
LOCK=/root/native68/evidence/v6pre.lock
U=$(date -u +%Y%m%dT%H%M%SZ) || { echo "no UTC stamp; nothing changed"; exit 3; }
KS=pa-start.py.r4-kept-$U
KO=pa-observe.py.r4-kept-$U
printf '%s  %s\n%s  %s\n' "$S3" pa-start.py "$O3" pa-observe.py | sha256sum -c -; r=$?
echo "r4 pin check rc=$r"
[ "$r" = 0 ] || { echo "pa-start.py / pa-observe.py not at the r4 pins (or unreadable); nothing changed"; exit 3; }
for f in pa-start.py pa-observe.py; do
  m=$(stat -c '%F|%u|%g|%a|%h' "$f"); r=$?
  echo "stat $f rc=$r: $m"
  [ "$r" = 0 ] && [ "$m" = "regular file|0|0|600|1" ] || { echo "$f is not a root:root 600 regular file with one link; nothing changed"; exit 3; }
done
l=$(ls -A /root/native68); r=$?
[ "$r" = 0 ] || { echo "listing failed; nothing changed"; exit 3; }
c=$(printf '%s\n' "$l" | grep -c '\.r4-kept-')
echo "earlier r4-kept names: $c"
[ "$c" = 0 ] || { echo "a *.r4-kept-* name exists; nothing changed"; exit 4; }
e=$(ls -A /root/native68/evidence); r=$?
[ "$r" = 0 ] || { echo "evidence listing failed; nothing changed"; exit 3; }
p=$(printf '%s\n' "$e" | grep '^pa-start-pas-')
echo "pas runs: $p"
[ "$p" = "$FAILED" ] || { echo "the pas runs are not exactly $FAILED; nothing changed"; exit 4; }
printf '%s  %s\n' "$FAILED_EXIT" "/root/native68/evidence/$FAILED/exit.json" | sha256sum -c - >/dev/null || { echo "the failed run's exit.json is not at its pin; nothing changed"; exit 4; }
c=$(printf '%s\n' "$e" | grep -c '^pa-start-pas2-')
echo "pas2 runs: $c"
[ "$c" = 0 ] || { echo "a pa-start-pas2 run exists; nothing changed"; exit 4; }
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
  [ "$r" = 0 ] || { echo "$2 is not at the r4 pin after the move (rc 7)"; exit 7; }
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
