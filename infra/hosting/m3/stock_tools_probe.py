"""Pinned native tool roundtrip via fixture-only shared ingress, Docker network-none."""
import base64
import argparse
import hashlib
import ipaddress
import json
import os
import ssl
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from runner.app import Native
from owned_event_fixture import OwnedEvents
from stock_wire import Client
from stock_stdio import StdioClient

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--legacy-history', action='store_true')
parser.add_argument('--legacy-tools', action='store_true')
parser.add_argument('--minimal-startup', action='store_true')
parser.add_argument('--stdio', action='store_true')
parser.add_argument('--code-mode-wire', action='store_true')
parser.add_argument('--enable-code-mode-host', action='store_true')
parser.add_argument('--permission-probe', action='store_true')
parser.add_argument('--fail-after-tool', action='store_true')
parser.add_argument('--runner-path', action='store_true')
options = parser.parse_args()
fixture_tool_names = ('research_list',) if options.runner_path else ('original_fixture_tool', 'web_fixture_tool')


def fixture_arguments(name):
    return {'type': 'document', 'limit': 1} if options.runner_path else {'content': name+'-synthetic'}

root = Path('/tmp/shared-tools'); root.mkdir(mode=0o700)
for name in ('home', 'codex', 'original', 'web'): (root / name).mkdir(mode=0o700)
models = json.loads(Path('/fixture-models.json').read_text())
audit = {'responsePosts': 0, 'toolOutputs': 0, 'wrongModel': False, 'nonResponsesPosts': 0,
         'codeModeExecAdvertised': False, 'registeredToolInPrompt': False,
         'codeModeDisabledObserved': False, 'codeModeHostMissingObserved': False,
         'postToolFailuresInjected': 0,
         'missingRegisteredTools': 0}
requests, callbacks, captures, failures = [], [], [], []
permission_results = {}
mutex = threading.Lock()


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        if 'accounts/check' in self.path:
            body = {'accounts': [{'id': 'synthetic-account', 'plan_type': 'plus',
                     'workspace_backend_origin': origin, 'account_routing_override': 'NO_CONSTRAINT'}],
                    'account_ordering': ['synthetic-account'], 'default_account_id': 'synthetic-account'}
        elif 'models' in self.path: body = models
        else: body = {}
        payload = json.dumps(body).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        if 'responses' not in self.path:
            audit['nonResponsesPosts'] += 1  # Other mock POSTs are not proof of auth refresh.
            self.send_response(400); self.end_headers(); return
        with mutex:
            audit['responsePosts'] += 1
            audit['wrongModel'] |= body.get('model') != 'gpt-6.1-sol'
            output_type = 'custom_tool_call_output' if options.code_mode_wire else 'function_call_output'
            outputs = [v for v in body.get('input', []) if v.get('type') == output_type]
            def tool_names(values):
                return [name for value in values for name in
                        ([value.get('name')] + tool_names(value.get('tools', [])))]
            def tool_specs(values):
                return [spec for value in values for spec in [value, *tool_specs(value.get('tools', []))]]
            names = tool_names(body.get('tools', []))
            advertised = tool_specs(body.get('tools', []))
            for item in body.get('input', []):
                if item.get('type') == 'additional_tools':
                    names += tool_names(item.get('tools', []))
                    advertised += tool_specs(item.get('tools', []))
            # Inspect advertised exec descriptions only, not user/model history
            # containing deliberate foreign-name negative probes.
            prompt = '\n'.join(spec.get('description', '') for spec in advertised if spec.get('name') == 'exec')
            prompt_names = [name for name in fixture_tool_names if name in prompt]
            audit['registeredToolInPrompt'] |= bool(prompt_names)
            audit['codeModeExecAdvertised'] |= 'exec' in names
            print(json.dumps({'registeredFixtureTools': [v for v in names if v in
                             fixture_tool_names],
                              'responseToolCount': len(body.get('tools', [])),
                              'inputToolNamesCount': len(names), 'registeredToolPromptNames': prompt_names}), flush=True)
            name = (next(iter(prompt_names), None) if options.code_mode_wire and 'exec' in names else
                    next((v for v in names if v in fixture_tool_names), None))
            if not name:
                audit['missingRegisteredTools'] += 1
                payload = b'{"error":{"message":"Fixture registered tool absent","type":"invalid_request_error"}}'
                self.send_response(400); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload))); self.end_headers()
                self.wfile.write(payload)
                return
            other = ('foreign_owner_tool' if options.runner_path else
                     'web_fixture_tool' if name == 'original_fixture_tool' else 'original_fixture_tool')
            assert other not in names, 'Foreign tool registration leaked'
            if options.code_mode_wire: assert other not in prompt_names
            call = name + '-call'
            if options.runner_path:
                # Resume includes prior tool outputs; only this explicit user turn
                # can trigger the current callback and completion.
                users = [v for v in body.get('input', []) if v.get('role') == 'user']
                assert users and isinstance(users[-1].get('id'), str)
                call += '-'+users[-1]['id']
                outputs = [v for v in outputs if v.get('call_id') == call]
            if outputs:
                if options.code_mode_wire:
                    print(json.dumps({'syntheticCodeModeOutputs': [str(v.get('output'))[:1200] for v in outputs]}), flush=True)
                text = ' '.join(str(v.get('output')) for v in outputs)
                if options.permission_probe:
                    for output in outputs:
                        for item in output.get('output', []) if isinstance(output.get('output'), list) else []:
                            if item.get('type') == 'input_text':
                                try: value = json.loads(item.get('text', ''))
                                except ValueError: continue
                                if isinstance(value, dict) and 'shellToolAvailable' in value:
                                    permission_results[name] = value
                audit['codeModeDisabledObserved'] |= 'code-mode host is disabled' in text
                audit['codeModeHostMissingObserved'] |= 'failed to spawn code-mode host' in text and 'No such file' in text
                if not any(v['call_id'] == call and 'saved' in str(v.get('output')) for v in outputs):
                    payload = b'{"error":{"message":"Fixture tool result unavailable","type":"invalid_request_error"}}'
                    self.send_response(400); self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
                    return
                if options.fail_after_tool:
                    audit['postToolFailuresInjected'] += 1
                    payload = b'{"error":{"message":"Injected post-tool failure","type":"invalid_request_error"}}'
                    self.send_response(400); self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
                    return
                audit['toolOutputs'] += 1
                item = {'type': 'message', 'id': name + '-message', 'role': 'assistant',
                        'status': 'completed', 'content': [{'type': 'output_text', 'text': 'Synthetic saved', 'annotations': []}]}
            else:
                if options.code_mode_wire:
                    code = 'text(await tools.'+name+'('+json.dumps(fixture_arguments(name))+'));'
                    if options.permission_probe:
                        code = ('text(JSON.stringify({shellToolAvailable:typeof tools.exec_command === "function",'
                                'foreignToolAvailable:typeof tools.'+other+' === "function",'
                                'requireAvailable:typeof require !== "undefined",'
                                'processAvailable:typeof process !== "undefined",'
                                'fetchAvailable:typeof fetch !== "undefined"}));'+code)
                    item = {'type': 'custom_tool_call', 'id': name+'-item', 'call_id': call,
                            'name': 'exec', 'input': code}
                else:
                    item = {'type': 'function_call', 'id': name + '-item', 'call_id': call,
                            'name': name, 'arguments': json.dumps(fixture_arguments(name))}
            requests.append((name, bool(outputs)))
        values = [{'type': 'response.created', 'response': {'id': name + '-response'}},
                  {'type': 'response.output_item.done', 'item': item},
                  {'type': 'response.completed', 'response': {'id': name + '-response',
                   'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}}]
        payload = ''.join('event: ' + v['type'] + '\ndata: ' + json.dumps(v) + '\n\n' for v in values).encode()
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)


key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic-loopback')])
cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc)+timedelta(minutes=20))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .sign(key, hashes.SHA256()))
cert_path, key_path = root / 'ca.pem', root / 'key.pem'
cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
key_path.chmod(0o600)
server = ThreadingHTTPServer(('127.0.0.1', 0), Mock)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert_path, key_path)
server.socket = context.wrap_socket(server.socket, server_side=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = 'https://127.0.0.1:' + str(server.server_port)
path = str(root / 'control.sock')
args = ['/usr/local/bin/codex', 'app-server', '--listen', 'stdio://' if options.stdio else 'unix://' + path]
settings = ['cli_auth_credentials_store="ephemeral"', 'features.daemon_auto_start=false',
            'features.code_mode_host=false', 'features.shell_tool=false', 'features.unified_exec=false',
            'features.multi_agent=false', 'features.multi_agent_v2=false', 'features.apps=false',
            'features.plugins=false', 'features.hooks=false', 'features.browser_use=false',
            'features.computer_use=false', 'features.unbounded_connection_retries=false',
            'analytics.enabled=false', 'otel.metrics_exporter="none"', 'model="gpt-6.1-sol"',
            'model_provider="fixture_openai"', 'forced_login_method="chatgpt"',
            'chatgpt_base_url=' + json.dumps(origin + '/backend-api'),
            'model_providers.fixture_openai.name="OpenAI synthetic fixture"',
            'model_providers.fixture_openai.wire_api="responses"',
            'model_providers.fixture_openai.requires_openai_auth=true',
            'model_providers.fixture_openai.base_url=' + json.dumps(origin + '/backend-api/codex'),
            'model_providers.fixture_openai.supports_websockets=false',
            'model_providers.fixture_openai.request_max_retries=0', 'model_providers.fixture_openai.stream_max_retries=0']
for setting in settings:
    if options.minimal_startup and setting.startswith('features.') and not setting.startswith('features.daemon_auto_start='):
        continue
    args += ['-c', setting]
if options.enable_code_mode_host:
    args += ['-c', 'features.code_mode_host=true']
if options.runner_path:
    from runner_mock_path import run_runner
    try:
        runner_report = run_runner(root, origin, cert_path, settings, options, audit, permission_results)
        print(json.dumps(runner_report))
        assert runner_report['acceptancePassed']
    finally:
        server.shutdown()
    raise SystemExit(0)
p = subprocess.Popen(args, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(root/'home'),
                     'CODEX_HOME': str(root/'codex'), 'CODEX_CA_CERTIFICATE': str(cert_path), 'LANG': 'C.UTF-8'},
                     cwd=root, stdin=subprocess.PIPE if options.stdio else None,
                     stdout=subprocess.PIPE if options.stdio else subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
original_httpx = httpx.Client


def callback(request):
    with mutex: callbacks.append((request.headers['Authorization'], json.loads(request.content)))
    return httpx.Response(200, json={'saved': True})


httpx.Client = lambda **kwargs: original_httpx(transport=httpx.MockTransport(callback), **kwargs)
clients, gates, natives, results = [], [], [], {}
report = {'hostAuthRead': False, 'realProviderContact': False, 'syntheticExternalTokenOnly': True,
          'productionGatewayImplemented': False, 'authenticatedAuthorityProven': False,
          'legacyHistory': options.legacy_history, 'legacyTools': options.legacy_tools,
          'minimalStartup': options.minimal_startup, 'transport': 'stdio' if options.stdio else 'unix'}
report['codeModeWire'] = options.code_mode_wire
report['codeModeHostEnabled'] = options.enable_code_mode_host
companion_path = Path('/usr/local/bin/codex-code-mode-host')
report['companionBinarySha256'] = hashlib.sha256(companion_path.read_bytes()).hexdigest() if companion_path.is_file() else None
report['companionMode'] = oct(companion_path.stat().st_mode & 0o777) if companion_path.is_file() else None
labels = ('web',) if options.stdio else ('original', 'web')
expected = len(labels)
callbacks_match = False
try:
    deadline = time.monotonic() + 10
    while not options.stdio and not Path(path).exists() and time.monotonic() < deadline:
        assert p.poll() is None; time.sleep(.05)
    clients = [StdioClient('web', p)] if options.stdio else [Client(label, path) for label in labels]
    def segment(value): return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    token = segment({'alg': 'none'}) + '.' + segment({'email': 'synthetic@example.invalid', 'exp': int(time.time())+3600,
             'https://api.openai.com/auth': {'chatgpt_account_id': 'synthetic-account', 'chatgpt_plan_type': 'plus'}}) + '.synthetic'
    assert clients[0].rpc('account/login/start', {'type': 'chatgptAuthTokens', 'accessToken': token,
                            'chatgptAccountId': 'synthetic-account', 'chatgptPlanType': 'plus'}) is not None
    assert clients[0].rpc('account/read', {'refreshToken': False}) is not None
    for index, client in enumerate(clients):
        label = labels[index]; tool = label + '_fixture_tool'
        registration = {'type': 'function', 'name': tool, 'description': 'Synthetic save',
                        'inputSchema': {'type': 'object', 'properties': {'content': {'type': 'string'}},
                                        'required': ['content'], 'additionalProperties': False}}
        if options.legacy_tools:
            registration.pop('type')
        request = {'model': 'gpt-6.1-sol', 'cwd': str(root/label), 'approvalPolicy': 'never',
                   'sandbox': 'read-only', 'dynamicTools': [registration]}
        if options.legacy_history:
            request.update(historyMode='legacy', ephemeral=False)
        response = client.rpc('thread/start', request)
        assert response
        gate = OwnedEvents(response['thread']['id'], label+'-session', label+'-request', label+'-token', 1, [tool])
        gates.append(gate)
        native = Native.__new__(Native)
        native.auth, native.blocked_reason, native.completed = None, None, {}
        native.callback_url, native.send = 'https://fixture.invalid/tools', client.send
        natives.append(native)
        client.events.clear()

    def run(index):
        client, gate, native = clients[index], gates[index], natives[index]
        label = labels[index]
        def handle(value):
            accepted = gate.accept(1, value)
            if 'method' in value and 'id' in value:
                print(json.dumps({'fixtureServerRequestMethod': value['method'],
                                  'paramKeys': sorted(value.get('params', {})),
                                  'acceptedByGate': bool(accepted)}), flush=True)
            if accepted and accepted['method'] == 'item/tool/call':
                with mutex: captures.append((index, accepted))
                native.reply_tool(accepted, gate.turn)
            if accepted and accepted['method'] == 'turn/completed':
                results[label] = accepted['params']['turn']['status']
        try:
            started = client.rpc('turn/start', {'threadId': gate.turn['thread'], 'model': 'gpt-6.1-sol',
                                  'input': [{'type': 'text', 'text': 'Synthetic save via own registered tool.'}]})
            assert started
            for value in client.events: handle(value)
            client.events.clear()
            for accepted in gate.start_response(1, started):
                with mutex: captures.append((index, accepted))
                native.reply_tool(accepted, gate.turn)
            deadline = time.monotonic() + 20
            while label not in results and time.monotonic() < deadline: handle(client.receive())
            assert results.get(label) == 'completed', label + ' did not complete'
        except Exception as error:
            failures.append({'client': label, 'type': type(error).__name__, 'detail': str(error)})
    workers = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(expected)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(25)
    assert not any(w.is_alive() for w in workers)
    # Replay actual captured server-request after completion/into foreign/old epoch.
    # These are deliberate synthetic replays, not native-generated late callbacks.
    for index, message in captures:
        assert gates[index].accept(1, message) is None
        if len(gates) > 1:
            assert gates[1-index].accept(1, message) is None
        assert gates[index].accept(0, message) is None
    expected_callbacks = [('Bearer '+label+'-token', {'sessionId': label+'-session',
                          'requestId': label+'-request', 'name': label+'_fixture_tool',
                          'arguments': {'content': label+'_fixture_tool-synthetic'}}) for label in labels]
    callbacks_match = len(callbacks) == expected and all(callbacks.count(value) == 1 for value in expected_callbacks)
    report.update(actualStockToolCalls=len(captures), callbackCount=len(callbacks), exactSessionCallbacks=callbacks_match,
                  separateModelToolRegistrations=not audit['missingRegisteredTools'],
                  completedTurns=sum(v == 'completed' for v in results.values()),
                  failedTurns=sum(v == 'failed' for v in results.values()),
                  capturedReplayRejected=bool(captures), nativeGeneratedLateCallbackTested=False)
finally:
    for client in clients: client.close()
    p.terminate()
    try: p.wait(timeout=50)
    except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=2)
    server.shutdown()
    metadata_names = set()
    metadata_seen = 0
    for file in (root/'codex'/'sessions').rglob('*.jsonl'):
        for line in file.read_text().splitlines():
            value = json.loads(line)
            if value.get('type') == 'session_meta':
                metadata_seen += 1
                for spec in value.get('payload', {}).get('dynamic_tools', []):
                    metadata_names.add(spec.get('name'))
    rows = 0
    for db in (root/'codex').glob('state_*.sqlite'):
        with sqlite3.connect('file:'+str(db)+'?mode=ro', uri=True) as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thread_dynamic_tools'").fetchone():
                rows += connection.execute('SELECT COUNT(*) FROM thread_dynamic_tools').fetchone()[0]
    report.update(audit, failures=failures, nativeExitCode=p.returncode,
                  authJsonPersisted=(root/'codex'/'auth.json').exists(),
                  sessionMetadataCount=metadata_seen, persistedToolRows=rows,
                  registeredToolsInMetadata=all(label+'_fixture_tool' in metadata_names for label in labels))
    report['permissionProbeResults'] = permission_results
    permission_keys = {'shellToolAvailable', 'foreignToolAvailable', 'requireAvailable', 'processAvailable', 'fetchAvailable'}
    permissions_match = (not options.permission_probe or
                         (set(permission_results) == {label+'_fixture_tool' for label in labels} and
                          all(set(values) == permission_keys and all(flag is False for flag in values.values())
                              for values in permission_results.values())))
    report['acceptancePassed'] = bool(
        not failures and len(captures) == expected and callbacks_match and
        audit['toolOutputs'] == expected and len(results) == expected and
        all(results.get(label) == 'completed' for label in labels) and
        not audit['wrongModel'] and not audit['nonResponsesPosts'] and
        p.returncode == 0 and not report['authJsonPersisted'] and permissions_match)
    print(json.dumps(report))
assert report['acceptancePassed'], 'Stock acceptance failed: worker/completion/output/authority/permission gate'
