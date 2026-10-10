"""Actual runner HTTP/Native regression with only synthetic private grant/cache."""
import base64
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient
from app.codex_policy import MODEL
from app.research_tools import definitions
from runner.app import Native, create_app
from runner.auth import read_private, write_private
from runner.binding import initialize_binding


def run_runner(root, origin, ca, settings, options, audit, permission_results):
    control, state_root = root/'private-control', root/'actual-runner'
    control.mkdir(mode=0o700)
    owner, state_id = str(uuid.uuid4()), str(uuid.uuid4())
    initialize_binding(state_root, owner, state_id)
    transport_token = 'synthetic-transport-token-' + 't'*40
    token_file = root/'transport-token'
    token_file.write_text(transport_token); token_file.chmod(0o600)
    now = datetime.now(timezone.utc)
    def segment(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    jwt = segment({'alg': 'none'})+'.'+segment({'email': 'synthetic@example.invalid',
          'sub': 'synthetic-subject', 'exp': int(time.time())+3600,
          'https://api.openai.com/auth': {'chatgpt_account_id': 'synthetic-account', 'chatgpt_plan_type': 'plus'}})+'.synthetic'
    write_private(control/'grant.json', {'version': 1, 'platformAccountId': owner, 'providerAccountId': 'synthetic-account',
                  'trialId': str(uuid.uuid4()), 'expiresAt': (now+timedelta(minutes=30)).isoformat(), 'maxDispatches': 3,
                  'executionScope': 'personal-private', 'refreshOwnership': 'exclusive-managed-native',
                  'procedureReviewed': True, 'hostGrantQuiescent': True, 'revoked': False})
    write_private(control/'auth.json', {'OPENAI_API_KEY': None, 'auth_mode': 'chatgpt',
                  'tokens': {'account_id': 'synthetic-account', 'id_token': jwt, 'access_token': jwt,
                             'refresh_token': 'synthetic-unused-refresh'}, 'last_refresh': now.isoformat()})
    os.environ.update(APP_ENV='personal-test', CODEX_EXECUTION_SCOPE='personal-private', CODEX_AUTH_MODE='personal-cache',
                      CODEX_PERSONAL_CONTROL_DIR=str(control), RUNNER_ACCOUNT_ID=owner, RUNNER_STATE_ID=state_id,
                      RUNNER_STATE_DIR=str(state_root), RUNNER_TOKEN_FILE=str(token_file), CODEX_MODEL=MODEL,
                      CODEX_BIN='/usr/local/bin/codex', RESEARCH_TOOL_CALLBACK_URL='https://fixture.invalid/tools')
    launched, trace, callbacks, authority = [], [], [], {}
    original_popen, original_client, original_rpc = subprocess.Popen, httpx.Client, Native.rpc

    def launch(command, **kwargs):
        assert 'features.code_mode_host=true' in command
        for flag in ('features.shell_tool=false', 'features.unified_exec=false', 'features.multi_agent=false',
                     'features.apps=false', 'features.plugins=false', 'web_search="disabled"'):
            assert flag in command
        launched.append(True)
        # Test-only route/trust substitution, retaining actual Native command/model/permissions.
        for setting in settings:
            if setting.startswith(('model_provider=', 'model_providers.fixture_openai.', 'chatgpt_base_url=')):
                command += ['-c', setting]
        kwargs['env'] = {**kwargs['env'], 'CODEX_CA_CERTIFICATE': str(ca)}
        return original_popen(command, **kwargs)

    def callback(request):
        body = json.loads(request.content)
        selected = authority.get(body.get('requestId'))
        assert selected and request.headers['Authorization'] == 'Bearer '+selected['token']
        assert body == {'sessionId': selected['session'], 'requestId': body['requestId'],
                        'name': 'research_list', 'arguments': {'type': 'document', 'limit': 1}}
        callbacks.append(body['requestId'])
        return httpx.Response(200, json={'saved': True, 'items': []})

    def rpc(self, method, params, turn=None):
        if method in ('thread/start', 'thread/resume', 'turn/start'):
            trace.append((method, params.get('model')))
        return original_rpc(self, method, params, turn)

    subprocess.Popen, httpx.Client, Native.rpc = launch, lambda **kw: original_client(transport=httpx.MockTransport(callback), **kw), rpc
    headers = {'Authorization': 'Bearer '+transport_token, 'X-Runner-State-Id': state_id}
    report = {'actualRunnerPath': True, 'realProviderContact': False, 'hostAuthRead': False,
              'actualAccountEntitlementProven': False, 'deploymentChanged': False, 'acceptancePassed': False}
    try:
        app = create_app()
        with TestClient(app) as client:
            assert client.get('/health', headers=headers).json()['available'] is True
            native = app.state.native
            assert native is not None and native.fixture is False
            def result(session):
                deadline = time.monotonic()+30
                while time.monotonic()<deadline:
                    response = client.get('/sessions/'+session, headers=headers)
                    assert response.status_code == 200
                    value = response.json()
                    assert value['ownerId'] == owner and value['stateId'] == state_id
                    if value['state'] != 'running': return value
                    time.sleep(.05)
                raise AssertionError('Actual runner did not complete')
            messages, successful = [], []
            for index in range(2):
                session, request = str(uuid.uuid4()), str(uuid.uuid4())
                token = 'synthetic-tool-token-'+str(index)*40
                authority[request] = {'token': token, 'session': session}
                body = {'requestId': request, 'text': 'Synthetic explicit runner turn', 'tools': definitions(), 'toolToken': token}
                if index == 0:
                    assert client.post('/sessions/'+session+'/turn', headers={**headers, 'Authorization': 'Bearer wrong'}, json=body).status_code == 401
                    assert client.post('/sessions/'+session+'/turn', headers={**headers, 'X-Runner-State-Id': str(uuid.uuid4())}, json=body).status_code == 503
                    assert client.post('/sessions/'+session+'/turn', headers=headers, json={**body, 'model': 'another-model'}).status_code == 422
                    assert read_private(control/'ledger.json')['requests'] == [] and not callbacks
                assert client.post('/sessions/'+session+'/turn', headers=headers, json=body).status_code == 202
                value = result(session)
                assert value['state'] == 'idle' and value['model'] == MODEL
                successful.append(value); messages.append((session, body))
                assert client.post('/sessions/'+session+'/turn', headers=headers, json=body).status_code == 202
                assert callbacks.count(request) == 1 and len(read_private(control/'ledger.json')['requests']) == index+1
            assert successful[0]['threadId'] != successful[1]['threadId']
            # Existing conversation resumes its private thread, failing after the third callback.
            session = messages[0][0]; request = str(uuid.uuid4()); token = 'synthetic-third-token-'+'3'*40
            authority[request] = {'token': token, 'session': session}
            body = {**messages[0][1], 'requestId': request, 'toolToken': token, 'text': 'Synthetic explicit follow-up',
                    'expectedThreadId': successful[0]['threadId']}
            before = audit['responsePosts']
            assert client.post('/sessions/'+session+'/turn', headers=headers,
                               json={**body, 'expectedThreadId': str(uuid.uuid4())}).status_code == 503
            assert audit['responsePosts'] == before and len(read_private(control/'ledger.json')['requests']) == 2
            options.fail_after_tool = True
            assert client.post('/sessions/'+session+'/turn', headers=headers, json=body).status_code == 202
            value = result(session)
            assert value['state'] == 'failed' and value['threadId'] == successful[0]['threadId']
            assert callbacks.count(request) == 1 and len(read_private(control/'ledger.json')['requests']) == 3
            before = audit['responsePosts']
            assert client.post('/sessions/'+session+'/turn', headers=headers, json=body).status_code == 202
            assert audit['responsePosts'] == before and callbacks.count(request) == 1
            rejected = client.post('/sessions/'+session+'/turn', headers=headers,
                                   json={**body, 'requestId': str(uuid.uuid4()), 'text': 'Fourth dispatch must fail'})
            assert rejected.status_code == 503 and rejected.json()['error'] == 'codex_budget_exhausted'
            assert audit['responsePosts'] == before and len(callbacks) == 3
            required = {'shellToolAvailable', 'foreignToolAvailable', 'requireAvailable', 'processAvailable', 'fetchAvailable'}
            assert set(permission_results['research_list']) == required
            assert all(v is False for v in permission_results['research_list'].values())
            assert launched == [True] and all(model == MODEL for _, model in trace)
            assert {'thread/start', 'thread/resume', 'turn/start'} <= {method for method, _ in trace}
            assert audit['toolOutputs'] == 2 and audit['postToolFailuresInjected'] == 1 and not audit['wrongModel']
            report.update(actualCallbacks=3, completedTurns=2, failedFollowUp=1, sameThreadResume=True,
                          exactSessionAuthority=True, bearerStateRefusedBeforeDispatch=True, duplicateDispatches=0,
                          consumedDispatches=3, fourthDispatchRefused=True, fixedModel=True,
                          forbiddenCapabilitiesAvailable=False, acceptancePassed=True)
        report['nativeExitCode'] = native.process.returncode
        report['nativeClosed'] = native.closed
        report['cacheRemoved'] = not (state_root/'codex'/'auth.json').exists()
        assert native.closed and report['cacheRemoved']
        return report
    finally:
        subprocess.Popen, httpx.Client, Native.rpc = original_popen, original_client, original_rpc
