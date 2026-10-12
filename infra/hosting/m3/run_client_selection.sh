#!/bin/sh
# Invoke with a noninteractive POSIX shell, not Bash: no BASH_ENV startup.
selection_unsafe=false
case $- in *x*|*v*) selection_unsafe=true ;; esac
set +x +v
case :${SHELLOPTS-}: in *:xtrace:*|*:verbose:*|*:functrace:*|*:errtrace:*) selection_unsafe=true ;; esac
case :${BASHOPTS-}: in *:extdebug:*) selection_unsafe=true ;; esac
[ -z "${BASH_ENV-}" ] && [ -z "${ENV-}" ] || selection_unsafe=true
set -eu
umask 077
IFS=' ' read -r selection_uptime selection_unused </proc/uptime
selection_fraction=${selection_uptime#*.}
case $selection_fraction in [0-9][0-9]) ;; *) exit 1 ;; esac
# POSIX arithmetic: remove the leading zero rather than interpreting octal.
selection_fraction=${selection_fraction#0}
selection_start=$((${selection_uptime%.*}*100+${selection_fraction:-0}+1))
# POSIX Bash can recompute imported option variables; check export presence
# without emitting or capturing any value.
if /usr/bin/printenv BASHOPTS >/dev/null 2>&1 || /usr/bin/printenv SHELLOPTS >/dev/null 2>&1; then
  selection_unsafe=true
fi
[ "$#" = 4 ] || exit 1
case $1 in /*) ;; *) exit 1 ;; esac
IFS=' ' read -r selection_uptime selection_unused </proc/uptime
selection_fraction=${selection_uptime#*.}; selection_fraction=${selection_fraction#0}
selection_now=$((${selection_uptime%.*}*100+${selection_fraction:-0}+1))
selection_remaining=$((selection_start+300-selection_now-25))
[ "$selection_remaining" -gt 0 ] || exit 1
selection_duration=$(printf '%d.%02ds' "$((selection_remaining/100))" "$((selection_remaining%100))")
exec /usr/bin/timeout --signal=TERM --kill-after=0.25s "$selection_duration" \
  /usr/bin/env -u BASH_ENV -u ENV -u SHELLOPTS -u BASHOPTS \
  /bin/bash --noprofile --norc "$1/infra/hosting/m3/client_selection_collect.sh" \
  "$@" "$selection_start" "$selection_unsafe"
