"""Pinned native tool roundtrip via fixture-only shared ingress, Docker network-none."""
import base64
import argparse
import ipaddress
import json
import os
import ssl
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

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--legacy-history', action='store_true')
parser.add_argument('--legacy-tools', action='store_true')
parser.add_argument('--minimal-startup', action='store_true')
options = parser.parse_args()

root = Path('/tmp/shared-tools'); root.mkdir(mode=0o700)
for name in ('home', 'codex', 'original', 'web'): (root / name).mkdir(mode=0o700)
models = json.loads(Path('/fixture-models.json').read_text())
audit = {'responsePosts': 0, 'toolOutputs': 0, 'wrongModel': False, 'nonResponsesPosts': 0,
         'missingRegisteredTools': 0}
requests, callbacks, captures, failures = [], [], [], []
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
            outputs = [v for v in body.get('input', []) if v.get('type') == 'function_call_output']
            def tool_names(values):
                return [name for value in values for name in
                        ([value.get('name')] + tool_names(value.get('tools', [])))]
            names = tool_names(body.get('tools', []))
            for item in body.get('input', []):
                if item.get('type') == 'additional_tools':
                    names += tool_names(item.get('tools', []))
            print(json.dumps({'registeredFixtureTools': [v for v in names if v in
                             ('original_fixture_tool', 'web_fixture_tool')],
                              'responseToolCount': len(body.get('tools', [])),
                              'inputToolNamesCount': len(names)}), flush=True)
            name = next((v for v in names if v in ('original_fixture_tool', 'web_fixture_tool')), None)
            if not name:
                audit['missingRegisteredTools'] += 1
                payload = b'{"error":{"message":"Fixture registered tool absent","type":"invalid_request_error"}}'
                self.send_response(400); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload))); self.end_headers()
                self.wfile.write(payload)
                return
            other = 'web_fixture_tool' if name == 'original_fixture_tool' else 'original_fixture_tool'
            assert other not in names, 'Foreign tool registration leaked'
            call = name + '-call'
            if outputs:
                assert any(v['call_id'] == call and 'saved' in str(v.get('output')) for v in outputs)
                audit['toolOutputs'] += 1
                item = {'type': 'message', 'id': name + '-message', 'role': 'assistant',
                        'status': 'completed', 'content': [{'type': 'output_text', 'text': 'Synthetic saved', 'annotations': []}]}
            else:
                item = {'type': 'function_call', 'id': name + '-item', 'call_id': call,
                        'name': name, 'arguments': json.dumps({'content': name + '-synthetic'})}
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
args = ['/usr/local/bin/codex', 'app-server', '--listen', 'unix://' + path]
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
p = subprocess.Popen(args, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(root/'home'),
                     'CODEX_HOME': str(root/'codex'), 'CODEX_CA_CERTIFICATE': str(cert_path), 'LANG': 'C.UTF-8'},
                     cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
original_httpx = httpx.Client


def callback(request):
    with mutex: callbacks.append((request.headers['Authorization'], json.loads(request.content)))
    return httpx.Response(200, json={'saved': True})


httpx.Client = lambda **kwargs: original_httpx(transport=httpx.MockTransport(callback), **kwargs)
clients, gates, natives, results = [], [], [], {}
report = {'hostAuthRead': False, 'realProviderContact': False, 'syntheticExternalTokenOnly': True,
          'productionGatewayImplemented': False, 'authenticatedAuthorityProven': False,
          'legacyHistory': options.legacy_history, 'legacyTools': options.legacy_tools,
          'minimalStartup': options.minimal_startup}
try:
    deadline = time.monotonic() + 10
    while not Path(path).exists() and time.monotonic() < deadline:
        assert p.poll() is None; time.sleep(.05)
    clients = [Client(label, path) for label in ('original', 'web')]
    def segment(value): return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    token = segment({'alg': 'none'}) + '.' + segment({'email': 'synthetic@example.invalid', 'exp': int(time.time())+3600,
             'https://api.openai.com/auth': {'chatgpt_account_id': 'synthetic-account', 'chatgpt_plan_type': 'plus'}}) + '.synthetic'
    assert clients[0].rpc('account/login/start', {'type': 'chatgptAuthTokens', 'accessToken': token,
                            'chatgptAccountId': 'synthetic-account', 'chatgptPlanType': 'plus'}) is not None
    assert clients[0].rpc('account/read', {'refreshToken': False}) is not None
    for index, client in enumerate(clients):
        label = ('original', 'web')[index]; tool = label + '_fixture_tool'
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
        label = ('original', 'web')[index]
        def handle(value):
            accepted = gate.accept(1, value)
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
    workers = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(25)
    assert not any(w.is_alive() for w in workers)
    if not failures:
        assert len(callbacks) == len(captures) == audit['toolOutputs'] == 2
        for label in ('original', 'web'):
            selected = [v for v in callbacks if v[0] == 'Bearer ' + label + '-token']
            assert len(selected) == 1 and selected[0][1]['sessionId'] == label+'-session'
            assert selected[0][1]['requestId'] == label+'-request'
            assert selected[0][1]['name'] == label+'_fixture_tool'
    # Replay actual captured server-request after completion/into foreign/old epoch.
    # These are deliberate synthetic replays, not native-generated late callbacks.
    for index, message in captures:
        assert gates[index].accept(1, message) is None
        assert gates[1-index].accept(1, message) is None
        assert gates[index].accept(0, message) is None
    report.update(actualStockToolCalls=len(captures), exactSessionCallbacks=not failures,
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
    report.update(audit, failures=failures, nativeExitCode=p.returncode,
                  authJsonPersisted=(root/'codex'/'auth.json').exists())
    print(json.dumps(report))
assert report.get('actualStockToolCalls') == 2 and not audit['wrongModel'] and not audit['nonResponsesPosts']
assert p.returncode == 0 and not report['authJsonPersisted']
