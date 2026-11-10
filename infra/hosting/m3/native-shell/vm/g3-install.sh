# guest, as root; stdin = one reviewed G3 file; $1 = its guest name; $2 = its sha256, only for g3-expected.json
# (reviewed after G3-1, so its hash is not in this file). Creates it new in /root/native68, root 0600; never
# overwrites, never touches another file (the g1cli-install.sh / v6-install.sh r2 pattern).
# G3: g3-switch.py and g3-start.py (G3-1c), g3-expected.json (G3-2 step 0). Used as installed: v6pre-turns.py and
# v6-turns.py (the base of g3-start.py), lib.sh; they must be at their pins before and after.
# rc: 0 installed and verified; 2 usage; 3/4 refused before any change (pin, exists); 5 the received bytes or the
# temp file failed (temp removed, nothing installed); 7 after the move a lookup failed or a pin/metadata/temp check
# did not hold: the partial state is kept as it is, nothing is retried, report it.
set -u
cd /root/native68 || exit 2
BASE=6170fec826400589ef0ac4ed38a4fd070f2abe2625edf6e1c8bb2887f7898e73
V6=d9f1e9a7be87adf13f4aee3d8e10f31cb8ab7839d2984d53ed19b7162ee19c48
LIB=4cae0e470ae4f5862c52343570a6e3c94adb70122a58aad6a97f807a6eb82475
case "${1:-}" in
  g3-switch.py) E=8d2f759a82a103f3a655d8a251d44a606a005f0630f097e63a7a92aaa2b0305e; [ $# = 1 ] || { echo "usage; nothing changed"; exit 2; } ;;
  g3-start.py) E=f61f6e787bd9f5affa040b378be3fb0d51ca17114c754c84834397f8dda0a67b; [ $# = 1 ] || { echo "usage; nothing changed"; exit 2; } ;;
  g3-expected.json)
    [ $# = 2 ] || { echo "usage: g3-expected.json needs its reviewed sha256; nothing changed"; exit 2; }
    E=$2
    case "$E" in *[!0-9a-f]*) echo "sha256 is not lowercase hex; nothing changed"; exit 2 ;; esac
    [ ${#E} = 64 ] || { echo "sha256 is not 64 characters; nothing changed"; exit 2; } ;;
  *) echo "name not allowed; nothing changed"; exit 2 ;;
esac
N=$1
printf '%s  %s\n%s  %s\n%s  %s\n' "$BASE" v6pre-turns.py "$V6" v6-turns.py "$LIB" lib.sh | sha256sum -c - >/dev/null || { echo "v6pre-turns.py, v6-turns.py or lib.sh not at its pin (or unreadable); nothing changed"; exit 3; }
if [ -e "$N" ] || [ -L "$N" ]; then echo "$N exists; nothing changed"; exit 4; fi
T=$(mktemp -p /root/native68 .g3-new.XXXXXX) || { echo "temp file not created; nothing changed"; exit 5; }
cat > "$T"
if ! printf '%s  %s\n' "$E" "$T" | sha256sum -c - >/dev/null; then rm -f -- "$T"; echo "received bytes are not the reviewed file; nothing changed"; exit 5; fi
chown root:root "$T" && chmod 600 "$T" || { rm -f -- "$T"; echo "owner/mode failed; nothing changed"; exit 5; }
mv -n -T "$T" "$N"
if [ -e "$T" ] || [ -L "$T" ]; then echo "install move not proved; $N not replaced, temp left at $T (rc 7)"; exit 7; fi
# After the move: every lookup and check fails with rc 7; the state is left as it is.
printf '%s  %s\n%s  %s\n%s  %s\n%s  %s\n' "$E" "$N" "$BASE" v6pre-turns.py "$V6" v6-turns.py "$LIB" lib.sh | sha256sum -c -; r=$?
echo "pin check after the move rc=$r"
[ "$r" = 0 ] || { echo "a pin does not hold after the move (or a file is unreadable) (rc 7)"; exit 7; }
m=$(stat -c '%F|%u|%g|%a|%h' "$N"); r=$?
echo "stat $N rc=$r: $m"
[ "$r" = 0 ] || { echo "stat failed after the move (rc 7)"; exit 7; }
[ "$m" = "regular file|0|0|600|1" ] || { echo "$N is not a root:root 600 regular file with one link (rc 7)"; exit 7; }
l=$(ls -A /root/native68); r=$?
echo "native68 listing rc=$r"
[ "$r" = 0 ] || { echo "listing failed: the temp count is unknown, not 0 (rc 7)"; exit 7; }
c=$(printf '%s\n' "$l" | grep -c '^\.g3-new\.')
echo "install temp files left: $c"
[ "$c" = 0 ] || { echo "install temp files are left (rc 7)"; exit 7; }
echo "install rc=0"
