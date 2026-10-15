#!/bin/bash
# PM-only exact diagnostic exec; never replay after ACK/transport failure.
set -eu
umask 077
ulimit -c 0
if [ "$#" != 4 ]; then exit 1; fi
contrast_kubectl=$1
contrast_pod=$2
contrast_command=$3
contrast_prefix=$4
case "$contrast_kubectl" in /*/kubectl) ;; *) exit 1 ;; esac
case "$contrast_prefix" in /*) ;; *) exit 1 ;; esac
if [[ ! "$contrast_pod" =~ ^access-runner-dns-(l4|l7|node)-v1-[a-z0-9-]+$ ]]; then exit 1; fi
contrast_directory=$(dirname "$contrast_prefix")
if [ "$(stat -c %a "$contrast_directory")" != 700 ] || [ "$(stat -c %u "$contrast_directory")" != "$(id -u)" ]; then exit 1; fi
if [ ! -f "$contrast_command" ] || [ -L "$contrast_command" ] || [ "$(stat -c %u "$contrast_command")" != "$(id -u)" ] || [ "$(stat -c %a "$contrast_command")" != 600 ]; then exit 1; fi
if [ "$(stat -c %s "$contrast_command")" -gt 65536 ]; then exit 1; fi
# Only the private source file emitted by access_dns_contrast_exec.py program.
# No host language toolchain or shell eval is used; argv is fixed below.
contrast_program=$(cat "$contrast_command")
if [ -z "$contrast_program" ]; then exit 1; fi
# Exclusive files in the owned private directory. Existing claims/results abort.
for contrast_suffix in claim stdout stderr exit; do
  if [ -e "$contrast_prefix.$contrast_suffix" ] || [ -L "$contrast_prefix.$contrast_suffix" ]; then exit 1; fi
done
set -o noclobber
: >"$contrast_prefix.claim"
: >"$contrast_prefix.stdout"
: >"$contrast_prefix.stderr"
: >"$contrast_prefix.exit"
set +o noclobber
contrast_exit=0
(
  # Bash's file-size unit can vary with POSIX mode;128 units stays <=128KiB.
  ulimit -f 128
  exec timeout --signal=TERM --kill-after=1s 12s "$contrast_kubectl" --request-timeout=8s \
    -n codex-trial exec "$contrast_pod" -c diagnose -- python -B -c "$contrast_program"
) >"$contrast_prefix.stdout" 2>"$contrast_prefix.stderr" || contrast_exit=$?
printf '%s\n' "$contrast_exit" >"$contrast_prefix.exit"
printf '{"transportExit":%s,"attemptConsumed":true}\n' "$contrast_exit"
exit "$contrast_exit"
