import json
import os
import time
import uuid

import pytest

from access_state_diagnose import summary
from access_diagnose_prepare import emit, DIAGNOSTIC_IMAGE
from test_access_web_prepare import package
from access_link_patch import patch
from test_prepare import apply


def test_state_summary_does_not_read_broker_journal_or_auth_contents(tmp_path):
    native, control = tmp_path/'native', tmp_path/'control'
    native.mkdir(mode=0o700); control.mkdir(mode=0o700)
    owner, state, window = [str(uuid.uuid4()) for _ in range(3)]
    def private(path, value):
        path.write_text(json.dumps(value)); path.chmod(0o600)
    private(native/'state-binding.json', {'version': 1, 'ownerId': owner, 'stateId': state})
    private(control/'access-ledger.json', {'version': 1, 'owner': owner, 'stateId': state,
            'accountId': 'PRIVATE_ACCOUNT_MUST_NOT_PRINT', 'windowId': window, 'windows': [window],
            'requests': [], 'deadline': int(time.time())-1, 'windowStartedAt': int(time.time())-901, 'blocked': None})
    for path in (native/'broker.json', control/'access-projection.json'):
        path.write_text('NOT_JSON_PRIVATE_CONTENT_DO_NOT_READ'); path.chmod(0o600)
    result = summary(native, control, owner, state, window)
    assert result['ledgerInstalled'] and result['bindingMatches'] and result['selectedWindowMatches']
    assert result['deadlinePassed'] and result['windowTimeLimitPassed'] and result['lifetimeReservations'] == 0
    assert result['projectionExists'] and result['brokerExists']
    assert 'PRIVATE_ACCOUNT' not in json.dumps(result) and owner not in json.dumps(result) and window not in json.dumps(result)
    assert 'NOT_JSON_PRIVATE' not in json.dumps(result)
    with pytest.raises(ValueError):
        summary(native, control, str(uuid.uuid4()), state)


def test_diagnostic_jobs_mount_state_readonly_or_no_state_at_all(package):
    snapshot, inputs, _ = package
    snapshot['runner'] = {'metadata': {'namespace': 'codex-trial', 'name': 'access-web-runner-v1'}, 'spec': {'replicas': 0}}
    snapshot['runnerPodCount'] = 0
    state = emit(snapshot, inputs, DIAGNOSTIC_IMAGE, 'state')
    deny, job = state['items']
    assert deny['spec']['egressDeny'] == [{'toEntities': ['all']}]
    pod = job['spec']['template']['spec']
    assert all(v['persistentVolumeClaim']['readOnly'] for v in pod['volumes'])
    assert all(v['readOnly'] for v in pod['containers'][0]['volumeMounts'])
    assert pod['automountServiceAccountToken'] is False and 'fsGroup' not in pod['securityContext']
    assert 'uvicorn' not in pod['containers'][0]['command'] and 'Native(' not in pod['containers'][0]['command'][3]
    network = emit(snapshot, inputs, DIAGNOSTIC_IMAGE, 'network')
    assert network['spec']['template']['spec']['volumes'] == []
    assert network['spec']['template']['spec']['containers'][0]['env'] == []
    snapshot['runner']['spec']['replicas'] = 1
    with pytest.raises(ValueError):
        emit(snapshot, inputs, DIAGNOSTIC_IMAGE, 'state')


def test_only_exact_old_callback_link_can_be_corrected(package):
    import access_web_prepare as web
    import copy
    _, inputs, images = package
    old = web.base.policies(dict(inputs, providerHosts=['chatgpt.com'], procedureRef='ACCESS-WEB-TEST.md'))['items'][1]
    old['metadata'].update(name='hosting-access-web-link-v1', uid='existing-link', resourceVersion='current-rv')
    revised = apply(old, patch(old, inputs, images))
    assert revised['spec']['enableDefaultDeny'] == {'ingress': False, 'egress': False}
    assert revised['spec']['ingress'] == old['spec']['ingress']
    assert revised['spec']['egress'] == old['spec']['egress'][:1]
    drift = copy.deepcopy(old)
    drift['spec']['endpointSelector'] = {'matchLabels': {'app': 'another-app'}}
    with pytest.raises(ValueError):
        patch(drift, inputs, images)


def test_api_diagnosis_never_prints_dsn_and_uses_no_other_secret(monkeypatch):
    import access_api_diagnose as api
    calls = []
    def secret(name):
        calls.append(name)
        assert name == 'DATABASE_URL'
        return 'postgresql+psycopg://user:PRIVATE_DSN_VALUE@private.invalid/database'
    monkeypatch.setattr(api, 'secret', secret)
    monkeypatch.setattr(api, 'probe', lambda *_: {'dns': 'failed', 'tcp': 'not_tested', 'tls': 'not_tested'})
    result = api.diagnose()
    assert calls == ['DATABASE_URL'] and result['dbNetwork']['dns'] == 'failed'
    assert 'PRIVATE_DSN_VALUE' not in json.dumps(result) and 'private.invalid' not in json.dumps(result)
    assert result['dataModified'] is False and result['sourceCredentialRead'] is False
