"""#68 runner image probe: the real `/state` layout, the runner UID, the runner's security settings.

Runs inside the V3 measurement Pod (`kubectl exec -i … python -I - < v3-probe.py`) and, before any release,
inside a local container started by `native-shell/thread-check.sh`. Prints one `key value` per line, never
secrets:
1. the process identity, SELinux label, effective capabilities and namespaces;
2. one standalone bubblewrap run (`codex sandbox -P native`, no app-server): a workspace write, and reads of fake
   CODEX_HOME state (marker, rollout, log, sqlite), of the denied `/state/home` and `/control`, and of the same
   files through workspace symlinks. This is not the turn path: CODEX_HOME/tmp/arg0 does not exist yet, so it
   does not prove what a sandboxed command sees during a turn (V4 measures that with real turns);
3. the runner's own app-server start (native_command, operator access mode, offline, no login) with the config
   reads and one thread/start with the runner's thread parameters; Codex creates the thread through bubblewrap
   and the arg0 helper, so this is the check a build cannot make;
4. with app-server alive: the entries of CODEX_HOME/tmp/arg0 (names, kinds, link targets, modes).
The markers are fake one-off strings written by this probe, printed only as counts. No network, no credentials,
no model call. Exit 0 once everything was attempted; the results are in the output."""
import json
import os
import queue
import secrets
import stat
import subprocess
import sys
import threading

sys.path.insert(0, '/app')


def line(key, value):
    print('%s %s' % (key, value), flush=True)


def read(path):
    try:
        with open(path) as file:
            return file.read().strip().replace('\x00', '')
    except OSError as error:
        return 'ERROR %s' % type(error).__name__


line('uid', '%d:%d' % (os.getuid(), os.getgid()))
line('selinux_label', read('/proc/self/attr/current'))
status = read('/proc/self/status')
line('capeff', next((l.split()[1] for l in status.splitlines() if l.startswith('CapEff:')), 'unknown'))
line('no_new_privs', next((l.split()[1] for l in status.splitlines() if l.startswith('NoNewPrivs:')), 'unknown'))
line('seccomp', next((l.split()[1] for l in status.splitlines() if l.startswith('Seccomp:')), 'unknown'))
for ns in ('user', 'pid', 'mnt', 'net'):
    try:
        line('ns_' + ns, os.readlink('/proc/self/ns/' + ns))
    except OSError as error:
        line('ns_' + ns, 'ERROR %s' % type(error).__name__)

root = '/state'
home, codex_home, work = (os.path.join(root, name) for name in ('home', 'codex', 'workspace'))
for path in (home, codex_home, work):
    os.makedirs(path, mode=0o700, exist_ok=True)

# Fake sensitive state: what a rollout, a log and a state database would be, plus one marker per denied root that
# this user can write. The token is random per run and never printed.
TOKEN = 'native68-probe-' + secrets.token_hex(8)
MARKERS = {'codex_marker': os.path.join(codex_home, 'probe-marker.txt'),
           'codex_rollout': os.path.join(codex_home, 'sessions', 'probe-rollout.jsonl'),
           'codex_log': os.path.join(codex_home, 'log', 'probe.log'),
           'codex_sqlite': os.path.join(codex_home, 'probe-state.sqlite'),
           'home_marker': os.path.join(home, 'probe-marker.txt'),
           'control_marker': '/control/probe-marker.txt'}
for name, path in MARKERS.items():
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, 'x') as file:
            file.write(TOKEN + '\n')
        line('marker_' + name, 'written')
    except OSError as error:
        line('marker_' + name, 'ERROR %s' % type(error).__name__)
LINKS = {'link_rollout': (MARKERS['codex_rollout'], os.path.join(work, 'probe-link-rollout')),
         'link_codex_home': (codex_home, os.path.join(work, 'probe-link-codex-home'))}
for name, (target, link) in LINKS.items():
    try:
        os.symlink(target, link)
        line(name, 'created')
    except OSError as error:
        line(name, 'ERROR %s' % type(error).__name__)

# 2. Standalone bubblewrap through the Codex sandbox and the image's `native` profile (no app-server).
READS = [MARKERS[name] for name in ('codex_marker', 'codex_rollout', 'codex_log', 'codex_sqlite', 'home_marker',
                                    'control_marker')]
READS += [LINKS['link_rollout'][1], os.path.join(LINKS['link_codex_home'][1], 'probe-marker.txt')]
INNER = r"""
echo "inner_uid $(id -u):$(id -g)"
echo "inner_home $HOME"
echo "login_home $(bash -lc 'echo "$HOME"' 2>/dev/null)"
echo "bash_startup [$( (bash -lc true; bash -c true) 2>&1 | tr '\n' ' ')]"
for f in .bash_profile .bashrc .profile; do
  if touch "$HOME/$f" 2>/dev/null; then echo "home_write ok $f"; else echo "home_write denied $f"; fi; done
echo marker > /state/workspace/v3-write && echo "inner_write ok"
for f in "$@"; do if cat -- "$f" >/dev/null 2>&1; then echo "read_ok $f"; else echo "read_denied $f"; fi; done
for d in /state/codex /state/codex/sessions /state/home /control; do
  if ls -A -- "$d" >/dev/null 2>&1; then echo "list_ok $d [$(ls -A -- "$d" | tr '\n' ' ')]"; else echo "list_denied $d"; fi
done
echo "state_listing [$(ls -A /state 2>&1 | tr '\n' ' ')]"
echo "token_lines $(cat -- "$@" 2>/dev/null | grep -c native68-probe-)"
"""
env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': home, 'CODEX_HOME': codex_home}
try:
    result = subprocess.run(['/usr/local/bin/codex', 'sandbox', '-P', 'native', '-C', work, '--',
                             'sh', '-c', INNER, 'probe'] + READS,
                            env=env, cwd=work, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    line('sandbox_rc', result.returncode)
    for out in result.stdout.splitlines():
        line('sandbox', out.replace(TOKEN, '<token>')[:400])
    for err in result.stderr.splitlines():
        if 'PATH aliases' not in err:
            line('sandbox_stderr', err.replace(TOKEN, '<token>')[:400])
except (OSError, subprocess.TimeoutExpired) as error:
    line('sandbox_rc', 'ERROR %s' % type(error).__name__)
line('workspace_marker', 'present' if os.path.exists(os.path.join(work, 'v3-write')) else 'absent')

# 3. The runner's app-server start, config reads and thread/start.
from runner.app import native_command  # noqa: E402
from runner import native_policy  # noqa: E402
from app.codex_policy import CodexFailure  # noqa: E402

command, env = native_command('/usr/local/bin/codex', home, codex_home, work, file_auth=False, external=True, fixture=False)
process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, env=env, cwd=work)
lines = queue.Queue()
threading.Thread(target=lambda: [lines.put(l) for l in process.stdout], daemon=True).start()
counter = [0]


def rpc(method, params):
    counter[0] += 1
    process.stdin.write(json.dumps({'id': counter[0], 'method': method, 'params': params}) + '\n')
    process.stdin.flush()
    while True:
        message = json.loads(lines.get(timeout=60))
        if message.get('id') == counter[0] and 'method' not in message:
            return message


try:
    rpc('initialize', {'clientInfo': {'name': 'anxious_research', 'version': '1'}, 'capabilities': {'experimentalApi': True}})
    process.stdin.write(json.dumps({'method': 'initialized'}) + '\n')
    process.stdin.flush()
    for label, method, params, check in (
            ('requirements', 'configRequirements/read', {}, native_policy.check_requirements),
            ('config', 'config/read', {'includeLayers': True, 'cwd': work},
             lambda result: native_policy.check_config(result, native_policy.STATE_ROOT))):
        message = rpc(method, params)
        if 'error' in message:
            line(label, 'ERROR %s' % json.dumps(message['error'])[:400])
            continue
        try:
            check(message['result'])
            line(label, 'check ok')
        except CodexFailure:
            line(label, 'check REFUSED')
    message = rpc('thread/start', {'model': 'gpt-6.1-sol', 'cwd': work, 'approvalPolicy': 'untrusted',
                                   'approvalsReviewer': 'user', 'permissions': native_policy.PROFILE,
                                   'config': {'web_search': 'disabled'}, 'dynamicTools': [],
                                   'historyMode': 'legacy', 'ephemeral': False})
    if 'error' in message:
        line('thread_start', 'ERROR %s' % json.dumps(message['error'])[:600])
    else:
        result = message['result']
        line('thread_start', 'approvalPolicy=%s approvalsReviewer=%s profile=%s cwd_is_workspace=%s' % (
            result.get('approvalPolicy'), result.get('approvalsReviewer'),
            (result.get('activePermissionProfile') or {}).get('id'), result.get('cwd') == work))
        line('thread_start_roots', json.dumps({'runtimeWorkspaceRoots': result.get('runtimeWorkspaceRoots'),
                                               'sandbox': result.get('sandbox')}, sort_keys=True))
        try:
            native_policy.check_thread(result, work)
            line('thread_start_check', 'ok')
        except CodexFailure:
            line('thread_start_check', 'REFUSED')

    # 4. With app-server alive, CODEX_HOME/tmp/arg0 exists: what Codex makes readable to the sandbox there.
    # Every entry, at both levels, with its kind from lstat (a link is never followed).
    arg0 = native_policy.codex_runtime_root(root)

    def kind(path):
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode):
            return 'link->%s mode=%o' % (os.readlink(path), mode)
        return '%s mode=%o' % ('dir' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) else 'other', mode)
    try:
        entries = sorted(os.listdir(arg0))
        line('arg0_entries', len(entries))
        for entry in entries:
            path = os.path.join(arg0, entry)
            line('arg0_entry', '%s %s' % (entry, kind(path)))
            if stat.S_ISDIR(os.lstat(path).st_mode):
                leaves = sorted(os.listdir(path))
                line('arg0_leaves', '%s %s' % (entry, ','.join(leaves)))
                for leaf in leaves:
                    line('arg0_leaf', '%s/%s %s' % (entry, leaf, kind(os.path.join(path, leaf))))
    except OSError as error:
        line('arg0_entry', 'ERROR %s' % type(error).__name__)
except (queue.Empty, ValueError, OSError) as error:
    line('app_server', 'ERROR %s' % type(error).__name__)
finally:
    try:
        process.stdin.close()
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        process.wait(timeout=10)
    line('app_server_exit', process.returncode)
line('probe', 'done')
