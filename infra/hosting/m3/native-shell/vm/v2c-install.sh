#!/bin/sh
# #68 dev-VM V2c: install exactly the reviewed bpftrace package set (v2c-manifest.txt, written by v2b-plan.sh at
# 2026-11-05T08:31:18Z) with rpm, nothing resolved again. PM approval 2026-11-05: development VM only, 33 new
# installs, kernel-headers the only kernel* package. Root in the guest after review cleared this SHA-256.
#
# Execution condition (exclusive window, PM 2026-11-05): during this one install nobody else changes packages on
# the development VM (only the back session works there; PM tells the user). The script reads, never changes, the
# automatic package jobs (dnf-automatic*, dnf-makecache, packagekit): it records each unit's state and next run and
# refuses when one is running or due within WINDOW_S; it refuses when a package manager process runs or the rpm DB
# lock is held (a failed lookup refuses too), checks the installed set against the plan baseline right before the
# install, and afterwards requires the change to be exactly the manifest set, so a concurrent change is detected.
#
# Before (nothing changes; any mismatch refuses, rc 3):
#   - the manifest equals MANIFEST_SHA; $STAGE holds exactly its 33 files, each with its digest, NEVRA and an
#     `rpm -K` that exits 0 with "digests signatures OK";
#   - the installed packages equal the plan baseline (BASELINE_SHA, 423 NEVRAs), checked twice;
#   - SELinux Enforcing; kernel and selinux-policy / container-selinux equal the V0 values; k3s and containerd
#     versions, sestatus, the loaded module list and audit status are collected raw (each command's status and a
#     non-empty result checked) and kept as the pre-install baseline (not in V0: compared before/after only);
#   - the ausearch time form is tried once; disk on / leaves MIN_FREE_KB; `rpm -ivh --test` with the same files.
# Install: one `rpm -ivh <the 33 files>`; stdout/stderr to a sidecar, its hash and exit code recorded.
# After: wait until no rpm process runs, snapshot again; success needs rpm rc 0, a hashed rpm log, the diff adding
#   exactly the manifest NEVRAs and removing or changing nothing, the collected state equal to the pre-install
#   state, and `bpftrace --version`. AVC and USER_AVC from the install window are counted separately.
# Once the install was attempted, any stop (failure, missing evidence, signal) is a possible partial change: rc 7,
# or rc 9 when rpm succeeded but evidence is incomplete; never rc 3. No automatic undo.
# rc: 0 installed and verified; 3 refused, nothing changed; 7 install failed or interrupted after it started
#     (partial change possible, post-check incomplete); 8 installed but a postcheck differs; 9 evidence incomplete.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
. "$here/evidence.sh"
STAGE=/root/native68/bpftrace-rpms
MANIFEST="$here/v2c-manifest.txt"
MANIFEST_SHA=cb04c91e1d3d0e3765f560346223c61f511c72c74d8bc3b21210d3e068d059ea
BASELINE_SHA=059d6127329f69dbc2c3b59fee40467d5d129214888aa6e815c415e1427d1dc6
V0_KERNEL=6.12.0-204.92.4.2.el9uek.x86_64
V0_POLICY='selinux-policy-38.1.65-1.0.1.el9.noarch container-selinux-2.240.0-3.el9_7.noarch'
NEED_KB=$(( 528 * 1024 ))
MIN_FREE_KB=$(( 10 * 1024 * 1024 ))
AUTO_UNITS='dnf-automatic.timer dnf-automatic-install.timer dnf-automatic-download.timer dnf-automatic-notifyonly.timer
dnf-makecache.timer dnf-makecache.service packagekit.service'
WINDOW_S=2700  # rpm timeout 1800 s + end wait 120 s + snapshots and post-check, with margin (review)
inflight=0; attempted=0; evidence_missing=

pkg_busy() {  # 0 busy, 1 idle; any other pgrep status is a failed lookup (2)
  pgrep -x 'rpm|dnf|yum|dnf-3|packagekitd|dnf-automatic' >/dev/null 2>&1; s=$?
  case "$s" in 0) return 0;; 1) return 1;; *) return 2;; esac
}
snapshot() {  # $1 sidecar name: rpm -qa and sort, each checked; prints the sorted file
  r=$(sidecar "$1-raw") || return 1
  rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' > "$r" 2>/dev/null || return 1
  [ -s "$r" ] || return 1
  o=$(sidecar "$1") || return 1
  sort "$r" > "$o" || return 1
  [ "$(wc -l < "$r")" = "$(wc -l < "$o")" ] || return 1
  printf '%s\n' "$o"
}
raw() {  # $1 sidecar name, then the command: run it into the sidecar; fails on non-zero status or empty output
  name=$1; shift
  f=$(sidecar "$name") || return 1
  "$@" > "$f" 2>/dev/null || return 1
  [ -s "$f" ] || return 1
  printf '%s\n' "$f"
}
collect_state() {  # $1 pre|post: raw sidecars, then one derived state file; prints its path; non-zero on any failure
  p=$1
  se=$(raw "$p-sestatus" sestatus) || return 1
  mo=$(raw "$p-semodule" semodule -l) || return 1
  kv=$(raw "$p-k3s-version" "$K3S" --version) || return 1
  cv=$(raw "$p-ctr-version" "$K3S" ctr version) || return 1
  au=$(raw "$p-auditctl" auditctl -s) || return 1
  po=$(raw "$p-policy-packages" rpm -q selinux-policy container-selinux) || return 1
  en=$(raw "$p-getenforce" getenforce) || return 1
  containerd=$(awk '/^Server:/{s=1} s && /Version:/{print $2; exit}' "$cv") && [ -n "$containerd" ] || return 1
  se_h=$(sha256sum "$se") || return 1; se_h=${se_h%% *}; [ ${#se_h} = 64 ] || return 1
  mo_h=$(sha256sum "$mo") || return 1; mo_h=${mo_h%% *}; [ ${#mo_h} = 64 ] || return 1
  mo_n=$(wc -l < "$mo") || return 1; [ "$mo_n" -gt 0 ] 2>/dev/null || return 1
  k3s_line=$(head -1 "$kv") && [ -n "$k3s_line" ] || return 1
  policy=$(tr '\n' ' ' < "$po") && [ -n "$policy" ] || return 1
  enforce=$(cat "$en") && [ -n "$enforce" ] || return 1
  audit=
  for field in enabled backlog_limit failure; do
    line=$(grep -E "^$field " "$au") && [ -n "$line" ] || return 1
    audit="$audit$line "
  done
  st=$(sidecar "$p-state") || return 1
  printf 'kernel %s\npolicy packages %s\nselinux %s\nsestatus %s\nloaded modules %s (%s)\nk3s %s\ncontainerd %s\naudit %s\n' \
    "$(uname -r)" "$policy" "$enforce" "$se_h" "$mo_h" "$mo_n" "$k3s_line" "$containerd" "$audit" > "$st" || return 1
  [ "$(wc -l < "$st")" = 8 ] || return 1
  printf '%s\n' "$st"
}
avc_count() {  # $1 type: prints the count since the install window start; non-zero on an ausearch error
  # Default output, not --format raw: with raw, "no match" is a silent rc 1 that cannot be told from an error;
  # the default prints "<no matches>" for that and a message for a bad time or type (measured on the guest).
  out=$(ausearch -m "$1" -ts "$avc_date" "$avc_time" 2>&1); s=$?
  case "$s" in
    0) printf '%s\n' "$out" | grep -c "^type=$1 ";;
    1) case "$out" in *'no matches'*) echo 0;; *) return 1;; esac;;
    *) return 1;;
  esac
}
stop_after_attempt() {  # $1 reason: collect what can be collected, never rc 3
  say "stopped after the install was attempted: $1"
  say 'partial change possible; post-check incomplete: wait until no rpm process runs, collect rpm -qa, compare with the baseline, report'
  finish 7
}
on_signal() {
  trap '' INT TERM
  say "interrupted (rpm in flight: $inflight, install attempted: $attempted)"
  [ "$attempted" = 1 ] && stop_after_attempt 'signal'
  finish 3
}
trap on_signal INT TERM

start_evidence v2c
# Manifest and staging.
[ "$(sha256sum "$MANIFEST" 2>/dev/null | cut -d' ' -f1)" = "$MANIFEST_SHA" ] || refuse 'manifest digest'
[ -d "$STAGE" ] && [ ! -L "$STAGE" ] || refuse "$STAGE missing"
entries=$(ls -A "$STAGE" 2>/dev/null | wc -l)
[ "$entries" = "$(wc -l < "$MANIFEST")" ] || refuse "staging holds $entries entries, the manifest $(wc -l < "$MANIFEST")"
files=; nevras=
while read -r sha file nevra; do
  f="$STAGE/$file"
  [ -f "$f" ] && [ ! -L "$f" ] || refuse "$file missing or not a regular file"
  [ "$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)" = "$sha" ] || refuse "$file digest"
  [ "$(rpm -qp --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}' "$f" 2>/dev/null)" = "$nevra" ] || refuse "$file NEVRA"
  k=$(rpm -K "$f" 2>&1) || refuse "$file signature check exited non-zero: $k"
  case "$k" in *'digests signatures OK') ;; *) refuse "$file signature: $k";; esac
  files="$files $f"; nevras="$nevras
$nevra"
done < "$MANIFEST"
nevras=$(echo "$nevras" | sed '/^$/d' | sort)
say "manifest ok: $(echo "$nevras" | wc -l) files, digests, NEVRAs and signatures match"

# Exclusive window (read only): automatic package jobs, package manager processes, the rpm DB lock.
check_auto() {  # $1 label; refuses on a running job, a job due within WINDOW_S, or any lookup it cannot read
  label=$1  # kept: the cron loop below reuses the positional parameters
  now=$(date +%s) || refuse 'clock'
  for u in $AUTO_UNITS; do
    load=$(systemctl show -P LoadState "$u" 2>/dev/null) && [ -n "$load" ] || refuse "$label: cannot read the load state of $u"
    if [ "$load" = not-found ]; then say "$label unit $u not installed"; continue; fi
    act=$(systemctl show -P ActiveState "$u" 2>/dev/null) && [ -n "$act" ] || refuse "$label: cannot read the active state of $u"
    file=$(systemctl show -P UnitFileState "$u" 2>/dev/null) || refuse "$label: cannot read the unit file state of $u"
    case "$u" in
      *.service)
        say "$label unit $u load=$load active=$act file=${file:-none}"
        case "$act" in inactive|failed) ;; *) refuse "$label: $u is $act";; esac;;
      *.timer)
        if [ "$act" != active ]; then
          case "$act" in inactive|failed) say "$label unit $u load=$load active=$act file=${file:-none} (not armed)"; continue;;
                         *) refuse "$label: $u is $act";; esac
        fi
        # The next elapse as systemd computed it (calendar and monotonic timers alike, randomized delay included):
        # list-timers JSON "next" in microseconds since the epoch. NextElapseUSecRealtime is empty for monotonic
        # timers such as dnf-makecache (OnBootSec/OnUnitInactiveSec), so it is recorded but not used.
        lt=$(systemctl list-timers --all -o json "$u" 2>/dev/null) || refuse "$label: cannot list the timer $u"
        nextus=$(printf '%s' "$lt" | python3 -I -c '
import json, sys
rows = [r for r in json.load(sys.stdin) if r.get("unit") == sys.argv[1]]
value = rows[0].get("next") if len(rows) == 1 else None
print(value if isinstance(value, int) and value > 0 else "")' "$u" 2>/dev/null) || refuse "$label: cannot parse the timer list for $u"
        [ -n "$nextus" ] || refuse "$label: $u is armed but its next run is unknown"
        due=$((nextus / 1000000))
        real=$(systemctl show -P NextElapseUSecRealtime "$u" 2>/dev/null) || refuse "$label: cannot read NextElapseUSecRealtime of $u"
        mono=$(systemctl show -P NextElapseUSecMonotonic "$u" 2>/dev/null) || refuse "$label: cannot read NextElapseUSecMonotonic of $u"
        delay=$(systemctl show -P RandomizedDelayUSec "$u" 2>/dev/null) && [ -n "$delay" ] || refuse "$label: cannot read the randomized delay of $u"
        persist=$(systemctl show -P Persistent "$u" 2>/dev/null) && [ -n "$persist" ] || refuse "$label: cannot read Persistent of $u"
        next=$(date -u -d "@$due" +%Y-%m-%dT%H:%M:%SZ) || refuse "$label: cannot format the next run of $u"
        say "$label unit $u load=$load active=$act file=${file:-none} next=$next (in $((due - now)) s; realtime=${real:-empty} monotonic=${mono:-empty}) randomized_delay=$delay persistent=$persist"
        [ $((due - now)) -gt "$WINDOW_S" ] || refuse "$label: $u is due at $next, within the install window; check again after it";;
    esac
  done
  # Other known automation: cron entries that run a package manager. Only existing files are read; a read error refuses.
  for c in /etc/crontab /etc/cron.d /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly /var/spool/cron; do
    [ -e "$c" ] || continue
    if [ -d "$c" ]; then set -- "$c"/*; else set -- "$c"; fi
    for f in "$@"; do
      [ -e "$f" ] || continue
      [ -f "$f" ] || refuse "$label: cron entry $f is not a regular file"
      grep -qE '(^|[^a-z])(dnf|yum|rpm)([^a-z]|$)' "$f"; g=$?
      case "$g" in 0) refuse "cron entry $f runs a package manager";; 1) ;; *) refuse "cannot read cron entry $f";; esac
    done
  done
  say "$label cron: no package manager entries"
}
check_auto start
pkg_busy; s=$?; [ "$s" = 1 ] || refuse "package manager process check: $( [ "$s" = 0 ] && echo running || echo 'lookup failed')"
dbpath=$(rpm --eval '%_dbpath' 2>/dev/null) && [ -d "$dbpath" ] || refuse 'rpm DB path'
lock="$dbpath/.rpm.lock"
if [ -e "$lock" ]; then
  flock -n -s "$lock" true 2>/dev/null || refuse "the rpm DB lock $lock is held"
  say "rpm DB lock $lock free"
else
  say "rpm DB lock $lock absent"
fi

# The system as planned.
before=$(snapshot rpm-before) || refuse 'rpm snapshot before'
h=$(sha256sum "$before") || refuse 'hash of the before snapshot'
say "before: $(wc -l < "$before") packages ${h%% *}"
[ "${h%% *}" = "$BASELINE_SHA" ] || refuse 'installed packages differ from the plan baseline'
pre=$(collect_state pre) || refuse 'a state lookup failed (raw outputs kept as pre-* sidecars)'
while IFS= read -r line; do say "pre $line"; done < "$pre"
[ "$(uname -r)" = "$V0_KERNEL" ] || refuse 'running kernel differs from V0'
[ "$(rpm -q selinux-policy container-selinux | tr '\n' ' ' | sed 's/ $//')" = "$V0_POLICY" ] || refuse 'policy packages differ from V0'
[ "$(getenforce)" = Enforcing ] || refuse 'SELinux is not Enforcing'
free=$(df -Pk / 2>/dev/null | awk 'NR==2{print $4}')
case "$free" in ''|*[!0-9]*) refuse 'df /';; esac
say "disk /: $free KiB available"
[ $((free - NEED_KB)) -ge "$MIN_FREE_KB" ] || refuse 'disk: too little space would stay free'
avc_date=$(date +%x) && avc_time=$(date +%T) || refuse 'clock'
for t in AVC USER_AVC; do c=$(avc_count "$t") || refuse "ausearch does not accept the window start ($avc_date $avc_time)"; done
say "install window starts $avc_date $avc_time (ausearch -ts form, checked)"
t=$(rpm -ivh --test $files 2>&1) || refuse "rpm -ivh --test: $(echo "$t" | tail -3 | tr '\n' ' ')"
say 'rpm -ivh --test: ok'
# Right before the install: no job due within the whole window from now, still idle, lock free, still the baseline.
check_auto 'before rpm'
pkg_busy; s=$?; [ "$s" = 1 ] || refuse 'a package manager process started (or the lookup failed) before the install'
[ ! -e "$lock" ] || flock -n -s "$lock" true 2>/dev/null || refuse 'the rpm DB lock is held before the install'
again=$(snapshot rpm-before-again) || refuse 'rpm snapshot right before the install'
[ "$(sha256sum "$again" | cut -d' ' -f1)" = "$BASELINE_SHA" ] || refuse 'installed packages changed before the install'

# Install exactly these files.
rpmlog=$(sidecar rpm-install-log) || refuse 'sidecar'
changing; attempted=1; inflight=1
timeout 1800 rpm -ivh $files > "$rpmlog" 2>&1; r=$?
inflight=0
say "rpm -ivh rc $r"
h=$(sha256sum "$rpmlog" 2>/dev/null) && [ -s "$rpmlog" ] && say "rpm log ${h%% *} $rpmlog" \
  || { say 'rpm log missing or not hashable'; evidence_missing="$evidence_missing rpm-log"; }
waited=0
while :; do
  pkg_busy; s=$?
  [ "$s" = 1 ] && break
  [ "$s" = 2 ] && stop_after_attempt 'package manager process lookup failed'
  [ "$waited" -ge 120 ] && stop_after_attempt 'an rpm process still runs after 120 s'
  sleep 2; waited=$((waited + 2))
done
after=$(snapshot rpm-after) || stop_after_attempt 'rpm snapshot after failed'
h=$(sha256sum "$after") || stop_after_attempt 'hash of the after snapshot failed'
say "after: $(wc -l < "$after") packages ${h%% *}"
added=$(comm -13 "$before" "$after") || stop_after_attempt 'comm failed'
removed=$(comm -23 "$before" "$after") || stop_after_attempt 'comm failed'
say "added $(echo "$added" | sed '/^$/d' | wc -l): $(echo "$added" | tr '\n' ' ')"
say "removed or changed $(echo "$removed" | sed '/^$/d' | wc -l): $(echo "$removed" | tr '\n' ' ')"
[ "$r" = 0 ] || stop_after_attempt "rpm -ivh exited $r"
done_change "rpm installed $(echo "$added" | sed '/^$/d' | wc -l) packages from $MANIFEST"

bad=
[ -z "$removed" ] || bad="$bad removed"
[ "$added" = "$nevras" ] || bad="$bad added-set"
post=$(collect_state post) || { bad="$bad state-lookup"; post=; }
[ -n "$post" ] && while IFS= read -r line; do say "post $line"; done < "$post"
[ -n "$post" ] && cmp -s "$pre" "$post" || bad="$bad state-changed"
for t in AVC USER_AVC; do
  c=$(avc_count "$t") && say "$t during install window: $c" || { say "$t during install window: LOOKUP FAILED"; bad="$bad avc-lookup"; }
done
v=$(bpftrace --version 2>&1) && say "bpftrace version: $v" || { say "bpftrace --version failed: $v"; bad="$bad bpftrace"; }
[ -z "$evidence_missing" ] || { say "evidence incomplete:$evidence_missing"; finish 9; }
[ -z "$bad" ] || { say "postcheck failed:$bad"; finish 8; }
finish 0
