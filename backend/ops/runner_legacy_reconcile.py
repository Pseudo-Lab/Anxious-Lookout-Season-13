"""Explicit zero-admission legacy recovery, never generic ambiguity clearing.

The legacy operator never completed native login. This tool accepts only empty
broker history and a validated service ledger with zero lifetime admissions,
plus no DB admissions/running conversations, pinned retained bindings, historical
same-boot node-local journal evidence and fresh host termination proof. Nonempty
history/unknown evidence needs a separately reviewed recovery procedure.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from uuid import UUID, uuid4
from sqlalchemy import text

from app.runner_termination_control import no_pending_work
from app.runner_termination_schema import validate
from app.runner_kube import LABEL
from runner.lifecycle import Fence, Phase, change, require
from runner.service_store import LEDGER, SCHEMA
from runner.service_access import ServiceAccessAuth
from ops.runner_binding_import import retained_file
from ops.runner_one_shot import request


def digest(value):
    return hashlib.sha256(value).hexdigest()


def journal_evidence(raw, *, pod_uid, boot_id, hostname):
    require(len(raw) <= 16_000_000)
    matches = 0
    for line in raw.splitlines():
        value = json.loads(line)
        if str(pod_uid) not in str(value.get('MESSAGE','')):
            continue
        require(value.get('_SYSTEMD_UNIT') == 'k3s.service' and value.get('_BOOT_ID') == boot_id.hex
                and value.get('_HOSTNAME') == hostname)
        matches += 1
    require(matches > 0)
    return digest(raw)


def empty_native_audit(native, control, owner, state_id, expected_account, *, read=retained_file):
    paths = [native/'account-id',native/'state-binding.json',native/'broker.json',control/'operator-account',control/LEDGER]
    require(not any(control.joinpath(LEDGER+suffix).exists() for suffix in ('-journal','-wal','-shm')))
    raw = [read(path) for path in paths]
    marker = json.loads(raw[1])
    require(type(marker.get('version')) is int)
    require(not (native/'codex/auth.json').exists() and not (native/'codex/auth.json').is_symlink())
    require(raw[0].decode().strip() == str(owner)
            and marker == {'version':1,'ownerId':str(owner),'stateId':str(state_id)}
            and json.loads(raw[2]) == {} and raw[3].decode().strip() == expected_account)
    # Deserialize into memory, so audit never recovers/rewrites the retained DB.
    with sqlite3.connect(':memory:') as db:
        db.deserialize(raw[4])
        db.execute('PRAGMA trusted_schema=OFF')
        require(db.execute('PRAGMA user_version').fetchone() == (3,))
        require({row[0] for row in db.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL')} == set(SCHEMA))
        auth = ServiceAccessAuth.__new__(ServiceAccessAuth)
        auth.owner, auth.state_id, auth.expected_account = str(owner),str(state_id),expected_account
        auth.audit(db)
        require(auth._state(db)['requests'] == 0)
        require(db.execute('SELECT count(*) FROM requests').fetchone() == (0,))
    require([read(path) for path in paths] == raw)
    return {str(path):digest(value) for path,value in zip(paths,raw)}


def lock_retained(stack, path):
    require(path == path.resolve() and not path.is_symlink())
    descriptor = os.open(path,os.O_RDONLY | os.O_NOFOLLOW)
    source = stack.enter_context(os.fdopen(descriptor,'rb'))
    info = os.fstat(source.fileno())
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 10001 and info.st_nlink == 1 and not info.st_mode & 0o077)
    fcntl.flock(source,fcntl.LOCK_EX | fcntl.LOCK_NB)


class LegacyRecovery:
    def __init__(self, observer):
        self.observer = observer

    def roots(self, db, runtime, state):
        result = {}
        for kind, ref in [('native',runtime.plan.native),('control',runtime.plan.control)]:
            pv = request(runtime.pods.client,'GET','/api/v1/persistentvolumes/'+ref.volume)
            uid = db.execute(text("SELECT uid FROM runner_bindings.resources WHERE owner_id=:owner AND kind='pv' AND name=:name"),
                             {'owner':state.owner,'name':ref.volume}).scalar_one()
            meta,spec = pv['metadata'],pv['spec']
            root = self.observer.storage_root/state.state_id.hex/kind
            require(pv['kind'] == 'PersistentVolume' and meta['name'] == ref.volume and meta['uid'] == str(uid)
                    and not meta.get('deletionTimestamp') and not meta.get('ownerReferences')
                    and meta['labels'].get(LABEL+'owner') == str(state.owner)
                    and meta['labels'].get(LABEL+'state') == str(state.state_id)
                    and spec['local'] == {'path':str(root)} and spec['persistentVolumeReclaimPolicy'] == 'Retain'
                    and spec['accessModes'] == ['ReadWriteOnce']
                    and spec['claimRef']['uid'] == str(ref.uid) and spec['claimRef']['name'] == ref.name
                    and spec['claimRef']['namespace'] == runtime.pods.namespace)
            result[kind] = root
        return result

    def run(self, fence, journal, hostname, account, *, approved_report=None):
        observer = self.observer
        with observer.pool.bound(fence.owner) as runtime, observer.engine.begin() as db, ExitStack() as locks:
            runtime.namespace()
            state = observer.current(db,runtime,fence.owner)
            prior = db.execute(text('''SELECT * FROM runner_termination.stops WHERE owner_id=:owner
                AND generation=:generation'''),{'owner':fence.owner,'generation':fence.generation}).mappings().one_or_none()
            if prior is not None:
                require(prior['state_id'] == fence.state_id and prior['pod_uid'] == fence.pod_uid
                        and prior['requested_revision'] == fence.revision and prior['completed_at'] is not None
                        and prior['proof'].get('mode') == 'legacy-zero-admission')
                return {'alreadyReconciled':True,'ownerId':str(fence.owner)}
            require(Fence.of(state) == fence and state.phase == Phase.FAILED and state.pod_uid is not None
                    and state.active_requests == 0)
            no_pending_work(db,state)
            require(db.execute(text('SELECT count(*) FROM runner_runtime.admissions WHERE owner_id=:owner'),
                               {'owner':state.owner}).scalar_one() == 0)
            proof = observer.host.verify(state.pod_uid,get_node=lambda:observer.node(runtime),get_pod=lambda:observer.pod(runtime))
            raw = private_evidence(journal)
            journal_hash = journal_evidence(raw,pod_uid=state.pod_uid,boot_id=observer.host.boot_id,hostname=hostname)
            roots = self.roots(db,runtime,state)
            for path in (roots['native']/'runner.lock',roots['control']/'lease.lock',roots['control']/'service-access.lock'):
                lock_retained(locks,path)
            files = empty_native_audit(roots['native'],roots['control'],state.owner,state.state_id,account,read=observer.read)
            facts = {'version':1,'mode':'legacy-zero-admission','ownerId':str(state.owner),
                     'stateId':str(state.state_id),'podUid':str(state.pod_uid),'generation':state.generation,
                     'revision':state.revision,'ambiguous':state.ambiguous,'activeRequests':state.active_requests,
                     'nodeName':observer.host.node_name,'nodeUid':str(observer.host.node_uid),
                     'bootId':str(observer.host.boot_id),'journalSha256':journal_hash,'retainedFiles':files}
            if approved_report is None:
                return facts
            require(approved_report == facts)
            # Recheck both immutable retained evidence and live termination before commit.
            require(private_evidence(journal) == raw)
            require(empty_native_audit(roots['native'],roots['control'],state.owner,state.state_id,account,read=observer.read) == files)
            proof = observer.host.verify(state.pod_uid,get_node=lambda:observer.node(runtime),get_pod=lambda:observer.pod(runtime))
            no_pending_work(db,state)
            record = {**proof,'mode':'legacy-zero-admission','audit':facts}
            db.execute(text('''INSERT INTO runner_termination.stops
                (owner_id,state_id,generation,pod_uid,stop_id,requested_revision,node_name,node_uid,boot_id,proof,completed_at)
                VALUES(:owner,:state,:generation,:pod,:stop,:revision,:node,:uid,:boot,CAST(:proof AS jsonb),now())'''),
                {'owner':state.owner,'state':state.state_id,'generation':state.generation,'pod':state.pod_uid,
                 'stop':uuid4(),'revision':state.revision,'node':observer.host.node_name,'uid':observer.host.node_uid,
                 'boot':observer.host.boot_id,'proof':json.dumps(record)})
            from app.runner_registry import Registry
            Registry(db)._save(state,change(state,phase=Phase.STOPPED,pod_uid=None,active_requests=0,ambiguous=False))
            return {**facts,'reconciled':True}


def private_evidence(path):
    path = Path(path)
    require(path.is_absolute() and path == path.resolve() and not path.is_symlink())
    with os.fdopen(os.open(path,os.O_RDONLY | os.O_NOFOLLOW),'rb') as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o077 and info.st_size <= 16_000_000)
        raw = source.read(16_000_001)
        require(len(raw) <= 16_000_000)
        after = os.fstat(source.fileno())
        require((info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns) ==
                (after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns))
    return raw


def main():
    import argparse
    from sqlalchemy import create_engine
    from app.settings import secret
    from app.runner_pool import Pool
    from runner.auth import read_private, write_private
    from ops.runner_termination import HostTermination
    from ops.runner_stop_observer import Observer
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('report','apply'))
    parser.add_argument('--inputs',required=True)
    parser.add_argument('--report',required=True)
    args = parser.parse_args()
    engine = None
    try:
        require(os.geteuid() == 0)
        config = read_private(Path(args.inputs))
        require(set(config) == {'pool','host','storageRoot','fence','journal','hostname','expectedAccountId'})
        target = config['fence']
        require(set(target) == {'owner','state','generation','revision','podUid'})
        fence = Fence(UUID(target['owner']),UUID(target['state']),target['generation'],target['revision'],UUID(target['podUid']))
        engine = create_engine(secret('OBSERVER_DATABASE_URL'),hide_parameters=True)
        observer = Observer(engine,Pool(engine,config['pool']),HostTermination(**config['host']),config['storageRoot'])
        report_path = Path(args.report)
        approved = read_private(report_path) if args.action == 'apply' else None
        if args.action == 'report':
            require(not report_path.exists() and not report_path.is_symlink())
        result = LegacyRecovery(observer).run(fence,config['journal'],config['hostname'],config['expectedAccountId'],approved_report=approved)
        if args.action == 'report': write_private(report_path,result)
        print(json.dumps({'owner':str(fence.owner),'stage':'legacy-'+args.action,
                          'result':'already_reconciled' if result.get('alreadyReconciled') else 'verified'}))
    except Exception:
        parser.exit(1,'Legacy reconciliation refused; preserve DB, journal, broker and ledger; no automatic reset\n')
    finally:
        if engine is not None: engine.dispose()


if __name__ == '__main__':
    main()
