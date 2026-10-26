#!/bin/bash
# #64 full run driver, run on the development server (ssh to the guest, jq not needed).
# Before each slot it reads both Pods' ledgers and asks `harness.py gate` for the next slot or a stop
# reason; any stop condition stops A and B. A slot's stdout and stderr stay in the Pod
# (/pilot/results/console) until the JWT-shape check over the Pod's result and workspace paths is clean;
# only then is the stdout line copied here (stderr only as a line count). A transport failure or a non-zero
# exit stops the run without calling the slot again: check the ledger first.
# A file named STOP in the evidence directory (for example on review's request) stops before the next slot.
# Usage: campaign.sh EVIDENCE_DIR [MAX_SLOTS]   exit 0 = all slots done, 2 = stopped, 3 = MAX_SLOTS reached
set -u
out=$1 max=${2:-60}
ssh=(ssh -i "$HOME/.ssh/anxious-back-devvm" -o IdentitiesOnly=yes -o ServerAliveInterval=30 -p 22107 opc@127.0.0.1)
K="sudo -n /usr/local/bin/k3s kubectl -n shell-full"
pod() { case $1 in A) echo 'pilot-a -c harness' ;; B) echo 'pilot-b -c agent' ;; esac; }
inpod() { local config=$1; shift; "${ssh[@]}" "$K exec -i $(pod "$config") -- $*"; }
stop() { echo "STOP $(date -u +%FT%TZ) $*" | tee -a "$out/progress.md"; exit 2; }

for ((n = 0; ; n++)); do
  [ -e "$out/STOP" ] && stop "operator stop file: $(head -c 200 "$out/STOP")"
  a=$(inpod A python -B /pilot/src/harness.py budget </dev/null) &&
    b=$(inpod B python -B /pilot/src/harness.py budget </dev/null) || stop 'ledger read failed'
  verdict=$(printf '{"A": %s, "B": %s}' "$a" "$b" | inpod A python -B /pilot/src/harness.py gate) || stop 'gate failed'
  case $verdict in
    DONE) echo "DONE $(date -u +%FT%TZ)" | tee -a "$out/progress.md"; exit 0 ;;
    NEXT\ *) read -r _ task config slot <<< "$verdict" ;;
    *) stop "${verdict#STOP }" ;;
  esac
  if ((n >= max)); then  # Checked after the gate, so the last slot's ledger is judged before pausing.
    echo "PAUSE $(date -u +%FT%TZ) $max slots run, next $task $config $slot" | tee -a "$out/progress.md"; exit 3
  fi
  name=$(printf '%s-%s-%02d' "$task" "$config" "$slot")
  console=/pilot/results/console/$name
  start=$(date -u +%FT%TZ)
  rc=$(inpod "$config" sh -c "'mkdir -p /pilot/results/console && python -B /pilot/src/harness.py run --task $task --config $config --slot $slot > $console.out 2> $console.err; echo rc=\$?'" </dev/null)
  transport=$?
  end=$(date -u +%FT%TZ)
  [ "$transport" -eq 0 ] && [[ $rc == rc=* ]] || stop "transport failure ($transport) during $name; check the ledger before anything else"
  if [ "$config" = A ]; then dirs='/pilot/results /pilot/ws /pilot/native-work'; else dirs='/pilot/results /pilot/runs'; fi
  check=$(inpod "$config" sh -c "'for d in $dirs; do test -d \$d || echo missing=\$d; done; grep -rlE \"eyJ[A-Za-z0-9_-]{20,}\\.eyJ\" $dirs; echo exit=\$?'" </dev/null)
  transport=$?
  check=$(echo $check)
  [ "$transport" -eq 0 ] || stop "transport failure ($transport) during the JWT check after $name; outputs stay in the Pod"
  echo "$start $end $name $rc jwt($dirs): $check" >> "$out/progress.md"
  [ "$check" = 'exit=1' ] || stop "JWT check not clean after $name: $check; outputs stay in the Pod"  # exit=2 or missing= fails.
  line=$(inpod "$config" cat "$console.out" </dev/null) && errors=$(inpod "$config" sh -c "'wc -l < $console.err'" </dev/null) ||
    stop "could not read the console of $name"
  echo "  $name stderr_lines=$errors $line" >> "$out/progress.md"
  [ "$rc" = rc=0 ] || stop "$name exited ${rc#rc=}; check the ledger before anything else"
done
