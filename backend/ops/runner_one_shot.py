"""Issue10 only: one durable creation permission, no reset/delete/provider actions."""
import argparse
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import ssl
import stat
import time
from uuid import UUID

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import runner_http_budget as budget
from app.runner_controller import Controller
from app.runner_kube import KubePods
from app.runner_registry import Registry
from app.runner_schema import validate
from ops.runner_policy import NAMESPACE, OWNER, STATE, defaults, plan_for
from runner.lifecycle import Fence, require

IMAGE = 'docker.io/library/anxious-s13-runner-rehearsal@sha256:bbec1f6b5933918d79ffa966fad3c0727071ca77acddb198af937887780a7ad1'
NAMESPACE_UID = 'f5b2e33f-5166-4163-a60a-5421b8ced42e'
DATABASE = 'runner_rehearsal_a9617ace'
API = 'https://10.0.0.107:6443'
VALUES = dict(image=IMAGE, callback='https://rehearsal-callback.invalid:443/research/tools',
              callbackNamespace='codex-runner-callback-disabled', callbackServiceAccount='callback-disabled',
              callbackPort='443', quotaResourceVersion='1')
PLAN = plan_for(VALUES)
PV_UIDS = ['56f5e504-4305-4382-9822-195dc5361a01', 'd2ee17a8-23f9-4f83-8bb7-e47f8d181844']


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()


def private_dir(path):
    require(path.is_absolute() and path == path.resolve())
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)


def private_file(path):
    private_dir(path.parent)
    require(stat.S_ISREG(path.lstat().st_mode))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
                stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and info.st_size <= 65536)
        value = source.read(65537)
    require(len(value) <= 65536)
    return value


def persist(path, value):
    private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(encoded(value)); target.flush(); os.fsync(target.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def binding():
    return dict(version=1, namespaceUid=NAMESPACE_UID, plan=PLAN.manifest(NAMESPACE),
                pvcUids=[str(PLAN.native.uid), str(PLAN.control.uid)], pvUids=PV_UIDS,
                secretUid=str(PLAN.transport.uid))


def claim(directory):
    """Even a crash before the first API call consumes this permission forever."""
    path = directory / 'create-permit.json'
    try:
        persist(path, binding())
        return True
    except FileExistsError:
        require(json.loads(private_file(path)) == binding())
        return False


def bind_uid(directory, uid):
    value = dict(podUid=str(UUID(str(uid))), bindingSha256=hashlib.sha256(encoded(binding())).hexdigest())
    path = directory/'pod-uid.json'
    try:
        persist(path, value)
    except FileExistsError:
        require(json.loads(private_file(path)) == value)


def database_url(path, admin=False):
    url = make_url(private_file(path).decode().strip())
    require(url.drivername == 'postgresql+psycopg' and url.host == '127.0.0.1' and
            url.port == 25410 and url.database == DATABASE and not url.query and
            url.username == ('postgres' if admin else 'anxious_api') and
            bool(url.password) and len(url.password) >= 16)
    return url


def engine_for(path, admin=False):
    return create_engine(database_url(path, admin), hide_parameters=True,
                         connect_args={'connect_timeout': 3, 'options': '-c statement_timeout=15000 -c lock_timeout=3000'})


def check_db(db):
    require(db.execute(text('SELECT current_database()')).scalar_one() == DATABASE)
    require(db.execute(text('SELECT binding FROM public.issue10_identity WHERE singleton')).scalar_one() == binding())
    validate(db)
    require(db.execute(text('SELECT count(*) FROM auth.accounts')).scalar_one() == 1)
    require(db.execute(text('SELECT count(*) FROM runner_runtime.registry')).scalar_one() == 1)
    current = Registry(db).read(UUID(OWNER))
    require(current is not None and str(current.state_id) == STATE and current.generation == 1)
    return current


def bootstrap(admin_file, password_file, directory):
    """Virgin disposable DB only; a partial bootstrap is retained, never repaired."""
    engine = engine_for(admin_file, True)
    password = private_file(password_file).decode().strip()
    require(len(password) >= 16)
    persist(directory/'database-bootstrap.json', binding())
    try:
        with engine.begin() as db:
            require(db.execute(text('SELECT current_database()')).scalar_one() == DATABASE)
            require(db.execute(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S','f')")).scalar_one() == 0)
            require(not db.execute(text("SELECT 1 FROM pg_roles WHERE rolname='anxious_api'")).first())
        # Only private file paths are placed in env, never credential values.
        os.environ['ADMIN_DATABASE_URL_FILE'] = str(admin_file)
        os.environ['API_DATABASE_PASSWORD_FILE'] = str(password_file)
        from app.migrate import migrate as auth_migrate
        from app.runner_migrate import migrate as runtime_migrate
        auth_migrate(); runtime_migrate()
        with engine.begin() as db:
            db.execute(text('CREATE TABLE public.issue10_identity(singleton boolean PRIMARY KEY CHECK(singleton), binding jsonb NOT NULL)'))
            db.execute(text('REVOKE ALL ON public.issue10_identity FROM PUBLIC'))
            db.execute(text('GRANT SELECT ON public.issue10_identity TO anxious_api'))
            db.execute(text('INSERT INTO public.issue10_identity VALUES(true,CAST(:binding AS jsonb))'), {'binding': encoded(binding()).decode()})
            db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:owner,'1000000000000000010','issue10-synthetic','editor',true)"), {'owner': UUID(OWNER)})
            now = datetime.now(timezone.utc)
            db.execute(text("INSERT INTO runner_runtime.registry(owner_id,state_id,phase,generation,revision,active_requests,ambiguous,last_activity) VALUES(:owner,:state,'not_prepared',0,0,0,false,:now)"), {'owner': UUID(OWNER), 'state': UUID(STATE), 'now': now})
            Registry(db).enter(UUID(OWNER), UUID(STATE), now)
            check_db(db)
    finally:
        engine.dispose()


def request(client, method, path, *, body=None, expected=200, deadline=10):
    require(0 < deadline <= 10)
    end = time.monotonic()+deadline
    token = budget.begin(end, time.monotonic)
    try:
        with client.stream(method, path, json=body, timeout=min(2, deadline)) as response:
            require(response.status_code == expected and not response.headers.get('warning') and
                    response.headers.get('content-encoding', 'identity') == 'identity')
            raw = bytearray()
            for chunk in response.iter_raw():
                raw.extend(chunk)
                require(len(raw) <= 2*1024*1024 and time.monotonic() < end)
            require(time.monotonic() < end)
            value = json.loads(raw)
            require(type(value) is dict)
            return value
    finally:
        budget.finish(token)


def exact_pod(obj, *, persisted=False):
    require(obj.get('apiVersion') == 'v1' and obj.get('kind') == 'Pod')
    meta = obj['metadata']
    expected = PLAN.manifest(NAMESPACE)['metadata']
    require(set(meta) <= set(expected) | {'uid', 'resourceVersion', 'creationTimestamp', 'managedFields', 'generation'})
    require(all(meta.get(key) == value for key, value in expected.items()))
    uid = str(UUID(meta['uid']))
    require(uid == meta['uid'])
    if persisted: require(bool(meta.get('resourceVersion')))
    spec = deepcopy(obj['spec'])
    if persisted and 'nodeName' in spec:
        require(spec.pop('nodeName') == 'anxious-lookout')
    for key in ('hostNetwork', 'hostPID', 'hostIPC'):
        spec.setdefault(key, False)
    require(spec == defaults(PLAN.manifest(NAMESPACE))['spec'])
    return uid


class Port(KubePods):
    def __init__(self, bearer, ca, transport_token, evidence, permit, *, fresh, _transport=None):
        super().__init__(API, NAMESPACE, bearer, ca, [PLAN], _transport=_transport)
        self.transport_token, self.evidence, self.fresh = transport_token, evidence, fresh
        self.permit = permit

    def preflight(self):
        references = []
        ns = request(self.client, 'GET', '/api/v1/namespaces/'+NAMESPACE)
        require(ns.get('kind') == 'Namespace' and ns['metadata']['uid'] == NAMESPACE_UID and
                ns['metadata']['name'] == NAMESPACE and not ns['metadata'].get('deletionTimestamp'))
        labels = ns['metadata']['labels']
        require(labels.get('kubernetes.io/metadata.name') == NAMESPACE and 'anxious-lookout.io/tenant' not in labels and
                labels.get('pod-security.kubernetes.io/enforce') == 'restricted' and
                labels.get('pod-security.kubernetes.io/enforce-version') == 'v1.36')
        self._references(PLAN)
        for ref, pv_uid, leaf in zip((PLAN.native, PLAN.control), PV_UIDS, ('native', 'control')):
            pv = request(self.client, 'GET', '/api/v1/persistentvolumes/'+ref.volume)
            require(pv.get('kind') == 'PersistentVolume' and pv['metadata']['uid'] == pv_uid and
                    pv['metadata']['name'] == ref.volume and not pv['metadata'].get('deletionTimestamp'))
            spec = pv['spec']; claim_ref = spec['claimRef']
            require(claim_ref['uid'] == str(ref.uid) and claim_ref['name'] == ref.name and claim_ref['namespace'] == NAMESPACE)
            require(spec.get('persistentVolumeReclaimPolicy') == 'Retain' and spec.get('accessModes') == ['ReadWriteOnce'] and
                    spec.get('storageClassName', '') == '' and spec.get('local') == {'path': '/var/lib/anxious-lookout/runner-rehearsal/'+STATE+'/'+leaf} and
                    spec['nodeAffinity']['required']['nodeSelectorTerms'] == [{'matchExpressions': [{'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': ['anxious-lookout']}]}])
            references.append(dict(pv=pv, pvcUid=str(ref.uid), pvcName=ref.name))
        secret = self._request('GET', 'secrets', PLAN.transport.name)
        require(base64.b64decode(secret['data']['token'], validate=True).decode() == self.transport_token)
        inventory = self._request('GET', 'pods')
        require(inventory.get('apiVersion') == 'v1' and inventory.get('kind') == 'PodList'
                and not inventory.get('metadata', {}).get('continue'))
        require(type(inventory.get('items')) is list and len(inventory['items']) <= 1)
        if inventory['items']:
            # Kubernetes list items omit TypeMeta. Only infer missing fields
            # inside this verified v1 PodList; explicit conflicting values fail.
            item = deepcopy(inventory['items'][0])
            item.setdefault('apiVersion', 'v1')
            item.setdefault('kind', 'Pod')
            exact_pod(item, persisted=True)
        self.observation_count = getattr(self, 'observation_count', 0)+1
        persist(self.evidence/f'preflight-{self.observation_count:02}.json', dict(
            time=datetime.now(timezone.utc).isoformat(), namespace=ns, references=references,
            secretUid=secret['metadata']['uid'], transportTokenMatched=True,
            podUids=[item['metadata']['uid'] for item in inventory['items']]))
        return inventory['items']

    def lookup(self, identity):
        require(identity == PLAN.identity)
        obj = self._request('GET', 'pods', identity.name, missing=True)
        if obj is None: return None
        exact_pod(obj, persisted=True)
        return self._pod(obj, PLAN)

    def create_once(self, identity):
        require(self.fresh and identity == PLAN.identity)
        self.fresh = False  # Consume before dry-run too; no retry after any failure.
        require(not self.preflight())
        dry = request(self.client, 'POST', '/api/v1/namespaces/'+NAMESPACE+'/pods?dryRun=All&fieldValidation=Strict',
                      body=PLAN.manifest(NAMESPACE), expected=201)
        exact_pod(dry)
        persist(self.evidence/'server-dry-run.json', dry)
        require(not self.preflight())
        obj = request(self.client, 'POST', '/api/v1/namespaces/'+NAMESPACE+'/pods?fieldValidation=Strict',
                      body=PLAN.manifest(NAMESPACE), expected=201)
        exact_pod(obj, persisted=True)
        bind_uid(self.permit, obj['metadata']['uid'])
        persist(self.evidence/'create-response.json', obj)
        return self._pod(obj, PLAN)


def initial_health(client):
    """Retry only connection setup for the initial authenticated GET /health."""
    end = time.monotonic()+10
    for attempt in range(10):
        remaining = end-time.monotonic()
        require(remaining > 0)
        try:
            return request(client, 'GET', '/health', deadline=min(10, remaining)), attempt+1
        except (httpx.ConnectError, httpx.ConnectTimeout):
            if attempt == 9: raise
            remaining = end-time.monotonic()
            require(remaining > 0)
            time.sleep(min(1, remaining))
    raise AssertionError('unreachable')


def health(port, uid):
    # Bounded GET polling only, never retry POST. Every observation is UID/spec checked.
    obj = None
    for attempt in range(12):
        obj = port._request('GET', 'pods', PLAN.identity.name)
        require(exact_pod(obj, persisted=True) == str(uid))
        if obj.get('status', {}).get('phase') == 'Running' and obj['status'].get('podIP'):
            break
        if attempt < 11: time.sleep(1)
    require(obj['status']['phase'] == 'Running')
    require(obj['spec'].get('nodeName') == 'anxious-lookout')
    address = ipaddress.ip_address(obj['status']['podIP'])
    require(address.version == 4 and address in ipaddress.ip_network('10.42.0.0/16'))
    persist(port.evidence/'get-pod.json', obj)
    results = []
    with httpx.Client(base_url=f'http://{address}:8080', trust_env=False, follow_redirects=False,
                      transport=budget.Transport(ssl.create_default_context())) as client:
        for label, bearer, state, expected in (
            ('correct', port.transport_token, STATE, 200), ('wrong-bearer', 'wrong-bearer', STATE, 401),
            ('wrong-state', port.transport_token, '00000000-0000-0000-0000-000000000000', 503),
            ('missing-state', port.transport_token, None, 503)):
            client.headers.clear()
            client.headers.update({'Authorization': 'Bearer '+bearer, 'Accept-Encoding': 'identity'})
            if state is not None: client.headers['X-Runner-State-Id'] = state
            if label == 'correct':
                body, attempts = initial_health(client)
            else:
                body, attempts = request(client, 'GET', '/health', expected=expected), 1
            if expected == 200:
                require((body.get('ownerId'), body.get('stateId'), body.get('podUid')) == (OWNER, STATE, str(uid)))
                require(body.get('available') is False and body.get('verification') == 'unverified' and body.get('reason') == 'not_configured')
            # Never persist arbitrary error bodies or credentials reflected by a server.
            results.append(dict(case=label, status=expected, attempts=attempts,
                                time=datetime.now(timezone.utc).isoformat()))
    return dict(podUid=str(uid), podIP=str(address), ownerId=OWNER, stateId=STATE,
                available=False, verification='unverified', reason='not_configured', cases=results)


def run(directory, evidence, dsn, sa, transport, ca):
    private_dir(directory); private_dir(evidence)
    require(not list(evidence.iterdir()))
    # Validate credentials before consuming; never expose their contents in evidence.
    bearer = private_file(sa).decode().strip()
    transport_token = private_file(transport).decode().strip()
    require(len(transport_token) >= 32 and not any(c.isspace() for c in transport_token))
    engine = engine_for(dsn)
    port = None
    try:
        with engine.begin() as db: current = check_db(db)
        fresh = claim(directory)
        port = Port(bearer, str(ca), transport_token, evidence, directory, fresh=fresh)
        inventory = port.preflight()
        if fresh:
            require(not inventory and current.pod_uid is None)
            result = Controller(engine, port, lambda: datetime.now(timezone.utc), max_pods=1).ensure(Fence.of(current))
            require(result is not None and result.pod_uid is not None)
            uid = result.pod_uid
        else:
            # No reserve/ensure on re-entry, even if a DB backup lost its receipt.
            pod = port.lookup(PLAN.identity)
            require(pod is not None)
            require(current.pod_uid is None or current.pod_uid == pod.uid)
            bind_uid(directory, pod.uid)
            if current.pod_uid is None:
                result = Controller(engine, port, lambda: datetime.now(timezone.utc), max_pods=0).reconcile(Fence.of(current))
                require(result is not None and result.pod_uid == pod.uid)
            uid = pod.uid
        bind_uid(directory, uid)
        persist(evidence/'health.json', health(port, uid))
        persist(evidence/'result.json', dict(binding=binding(), freshPermit=fresh,
                outcome='transport-only-success', networkDenyProbes='unverified'))
    finally:
        if port is not None: port.close()
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('bootstrap-db', 'run'))
    parser.add_argument('--private', type=Path, default=Path('/private'))
    parser.add_argument('--permit', type=Path, default=Path('/permit'))
    parser.add_argument('--evidence', type=Path, default=Path('/evidence'))
    args = parser.parse_args()
    try:
        if args.action == 'bootstrap-db':
            bootstrap(args.private/'admin-dsn', args.private/'api-password', args.permit)
        else:
            run(args.permit, args.evidence, args.private/'database-dsn', args.private/'sa-token',
                args.private/'transport-token', args.private/'ca.crt')
    except Exception:
        parser.exit(1, 'Issue10 operation refused; preserve permit/DB/evidence, never replay create or reset\n')
    print('PASS requested Issue10 operation; no provider or network-deny proof implied')


if __name__ == '__main__':
    main()
