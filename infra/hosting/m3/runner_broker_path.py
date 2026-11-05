"""Actual runner access-service path: pinned app-server 401 -> refresh callback -> real BrokerClient (#60).

The runner, ServiceAccessAuth (envelope v2) and BrokerClient are the deployed code. Only the
broker is a loopback fixture: the production Service name resolves to 127.0.0.1 inside this
process, so BrokerClient keeps its .svc check and its own HTTP client. Output is booleans and
counts; tokens, headers and envelopes are never printed, also when a check fails.
"""
import base64
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from fastapi.testclient import TestClient
from app.codex_policy import MODEL
from app.research_tools import definitions
from app.runner_kube import BROKER_URL
from runner.app import create_app
from runner.binding import initialize_binding

BROKER_HOST = 'codex-broker.codex-broker.svc'
BROKER_PORT = 8080
MODES = ('refresh', 'forbidden', 'same', 'timeout', 'unwired')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def access_token(subject):
    def segment(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return (segment({'alg': 'RS256'})+'.'+segment({'email': 'synthetic@example.invalid', 'sub': subject,
            'exp': int(time.time())+3600, 'https://api.openai.com/auth': {
                'chatgpt_account_id': 'synthetic-account', 'chatgpt_plan_type': 'plus'}})+'.synthetic')


class Broker:
    """Loopback stand-in for codex-broker /v1/access; counts requests, keeps no plaintext log."""
    def __init__(self, mode, projected, owner, state_id):
        self.mode, self.projected, self.owner, self.state_id = mode, projected, owner, state_id
        self.first, self.second = access_token('synthetic-first'), access_token('synthetic-second')
        self.first_snapshot, self.second_snapshot = str(uuid.uuid4()), str(uuid.uuid4())
        self.counts = {'pulls': 0, 'refreshes': 0, 'refused': 0}
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                valid = (self.path == '/v1/access' and self.headers.get('Authorization') == 'Bearer '+broker.projected
                         and set(body) == {'ownerId', 'stateId', 'refresh'} and body['ownerId'] == broker.owner
                         and body['stateId'] == broker.state_id and type(body['refresh']) is bool)
                if not valid:
                    broker.counts['refused'] += 1
                    return self.reply(403, {'error': 'refused'})
                if not body['refresh']:
                    broker.counts['pulls'] += 1
                    return self.reply(200, broker.envelope(broker.first, broker.first_snapshot))
                broker.counts['refreshes'] += 1
                if broker.mode == 'forbidden':
                    return self.reply(403, {'error': 'refused'})
                if broker.mode == 'timeout':
                    time.sleep(20)  # Past BrokerClient's 15 s client timeout.
                    return self.reply(503, {'error': 'unavailable'})
                if broker.mode == 'same':
                    return self.reply(200, broker.envelope(broker.first, broker.first_snapshot))
                return self.reply(200, broker.envelope(broker.second, broker.second_snapshot))

            def reply(self, status, value):
                payload = json.dumps(value).encode()
                self.send_response(status); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)

        self.server = ThreadingHTTPServer(('127.0.0.1', BROKER_PORT), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def envelope(self, token, snapshot):
        exp = json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'=='))['exp']
        return {'version': 2, 'ownerId': self.owner, 'stateId': self.state_id, 'accountId': 'synthetic-account',
                'accessToken': token, 'expiresAt': exp, 'snapshotId': snapshot}


def run_broker(root, origin, ca, settings, options, audit, response_log):
    mode = options.broker_path
    assert mode in MODES
    control, state_root = root/'private-control', root/'actual-runner'
    control.mkdir(mode=0o700)
    owner, state_id = str(uuid.uuid4()), str(uuid.uuid4())
    initialize_binding(state_root, owner, state_id)
    transport_token = 'synthetic-transport-token-' + 't'*40
    token_file, account_file, projected_file = root/'transport-token', root/'operator-account', root/'broker-token'
    projected = 'synthetic-projected-' + 'p'*48
    for file, value in ((token_file, transport_token), (account_file, 'synthetic-account'), (projected_file, projected)):
        file.write_text(value); file.chmod(0o600)
    os.environ.update(APP_ENV='operator-service', CODEX_EXECUTION_SCOPE='operator-access-service',
                      CODEX_AUTH_MODE='access-service', CODEX_ACCESS_CONTROL_DIR=str(control),
                      CODEX_OPERATOR_ACCOUNT_FILE=str(account_file), RUNNER_ACCOUNT_ID=owner, RUNNER_STATE_ID=state_id,
                      RUNNER_STATE_DIR=str(state_root), RUNNER_TOKEN_FILE=str(token_file), CODEX_MODEL=MODEL,
                      CODEX_BIN='/usr/local/bin/codex', RESEARCH_TOOL_CALLBACK_URL='https://fixture.invalid/tools',
                      BROKER_URL=BROKER_URL, BROKER_TOKEN_FILE=str(projected_file))
    broker = Broker(mode, projected, owner, state_id)
    first, second = digest('Bearer '+broker.first), digest('Bearer '+broker.second)
    resolved, callbacks, authority, launched = [], [], {}, []
    original_popen, original_client, original_lookup = subprocess.Popen, httpx.Client, socket.getaddrinfo

    def launch(command, **kwargs):
        launched.append(True)
        for setting in settings:
            # Keep the production alias/retry policy; substitute loopback endpoint/trust only.
            if setting.startswith('chatgpt_base_url='):
                command += ['-c', setting]
            elif setting.startswith('model_providers.fixture_openai.base_url='):
                command += ['-c', setting.replace('fixture_openai', 'access_only_openai')]
        command += ['-c', 'features.enable_request_compression=false']  # Loopback mock cannot decode zstd.
        kwargs['env'] = {**kwargs['env'], 'CODEX_CA_CERTIFICATE': str(ca)}
        return original_popen(command, **kwargs)

    def lookup(host, port, *args, **kwargs):
        # Test-only name resolution for the in-cluster Service; every other name is untouched.
        if host == BROKER_HOST:
            resolved.append(port)
            return original_lookup('127.0.0.1', port, *args, **kwargs)
        return original_lookup(host, port, *args, **kwargs)

    def callback(request):
        body = json.loads(request.content)
        selected = authority.get(body.get('requestId'))
        if selected and request.headers['Authorization'] == 'Bearer '+selected['token']:
            callbacks.append(body['requestId'])
            return httpx.Response(200, json={'saved': True, 'items': []})
        return httpx.Response(403, json={'error': 'refused'})

    def client(**kwargs):
        # BrokerClient always passes its own transport (None in production): leave it the real client.
        # Tool callbacks never pass one and keep the fixture transport.
        if 'transport' in kwargs:
            return original_client(**kwargs)
        return original_client(transport=httpx.MockTransport(callback), **kwargs)

    subprocess.Popen, httpx.Client, socket.getaddrinfo = launch, client, lookup
    headers = {'Authorization': 'Bearer '+transport_token, 'X-Runner-State-Id': state_id}
    checks = {}
    try:
        app = create_app()
        with TestClient(app) as http:
            deadline = time.monotonic()+30
            while time.monotonic() < deadline and http.get('/health', headers=headers).json().get('available') is not True:
                time.sleep(.1)
            checks['brokerPullStartedRunner'] = http.get('/health', headers=headers).json().get('available') is True
            native = app.state.native
            checks['serviceAccessAuth'] = native is not None and type(native.auth).__name__ == 'ServiceAccessAuth'
            if mode == 'unwired':
                native.auth.refresher = None  # Sensitivity: the success checks below must now fail.

            def turn(session, text, expected=None):
                request, token = str(uuid.uuid4()), 'synthetic-tool-token-'+uuid.uuid4().hex
                authority[request] = {'token': token, 'session': session}
                body = {'requestId': request, 'text': text, 'tools': definitions(), 'toolToken': token,
                        'scope': {'page': {'kind': 'general'}}}
                if expected:
                    body['expectedThreadId'] = expected
                if http.post('/sessions/'+session+'/turn', headers=headers, json=body).status_code != 202:
                    return {'state': 'not_accepted'}, request
                deadline = time.monotonic()+60
                while time.monotonic() < deadline:
                    value = http.get('/sessions/'+session, headers=headers).json()
                    if value.get('state') != 'running':
                        return value, request
                    time.sleep(.05)
                return {'state': 'timeout'}, request

            session = str(uuid.uuid4())
            value, request = turn(session, 'Synthetic broker-path turn')
            checks['firstTurnIdle'] = value.get('state') == 'idle' and value.get('model') == MODEL
            checks['firstTurnFirstToken'] = bool(response_log) and all(v['auth'] == first for v in response_log)
            thread, before = value.get('threadId'), len(response_log)
            options.unauthorized_once = True
            value, request = turn(session, 'Synthetic follow-up after upstream 401', thread)
            window = response_log[before:]
            unauthorized = [i for i, v in enumerate(window) if v['unauthorized']]
            failed = window[unauthorized[0]] if len(unauthorized) == 1 else None
            after = window[unauthorized[0]+1:] if failed else []
            # The post-401 window is checked apart from the turn's later tool roundtrip: a retry
            # repeats the failed request (same user turn, same tool-output state).
            retries = [v for v in after if failed and v['user'] == failed['user'] and v['outputs'] == failed['outputs']]
            state = native.auth.state()
            observed = {
                'unauthorizedPosts': len(unauthorized),
                'unauthorizedUsedFirstToken': bool(failed) and failed['auth'] == first,
                'refreshRequests': broker.counts['refreshes'],
                'postsAfter401': len(after), 'retriesOfFailedRequest': len(retries),
                'toolRoundtripsAfterRetry': sum(v['outputs'] and not failed['outputs'] for v in after) if failed else 0,
                'retryIsSameRequest': bool(after) and after[0] in retries,
                'retryUsesNewToken': bool(after) and after[0]['auth'] == second,
                'firstTokenAfter401': sum(v['auth'] == first for v in after),
                'followUpState': value.get('state'), 'followUpErrorCode': value.get('errorCode'),
                'sameThread': value.get('threadId') == thread, 'callbacks': callbacks.count(request),
                'blocked': state['blocked'], 'ledgerRequests': state['requests'],
            }
            # The success contract: one real 401, one broker refresh, exactly one retry of the
            # failed request with the new token, then normal completion with nothing blocked.
            success = {
                'oneUnauthorized': observed['unauthorizedPosts'] == 1 and observed['unauthorizedUsedFirstToken'],
                'oneRefresh': observed['refreshRequests'] == 1,
                'retriedOnceWithNewToken': observed['retryIsSameRequest'] and observed['retryUsesNewToken']
                    and observed['retriesOfFailedRequest'] == 1,
                'oneToolRoundtripAfterRetry': observed['toolRoundtripsAfterRetry'] == 1 and observed['postsAfter401'] == 2,
                'noFirstTokenAfter401': observed['firstTokenAfter401'] == 0,
                'completed': observed['followUpState'] == 'idle' and observed['sameThread'] and observed['callbacks'] == 1,
                'notBlocked': observed['blocked'] is None,
            }
            if mode == 'refresh':
                checks.update(success)
            else:
                # Refused refresh: fixed revocation, the 401 is never replayed, no endless retry.
                checks['successContractFails'] = not all(success.values())
                checks['refreshRequests'] = observed['refreshRequests'] == (0 if mode == 'unwired' else 1)
                checks['fixedRevocation'] = (observed['followUpState'] == 'failed'
                                             and observed['followUpErrorCode'] == 'codex_auth_revoked'
                                             and observed['blocked'] == 'auth_revoked')
                checks['noReplay'] = observed['postsAfter401'] == 0 and observed['unauthorizedPosts'] == 1
            checks['ledgerCountsDispatches'] = observed['ledgerRequests'] == 2
            checks['realBrokerHttp'] = bool(resolved) and all(port == BROKER_PORT for port in resolved) \
                and broker.counts['pulls'] >= 1 and broker.counts['refused'] == 0
            checks['noNonResponsesPosts'] = audit['nonResponsesPosts'] == 0
            checks['noAuthJson'] = not (state_root/'codex'/'auth.json').exists()
            checks['oneNative'] = launched == [True]
        checks['nativeClosed'] = native.closed and native.process.returncode == 0
        report = {'brokerPath': mode, 'actualRunnerPath': True, 'realAppServer': True, 'realProviderContact': False,
                  'brokerFixture': 'loopback', 'counts': {k: observed[k] for k in
                  ('unauthorizedPosts', 'refreshRequests', 'postsAfter401', 'retriesOfFailedRequest',
                   'toolRoundtripsAfterRetry', 'firstTokenAfter401', 'ledgerRequests')},
                  'brokerPulls': broker.counts['pulls'], 'checks': checks,
                  'failedChecks': sorted(k for k, v in checks.items() if not v)}
        report['acceptancePassed'] = not report['failedChecks']
        return report
    finally:
        subprocess.Popen, httpx.Client, socket.getaddrinfo = original_popen, original_client, original_lookup
        broker.server.shutdown()
