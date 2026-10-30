#!/bin/sh
# #72 C: read-only check after the operator's `codex login --device-auth`. Run as root.
# Records the login-status rc and auth.json mode/owner/size only. It never reads auth.json or prints
# `codex login status` output. /healthz is liveness only; the login is proven by the D canary.
# rc 0 = Pod ready, login status rc 0, CODEX_HOME 700 and auth.json 600 owned by 10001; rc 3 otherwise.
set -u
K='/usr/local/bin/k3s kubectl'
X="timeout 30 $K -n codex-broker exec codex-broker-0 --"
bad=
p=$(timeout 30 $K -n codex-broker get pod codex-broker-0 -o jsonpath='{.status.phase}|{.status.containerStatuses[0].ready}|{.status.containerStatuses[0].restartCount}' 2>/dev/null)
echo "broker pod: ${p:-lookup_failed}"
[ "$p" = 'Running|true|0' ] || bad="$bad pod"
$X codex login status > /dev/null 2>&1; s=$?
echo "login status rc=$s"
[ $s = 0 ] || bad="$bad login"
h=$($X stat -c '%a %u' /data/codex 2>/dev/null); echo "CODEX_HOME: ${h:-lookup_failed}"
[ "$h" = '700 10001' ] || bad="$bad home"
a=$($X stat -c '%a %u %s' /data/codex/auth.json 2>/dev/null); echo "auth.json: ${a:-lookup_failed}"
case "$a" in '600 10001 '[1-9]*) ;; *) bad="$bad auth.json";; esac
[ -z "$bad" ] && rc=0 || { echo "not ready:$bad"; rc=3; }
echo "result rc=$rc"
exit $rc
