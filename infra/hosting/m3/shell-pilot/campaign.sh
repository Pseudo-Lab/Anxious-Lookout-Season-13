#!/bin/bash
# #64 full run driver, run on the development server (ssh to the guest, jq not needed).
# Before each slot it reads both Pods' ledgers and asks `harness.py gate` for the next slot or a stop
# reason; any stop condition stops A and B. After each slot it runs the JWT-shape check (file names only).
# A file named STOP in the evidence directory (for example on review's request) stops before the next slot.
# Usage: campaign.sh EVIDENCE_DIR [MAX_SLOTS]   exit 0 = all slots done, 2 = stopped, 3 = MAX_SLOTS reached
set -u
out=$1 max=${2:-60}
ssh=(ssh -i "$HOME/.ssh/anxious-back-devvm" -o IdentitiesOnly=yes -o ServerAliveInterval=30 -p 22107 opc@127.0.0.1)
K="sudo -n /usr/local/bin/k3s kubectl -n shell-full"
pod() { case $1 in A) echo 'pilot-a -c harness' ;; B) echo 'pilot-b -c agent' ;; esac; }
harness() { local config=$1; shift; "${ssh[@]}" "$K exec -i $(pod "$config") -- python -B /pilot/src/harness.py $*"; }
stop() { echo "STOP $(date -u +%FT%TZ) $*" | tee -a "$out/progress.md"; exit 2; }

for ((n = 0; n < max; n++)); do
  [ -e "$out/STOP" ] && stop "operator stop file: $(head -c 200 "$out/STOP")"
  a=$(harness A budget </dev/null) && b=$(harness B budget </dev/null) || stop 'ledger read failed'
  verdict=$(printf '{"A": %s, "B": %s}' "$a" "$b" | harness A gate) || stop 'gate failed'
  case $verdict in
    DONE) echo "DONE $(date -u +%FT%TZ)" | tee -a "$out/progress.md"; exit 0 ;;
    NEXT\ *) read -r _ task config slot <<< "$verdict" ;;
    *) stop "${verdict#STOP }" ;;
  esac
  name=$(printf '%s-%s-%02d' "$task" "$config" "$slot")
  start=$(date -u +%FT%TZ)
  line=$(harness "$config" run --task "$task" --config "$config" --slot "$slot" </dev/null 2> "$out/$name.stderr")
  rc=$?
  echo "$start $(date -u +%FT%TZ) $name rc=$rc $line" >> "$out/progress.md"
  if [ "$config" = A ]; then dirs='/pilot/results /pilot/ws /pilot/native-work'; else dirs='/pilot/results /pilot/runs'; fi
  check=$("${ssh[@]}" "$K exec $(pod "$config") -- sh -c 'for d in $dirs; do test -d \$d || echo missing=\$d; done; grep -rlE \"eyJ[A-Za-z0-9_-]{20,}\\.eyJ\" $dirs; echo exit=\$?'" </dev/null)
  check=$(echo $check)
  echo "  jwt $name ($dirs): $check" >> "$out/progress.md"
  [ "$check" = 'exit=1' ] || stop "JWT check not clean after $name: $check"  # exit=2 or missing= is a failed check.
done
echo "PAUSE $(date -u +%FT%TZ) $max slots run" | tee -a "$out/progress.md"
exit 3
