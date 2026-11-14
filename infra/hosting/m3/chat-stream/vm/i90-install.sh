# #90 stage C install (chat-stream/DEV-VM.md P2; guest, as root, sent as the argument of `sh -c`; never installed itself):
# stdin = one reviewed stage C file, $1 = its guest name (i90-switch.py or i90-expected.json), $2 = its reviewed sha256
# (given in review's approval; the expected file is reviewed after the survey, so no hash is pinned here). The file is
# written new as /root/issue90/<name>, root:root 0600. An existing regular file of that name is kept as <name>.rN (the
# first free N in 0-9, never overwritten) before the new one moves in; anything else at that name refuses.
# native-shell/vm/pa-switch-install.sh's temp / hash / mv -n / after-move checks, without its pins of other files.
# rc: 0 installed and verified (or already the reviewed file, root:root 0600 with one link: nothing changed); 2 usage;
#     3 refused before any change (also the reviewed bytes with other owner / mode / link count);
#     5 the received bytes or the temp file failed (temp removed, nothing changed); 7 after a move a check did not
#     hold: the state is kept as it is, nothing is retried, report it.
set -u
D=/root/issue90
WANT='regular file|0|0|600|1'  # stat %F|%u|%g|%a|%h of an installed file (new, or already the reviewed one)
case "${1:-}" in
  i90-switch.py|i90-expected.json) ;;
  *) echo 'usage: i90-install.sh i90-switch.py|i90-expected.json <sha256> < file; nothing changed'; exit 2 ;;
esac
[ $# = 2 ] || { echo 'usage: the reviewed sha256 is required; nothing changed'; exit 2; }
N=$1
E=$2
case "$E" in *[!0-9a-f]*|'') echo 'sha256 is not lowercase hex; nothing changed'; exit 2 ;; esac
[ ${#E} = 64 ] || { echo 'sha256 is not 64 characters; nothing changed'; exit 2; }
[ "$(id -u)" = 0 ] || { echo 'not root; nothing changed'; exit 3; }
if [ ! -d "$D" ] || [ -L "$D" ]; then echo "$D is not a directory; nothing changed"; exit 3; fi
[ "$(stat -c '%u:%g:%a' "$D")" = '0:0:700' ] || { echo "$D is not root:root 700; nothing changed"; exit 3; }
cd "$D" || { echo "cd $D failed; nothing changed"; exit 3; }
old=
if [ -e "$N" ] || [ -L "$N" ]; then
  if [ -L "$N" ] || [ ! -f "$N" ]; then echo "$N exists and is not a regular file; nothing changed"; exit 3; fi
  if printf '%s  %s\n' "$E" "$N" | sha256sum -c - >/dev/null 2>&1; then
    # The same bytes count only with the installed metadata (review eba3ede major): never accept a file another
    # user can write, owned by another user, or with a second link.
    m=$(stat -c '%F|%u|%g|%a|%h' "$N") || { echo "stat $N failed; nothing changed"; exit 3; }
    [ "$m" = "$WANT" ] || { echo "$N has the reviewed bytes but is $m, not $WANT; nothing changed"; exit 3; }
    echo "$N is already the reviewed file $E ($m); nothing changed"; echo 'install rc=0'; exit 0
  fi
  for k in 0 1 2 3 4 5 6 7 8 9; do
    if [ ! -e "$N.r$k" ] && [ ! -L "$N.r$k" ]; then old=$N.r$k; break; fi
  done
  [ -n "$old" ] || { echo "$N.r0-$N.r9 all exist; nothing changed"; exit 3; }
fi
T=$(mktemp -p "$D" .i90-install-new.XXXXXX) || { echo 'temp file not created; nothing changed'; exit 5; }
cat > "$T" || { rm -f -- "$T"; echo 'stdin not received; nothing changed'; exit 5; }
if ! printf '%s  %s\n' "$E" "$T" | sha256sum -c - >/dev/null 2>&1; then
  rm -f -- "$T"; echo 'received bytes are not the reviewed file; nothing changed'; exit 5
fi
chown root:root "$T" && chmod 600 "$T" || { rm -f -- "$T"; echo 'owner/mode failed; nothing changed'; exit 5; }
if [ -n "$old" ]; then
  h=$(sha256sum "$N") || { rm -f -- "$T"; echo "$N unreadable; nothing changed"; exit 5; }
  echo "keep $N (sha256 ${h%% *}) as $old"
  mv -n -T "$N" "$old"
  if [ -e "$N" ] || [ -L "$N" ] || [ ! -f "$old" ]; then echo "keep move not proved; temp left at $T (rc 7)"; exit 7; fi
fi
mv -n -T "$T" "$N"
if [ -e "$T" ] || [ -L "$T" ]; then echo "install move not proved; temp left at $T (rc 7)"; exit 7; fi
# After the move: every lookup and check fails with rc 7; the state is left as it is.
printf '%s  %s\n' "$E" "$N" | sha256sum -c -; r=$?
echo "sha256 check after the move rc=$r"
[ "$r" = 0 ] || { echo "$N is not the reviewed file after the move (rc 7)"; exit 7; }
m=$(stat -c '%F|%u|%g|%a|%h' "$N"); r=$?
echo "stat $N rc=$r: $m"
[ "$r" = 0 ] || { echo 'stat failed after the move (rc 7)'; exit 7; }
[ "$m" = "$WANT" ] || { echo "$N is not a root:root 600 regular file with one link (rc 7)"; exit 7; }
l=$(ls -A "$D"); r=$?
echo "listing rc=$r"
[ "$r" = 0 ] || { echo 'listing failed: the temp count is unknown, not 0 (rc 7)'; exit 7; }
c=$(printf '%s\n' "$l" | grep -c '^\.i90-install-new\.')
echo "install temp files left: $c"
[ "$c" = 0 ] || { echo 'install temp files are left (rc 7)'; exit 7; }
echo 'install rc=0'
