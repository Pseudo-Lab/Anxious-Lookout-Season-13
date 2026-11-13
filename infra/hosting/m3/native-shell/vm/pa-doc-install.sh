# guest, as root; stdin = one reviewed pa-doc file; $1 = its guest name. Creates it new in /root/native68, root
# 0600; never overwrites, never touches another file (pa-install.sh r2 a75c7af7…, committed 49aff3f, with the pa-doc
# names). pa-doc: pa-doc-content.json (PM's fixed bytes), then pa-doc.py (it pins the content and v6pre-turns.py).
# Used as installed: v6pre-turns.py (the base of pa-doc.py) and the other P-A bases, which must be at their pins
# before and after.
# rc: 0 installed and verified; 2 usage; 3/4 refused before any change (pin, exists); 5 the received bytes or the
# temp file failed (temp removed, nothing installed); 7 after the move a lookup failed or a pin/metadata/temp check
# did not hold: the partial state is kept as it is, nothing is retried, report it.
set -u
cd /root/native68 || exit 2
BASE=6170fec826400589ef0ac4ed38a4fd070f2abe2625edf6e1c8bb2887f7898e73
V6=d9f1e9a7be87adf13f4aee3d8e10f31cb8ab7839d2984d53ed19b7162ee19c48
LIB=4cae0e470ae4f5862c52343570a6e3c94adb70122a58aad6a97f807a6eb82475
G3START=f61f6e787bd9f5affa040b378be3fb0d51ca17114c754c84834397f8dda0a67b
G4TURN=0687f375444cb1b08ed08e88807e31249533bcf2959c42b7d741c7a9ef11cc43
G4BTURN=9b90a66e8652d401a4f9e7a273aafc5262ed06374427dc1e53622433ca0cb736
G5START=2856cd2962e0a95dbdc27440946ce9e6b671fab515dfddab8bc73608581d3920
case "${1:-}" in
  pa-doc-content.json) E=c5e66f195ab916dacab7b3c8d739bd8d3ef6a27a693d01cfcdfa36129d3f0eac; [ $# = 1 ] || { echo "usage; nothing changed"; exit 2; } ;;
  pa-doc.py) E=5aa637c632850f6a54f6b9ba1328130e7fca23fca3c9c08350d4e339e5ae67f6; [ $# = 1 ] || { echo "usage; nothing changed"; exit 2; } ;;
  *) echo "name not allowed; nothing changed"; exit 2 ;;
esac
N=$1
printf '%s  %s\n%s  %s\n%s  %s\n%s  %s\n%s  %s\n%s  %s\n%s  %s\n' "$BASE" v6pre-turns.py "$V6" v6-turns.py "$LIB" lib.sh "$G3START" g3-start.py "$G4TURN" g4-turn.py "$G4BTURN" g4b-turn.py "$G5START" g5-start.py | sha256sum -c - >/dev/null || { echo "a base (v6pre-turns.py, v6-turns.py, lib.sh, g3-start.py, g4-turn.py, g4b-turn.py, g5-start.py) not at its pin (or unreadable); nothing changed"; exit 3; }
if [ -e "$N" ] || [ -L "$N" ]; then echo "$N exists; nothing changed"; exit 4; fi
T=$(mktemp -p /root/native68 .pa-doc-new.XXXXXX) || { echo "temp file not created; nothing changed"; exit 5; }
cat > "$T"
if ! printf '%s  %s\n' "$E" "$T" | sha256sum -c - >/dev/null; then rm -f -- "$T"; echo "received bytes are not the reviewed file; nothing changed"; exit 5; fi
chown root:root "$T" && chmod 600 "$T" || { rm -f -- "$T"; echo "owner/mode failed; nothing changed"; exit 5; }
mv -n -T "$T" "$N"
if [ -e "$T" ] || [ -L "$T" ]; then echo "install move not proved; $N not replaced, temp left at $T (rc 7)"; exit 7; fi
# After the move: every lookup and check fails with rc 7; the state is left as it is.
printf '%s  %s\n%s  %s\n%s  %s\n%s  %s\n%s  %s\n%s  %s\n%s  %s\n%s  %s\n' "$E" "$N" "$BASE" v6pre-turns.py "$V6" v6-turns.py "$LIB" lib.sh "$G3START" g3-start.py "$G4TURN" g4-turn.py "$G4BTURN" g4b-turn.py "$G5START" g5-start.py | sha256sum -c -; r=$?
echo "pin check after the move rc=$r"
[ "$r" = 0 ] || { echo "a pin does not hold after the move (or a file is unreadable) (rc 7)"; exit 7; }
m=$(stat -c '%F|%u|%g|%a|%h' "$N"); r=$?
echo "stat $N rc=$r: $m"
[ "$r" = 0 ] || { echo "stat failed after the move (rc 7)"; exit 7; }
[ "$m" = "regular file|0|0|600|1" ] || { echo "$N is not a root:root 600 regular file with one link (rc 7)"; exit 7; }
l=$(ls -A /root/native68); r=$?
echo "native68 listing rc=$r"
[ "$r" = 0 ] || { echo "listing failed: the temp count is unknown, not 0 (rc 7)"; exit 7; }
c=$(printf '%s\n' "$l" | grep -c '^\.pa-doc-new\.')
echo "install temp files left: $c"
[ "$c" = 0 ] || { echo "install temp files are left (rc 7)"; exit 7; }
echo "install rc=0"
