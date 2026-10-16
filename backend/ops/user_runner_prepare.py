"""Prepare one real owner in an existing isolated namespace, inside Docker as root.

No Pod creation, provider credential reads, policy changes or deletion. Produces a
private runtime binding consumed by the web process; every resource is new. Keep
partial output on failure for operator reconciliation, never overwrite/adopt it.
"""
import argparse
import base64
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets
from uuid import UUID, uuid4

from sqlalchemy import create_engine

from app.runner_controller import Identity
from app.runner_kube import KubePods, LABEL, name
from app.runner_registry import Registry
from app.runner_schema import validate
from app.settings import secret
from ops.runner_one_shot import request
from runner.auth import read_private
from runner.binding import initialize_binding
from runner.lifecycle import require, Phase


def write(path, value, *, owner=10001):
    raw = value if isinstance(value, str) else json.dumps(value, indent=2)+'\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as target:
        target.write(raw); target.flush(); os.fsync(target.fileno())
    os.chown(path, owner, owner)


def resource_names(state):
    suffix = state.hex
    return {'native': 'native-'+suffix, 'control': 'control-'+suffix, 'transport': 'transport-'+suffix}


def volume(name_, namespace, path, node, labels, capacity='1Gi'):
    return {'apiVersion': 'v1', 'kind': 'PersistentVolume', 'metadata': {'name': name_, 'labels': labels},
            'spec': {'capacity': {'storage': capacity}, 'accessModes': ['ReadWriteOnce'],
                     'persistentVolumeReclaimPolicy': 'Retain', 'storageClassName': '',
                     'volumeMode': 'Filesystem', 'local': {'path': str(path)},
                     'claimRef': {'namespace': namespace, 'name': name_},
                     'nodeAffinity': {'required': {'nodeSelectorTerms': [{'matchExpressions': [
                         {'key': 'kubernetes.io/hostname', 'operator': 'In', 'values': [node]}]}]}}}}


def prepare(value, output, *, from_intent=False, storage_allocator=None):
    require(os.geteuid() == 0 and output.is_absolute() and output == output.resolve())
    from ops.runner_storage import sizes, BYTES
    capacities = {kind: str(size//BYTES)+'Gi' for kind, size in sizes().items()} if from_intent else dict(native='1Gi', control='1Gi')
    owner = UUID(value['owner'])
    require(str(owner) == value['owner'])
    namespace = name(value['namespace']); namespace_uid = UUID(value['namespaceUid'])
    root = Path(value['hostRoot'])
    require(root.is_absolute() and root == root.resolve() and root.is_dir()
            and str(root).startswith('/var/lib/anxious-lookout/'))
    require(ipaddress.ip_network(value['podCIDR']).version == 4)
    name(value['node'])
    account = value['providerAccountId']
    require(isinstance(account, str) and 1 <= len(account) <= 200 and not any(c.isspace() for c in account))
    # Validate the immutable image/callback before any mutation.
    from app.runner_kube import Plan, Reference, ExecutorPlan
    executor = None
    if 'executor' in value:
        config = value['executor']
        require(type(config) is dict and set(config) == {'image', 'wallSeconds', 'outputBytes'})
        executor = ExecutorPlan(config['image'], config['wallSeconds'], config['outputBytes'])
    probe = Reference('validation', uuid4(), 'validation')
    Plan(Identity(owner, uuid4(), 1), value['image'], probe,
         Reference('other', uuid4(), 'other'), Reference('token', uuid4()), value['callback'], 'runner-workload', executor)
    bearer = read_private(Path(value['kubeTokenFile']), json_format=False).strip()
    pods = KubePods(value['api'], namespace, bearer, value['caFile'], [])
    engine = create_engine(secret('DATABASE_URL'), hide_parameters=True,
                           connect_args={'connect_timeout': 3, 'options': '-c statement_timeout=15000'})
    guard = None
    try:
        ns = request(pods.client, 'GET', '/api/v1/namespaces/'+namespace)
        require(ns['metadata']['uid'] == str(namespace_uid) and not ns['metadata'].get('deletionTimestamp'))
        require(ns['metadata']['labels'].get('pod-security.kubernetes.io/enforce') == 'restricted')
        policy = request(pods.client, 'GET', '/apis/networking.k8s.io/v1/namespaces/'+namespace+'/networkpolicies/runner-default-deny')
        require(policy['spec'].get('podSelector') == {} and not policy['spec'].get('ingress')
                and not policy['spec'].get('egress') and set(policy['spec']['policyTypes']) == {'Ingress', 'Egress'})
        sa = pods._request('GET', 'serviceaccounts', 'runner-workload')
        require(sa.get('automountServiceAccountToken') is False)
        # New output directory is also the local preparation permit. A failed run
        # cannot silently repeat CREATEs or reset an ambiguous generation.
        output.mkdir(mode=0o700)
        if not from_intent:
            from app.runner_migrate import migrate
            migrate()
        with engine.begin() as db:
            validate(db)
            registry = Registry(db)
            registry._eligible(owner)
            registry._lock(owner)
            if from_intent:
                state = registry._current(owner)
                require(state.pod_uid is None and not registry.creation_reserved(state))
            else:
                require(registry.read(owner) is None)
                state, _ = registry.enter(owner, uuid4(), lambda: datetime.now(timezone.utc))
            require(state.phase == Phase.PREPARING and state.generation == 1)
        if from_intent:
            guard = engine.connect()
            guard.begin()
            guarded = Registry(guard)
            guarded._eligible(owner)
            require(guarded._current(owner) == state)
        write(output/'preparation.json', {'owner': str(owner), 'state': str(state.state_id),
              'namespace': namespace, 'namespaceUid': str(namespace_uid)}, owner=0)
        names = resource_names(state.state_id)
        labels = {LABEL+'owner': str(owner), LABEL+'state': str(state.state_id)}
        storage = root/state.state_id.hex
        if from_intent:
            require(storage_allocator is not None)
            storage_allocator(storage)
            write(output/'storage-restore.json', {'version': 1, 'owner': str(owner),
                  'state': str(state.state_id), 'path': str(storage), 'layout': 'bounded-ext4'}, owner=0)
        else:
            storage.mkdir(mode=0o700)
        refs = {}
        for kind in ('native', 'control'):
            if from_intent:
                from ops.runner_storage import data_path
                path = data_path(storage, kind)
                require(path.is_dir() and path == path.resolve() and not any(path.iterdir()))
            else:
                path = storage/kind; path.mkdir(mode=0o700)
            if kind == 'native':
                initialize_binding(path, str(owner), str(state.state_id))
            else:
                write(path/'operator-account', account, owner=0)
            for file in path.iterdir():
                os.chown(file, 10001, 10001)
            os.chown(path, 10001, 10001)
            manifest = volume(names[kind], namespace, path, value['node'], labels, capacities[kind])
            pv = request(pods.client, 'POST', '/api/v1/persistentvolumes', body=manifest, expected=201)
            pvc = pods._request('POST', 'persistentvolumeclaims', body={
                'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim',
                'metadata': {'name': names[kind], 'namespace': namespace, 'labels': labels},
                'spec': {'accessModes': ['ReadWriteOnce'], 'storageClassName': '', 'volumeMode': 'Filesystem',
                         'volumeName': names[kind], 'resources': {'requests': {'storage': capacities[kind]}}}})
            refs[kind] = {'name': names[kind], 'uid': pvc['metadata']['uid'], 'volume': pv['metadata']['name']}
        transport = secrets.token_urlsafe(48)
        obj = pods._request('POST', 'secrets', body={'apiVersion': 'v1', 'kind': 'Secret',
            'metadata': {'name': names['transport'], 'namespace': namespace, 'labels': labels},
            'immutable': True, 'type': 'Opaque', 'data': {'token': base64.b64encode(transport.encode()).decode()}})
        write(output/'transport-token', transport)
        # Paths refer to the SAME directory mounted into the API container.
        write(output/'runtime.json', {'version': 1, 'api': value['api'], 'namespace': namespace,
            'namespaceUid': str(namespace_uid), 'podCIDR': value['podCIDR'],
            'caFile': '/run/runner-kube/ca.crt',
            'transportTokenFile': '/run/user-runner/private/transport-token',
            'plan': {'owner': str(owner), 'state': str(state.state_id), 'generation': 1,
                     'image': value['image'], 'native': refs['native'], 'control': refs['control'],
                     'transport': {'name': names['transport'], 'uid': obj['metadata']['uid']},
                     'callback': value['callback'], 'serviceAccount': 'runner-workload',
                     **({'version': 2, 'executor': value['executor']} if executor else {})}})
        write(output/'supply-inputs.json', {'ownerId': str(owner), 'stateId': str(state.state_id),
              'snapshotId': str(uuid4()), 'expectedAccountId': account})
        os.chown(output, 10001, 10001)
    finally:
        if guard is not None:
            guard.rollback()
            guard.close()
        pods.close(); engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        prepare(read_private(Path(args.inputs)), Path(args.output))
    except Exception:
        parser.exit(1, 'Preparation refused; preserve private partial output for reconciliation. No automatic cleanup.\n')
    print('PASS owner preparation; no Pod/provider/policy/delete operation. Private runtime.json ready.')


if __name__ == '__main__':
    main()
