#!/usr/bin/env bash
# #68 pre-release check of a built runner image (README section 7, "What catches what").
#
#   infra/hosting/m3/native-shell/thread-check.sh <runner image> [<AppArmor profile>] > thread-check.out
#
# Runs vm/v3-probe.py in one throwaway local container with the runner Pod's layout and security settings:
# UID 10001 (the image's USER), read-only root, /state, /control and /tmp as separate writable mounts owned by
# 10001, all capabilities dropped, no-new-privileges, the runner seccomp profile from this directory, no network.
# The AppArmor profile must allow unprivileged user namespaces (on the shared development host:
# codex-local-userns, the default). SELinux `container_t` is not part of this check: V3 measures it on the VM.
# Prints the probe output, then `verdict pass` (exit 0) or `verdict fail <first failed check>` (exit 1).
# Writes nothing outside the container; the container is removed.
set -euo pipefail
image=${1:?runner image}
apparmor=${2:-codex-local-userns}
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

out=$(docker run --rm -i --network none --read-only \
  --tmpfs /state:uid=10001,gid=10001,mode=0700 \
  --tmpfs /control:uid=10001,gid=10001,mode=0700 \
  --tmpfs /tmp:uid=10001,gid=10001,mode=1777 \
  --cap-drop ALL --security-opt no-new-privileges \
  --security-opt "apparmor=$apparmor" \
  --security-opt "seccomp=$here/seccomp-runner-userns-v1.json" \
  --entrypoint python "$image" -I - < "$here/vm/v3-probe.py" 2>&1) || true
printf '%s\n' "$out"

fail() { printf 'verdict fail %s\n' "$1"; exit 1; }
has() { grep -Fxq -- "$1" <<<"$out" || fail "$1"; }

for want in 'uid 10001:10001' 'capeff 0000000000000000' 'no_new_privs 1' 'seccomp 2' \
            'sandbox_rc 0' 'sandbox inner_uid 10001:10001' 'sandbox inner_write ok' 'workspace_marker present' \
            'sandbox token_lines 0' 'requirements check ok' 'config check ok' 'thread_start_check ok' \
            'app_server_exit 0' 'probe done'; do
  has "$want"
done
for name in codex_marker codex_rollout codex_log codex_sqlite home_marker control_marker; do
  has "marker_$name written"
done
has 'link_rollout created'
has 'link_codex_home created'
# Every fake state file and both symlinks unreadable, every denied directory unlistable.
[ "$(grep -c '^sandbox read_denied ' <<<"$out")" = 8 ] || fail 'eight read_denied'
! grep -q '^sandbox read_ok ' <<<"$out" || fail 'a read succeeded'
for dir in /state/codex /state/codex/sessions /state/home /control; do
  has "sandbox list_denied $dir"
done
# CODEX_HOME/tmp/arg0 holds only Codex's own links to the pinned binary and its lock file.
grep -q '^arg0_leaf ' <<<"$out" || fail 'no arg0 entries'
! grep '^arg0_leaf ' <<<"$out" | grep -Ev '^arg0_leaf codex-arg0[A-Za-z0-9]+/(\.lock file mode=100644|(apply_patch|applypatch|codex-execve-wrapper|codex-linux-sandbox) link->/usr/local/bin/codex mode=120777)$' \
  || fail 'unexpected arg0 entry'
! grep -q 'ERROR' <<<"$out" || fail 'an ERROR line'
printf 'verdict pass\n'
