#!/bin/sh
# #68 dev-VM V2b: install bpftrace for the V6-3 exec observer (PM approval 2026-11-05: development VM only, package
# name, version and state before/after recorded; whether it stays is decided at V9 and recorded in PARITY).
# Root in the guest after review cleared this SHA-256. Never on the production node. Loads no probe (V4 does).
#
# 1. Refuse without kernel BTF (/sys/kernel/btf/vmlinux): a kernel change or reboot is out of scope; report.
# 2. Snapshot every installed package (rpm -qa, sorted) to a sidecar evidence file.
# 3. Dry run (`dnf --assumeno install`): the plan may only install new packages. Any upgrade, downgrade, removal,
#    reinstall, or a kernel* package refuses before anything changes. The plan is kept in a sidecar.
# 4. Install with the same options, then snapshot again, keep `dnf history info last` and the before/after diff.
#    Success needs: dnf rc 0, the diff only adds packages, exactly the planned ones, and `bpftrace --version` runs.
# A failed, timed-out or interrupted install is a possible partial change (dnf's own state included): rc 7, no
# automatic undo; recovery is decided from the recorded transaction and the before snapshot.
# rc: 0 installed and verified (or already installed: no change); 3 refused, nothing changed; 7 install failed or
#     interrupted, partial change possible; 8 installed but the diff or `bpftrace --version` check failed;
#     9 evidence incomplete after the change.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
. "$here/evidence.sh"
DNF_OPTS='-y --setopt=install_weak_deps=False'
inflight=0
trap 'trap "" INT TERM; say "interrupted (install in flight: $inflight)"; [ "$inflight" = 1 ] && say "partial change possible: check dnf history and rpm -qa against the before snapshot"; finish 7' INT TERM

snapshot() {  # $1 sidecar name; prints the path; non-zero on failure
  f=$(sidecar "$1") || return 1
  rpm -qa --qf '%{NAME}-%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n' 2>/dev/null | sort > "$f" || return 1
  [ -s "$f" ] || return 1
  printf '%s\n' "$f"
}

start_evidence v2b
say "kernel $(uname -r)"
[ -r /sys/kernel/btf/vmlinux ] || refuse 'no kernel BTF; a kernel change is out of scope, report'
if rpm -q bpftrace >/dev/null 2>&1; then
  say "already installed: $(rpm -q bpftrace)"
  v=$(bpftrace --version 2>&1) || { say "bpftrace --version failed: $v"; finish 8; }
  say "bpftrace version: $v"
  finish 0
fi
before=$(snapshot rpm-before) || refuse 'package snapshot before'
say "before: $(wc -l < "$before") packages, $(sha256sum "$before" | cut -d' ' -f1) $before"
say "dnf history last before: $(dnf -q history list 2>/dev/null | awk 'NR==3{print $1}')"

# Dry run: dnf prints the transaction table and aborts (non-zero) without changing anything.
plan=$(sidecar dnf-plan) || refuse 'plan sidecar'
timeout 600 dnf $DNF_OPTS --assumeno install bpftrace > "$plan" 2>&1
grep -q '^Transaction Summary' "$plan" || refuse "dry run produced no transaction (see $plan)"
sections=$(awk '/^[A-Z][A-Za-z ]*:$/{print}' "$plan" | sort -u | tr '\n' ' ')
say "plan sections: $sections"
case " $sections" in *' Installing:'*) ;; *) refuse 'plan has no Installing section';; esac
pkgs=$(sidecar dnf-plan-packages) || refuse 'plan packages sidecar'
awk '/^[A-Z][A-Za-z ]*:$/{sec=$0; next} /^Transaction Summary/{exit} sec!="" && /^ [^ ]/{print sec"|"$1}' "$plan" > "$pkgs" \
  || refuse 'plan parse'
bad=$(grep -Ev '^(Installing|Installing dependencies|Installing weak dependencies):\|' "$pkgs")
[ -z "$bad" ] || refuse "plan changes existing packages: $(echo "$bad" | tr '\n' ' ')"
kern=$(cut -d'|' -f2 "$pkgs" | grep -E '^kernel' || true)
[ -z "$kern" ] || refuse "plan includes kernel packages: $kern"
planned=$(cut -d'|' -f2 "$pkgs" | sort -u)
[ -n "$planned" ] && echo "$planned" | grep -qx bpftrace || refuse 'bpftrace is not in the plan'
say "plan installs: $(echo "$planned" | tr '\n' ' ')"
say "plan $(sha256sum "$plan" | cut -d' ' -f1) $plan"

# Install.
changing; inflight=1
out=$(timeout 900 dnf $DNF_OPTS install bpftrace 2>&1); r=$?
inflight=0
say "dnf rc $r"
lines=$(printf '%s\n' "$out" | grep -E '^(Installing|Installed|Upgrading|Removing|Complete|Error|Running transaction)')
while IFS= read -r line; do [ -z "$line" ] || say "dnf $line"; done <<EOF
$lines
EOF
history=$(sidecar dnf-history) && dnf -q history info last > "$history" 2>&1 \
  && say "dnf history info last $(sha256sum "$history" | cut -d' ' -f1) $history" || say 'dnf history info: LOOKUP FAILED'
after=$(snapshot rpm-after) || { say 'package snapshot after: FAILED'; [ "$r" = 0 ] && finish 8; finish 7; }
say "after: $(wc -l < "$after") packages, $(sha256sum "$after" | cut -d' ' -f1) $after"
added=$(comm -13 "$before" "$after"); removed=$(comm -23 "$before" "$after")
say "added: $(echo "$added" | tr '\n' ' ')"
say "removed or changed: $(echo "$removed" | tr '\n' ' ')"
[ "$r" = 0 ] || { say 'install failed: partial change possible; recover from the recorded transaction and the before snapshot'; finish 7; }
done_change "packages added: $(echo "$added" | sed 's/-[0-9]*:.*//' | tr '\n' ' ')"
names=$(echo "$added" | sed 's/-[0-9]*:.*//' | sort -u)
[ -z "$removed" ] && [ "$names" = "$planned" ] || { say 'the change differs from the plan'; finish 8; }
v=$(bpftrace --version 2>&1) || { say "bpftrace --version failed: $v"; finish 8; }
say "bpftrace version: $v"
finish 0
