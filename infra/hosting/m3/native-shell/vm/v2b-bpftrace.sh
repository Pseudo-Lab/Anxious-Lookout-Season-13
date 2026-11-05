#!/bin/sh
# #68 dev-VM V2b: install bpftrace for the V6-3 exec observer (PM approval 2026-11-05: development VM only, package
# name, version and state before/after recorded; whether it stays is decided at V9 and recorded in PARITY).
# Root in the guest after review cleared this SHA-256. Never on the production node.
# Refuses without kernel BTF (/sys/kernel/btf/vmlinux): a kernel change or reboot is out of scope; report instead.
# Installs the one package `bpftrace` with dnf from the guest's configured repositories (dnf is transactional:
# a failed install leaves no partial package). Records rpm -q before/after and the transaction's package list.
# It does not load any probe; the observer and its self-check are V4.
# rc: 0 installed (or already installed) and runnable; 3 refused, nothing changed; 5 dnf failed (nothing installed);
#     8 installed but `bpftrace --version` fails; 9 evidence incomplete after a change.
set -u
umask 077
here=$(dirname "$0")
. "$here/lib.sh"
. "$here/evidence.sh"
trap 'say "interrupted: check rpm -q bpftrace and dnf history before any later stage"; exit 130' INT TERM

start_evidence v2b
say "kernel $(uname -r)"
[ -r /sys/kernel/btf/vmlinux ] || refuse 'no kernel BTF; a kernel change is out of scope, report'
say "before: $(rpm -q bpftrace 2>&1)"
say "dnf history before: $(dnf -q history list 2>/dev/null | sed -n 3p)"
require_evidence
if rpm -q bpftrace >/dev/null 2>&1; then
  say 'already installed: no change'
else
  changing
  out=$(timeout 900 dnf -y install bpftrace 2>&1); r=$?
  say "dnf rc $r"
  lines=$(printf '%s\n' "$out" | grep -E '^(Installing|Installed|Upgrading|Complete|Error)')
  while IFS= read -r line; do [ -z "$line" ] || say "dnf $line"; done <<EOF
$lines
EOF
  [ "$r" = 0 ] || { say "after: $(rpm -q bpftrace 2>&1)"; finish 5; }
  say "transaction: $(dnf -q history info last 2>/dev/null | grep -E '^ +(Install|Dependency)' | awk '{print $2}' | tr '\n' ' ')"
fi
say "after: $(rpm -q bpftrace 2>&1)"
v=$(bpftrace --version 2>&1) || { say "bpftrace --version failed: $v"; finish 8; }
say "bpftrace version: $v"
finish 0
