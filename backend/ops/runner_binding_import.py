"""Read-only resource/storage audit, then immutable publication; no re-preparation."""
import argparse
import json
import os
from pathlib import Path
import stat
from uuid import UUID

from sqlalchemy import create_engine
from app.runner_bindings import Bindings, decode, validate
from app.runner_kube import KubePods, LABEL
from app.runner_registry import Registry
from app.settings import secret
from ops.runner_one_shot import request
from runner.auth import read_private
from runner.lifecycle import require


def retained_file(path):
    path = Path(path)
    require(path.is_absolute() and path == path.resolve() and not path.is_symlink())
    parent = path.parent.stat()
    require(parent.st_uid == 10001 and stat.S_ISDIR(parent.st_mode) and not parent.st_mode & 0o077)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 10001 and not info.st_mode & 0o077
                and info.st_nlink == 1 and info.st_size <= 1_000_000)
        raw = source.read(1_000_001)
        require(len(raw) <= 1_000_000)
        after = os.fstat(source.fileno())
        require((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns))
    require(path.stat().st_ino == info.st_ino)
    return raw


def import_binding(engine, pods, config, storage_root, expected_account, *, read=retained_file, bounded=False):
    require(config['version'] == 1 and config['namespace'] == pods.namespace)
    value = dict(config['plan'], generation=1)
    plan = decode(value)
    nsuid = UUID(config['namespaceUid'])
    ns = request(pods.client, 'GET', '/api/v1/namespaces/'+pods.namespace)
    require(ns['metadata']['uid'] == str(nsuid) and not ns['metadata'].get('deletionTimestamp'))
    require(ns['metadata']['labels'].get('pod-security.kubernetes.io/enforce') == 'restricted')
    root = Path(storage_root)
    require(root.is_absolute() and root == root.resolve()
            and str(root).startswith('/var/lib/anxious-lookout/'))
    base = root/plan.identity.state_id.hex
    locations = {kind: base/kind/('data' if bounded else '') for kind in ('native', 'control')}
    if bounded:
        from ops.runner_storage import verify
        verify(base)
    paths = [locations['native']/'account-id', locations['native']/'state-binding.json', locations['control']/'operator-account']
    before = [read(path) for path in paths]
    require(before[0].decode().strip() == str(plan.identity.owner))
    require(json.loads(before[1]) == {'version': 1, 'ownerId': str(plan.identity.owner), 'stateId': str(plan.identity.state_id)})
    require(before[2].decode().strip() == expected_account)
    token = pods._references(plan)
    require(read(Path(config['transportTokenFile'])).decode().strip() == token)
    pv_uids = {}
    for kind, ref in [('native', plan.native), ('control', plan.control)]:
        pv = request(pods.client, 'GET', '/api/v1/persistentvolumes/'+ref.volume)
        meta, spec = pv['metadata'], pv['spec']
        require(pv.get('kind') == 'PersistentVolume' and meta['name'] == ref.volume
                and not meta.get('deletionTimestamp') and not meta.get('ownerReferences'))
        require(meta['labels'].get(LABEL+'owner') == str(plan.identity.owner)
                and meta['labels'].get(LABEL+'state') == str(plan.identity.state_id))
        require(spec.get('local') == {'path': str(locations[kind])} and spec['persistentVolumeReclaimPolicy'] == 'Retain'
                and spec['accessModes'] == ['ReadWriteOnce'] and spec.get('storageClassName', '') == ''
                and spec.get('volumeMode', 'Filesystem') == 'Filesystem'
                and spec['claimRef']['namespace'] == pods.namespace and spec['claimRef']['name'] == ref.name
                and spec['claimRef']['uid'] == str(ref.uid))
        pv_uids[kind] = UUID(meta['uid'])
    with engine.begin() as db:
        validate(db)
        Registry(db)._eligible(plan.identity.owner)
        Registry(db)._lock(plan.identity.owner)
        require(before == [read(path) for path in paths])
        return Bindings(db).publish(value, pods.namespace, nsuid, pv_uids)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True)
    args = parser.parse_args()
    engine = pods = None
    try:
        inputs = read_private(Path(args.inputs))
        config = inputs['runtime']
        bearer = read_private(Path(inputs['kubeTokenFile']), json_format=False).strip()
        pods = KubePods(config['api'], config['namespace'], bearer, inputs['caFile'], [])
        engine = create_engine(secret('CONTROLLER_DATABASE_URL'), hide_parameters=True)
        changed = import_binding(engine, pods, config, inputs['storageRoot'], inputs['expectedAccountId'])
        print(json.dumps({'bindingPublished': changed, 'resourcesModified': False}))
    except Exception:
        raise SystemExit('Binding import refused; retained resources unchanged') from None
    finally:
        if pods is not None: pods.close()
        if engine is not None: engine.dispose()


if __name__ == '__main__':
    main()
