#!/bin/sh
# #68 dev-VM V2c: install exactly the reviewed bpftrace package set (v2c-manifest.txt, written by v2b-plan.sh at
# 2026-11-05T08:31:18Z) with rpm, nothing resolved again. PM approval 2026-11-05: development VM only, 33 new
# installs, kernel-headers the only kernel* package. Root in the guest after review cleared this SHA-256.
#
# Before (nothing changes; any mismatch refuses):
#   - the installed manifest equals MANIFEST_SHA; $STAGE holds exactly its 33 files, each with its digest, NEVRA,
#     and `rpm -K` "digests signatures OK";
#   - the installed packages equal the plan's baseline snapshot exactly (BASELINE_SHA, 423 NEVRAs);
#   - no rpm, dnf, yum or packagekitd process runs;
#   - SELinux Enforcing; kernel and selinux-policy / container-selinux equal the V0 values; k3s and containerd
#     versions, sestatus, the loaded module list and audit status are recorded as the pre-install baseline
#     (they are not in V0, so the comparison is before/after this install only);
#   - disk on / leaves MIN_FREE_KB after the installed size; `rpm -ivh --test` with the same files and options.
# Install: one `rpm -ivh <the 33 files>`; its stdout/stderr go to a sidecar evidence file, its exit code is recorded.
# After: wait until no rpm process runs, snapshot again; success needs rpm rc 0, the diff adds exactly the manifest
#   NEVRAs and removes or changes nothing, the recorded versions/state equal the pre-install values, and
#   `bpftrace --version` runs. AVC records from the install window are counted separately (from a recorded start
#   time) so they are never mixed with V3's test Pod.
# A failed, timed-out or interrupted install is a possible partial change: rc 7, the after snapshot and rpm log are
# still collected when rpm has ended, no automatic undo (recovery from the exact before/after sets).
# rc: 0 installed and verified; 3 refused, nothing changed; 7 install failed or interrupted (partial change
#     possible); 8 installed but a postcheck differs; 9 evidence incomplete after the change.
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
inflight=0

snapshot() {  # $1 sidecar name: rpm -qa and sort, each checked; prints the sorted file
  r=$(sidecar "$1-raw") || return 1
  rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' > "$r" 2>/dev/null || return 1
  [ -s "$r" ] || return 1
  o=$(sidecar "$1") || return 1
  sort "$r" > "$o" || return 1
  [ "$(wc -l < "$r")" = "$(wc -l < "$o")" ] || return 1
  printf '%s\n' "$o"
}
pkg_busy() { pgrep -x 'rpm|dnf|yum|dnf-3|packagekitd' >/dev/null 2>&1; }
state_lines() {  # the recorded system state, one line each; non-zero if a lookup failed
  st_failed=0
  printf 'kernel %s\n' "$(uname -r)"
  p=$(rpm -q selinux-policy container-selinux 2>/dev/null | tr '\n' ' ') || st_failed=1
  printf 'policy packages %s\n' "$p"
  e=$(getenforce 2>/dev/null) || st_failed=1; printf 'selinux %s\n' "$e"
  s=$(sestatus 2>/dev/null | tr -s ' ' | tr '\n' ';') || st_failed=1; printf 'sestatus %s\n' "$s"
  m=$(semodule -l 2>/dev/null | sha256sum | cut -d' ' -f1) && [ -n "$m" ] || st_failed=1
  printf 'loaded modules %s (%s)\n' "$m" "$(semodule -l 2>/dev/null | wc -l)"
  k=$("$K3S" --version 2>/dev/null | head -1) || st_failed=1; printf 'k3s %s\n' "$k"
  c=$("$K3S" ctr version 2>/dev/null | awk '/^Server:/{s=1} s && /Version:/{print $2; exit}') && [ -n "$c" ] || st_failed=1
  printf 'containerd %s\n' "$c"
  a=$(auditctl -s 2>/dev/null | grep -E '^(enabled|lost|backlog_limit) ' | tr '\n' ' ') || st_failed=1
  printf 'audit %s\n' "$a"
  return $st_failed
}
on_signal() {
  trap '' INT TERM
  say "interrupted (install in flight: $inflight)"
  if [ "$inflight" = 1 ]; then
    say 'partial change possible; wait for rpm to end, then collect the after snapshot by hand and compare with the baseline'
    finish 7
  fi
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
  k=$(rpm -K "$f" 2>&1); case "$k" in *'digests signatures OK') ;; *) refuse "$file signature: $k";; esac
  files="$files $f"; nevras="$nevras
$nevra"
done < "$MANIFEST"
nevras=$(echo "$nevras" | sed '/^$/d' | sort)
say "manifest ok: $(echo "$nevras" | wc -l) files, digests, NEVRAs and signatures match"

# The system as planned: package set, no concurrent package work, policy and kernel as in V0.
pkg_busy && refuse 'a package manager process is running'
before=$(snapshot rpm-before) || refuse 'rpm snapshot before'
h=$(sha256sum "$before") || refuse 'hash of the before snapshot'
say "before: $(wc -l < "$before") packages ${h%% *}"
[ "${h%% *}" = "$BASELINE_SHA" ] || refuse 'installed packages differ from the plan baseline'
pre=$(state_lines) || refuse 'a state lookup failed'
while IFS= read -r line; do say "pre $line"; done <<EOF
$pre
EOF
[ "$(uname -r)" = "$V0_KERNEL" ] || refuse 'running kernel differs from V0'
[ "$(rpm -q selinux-policy container-selinux | tr '\n' ' ' | sed 's/ $//')" = "$V0_POLICY" ] || refuse 'policy packages differ from V0'
[ "$(getenforce)" = Enforcing ] || refuse 'SELinux is not Enforcing'
free=$(df -Pk / 2>/dev/null | awk 'NR==2{print $4}')
case "$free" in ''|*[!0-9]*) refuse 'df /';; esac
say "disk /: $free KiB available"
[ $((free - NEED_KB)) -ge "$MIN_FREE_KB" ] || refuse 'disk: too little space would stay free'
t=$(rpm -ivh --test $files 2>&1) || refuse "rpm -ivh --test: $(echo "$t" | tail -3 | tr '\n' ' ')"
say 'rpm -ivh --test: ok'
avc_from=$(date '+%m/%d/%Y %H:%M:%S') || refuse 'clock'
say "install window starts $avc_from (local time, for ausearch)"

# Install exactly these files.
rpmlog=$(sidecar rpm-install-log) || refuse 'sidecar'
changing; inflight=1
timeout 1800 rpm -ivh $files > "$rpmlog" 2>&1; r=$?
inflight=0
say "rpm -ivh rc $r"
h=$(sha256sum "$rpmlog") && say "rpm log ${h%% *} $rpmlog" || say 'rpm log hash: LOOKUP FAILED'
waited=0
while pkg_busy && [ "$waited" -lt 120 ]; do sleep 2; waited=$((waited + 2)); done
pkg_busy && { say 'an rpm process is still running after 120 s; check by hand'; finish 7; }
after=$(snapshot rpm-after) || { say 'rpm snapshot after: FAILED'; [ "$r" = 0 ] && finish 9; finish 7; }
h=$(sha256sum "$after") || { say 'hash of the after snapshot: FAILED'; finish 9; }
say "after: $(wc -l < "$after") packages ${h%% *}"
added=$(comm -13 "$before" "$after") || { say 'comm failed'; finish 9; }
removed=$(comm -23 "$before" "$after") || { say 'comm failed'; finish 9; }
say "added $(echo "$added" | sed '/^$/d' | wc -l): $(echo "$added" | tr '\n' ' ')"
say "removed or changed $(echo "$removed" | sed '/^$/d' | wc -l): $(echo "$removed" | tr '\n' ' ')"
[ "$r" = 0 ] || { say 'install failed: partial change possible; recover from the before and after sets'; finish 7; }
done_change "rpm installed $(echo "$added" | sed '/^$/d' | wc -l) packages from $MANIFEST"

bad=
[ -z "$removed" ] || bad="$bad removed"
[ "$added" = "$nevras" ] || bad="$bad added-set"
post=$(state_lines) || bad="$bad state-lookup"
while IFS= read -r line; do say "post $line"; done <<EOF
$post
EOF
[ "$post" = "$pre" ] || bad="$bad state-changed"
out=$(ausearch -m AVC,USER_AVC -ts "$avc_from" --format raw 2>/dev/null); a=$?
case "$a" in 0) say "avc during install window $(echo "$out" | grep -c '^type=AVC')";; 1) say 'avc during install window 0';; *) say "ausearch rc $a"; bad="$bad avc-lookup";; esac
v=$(bpftrace --version 2>&1) || bad="$bad bpftrace"
say "bpftrace version: $v"
[ -z "$bad" ] || { say "postcheck failed:$bad"; finish 8; }
finish 0
