# guest, as root; stdin = one reviewed file. $1 = its name. Order fixed: v6-settle.py first (v6-turns.py must not exist
# yet), then v6-turns.py (v6-settle.py must already be installed with its pin). Creates the file new in /root/native68
# (root 0600); never overwrites, never touches another file. The base v6pre-turns.py must be 6170fec8 before and after.
# rc: 0 installed and verified; 2 usage; 3/4 refused before any change (pin, order, exists); 5 the received bytes or the
# temp file failed (temp removed, nothing installed); 7 after the move a lookup failed or a pin/metadata/temp check did
# not hold: the partial state is kept as it is, nothing is retried, report it (review r1 major 2).
set -u
cd /root/native68 || exit 2
BASE=6170fec826400589ef0ac4ed38a4fd070f2abe2625edf6e1c8bb2887f7898e73
SETTLE=1d96c6f666779c589dd81df8690358476c76b75450cc892111c996314b757e48
TURNS=d9f1e9a7be87adf13f4aee3d8e10f31cb8ab7839d2984d53ed19b7162ee19c48
case "${1:-}" in
  v6-settle.py) E=$SETTLE; O=v6-turns.py ;;
  v6-turns.py) E=$TURNS; O=v6-settle.py ;;
  *) echo "name not allowed; nothing changed"; exit 2 ;;
esac
N=$1
printf '%s  %s\n' "$BASE" v6pre-turns.py | sha256sum -c - >/dev/null || { echo "v6pre-turns.py is not 6170fec8 (or unreadable); nothing changed"; exit 3; }
if [ "$N" = v6-settle.py ]; then
  if [ -e "$O" ] || [ -L "$O" ]; then echo "$O exists before v6-settle.py; order broken, nothing changed"; exit 3; fi
else
  printf '%s  %s\n' "$SETTLE" v6-settle.py | sha256sum -c - >/dev/null || { echo "v6-settle.py is not installed with its pin; nothing changed"; exit 3; }
fi
if [ -e "$N" ] || [ -L "$N" ]; then echo "$N exists; nothing changed"; exit 4; fi
T=$(mktemp -p /root/native68 .v6-new.XXXXXX) || { echo "temp file not created; nothing changed"; exit 5; }
cat > "$T"
if ! printf '%s  %s\n' "$E" "$T" | sha256sum -c - >/dev/null; then rm -f -- "$T"; echo "received bytes are not the reviewed file; nothing changed"; exit 5; fi
chown root:root "$T" && chmod 0600 "$T" || { rm -f -- "$T"; echo "owner/mode failed; nothing changed"; exit 5; }
mv -n -T "$T" "$N"
if [ -e "$T" ] || [ -L "$T" ]; then echo "install move not proved; $N not replaced, temp left at $T (rc 7)"; exit 7; fi
# After the move: every lookup and check fails with rc 7; the state is left as it is.
if [ "$N" = v6-turns.py ]; then P="$SETTLE  v6-settle.py
$TURNS  v6-turns.py"; else P="$SETTLE  v6-settle.py"; fi
printf '%s\n%s  %s\n' "$P" "$BASE" v6pre-turns.py | sha256sum -c -; r=$?
echo "pin check after the move rc=$r"
[ "$r" = 0 ] || { echo "a pin does not hold after the move (or a file is unreadable) (rc 7)"; exit 7; }
m=$(stat -c '%F|%u|%g|%a|%h' "$N"); r=$?
echo "stat $N rc=$r: $m"
[ "$r" = 0 ] || { echo "stat failed after the move (rc 7)"; exit 7; }
[ "$m" = "regular file|0|0|600|1" ] || { echo "$N is not a root:root 0600 regular file with one link (rc 7)"; exit 7; }
if [ "$N" = v6-settle.py ]; then
  if [ -e "$O" ] || [ -L "$O" ]; then echo "$O appeared during the install (rc 7)"; exit 7; fi
fi
l=$(ls -A /root/native68); r=$?
echo "native68 listing rc=$r"
[ "$r" = 0 ] || { echo "listing failed: the temp count is unknown, not 0 (rc 7)"; exit 7; }
c=$(printf '%s\n' "$l" | grep -c '^\.v6-new\.')
echo "install temp files left: $c"
[ "$c" = 0 ] || { echo "install temp files are left (rc 7)"; exit 7; }
echo "install rc=0"
