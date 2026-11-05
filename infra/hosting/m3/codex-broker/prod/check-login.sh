#!/bin/sh
# #72 C: read-only check after the operator's `codex login --device-auth`. Run as root.
# Records the login-status rc and auth.json mode/owner/size/mtime only. It never reads auth.json or prints
# `codex login status` output. /healthz is liveness only; the login is proven by the D canary.
# Every lookup's own exit status is checked before its output is used; output of a failed lookup is
# discarded (printed as lookup_failed), and every field is validated in full.
# rc 0 = Pod Running/ready/0 restarts, login status rc 0, CODEX_HOME 700 (or 2700 with the volume's setgid) and auth.json 600 owned by 10001
# with a non-zero size and a 10-digit mtime; rc 3 otherwise.
set -u
K='/usr/local/bin/k3s kubectl'
X="timeout 30 $K -n codex-broker exec codex-broker-0 --"
bad=
p=$(timeout 30 $K -n codex-broker get pod codex-broker-0 -o jsonpath='{.status.phase}|{.status.containerStatuses[0].ready}|{.status.containerStatuses[0].restartCount}' 2>/dev/null) || p=
echo "broker pod: ${p:-lookup_failed}"
[ "$p" = 'Running|true|0' ] || bad="$bad pod"
$X codex login status > /dev/null 2>&1; s=$?
echo "login status rc=$s"
[ $s = 0 ] || bad="$bad login"
h=$($X stat -c '%a %u' /data/codex 2>/dev/null) || h=
echo "CODEX_HOME: ${h:-lookup_failed}"
# Owner-only either way: kubelet applies the StatefulSet fsGroup to the local-path volume root (2777 root:10001),
# so the 0700 directory the broker creates inherits setgid and stats as 2700 (production, #72 C, 2026-10-31).
case "$h" in '700 10001'|'2700 10001') ;; *) bad="$bad home";; esac
# The mtime (epoch) corroborates refreshes; E4 gates on the broker's own refresh events.
a=$($X stat -c '%a %u %s %Y' /data/codex/auth.json 2>/dev/null) || a=
echo "auth.json: ${a:-lookup_failed}"
echo "$a" | grep -qxE '600 10001 [1-9][0-9]* [0-9]{10}' || bad="$bad auth.json"
[ -z "$bad" ] && rc=0 || { echo "not ready:$bad"; rc=3; }
echo "result rc=$rc"
exit $rc
