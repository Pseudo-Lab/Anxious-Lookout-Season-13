#!/bin/sh
# #68 dev-VM V2b (plan): collect the exact bpftrace package set for the V6-3 exec observer. No package changes.
# PM approval 2026-11-05: development VM only. Root in the guest after review cleared this SHA-256.
#
# 1. Refuse without kernel BTF (/sys/kernel/btf/vmlinux): a kernel change or reboot is out of scope; report.
# 2. Snapshot the installed packages (rpm -qa raw, then sorted; each step's status checked).
# 3. `dnf --assumeno install bpftrace`: only new installs may appear (no upgrade, downgrade, removal, reinstall);
#    no kernel* package except exactly kernel-headers (PM 2026-11-05: userspace headers), no selinux-policy*,
#    container-selinux, k3s*, containerd*, runc, cri-*, crun or podman*; bpftrace must be in the plan. The parsed
#    plan must also equal the plan PM approved (APPROVED_ROWS_SHA, 33 new installs, issue comment 6653343354):
#    any other plan stops (rc 4) and is reported before anything is downloaded.
# 4. Disk: record df for / and the k3s data directory; refuse unless MIN_FREE_KB stays free on / after the
#    download and installed sizes (V3-V8 need room for Pods and images).
# 5. Download exactly the planned NEVRAs into $STAGE (new directory), then check: every staging entry is a regular
#    *.rpm (the full listing, hidden entries included, is recorded), one file per planned NEVRA with no duplicate,
#    `rpm -K` digests and signatures OK, and `rpm -ivh --test` (the exact transaction against the current rpmdb,
#    no resolution) succeeds.
# 6. Write the manifest (sha256, file, NEVRA). v2c-install.sh is written from this manifest and installs exactly
#    these files with rpm; nothing is resolved again.
# For packages and product settings the only change is $STAGE (downloaded files; dnf's own cache and log may also
# change). $STAGE is listed when the run stops after creating it.
# rc: 0 plan ready (or bpftrace already installed: nothing to plan); 3 refused, nothing changed; 4 the plan is out
#     of scope or differs from the approved plan (refused, nothing changed); 5 download or a check of the
#     downloaded files failed ($STAGE stays); 9 evidence incomplete after $STAGE was created; 130 interrupted.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
. "$here/evidence.sh"
STAGE=/root/native68/bpftrace-rpms
DNF_OPTS='-y --setopt=install_weak_deps=False'
APPROVED_ROWS_SHA=9666b00400340313be3cecbc903b77740c55011ea15fb1de4aa738993e584cbc
APPROVED_COUNT=33
NEED_KB=$(( (157 + 528) * 1024 ))   # download + installed size of the approved plan
MIN_FREE_KB=$(( 10 * 1024 * 1024 ))  # left free on / afterwards for V3-V8
K3S_DATA=/var/lib/rancher/k3s
trap 'trap "" INT TERM; say "interrupted; no package was changed by this script"; finish 130' INT TERM
fail() { say "$1"; finish "$2"; }

start_evidence v2b-plan
say "kernel $(uname -r)"
[ -r /sys/kernel/btf/vmlinux ] || refuse 'no kernel BTF; a kernel change is out of scope, report'
if rpm -q bpftrace >/dev/null 2>&1; then say "already installed: $(rpm -q bpftrace); nothing to plan"; finish 0; fi
dnf download --help >/dev/null 2>&1 || refuse 'dnf download is unavailable (dnf-plugins-core)'
if [ -e "$STAGE" ] || [ -L "$STAGE" ]; then refuse "$STAGE exists"; fi

# Installed packages: raw query and sort are separate checked steps; an empty or failed query is never a list.
raw=$(sidecar rpm-before-raw) || refuse 'sidecar'
rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' > "$raw" 2>/dev/null || refuse 'rpm -qa failed'
[ -s "$raw" ] || refuse 'rpm -qa returned nothing'
before=$(sidecar rpm-before) || refuse 'sidecar'
sort "$raw" > "$before" || refuse 'sort of the package list failed'
[ "$(wc -l < "$raw")" = "$(wc -l < "$before")" ] || refuse 'sorted list length differs'
h=$(sha256sum "$before") || refuse 'hash of the package list'
say "installed: $(wc -l < "$before") packages, ${h%% *} $before"

# Dry run: the table and summary are kept; dnf exits non-zero after --assumeno by design.
plan=$(sidecar dnf-plan) || refuse 'sidecar'
timeout 600 dnf $DNF_OPTS --assumeno install bpftrace > "$plan" 2>&1
grep -q '^Transaction Summary' "$plan" || refuse "dry run produced no transaction (see $plan)"
h=$(sha256sum "$plan") || refuse 'hash of the plan'
say "plan ${h%% *} $plan"
rows=$(sidecar dnf-plan-rows) || refuse 'sidecar'
# section|name|arch|version|repository; wrapped rows (long names) put the name alone on one line.
awk '/^[A-Z][A-Za-z ]*:$/{sec=$0; next} /^Transaction Summary/{exit}
     sec!="" && /^ [^ ]/ { if (NF >= 5) print sec"|"$1"|"$2"|"$3"|"$4; else { name=$1; wrapped=1 } next }
     sec!="" && wrapped && /^  +[^ ]/ { print sec"|"name"|"$1"|"$2"|"$3; wrapped=0 }' "$plan" > "$rows" || refuse 'plan parse'
[ -s "$rows" ] || refuse 'the plan lists no package'
outside=$(grep -Ev '^(Installing|Installing dependencies|Installing weak dependencies):\|' "$rows")
[ -z "$outside" ] || { say "out of scope (changes existing packages): $(echo "$outside" | tr '\n' ' ')"; finish 4; }
# PM scope (2026-11-05): kernel (except kernel-headers), SELinux policy and container packages are reported before
# any install (V0 recorded selinux-policy and container-selinux as the V3 baseline; they must not move).
kern=$(cut -d'|' -f2 "$rows" | grep -E '^(kernel|selinux-policy|container-selinux|k3s|containerd|runc$|cri-|crun$|podman)' \
  | grep -vx kernel-headers)
[ -z "$kern" ] || { say "out of scope (kernel, SELinux policy or container packages): $(echo "$kern" | tr '\n' ' ')"; finish 4; }
cut -d'|' -f2 "$rows" | grep -qx bpftrace || refuse 'bpftrace is not in the plan'
h=$(sha256sum "$rows") || refuse 'hash of the plan rows'
count=$(wc -l < "$rows")
say "plan rows ${h%% *} ($count packages)"
[ "${h%% *}" = "$APPROVED_ROWS_SHA" ] && [ "$count" = "$APPROVED_COUNT" ] \
  || { say "out of scope: the plan differs from the approved plan ($APPROVED_ROWS_SHA, $APPROVED_COUNT packages)"; finish 4; }
# Planned NEVRAs in rpm's form NAME-EPOCH:VERSION-RELEASE.ARCH (dnf omits epoch 0).
planned=$(awk -F'|' '{v=$4; if (index(v, ":") == 0) v="0:" v; print $2"-"v"."$3}' "$rows" | sort -u)
say "planned: $(echo "$planned" | tr '\n' ' ')"
spec=$(awk -F'|' '{print $2"-"$4"."$3}' "$rows" | sort -u | tr '\n' ' ')

# Disk: / and the k3s data directory (one filesystem or two), before anything is downloaded.
root_free=
for path in / "$K3S_DATA"; do
  d=$(df -Pk "$path" 2>/dev/null | awk 'NR==2{print $1" "$4" "$6}')
  [ -n "$d" ] || refuse "df $path"
  say "disk $path: filesystem $(echo "$d" | cut -d' ' -f1), available $(echo "$d" | cut -d' ' -f2) KiB, mount $(echo "$d" | cut -d' ' -f3)"
  [ "$path" = / ] && root_free=$(echo "$d" | cut -d' ' -f2)
done
case "$root_free" in ''|*[!0-9]*) refuse 'disk: no free size for /';; esac
[ $((root_free - NEED_KB)) -ge "$MIN_FREE_KB" ] \
  || refuse "disk: $root_free KiB free on /, $NEED_KB KiB needed, $MIN_FREE_KB KiB must stay free"
say "disk ok: $((root_free - NEED_KB)) KiB would stay free on /"

# Download exactly those NEVRAs; from here $STAGE exists.
changing
install -d -o root -g root -m 0700 "$STAGE" || fail 'cannot create the staging directory' 5
done_change "staging directory $STAGE"
out=$(timeout 900 dnf download --destdir="$STAGE" $spec 2>&1) || fail "dnf download failed: $(echo "$out" | tail -3 | tr '\n' ' ')" 5
listing=$(ls -lA "$STAGE" 2>&1) || fail 'staging listing' 5
while IFS= read -r line; do say "stage $line"; done <<EOF
$listing
EOF
entries=$(ls -A "$STAGE" | wc -l)
rpms=$(ls -A "$STAGE" | grep -c '\.rpm$')
want=$(echo "$planned" | wc -l)
say "stage entries $entries, rpm files $rpms, planned $want"
[ "$entries" = "$rpms" ] || fail 'staging holds entries other than *.rpm' 5
[ "$rpms" = "$want" ] || fail 'staging file count differs from the plan' 5
manifest=$(sidecar manifest) || fail 'sidecar' 5
got=
for f in "$STAGE"/*.rpm; do
  [ -f "$f" ] && [ ! -L "$f" ] || fail "unexpected staging entry $f" 5
  n=$(rpm -qp --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}' "$f" 2>/dev/null) && [ -n "$n" ] || fail "rpm -qp failed for $f" 5
  k=$(rpm -K "$f" 2>&1) || fail "signature check failed: $k" 5
  case "$k" in *'digests signatures OK') ;; *) fail "signature check not OK: $k" 5;; esac
  s=$(sha256sum "$f") || fail "hash of $f" 5
  printf '%s  %s  %s\n' "${s%% *}" "$(basename "$f")" "$n" >> "$manifest" || fail 'manifest write' 5
  got="$got
$n"
done
got=$(echo "$got" | sed '/^$/d' | sort)
dups=$(echo "$got" | uniq -d)
[ -z "$dups" ] || fail "duplicate NEVRA in staging: $(echo "$dups" | tr '\n' ' ')" 5
[ "$got" = "$planned" ] || fail "downloaded set differs from the plan: $(echo "$got" | tr '\n' ' ')" 5
t=$(rpm -ivh --test "$STAGE"/*.rpm 2>&1) || fail "rpm -ivh --test failed: $(echo "$t" | tail -3 | tr '\n' ' ')" 5
say 'rpm -ivh --test: ok'
while IFS= read -r line; do say "manifest $line"; done < "$manifest"
h=$(sha256sum "$manifest") || fail 'hash of the manifest' 5
say "manifest ${h%% *} $manifest"
finish 0
