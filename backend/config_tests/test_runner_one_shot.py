from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from ops import runner_one_shot as one
from ops.runner_policy import defaults
from app.runner_controller import Controller, Pod
from runner.lifecycle import Fence, Refused


def directory(path):
    path.mkdir(mode=0o700)
    return path


def secret(path, value):
    path.write_text(value)
    path.chmod(0o600)
    return path


def observed():
    obj = defaults(one.PLAN.manifest(one.NAMESPACE))
    obj['metadata'].update(uid='23c64cfe-f7bf-4cdb-9d46-eb250fc3f230', resourceVersion='123')
    return obj


def test_permit_survives_reentry_and_foreign_or_partial_refused(tmp_path):
    assert one.claim(tmp_path)
    assert not one.claim(tmp_path)
    marker = tmp_path/'create-permit.json'
    marker.write_text('{')
    with pytest.raises(ValueError): one.claim(tmp_path)
    marker.write_text('{}')
    with pytest.raises(Refused): one.claim(tmp_path)


@pytest.mark.parametrize('bad', ['symlink', 'fifo', 'mode', 'hardlink'])
def test_private_file_refuses_unsafe_inputs(tmp_path, bad):
    path = secret(tmp_path/'secret', 'x'*40)
    if bad == 'symlink':
        other = tmp_path/'target'; path.rename(other); path.symlink_to(other)
    if bad == 'fifo': path.unlink(); os.mkfifo(path, 0o600)
    if bad == 'mode': path.chmod(0o644)
    if bad == 'hardlink': os.link(path, tmp_path/'other')
    with pytest.raises(Refused): one.private_file(path)


@pytest.mark.parametrize('suffix', ['?host=production', '?port=5432', '?options=-csearch_path=other'])
def test_dsn_query_override_refused(tmp_path, suffix):
    path = secret(tmp_path/'dsn', 'postgresql+psycopg://anxious_api:synthetic-password@127.0.0.1:25410/'+one.DATABASE+suffix)
    with pytest.raises(Refused): one.database_url(path)


@pytest.mark.parametrize('mutation', ['image', 'labels', 'node', 'annotation', 'default', 'tolerations', 'host'])
def test_exact_server_defaults_refuse_drift(mutation):
    obj = observed()
    assert one.exact_pod(obj, persisted=True)
    if mutation == 'image': obj['spec']['containers'][0]['image'] = 'foreign'
    if mutation == 'labels': obj['metadata']['labels']['extra'] = 'true'
    if mutation == 'node': obj['spec']['nodeName'] = 'other'
    if mutation == 'annotation': obj['metadata']['annotations'] = {'x':'y'}
    if mutation == 'default': obj['spec']['priority'] = 1
    if mutation == 'tolerations': obj['spec']['tolerations'].reverse()
    if mutation == 'host': obj['spec']['hostNetwork'] = True
    with pytest.raises(Refused): one.exact_pod(obj, persisted=True)


def test_native_false_omission_and_scheduled_node():
    obj = observed()
    for key in ('hostNetwork', 'hostPID', 'hostIPC'): obj['spec'].pop(key)
    obj['spec']['nodeName'] = 'anxious-lookout'
    one.exact_pod(obj, persisted=True)
    with pytest.raises(Refused): one.exact_pod(obj)


@pytest.mark.parametrize('failure', ['dry-run', 'drift', 'post', 'none'])
def test_port_attempt_is_consumed_before_dry_run(tmp_path, failure):
    calls = []
    def handler(req):
        calls.append(str(req.url))
        if (failure == 'dry-run' and 'dryRun' in req.url.query.decode()) or (failure == 'post' and 'dryRun' not in req.url.query.decode()):
            raise httpx.ReadTimeout('synthetic')
        obj = observed()
        if failure == 'drift': obj['spec']['hostNetwork'] = True
        return httpx.Response(201, stream=httpx.ByteStream(json.dumps(obj).encode()))
    port = object.__new__(one.Port)
    port.fresh = True; port.evidence = tmp_path; port.permit = tmp_path
    port.client = httpx.Client(base_url=one.API, transport=httpx.MockTransport(handler))
    port.preflight = lambda: []
    port._pod = lambda obj, plan: Pod(plan.identity, UUID(obj['metadata']['uid']))
    try:
        if failure == 'none': port.create_once(one.PLAN.identity)
        else:
            with pytest.raises(Exception): port.create_once(one.PLAN.identity)
        before = len(calls)
        with pytest.raises(Refused): port.create_once(one.PLAN.identity)
        assert len(calls) == before
        assert len([c for c in calls if 'dryRun' not in c]) <= 1
        assert (tmp_path/'server-dry-run.json').exists() == (failure in ('post', 'none'))
    finally: port.client.close()


@pytest.mark.skipif(os.getenv('ISSUE10_DISPOSABLE_DB') != '1', reason='dedicated Docker PostgreSQL required')
def test_real_disposable_db_receipt_crash_and_reentry(tmp_path, monkeypatch):
    creds = directory(tmp_path/'private'); permit = directory(tmp_path/'permit')
    admin = secret(creds/'admin-dsn', 'postgresql+psycopg://postgres:synthetic-test-password@127.0.0.1:25410/'+one.DATABASE)
    api = secret(creds/'database-dsn', 'postgresql+psycopg://anxious_api:synthetic-api-password@127.0.0.1:25410/'+one.DATABASE)
    password = secret(creds/'api-password', 'synthetic-api-password')
    one.bootstrap(admin, password, permit)
    with pytest.raises(FileExistsError): one.bootstrap(admin, password, permit)
    engine = one.engine_for(api)
    class CrashPort:
        posts = 0
        def lookup(self, identity): return None
        def create_once(self, identity):
            self.posts += 1
            raise RuntimeError('lost response')
    port = CrashPort()
    try:
        with engine.begin() as db: current = one.check_db(db)
        assert one.claim(permit)
        controller = Controller(engine, port, lambda: datetime.now(timezone.utc), max_pods=1)
        with pytest.raises(RuntimeError): controller.ensure(Fence.of(current))
        with engine.begin() as db:
            current = one.check_db(db)
            assert db.execute(text('SELECT count(*) FROM runner_runtime.outbox WHERE settled')).scalar_one() == 1
        assert not one.claim(permit)
        with pytest.raises(Refused): controller.ensure(Fence.of(current))
        assert port.posts == 1
        port.lookup = lambda identity: Pod(identity, UUID(observed()['metadata']['uid']))
        result = controller.reconcile(Fence.of(current))
        assert str(result.pod_uid) == observed()['metadata']['uid']
        with engine.begin() as db: one.check_db(db)
        # Invoke the actual CLI workflow function again: lookup/report only.
        secret(creds/'sa-token', 's'*40); secret(creds/'transport-token', 'x'*40)
        port.preflight = lambda: [observed()]
        port.close = lambda: None
        monkeypatch.setattr(one, 'Port', lambda *args, **kwargs: port)
        monkeypatch.setattr(one, 'health', lambda *args: {'transport': 'synthetic-test'})
        evidence = directory(tmp_path/'evidence')
        one.run(permit, evidence, api, creds/'sa-token', creds/'transport-token', creds/'ca.crt')
        assert json.loads((evidence/'result.json').read_text())['freshPermit'] is False
        assert port.posts == 1
        # A DB whose receipt was lost cannot regain creation permission through run.
        admin_engine = one.engine_for(admin, True)
        try:
            with admin_engine.begin() as db:
                db.execute(text("UPDATE runner_runtime.registry SET pod_uid=NULL,phase='provisioning',ambiguous=false"))
                db.execute(text('UPDATE runner_runtime.outbox SET settled=false'))
        finally: admin_engine.dispose()
        with pytest.raises(Refused):
            one.run(permit, directory(tmp_path/'restored-db-evidence'), api, creds/'sa-token', creds/'transport-token', creds/'ca.crt')
        assert port.posts == 1
    finally: engine.dispose()


def test_concurrent_permit_only_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    # Competing readers may see an incomplete marker and refuse: never a new grant.
    def compete(_):
        try: return one.claim(tmp_path)
        except (ValueError, Refused): return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(compete, range(24))) == 1


def resources():
    labels = {'anxious.io/owner': one.OWNER, 'anxious.io/state': one.STATE}
    objects = {}
    for ref, uid, leaf in zip((one.PLAN.native, one.PLAN.control), one.PV_UIDS, ('native', 'control')):
        objects['persistentvolumeclaims/'+ref.name] = dict(apiVersion='v1', kind='PersistentVolumeClaim',
            metadata=dict(name=ref.name, namespace=one.NAMESPACE, uid=str(ref.uid), labels=labels),
            spec=dict(volumeName=ref.volume), status=dict(phase='Bound'))
        objects['/api/v1/persistentvolumes/'+ref.volume] = dict(kind='PersistentVolume',
            metadata=dict(name=ref.volume, uid=uid), spec=dict(claimRef=dict(uid=str(ref.uid), name=ref.name, namespace=one.NAMESPACE),
            persistentVolumeReclaimPolicy='Retain', accessModes=['ReadWriteOnce'], storageClassName='',
            local={'path': '/var/lib/anxious-lookout/runner-rehearsal/'+one.STATE+'/'+leaf},
            nodeAffinity={'required': {'nodeSelectorTerms': [{'matchExpressions': [{'key':'kubernetes.io/hostname','operator':'In','values':['anxious-lookout']}]}]}}))
    import base64
    objects['secrets/'+one.PLAN.transport.name] = dict(apiVersion='v1', kind='Secret', type='Opaque', immutable=True,
        metadata=dict(name=one.PLAN.transport.name, namespace=one.NAMESPACE, uid=str(one.PLAN.transport.uid), labels=labels),
        data={'token': base64.b64encode(b'x'*40).decode()})
    objects['/api/v1/namespaces/'+one.NAMESPACE] = dict(kind='Namespace', metadata=dict(name=one.NAMESPACE,
        uid=one.NAMESPACE_UID, labels={'kubernetes.io/metadata.name':one.NAMESPACE,
        'pod-security.kubernetes.io/enforce':'restricted','pod-security.kubernetes.io/enforce-version':'v1.36'}))
    objects['pods'] = dict(kind='PodList', items=[])
    return objects


@pytest.mark.parametrize('drift', ['none', 'namespace', 'tenant', 'pvc', 'pv', 'claimref', 'path', 'secret', 'token', 'foreign-pod'])
def test_preflight_references_and_inventory(monkeypatch, tmp_path, drift):
    objects = resources()
    if drift == 'namespace': objects['/api/v1/namespaces/'+one.NAMESPACE]['metadata']['uid'] = 'foreign'
    if drift == 'tenant': objects['/api/v1/namespaces/'+one.NAMESPACE]['metadata']['labels']['anxious-lookout.io/tenant'] = 'true'
    if drift == 'pvc': objects['persistentvolumeclaims/'+one.PLAN.native.name]['metadata']['uid'] = str(UUID(int=1))
    pv = objects['/api/v1/persistentvolumes/'+one.PLAN.native.volume]
    if drift == 'pv': pv['metadata']['uid'] = 'foreign'
    if drift == 'claimref': pv['spec']['claimRef']['uid'] = 'foreign'
    if drift == 'path': pv['spec']['local']['path'] = '/foreign'
    if drift == 'secret': objects['secrets/'+one.PLAN.transport.name]['metadata']['uid'] = str(UUID(int=2))
    if drift == 'foreign-pod': objects['pods']['items'] = [observed()]; objects['pods']['items'][0]['metadata']['name'] = 'other'
    port = object.__new__(one.Port)
    port.namespace = one.NAMESPACE
    port.evidence = tmp_path
    port.client = None
    port.transport_token = 'y'*40 if drift == 'token' else 'x'*40
    port._request = lambda method, resource, name=None: objects[resource+('/'+name if name else '')]
    monkeypatch.setattr(one, 'request', lambda client, method, path: objects[path])
    if drift == 'none': assert port.preflight() == []
    else:
        with pytest.raises((Refused, ValueError)): port.preflight()


def test_pod_uid_cannot_be_rebound(tmp_path):
    one.bind_uid(tmp_path, UUID(int=1))
    one.bind_uid(tmp_path, UUID(int=1))
    with pytest.raises(Refused): one.bind_uid(tmp_path, UUID(int=2))


def test_health_four_auth_cases_and_no_secret_evidence(tmp_path, monkeypatch):
    obj = observed()
    obj['spec']['nodeName'] = 'anxious-lookout'
    obj['status'] = dict(phase='Running', podIP='10.42.0.99')
    port = object.__new__(one.Port)
    port.evidence = tmp_path; port.transport_token = 'private-transport-token'*3
    port._request = lambda *args: deepcopy(obj)
    calls = []
    def handler(req):
        calls.append(req)
        if req.headers['Authorization'] != 'Bearer '+port.transport_token: code, body = 401, {'detail':'unauthorized'}
        elif req.headers.get('X-Runner-State-Id') != one.STATE: code, body = 503, {'detail':'refused'}
        else:
            code, body = 200, dict(ownerId=one.OWNER, stateId=one.STATE, podUid=obj['metadata']['uid'], available=False, verification='unverified', reason='not_configured')
        return httpx.Response(code, stream=httpx.ByteStream(json.dumps(body).encode()))
    client = httpx.Client(base_url='http://10.42.0.99:8080', transport=httpx.MockTransport(handler))
    monkeypatch.setattr(one.httpx, 'Client', lambda **kwargs: client)
    result = one.health(port, UUID(obj['metadata']['uid']))
    assert [row['status'] for row in result['cases']] == [200, 401, 503, 503]
    assert len(calls) == 4 and all(req.method == 'GET' and req.url.path == '/health' for req in calls)
    assert port.transport_token not in json.dumps(result)
    assert port.transport_token not in (tmp_path/'get-pod.json').read_text()


@pytest.mark.parametrize('connected', [True, False])
def test_host_probe_never_infers_policy_deny(tmp_path, monkeypatch, connected):
    from ops import runner_host_probe as probe
    path = secret(tmp_path/'health.json', json.dumps(dict(podUid=str(UUID(int=1)), podIP='10.42.0.99')))
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): assert value == 3
        def connect(self, address):
            assert address == ('10.42.0.99', 8081)
            if not connected: raise TimeoutError()
    monkeypatch.setattr(probe.socket, 'socket', lambda *args: Socket())
    probe.probe(path, tmp_path/'probe.json')
    result = json.loads((tmp_path/'probe.json').read_text())
    assert result['policyVerdict'] == 'unverified'
    assert result['socketOutcome'] == ('connected' if connected else 'not-connected')
