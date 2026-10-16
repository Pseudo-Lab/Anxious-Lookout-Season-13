"""Disposable PostgreSQL immutable bindings and per-request plan resolution."""
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
import os

from app.runner_bindings import Bindings, validate
from app.runner_controller import Identity
from app.runner_kube import KubePods, Plan, Reference
from app.runner_pool import Pool
from ops.runner_bindings_migrate import migrate
from runner.lifecycle import Refused
from config_tests.test_runner_registry import schema, admin, owner, t0, enter, read, preservation_snapshot
from config_tests.test_runner_kube import Server


@pytest.fixture(autouse=True)
def binding_schema(schema):
    migrate()


def plan_value(state):
    suffix = state.owner.hex
    return {'owner': str(state.owner), 'state': str(state.state_id), 'generation': 1,
            'image': 'registry.invalid/runner@sha256:'+'a'*64,
            'native': {'name': 'native-'+suffix, 'uid': str(uuid4()), 'volume': 'npv-'+suffix},
            'control': {'name': 'control-'+suffix, 'uid': str(uuid4()), 'volume': 'cpv-'+suffix},
            'transport': {'name': 'token-'+suffix, 'uid': str(uuid4())},
            'callback': 'https://api.invalid/tools', 'serviceAccount': 'runner'}


def test_publication_preserves_registry_and_rejects_drift(admin, owner, t0):
    state, _ = enter(admin, owner, uuid4(), t0)
    value = plan_value(state); ns = uuid4(); pvs = {'native': uuid4(), 'control': uuid4()}
    before = preservation_snapshot(admin)
    with admin.begin() as db:
        assert Bindings(db).publish(value, 'isolated', ns, pvs)
        assert not Bindings(db).publish(value, 'isolated', ns, pvs)
    assert preservation_snapshot(admin) == before
    for changed in (dict(value, image='registry.invalid/runner@sha256:'+'b'*64), dict(value, accessToken='never-store')):
        with pytest.raises(Refused):
            with admin.begin() as db: Bindings(db).publish(changed, 'isolated', ns, pvs)
    with pytest.raises(Refused):
        with admin.begin() as db: Bindings(db).resolve(state, 'isolated', uuid4())
    with admin.begin() as db:
        future = replace(state, generation=2)
        assert Bindings(db).resolve(future, 'isolated', ns).identity == Identity.of(future)


def test_cross_owner_resource_reuse_is_transactionally_refused(admin, owner, t0):
    first, _ = enter(admin, owner, uuid4(), t0)
    other = uuid4()
    with admin.begin() as db:
        db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:id,'6789','other','editor',true)"), {'id': other})
    second, _ = enter(admin, other, uuid4(), t0)
    value = plan_value(first); other_value = plan_value(second); ns = uuid4()
    with admin.begin() as db: Bindings(db).publish(value, 'isolated', ns, {'native': uuid4(), 'control': uuid4()})
    other_value['native'] = value['native']
    with pytest.raises(DBAPIError):
        with admin.begin() as db: Bindings(db).publish(other_value, 'isolated', ns, {'native': uuid4(), 'control': uuid4()})
    with admin.begin() as db: assert Bindings(db).resolve(second, 'isolated', ns) is None


def test_api_cannot_publish_or_mutate_binding(admin, owner, t0):
    state, _ = enter(admin, owner, uuid4(), t0)
    value = plan_value(state); ns = uuid4()
    api = create_engine(os.environ['DATABASE_URL'])
    try:
        with pytest.raises(DBAPIError):
            with api.begin() as db: Bindings(db).publish(value, 'isolated', ns, {'native': uuid4(), 'control': uuid4()})
        with admin.begin() as db: Bindings(db).publish(value, 'isolated', ns, {'native': uuid4(), 'control': uuid4()})
        with api.begin() as db:
            validate(db)
            assert Bindings(db).resolve(state, 'isolated', ns).identity.owner == owner
        with pytest.raises(DBAPIError):
            with api.begin() as db: db.execute(text('DELETE FROM runner_bindings.bindings'))
    finally:
        api.dispose()


def test_pool_observes_late_publication_without_restart_or_cross_owner_cache(admin, owner, t0, tmp_path):
    from app.runner_bindings import decode
    state, _ = enter(admin, owner, uuid4(), t0)
    ns = uuid4(); token = tmp_path/'kube-token'; token.write_text('k'*40)
    config = {'version': 2, 'api': 'https://kube.invalid', 'namespace': 'isolated', 'namespaceUid': str(ns),
              'podCIDR': '10.42.0.0/16', 'caFile': None, 'kubeTokenFile': str(token)}
    value = plan_value(state); server = Server(decode(value))
    def factory(*args, **kwargs):
        return KubePods(*args, **kwargs, _transport=httpx.MockTransport(server))
    pool = Pool(admin, config, port_factory=factory)
    with pytest.raises(Refused):
        with pool.bound(owner): pass
    before = preservation_snapshot(admin)
    with admin.begin() as db: Bindings(db).publish(value, 'isolated', ns, {'native': uuid4(), 'control': uuid4()})
    with pool.bound(owner) as runtime:
        assert runtime.plan.identity == Identity.of(state) and runtime.token == 't'*40
    with pytest.raises(Refused):
        with pool.bound(uuid4()): pass
    pool.ensure(owner)
    assert preservation_snapshot(admin) == before
    assert all(method == 'GET' for method, _ in server.requests)
    # A Secret replaced under the same name is not accepted or cached.
    server.objects[value['transport']['name']]['metadata']['uid'] = str(uuid4())
    with pytest.raises(Refused):
        with pool.bound(owner): pass


@pytest.mark.parametrize('damage', [None, 'storage-owner', 'pv-claim', 'secret-uid', 'namespace-uid'])
def test_import_only_publishes_verified_existing_binding(admin, owner, t0, damage):
    from pathlib import Path
    from app.runner_bindings import decode
    from app.runner_kube import LABEL
    from ops.runner_binding_import import import_binding
    from config_tests.test_runner_kube import response
    import json
    state, _ = enter(admin, owner, uuid4(), t0)
    # Import must also preserve a failed previous generation without recovery.
    with admin.begin() as db:
        db.execute(text("UPDATE runner_runtime.registry SET phase='runner_failed',pod_uid=:uid WHERE owner_id=:owner"),
                   {'owner': owner, 'uid': uuid4()})
    value = plan_value(state); plan = decode(value); ns = uuid4()
    server = Server(plan)
    root = Path('/var/lib/anxious-lookout/user-runners')
    base = root/state.state_id.hex
    data = {base/'native/account-id': str(owner).encode(),
            base/'native/state-binding.json': json.dumps({'version':1,'ownerId':str(owner),'stateId':str(state.state_id)}).encode(),
            base/'control/operator-account': b'operator-account', Path('/private/token'): b't'*40}
    if damage == 'storage-owner': data[base/'native/account-id'] = str(uuid4()).encode()
    if damage == 'secret-uid': server.objects[plan.transport.name]['metadata']['uid'] = str(uuid4())
    volumes = {}
    for kind, ref in [('native',plan.native),('control',plan.control)]:
        volumes[ref.volume] = {'kind':'PersistentVolume', 'metadata': {'name':ref.volume,'uid':str(uuid4()),
            'labels':{LABEL+'owner':str(owner),LABEL+'state':str(state.state_id)}},
            'spec':{'local':{'path':str(base/kind)},'persistentVolumeReclaimPolicy':'Retain',
                'accessModes':['ReadWriteOnce'], 'claimRef': {'namespace':'isolated','name':ref.name,'uid':str(ref.uid)}}}
    if damage == 'pv-claim': volumes[plan.native.volume]['spec']['claimRef']['uid'] = str(uuid4())
    operations = []
    def handler(request):
        operations.append(request.method)
        if request.url.path == '/api/v1/namespaces/isolated':
            return response(200, {'metadata':{'uid':str(uuid4() if damage == 'namespace-uid' else ns),
                                             'labels':{'pod-security.kubernetes.io/enforce':'restricted'}}})
        if request.url.path.startswith('/api/v1/persistentvolumes/'):
            return response(200, volumes[request.url.path.rsplit('/',1)[1]])
        return server(request)
    pods = KubePods('https://kube.invalid','isolated','k'*40,None,[],_transport=httpx.MockTransport(handler))
    config = {'version':1, 'plan':value,'namespace':'isolated','namespaceUid':str(ns), 'transportTokenFile':'/private/token'}
    before = preservation_snapshot(admin); retained = deepcopy(data)
    try:
        if damage:
            with pytest.raises(Refused): import_binding(admin,pods,config,root,'operator-account',read=data.__getitem__)
            with admin.begin() as db: assert Bindings(db).resolve(state,'isolated',ns) is None
        else:
            assert import_binding(admin,pods,config,root,'operator-account',read=data.__getitem__)
            assert not import_binding(admin,pods,config,root,'operator-account',read=data.__getitem__)
        assert preservation_snapshot(admin) == before and data == retained
        assert set(operations) == {'GET'}
    finally:
        pods.close()


def test_limits_are_explicit_bounded_inputs(monkeypatch):
    from app.runner_limits import load_limits
    monkeypatch.delenv('CODEX_USER_RUNNER_MAX_PODS', raising=False)
    monkeypatch.delenv('CODEX_USER_RUNNER_IDLE_SECONDS', raising=False)
    assert load_limits().max_pods == 3 and load_limits().idle_seconds == 1800
    for key, value in [('CODEX_USER_RUNNER_MAX_PODS','-1'), ('CODEX_USER_RUNNER_MAX_PODS','1.0'),
                       ('CODEX_USER_RUNNER_IDLE_SECONDS','0')]:
        with monkeypatch.context() as patch:
            patch.setenv(key, value)
            with pytest.raises(Refused): load_limits()


def test_controller_capability_can_publish_but_cannot_approve_users(admin, owner, t0):
    from pathlib import Path
    from sqlalchemy import inspect
    state, _ = enter(admin, owner, uuid4(), t0)
    with admin.begin() as db:
        if not db.execute(text("SELECT 1 FROM pg_roles WHERE rolname='anxious_runner_controller'")).first():
            db.connection.driver_connection.execute(Path('/source/db/016_runner_controller_grants.sql').read_text())
    with admin.begin() as db:
        db.execute(text('SET LOCAL ROLE anxious_runner_controller'))
        validate(db)
        from app.runner_registry import Registry
        Registry(db)._eligible(owner)
        assert Bindings(db).publish(plan_value(state), 'isolated', uuid4(), {'native':uuid4(),'control':uuid4()})
    with pytest.raises(DBAPIError):
        with admin.begin() as db:
            db.execute(text('SET LOCAL ROLE anxious_runner_controller'))
            db.execute(text('UPDATE auth.accounts SET is_approved=false WHERE id=:owner'), {'owner':owner})


@pytest.mark.parametrize('privilege', ['INSERT', 'UPDATE', 'REFERENCES'])
@pytest.mark.parametrize('table,column', [('bindings', 'plan'), ('resources', 'uid')])
def test_api_column_privilege_drift_is_rejected(admin, privilege, table, column):
    # Fixed test parameters only; the temporary grant is rolled back even on failure.
    with admin.connect() as db:
        transaction = db.begin()
        try:
            db.execute(text(f'GRANT {privilege} ({column}) ON runner_bindings.{table} TO anxious_api'))
            db.execute(text('SET LOCAL ROLE anxious_api'))
            with pytest.raises(Refused): validate(db)
        finally:
            transaction.rollback()


def test_api_column_insert_publication_probe_is_blocked_by_validation(admin, owner, t0):
    state, _ = enter(admin, owner, uuid4(), t0)
    ns = uuid4()
    with admin.connect() as db:
        transaction = db.begin()
        try:
            db.execute(text('GRANT INSERT (owner_id,state_id,namespace,namespace_uid,plan) ON runner_bindings.bindings TO anxious_api'))
            db.execute(text('GRANT INSERT (owner_id,kind,name,uid) ON runner_bindings.resources TO anxious_api'))
            db.execute(text('SET LOCAL ROLE anxious_api'))
            with pytest.raises(Refused):
                validate(db)
                Bindings(db).publish(plan_value(state), 'isolated', ns, {'native':uuid4(), 'control':uuid4()})
            assert db.execute(text('SELECT count(*) FROM runner_bindings.bindings')).scalar_one() == 0
        finally:
            transaction.rollback()
