"""#64 shell-surface pilot: one task under configuration A (current) or B (native shell).

Runs inside the pilot Pod as UID 10001 with the runner image (PYTHONPATH=/app).

A  runner's app-server flags: native shell and unified_exec off, read-only sandbox,
   the MCP `shell` bridge to a ShellBroker and the shell-executor sidecar (30 s, 64 KiB),
   Codex cwd an empty native-work directory, commands run in the executor workspace.
B  native shell, apply_patch and unified_exec, workspace-write sandbox (bubblewrap),
   cwd = workspace, no MCP shell, project .codex config untrusted.

Credentials: `export` in the pilot-login Pod reads only the access token from its auth.json
(written by `codex login --device-auth`) into the Secret pilot-access; pilot Pods read that and
give it to the app-server with
`account/login/start {type: chatgptAuthTokens}`; the store is ephemeral, nothing is written
back, and no token text is ever printed or saved. Results go to /pilot/results.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pilot_tasks  # noqa: E402

MODEL = 'gpt-6.1-sol'
ACCESS = Path('/access/access.json')  # Secret pilot-access: access token only, exported from pilot-login.
PILOT = Path('/pilot')
RESULTS = PILOT/'results'
BUDGET = RESULTS/'budget.json'
LIMITS = {'input': 2_400_000, 'output': 100_000}  # Issue #64 cap for the whole pilot.
# A and B run in separate Pods with separate budget files, so each configuration gets half:
# the two can never add up past the pilot cap.
SHARE = {key: value // 2 for key, value in LIMITS.items()}
PROVIDER = ['model_provider="access_only_openai"', 'model_providers.access_only_openai.name="OpenAI"',
            'model_providers.access_only_openai.base_url="https://chatgpt.com/backend-api/codex"',
            'model_providers.access_only_openai.wire_api="responses"',
            'model_providers.access_only_openai.requires_openai_auth=true',
            'model_providers.access_only_openai.supports_websockets=false',
            'analytics.enabled=false', 'otel.exporter="none"', 'otel.trace_exporter="none"', 'otel.metrics_exporter="none"']
COMMON = ['features.multi_agent=false', 'features.multi_agent_v2=false', 'features.apps=false', 'features.plugins=false',
          'features.remote_plugin=false', 'features.hooks=false', 'features.daemon_auto_start=false',
          'features.browser_use=false', 'features.computer_use=false', 'features.image_generation=false',
          'web_search="disabled"', 'forced_login_method="chatgpt"', 'cli_auth_credentials_store="ephemeral"',
          f'model="{MODEL}"']
CONFIGS = {
    # Same feature switches as runner.app.Native for operator-access runners with the shell executor.
    'A': COMMON + ['features.shell_tool=false', 'features.unified_exec=false', 'features.code_mode_host=true',
                   'mcp_servers.shell.command="/usr/local/bin/python"', 'mcp_servers.shell.args=["-I","/app/runner/shell_bridge.py"]',
                   'mcp_servers.shell.enabled_tools=["shell"]', 'mcp_servers.shell.tools.shell.approval_mode="approve"',
                   'mcp_servers.shell.tool_timeout_sec=325', 'mcp_servers.shell.enabled=false'],
    'B': COMMON + ['features.shell_tool=true', 'features.unified_exec=true', 'features.code_mode_host=true'],
}


def export():
    """Run in pilot-login: print the access token envelope for the pilot-access Secret (piped, never shown)."""
    from ops.access_snapshot import selected_file
    from runner.access import claims
    token, account, _ = selected_file('/login/codex/auth.json', expected_uid=os.geteuid())
    value, _ = claims(token)
    plan = value.get('https://api.openai.com/auth', {}).get('chatgpt_plan_type')
    sys.stdout.write(json.dumps({'accessToken': token, 'chatgptAccountId': account,
                                 **({'chatgptPlanType': plan} if isinstance(plan, str) else {})}))


def access():
    from runner.access import claims
    value = json.loads(ACCESS.read_text())
    claims(value['accessToken'])  # Refuse an expired or malformed token before any turn.
    return {'type': 'chatgptAuthTokens', **value}


def spent():
    try:
        return json.loads(BUDGET.read_text())
    except FileNotFoundError:
        return {'input': 0, 'cachedInput': 0, 'output': 0, 'runs': [], 'unknown': [], 'inflight': None}


def save(budget):
    # Atomic replace with fsync: a crash leaves either the previous or the new state, never none.
    partial = BUDGET.with_suffix('.partial')
    with open(partial, 'w') as stream:
        json.dump(budget, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, BUDGET)


def blocked(budget):
    if budget.get('inflight'):
        return 'run '+budget['inflight']+' never settled; reconcile it before another paid run'
    if budget.get('unknown'):
        return 'a previous run has unconfirmed token usage; reconcile it before another paid run'
    if budget.get('halted'):
        return 'run '+budget['halted']+' crossed the budget share; the pilot stops here'
    if budget['input'] >= SHARE['input'] or budget['output'] >= SHARE['output']:
        return 'pilot budget share exhausted; not starting'
    return None


class Session:
    def __init__(self, config, home, cwd):
        command = ['/usr/local/bin/codex', 'app-server', '--listen', 'stdio://']
        for setting in CONFIGS[config] + PROVIDER:
            command += ['-c', setting]
        if config == 'B':
            command += ['-c', f'projects."{cwd}".trust_level="untrusted"']
        env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(home), 'CODEX_HOME': str(home)}
        self.process = subprocess.Popen(command, env=env, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.events, self.replies, self.next_id = queue.Queue(), {}, 0
        self.lock = threading.Condition()
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if 'id' in message and 'method' not in message:
                with self.lock:
                    self.replies[message['id']] = message
                    self.lock.notify_all()
            else:
                self.events.put(message)
        self.events.put({'method': 'harness/exited'})

    def send(self, message):
        self.process.stdin.write(json.dumps(message)+'\n')
        self.process.stdin.flush()

    def rpc(self, method, params, timeout=60):
        with self.lock:
            self.next_id += 1
            ident = self.next_id
        self.send({'id': ident, 'method': method, 'params': params})
        with self.lock:
            self.lock.wait_for(lambda: ident in self.replies, timeout)
            reply = self.replies.pop(ident, None)
        if reply is None or 'error' in reply:
            raise RuntimeError(method+' failed: '+json.dumps((reply or {}).get('error', 'timeout'))[:200])
        return reply['result']

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        except Exception:
            self.process.kill()


def prepare_workspace(config, task, run):
    if config == 'A':
        # The executor sees only its fixed /state/workspace mount; reuse it, emptied per run.
        workspace, cwd = PILOT/'ws', PILOT/'native-work'/run
        for child in workspace.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        cwd.mkdir(parents=True, mode=0o700)
    else:
        workspace = PILOT/'runs'/run/'work'
        workspace.mkdir(parents=True, mode=0o700)
        cwd = workspace
    pilot_tasks.materialize(task, workspace)
    if config == 'A':
        for path in workspace.rglob('*'):  # The emptyDir root itself is root-owned (fsGroup 10001, 2777).
            path.chmod(0o2770 if path.is_dir() else 0o660)  # Executor UID 20002 shares group 10001.
    return workspace, cwd


def summarize(item, counters, commands):
    kind = item.get('type')
    if kind == 'commandExecution':
        counters['toolCalls'] += 1
        commands.append(item.get('command', '')[:200])
        if item.get('status') != 'completed' or item.get('exitCode') not in (0, None):
            counters['toolFailures'] += 1
    elif kind == 'mcpToolCall':
        counters['toolCalls'] += 1
        arguments = item.get('arguments') or {}
        commands.append(str(arguments.get('command', ''))[:200])
        text = json.dumps(item.get('result') or item.get('error') or {})
        if item.get('status') != 'completed' or '"timed_out"' in text or 'shell_timeout' in text:
            counters['toolFailures'] += 1
        if 'shell_timeout' in text or '"timed_out"' in text:
            counters['timeouts'] += 1
        if '"truncated": true' in text or '"truncated":true' in text:
            counters['truncated'] += 1
    elif kind == 'fileChange':
        counters['fileChanges'] += 1
        if item.get('status') != 'completed':
            counters['patchFailures'] += 1
    elif kind == 'agentMessage':
        counters['agentMessages'] += 1


def run(task, config, wall_limit):
    RESULTS.mkdir(parents=True, exist_ok=True)
    budget = spent()
    reason = blocked(budget)
    if reason:
        raise SystemExit(reason)
    run_id = f'{task}-{config}-{uuid.uuid4().hex[:8]}'
    workspace, cwd = prepare_workspace(config, task, run_id)
    home = PILOT/'homes'/run_id
    home.mkdir(parents=True, mode=0o700)
    broker = None
    if config == 'A':
        from runner.shell_broker import ShellBroker
        (PILOT/'ipc').mkdir(mode=0o700, exist_ok=True)
        broker = ShellBroker(PILOT/'ipc'/(run_id+'.sock'), lambda turn, arguments: True, lambda value: value,
                             executor='/run/shell-executor/command.sock', wall=30)
    session = Session(config, home, cwd)
    counters = dict.fromkeys(['toolCalls', 'toolFailures', 'timeouts', 'truncated', 'fileChanges', 'patchFailures',
                              'agentMessages', 'modelRequests'], 0)
    commands, usage, status, started = [], None, 'unknown', time.monotonic()
    turn_started = settled = False
    try:
        session.rpc('initialize', {'clientInfo': {'name': 'shell_pilot', 'version': '1'}, 'capabilities': {'experimentalApi': True}})
        session.send({'method': 'initialized'})
        session.rpc('account/login/start', access())
        params = {'model': MODEL, 'cwd': str(cwd), 'approvalPolicy': 'never', 'ephemeral': False}
        if config == 'A':
            lease = broker.activate({'pilot': run_id})
            params.update(sandbox='read-only', config={'mcp_servers.shell.enabled': True,
                          'mcp_servers.shell.env.SHELL_BROKER_SOCKET': str(broker.path),
                          'mcp_servers.shell.env.SHELL_BRIDGE_LEASE': lease})
        else:
            params.update(sandbox='workspace-write')
        thread = session.rpc('thread/start', params)['thread']['id']
        # Durable before the paid call: if anything below dies, the next run sees an unsettled run.
        budget['inflight'] = run_id
        save(budget)
        turn_started = True
        session.rpc('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': pilot_tasks.TASKS[task]['prompt']}]})
        remaining = {'input': SHARE['input'] - budget['input'], 'output': SHARE['output'] - budget['output']}
        stop_reason, drain_until = None, None
        while True:
            now = time.monotonic()
            if stop_reason is None and now - started >= wall_limit:
                stop_reason = 'wall_limit'
            if stop_reason is not None and drain_until is None:
                # Interrupt, then keep reading so the final usage of the interrupted turn is counted.
                session.send({'id': 999999, 'method': 'turn/interrupt', 'params': {'threadId': thread}})
                drain_until = now + 60
            if drain_until is not None and now >= drain_until:
                status = stop_reason
                break
            try:
                event = session.events.get(timeout=5)
            except queue.Empty:
                continue
            method = event.get('method')
            if 'id' in event and method:  # Server request: no approvals or token refresh in the pilot.
                session.send({'id': event['id'], 'error': {'code': -32601, 'message': 'unavailable in pilot'}})
            elif method == 'item/completed':
                summarize(event.get('params', {}).get('item', {}), counters, commands)
            elif method == 'thread/tokenUsage/updated':
                counters['modelRequests'] += 1
                usage = event['params']['tokenUsage']['total']
                if stop_reason is None and (usage['inputTokens'] > remaining['input'] or usage['outputTokens'] > remaining['output']):
                    stop_reason = 'budget_stop'
            elif method == 'turn/completed':
                status = stop_reason or event['params'].get('turn', {}).get('status', 'completed')
                settled = True  # The final usage of a turn precedes its completion event.
                break
            elif method == 'harness/exited':
                status = 'app_server_exited'
                break
    except Exception as failure:
        status = 'harness_error: ' + type(failure).__name__  # Recorded like any run; usage rules below apply.
    finally:
        wall = time.monotonic() - started
        tokens = {k: (usage or {}).get(k, 0) for k in ('inputTokens', 'cachedInputTokens', 'outputTokens', 'reasoningOutputTokens')}
        if turn_started:
            # Settle before anything else can fail (close, grading, result files). Observed usage is a lower
            # bound; without the turn's completion event it is unconfirmed and blocks later runs.
            budget['input'] += tokens['inputTokens']
            budget['cachedInput'] += tokens['cachedInputTokens']
            budget['output'] += tokens['outputTokens']
            budget['runs'].append(run_id)
            if not (settled and usage is not None):
                # Confirmed only with both the completion event and a usage report; anything else blocks.
                budget.setdefault('unknown', []).append(run_id)
            if status == 'budget_stop':
                budget['halted'] = run_id  # The share was crossed: no further run in this Pod.
            budget['inflight'] = None
            save(budget)
        for closing in (session.close, broker.close if broker is not None else None):
            try:
                if closing: closing()
            except Exception:
                pass
    grade = pilot_tasks.grade(task, workspace)
    repeated = len(commands) - len(set(commands))
    result = {'run': run_id, 'task': task, 'config': config, 'turnStatus': status, 'settled': settled and usage is not None,
              'wallSeconds': round(wall, 1), **grade, **counters, 'repeatedCommands': repeated, 'tokens': tokens,
              'commands': commands}
    (RESULTS/(run_id+'.json')).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'commands'}))


def selfcheck(config):
    """No model call: app-server starts with the configuration, thread/start works, sandbox mode is accepted."""
    run_id = f'selfcheck-{config}-{uuid.uuid4().hex[:6]}'
    workspace, cwd = prepare_workspace(config, 'fix-failing-test', run_id)
    home = PILOT/'homes'/run_id
    home.mkdir(parents=True, mode=0o700)
    session = Session(config, home, cwd)
    try:
        session.rpc('initialize', {'clientInfo': {'name': 'shell_pilot', 'version': '1'}, 'capabilities': {'experimentalApi': True}})
        session.send({'method': 'initialized'})
        thread = session.rpc('thread/start', {'model': MODEL, 'cwd': str(cwd), 'approvalPolicy': 'never',
                                               'sandbox': 'read-only' if config == 'A' else 'workspace-write'})
        print(json.dumps({'selfcheck': config, 'thread': bool(thread.get('thread', {}).get('id')),
                          'model': thread.get('model'), 'workspace': str(workspace)}))
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    one = sub.add_parser('run')
    one.add_argument('--task', choices=sorted(pilot_tasks.TASKS), required=True)
    one.add_argument('--config', choices=('A', 'B'), required=True)
    one.add_argument('--wall-limit', type=int, default=900)
    check = sub.add_parser('selfcheck')
    check.add_argument('--config', choices=('A', 'B'), required=True)
    sub.add_parser('budget')
    sub.add_parser('export')
    args = parser.parse_args()
    if args.action == 'run':
        run(args.task, args.config, args.wall_limit)
    elif args.action == 'selfcheck':
        selfcheck(args.config)
    elif args.action == 'export':
        export()
    else:
        print(json.dumps(spent()))


if __name__ == '__main__':
    main()
