# guest, as root; no argument, no stdin. v6-item change E (r5 r4) replacement, step 1 of 2 (r1's pattern, r3): keeps the
# installed change D bytes of v6-item.py (77b1a391…, committed 29c7ffe, installed by sheet D's D2) under a new name
# (v6-item.py.d-kept-<UTC>), so v6-item-install.sh r4 (it never overwrites) can then create the change E file under the
# stable name (step 2). Nothing is deleted, overwritten or edited; no other file is touched (the a-/c-kept files stay).
# Refused before any change (rc 3 / 4): v6-item.py not at the change D pin or not a root:root 0600 regular file with one link;
# an earlier *.d-kept-* name; r3: any v6-item-rejudge-* evidence directory (E must be in place before the rejudge); the base
# lock (evidence/v6pre.lock, taken by every unit's main) not free.
# The move is `mv -n -T` and its result is verified, not assumed: the old name gone, the kept name at the A pin,
# root:root 0600, one link. rc: 0 kept and verified; 2 no /root/native68; 3/4 refused before any change; 7 the move
# or a check after it failed: the partial state is kept as it is, nothing retried, report it.
set -u
cd /root/native68 || exit 2
A=77b1a3919c9246706449c2c76d863e83da4dbe3401b2c90a5293bea25db07af5
LOCK=/root/native68/evidence/v6pre.lock
U=$(date -u +%Y%m%dT%H%M%SZ) || { echo "no UTC stamp; nothing changed"; exit 3; }
K=v6-item.py.d-kept-$U
printf '%s  %s\n' "$A" v6-item.py | sha256sum -c -; r=$?
echo "change D pin check rc=$r"
[ "$r" = 0 ] || { echo "v6-item.py not at the change D pin (or unreadable); nothing changed"; exit 3; }
m=$(stat -c '%F|%u|%g|%a|%h' v6-item.py); r=$?
echo "stat v6-item.py rc=$r: $m"
[ "$r" = 0 ] && [ "$m" = "regular file|0|0|600|1" ] || { echo "v6-item.py is not a root:root 600 regular file with one link; nothing changed"; exit 3; }
l=$(ls -A /root/native68); r=$?
[ "$r" = 0 ] || { echo "listing failed; nothing changed"; exit 3; }
c=$(printf '%s\n' "$l" | grep -c '\.d-kept-')
echo "earlier d-kept names: $c"
[ "$c" = 0 ] || { echo "a *.d-kept-* name exists; nothing changed"; exit 4; }
e=$(ls -A /root/native68/evidence); r=$?
[ "$r" = 0 ] || { echo "evidence listing failed; nothing changed"; exit 3; }
c=$(printf '%s\n' "$e" | grep -c '^v6-item-rejudge-')
echo "v6-item rejudge dirs: $c"
[ "$c" = 0 ] || { echo "a v6-item-rejudge-* evidence directory exists; nothing changed"; exit 4; }
[ -f "$LOCK" ] && [ ! -L "$LOCK" ] || { echo "no base lock file; nothing changed"; exit 3; }
command -v flock >/dev/null || { echo "no flock; nothing changed"; exit 3; }
exec 9<"$LOCK" || { echo "base lock not opened; nothing changed"; exit 3; }
flock -n 9 || { echo "the base lock is held (a unit is running); nothing changed"; exit 4; }
# The change. After the move every failure is rc 7 and the state is left as it is.
mv -n -T v6-item.py "$K"; r=$?
echo "keep v6-item.py -> $K mv rc=$r"
if [ -e v6-item.py ] || [ -L v6-item.py ]; then echo "v6-item.py still present: move not proved (rc 7)"; exit 7; fi
printf '%s  %s\n' "$A" "$K" | sha256sum -c -; r=$?
[ "$r" = 0 ] || { echo "$K is not at the change D pin after the move (rc 7)"; exit 7; }
m=$(stat -c '%F|%u|%g|%a|%h' "$K"); r=$?
echo "stat $K rc=$r: $m"
[ "$r" = 0 ] && [ "$m" = "regular file|0|0|600|1" ] || { echo "$K is not a root:root 600 regular file with one link (rc 7)"; exit 7; }
l=$(ls -A /root/native68); r=$?
[ "$r" = 0 ] || { echo "listing after the move failed (rc 7)"; exit 7; }
echo "names now:"; printf '%s\n' "$l" | grep '^v6-item'
c=$(printf '%s\n' "$l" | grep -c '^v6-item\.py$')
[ "$c" = 0 ] || { echo "the stable name is present after the move (rc 7)"; exit 7; }
echo "keep rc=0"
