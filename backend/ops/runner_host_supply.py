"""Finite trusted host access-token supply. No refresh, token files, or auto dispatch."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import stat
import subprocess
from uuid import UUID, uuid5, NAMESPACE_URL

from sqlalchemy import text
from app.runner_registry import Registry
from app.runner_termination_control import stop_for, no_pending_work
from app.runner_termination_schema import validate
from app.conversations import Runner
from app.codex_policy import CodexFailure
from ops.access_snapshot import selected_file, service_envelope
from ops.runner_provision_worker import allowed
from runner.lifecycle import Fence, Phase, require
from runner.auth import write_private

SOURCE = '/home/opc/.codex/auth.json'


def snapshot_id(owner, state, source):
    return uuid5(NAMESPACE_URL,str(owner)+'/'+str(state.state_id)+'/'+str(state.generation)+'/'+str(state.pod_uid)+'/'+hashlib.sha256(source[0].encode()).hexdigest())


class Supply:
    def __init__(self, engine, pool, *, account, source_uid, kubeconfig, attempt_root, kubectl='/usr/local/bin/kubectl',
                 source_reader=selected_file, run=subprocess.run, runner=Runner):
        require(os.geteuid() == 0 and type(source_uid) is int and source_uid >= 0)
        require(isinstance(account,str) and 1 <= len(account) <= 200 and not any(c.isspace() for c in account))
        executable = Path(kubectl)
        require(executable.is_absolute() and executable == executable.resolve()
                and executable.stat().st_uid == 0 and stat.S_ISREG(executable.stat().st_mode)
                and not executable.stat().st_mode & 0o022 and os.access(executable,os.X_OK))
        self.engine,self.pool = engine,pool
        self.account,self.source_uid = account,source_uid
        self.kubeconfig,self.kubectl = str(kubeconfig),str(executable)
        self.attempt_root = Path(attempt_root)
        require(self.attempt_root.is_absolute() and self.attempt_root == self.attempt_root.resolve()
                and self.attempt_root.is_dir() and self.attempt_root.stat().st_uid == 0
                and not self.attempt_root.stat().st_mode & 0o077)
        self.source_reader,self.run,self.runner = source_reader,run,runner

    def diagnose(self, owner):
        from ops.runner_supply_diagnostics import diagnose
        return diagnose(self,owner)

    def process(self, owner):
        require(isinstance(owner,UUID) and allowed(owner))
        with self.pool.bound(owner) as runtime, self.engine.begin() as db:
            Registry(db)._eligible(owner)  # Membership before owner; retained through exec.
            validate(db)
            state = runtime.state(db,owner)
            require(state.phase in {Phase.READY,Phase.UNREACHABLE} and state.pod_uid is not None
                    and stop_for(db,state) is None and state.active_requests == 0)
            no_pending_work(db,state)
            runtime.namespace()
            obj = runtime.pods._request('GET','pods',runtime.plan.identity.name)
            require(not obj['metadata'].get('deletionTimestamp')
                    and runtime.pods._pod(obj,runtime.plan).uid == state.pod_uid
                    and obj['status']['phase'] == 'Running')
            address = ipaddress.ip_address(obj['status']['podIP'])
            require(address in runtime.network and not address.is_loopback and not address.is_link_local)
            host = '['+str(address)+']' if address.version == 6 else str(address)
            health = self.runner('http://'+host+':8080',runtime.token,owner,str(state.state_id)).health()
            require(health.get('ownerId') == str(owner) and health.get('stateId') == str(state.state_id)
                    and health.get('podUid') == str(state.pod_uid))
            if health.get('available') is True and health.get('reason') is None:
                return 'already_available'  # No source reads or provider re-login on polling.
            require(health.get('available') is False and health.get('reason') in {'not_configured','auth_expired','auth_revoked'})
            source = self.source_reader(SOURCE,expected_uid=self.source_uid)
            # Stable identity makes retry of the same source idempotent in the service ledger.
            snapshot = snapshot_id(owner,state,source)
            envelope = service_envelope(source,{'ownerId':str(owner),'stateId':str(state.state_id),
                'snapshotId':str(snapshot),'expectedAccountId':self.account})
            # Durable no-replay permit. Unknown/rejected delivery of this exact
            # source/Pod needs operator reconciliation, never timer re-login loops.
            attempt = self.attempt_root/(str(snapshot)+'.json')
            metadata = {'owner':str(owner),'state':str(state.state_id),'generation':state.generation,
                        'podUid':str(state.pod_uid),'snapshotId':str(snapshot),'result':'unknown'}
            with os.fdopen(os.open(attempt,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w') as stream:
                json.dump(metadata,stream); stream.flush(); os.fsync(stream.fileno())
            directory = os.open(self.attempt_root,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
            # Exact UID is checked INSIDE the exec receiver before it reads stdin.
            # A same-name replacement cannot consume this owner's token stream.
            result = self.run([self.kubectl,'--kubeconfig',self.kubeconfig,'--request-timeout=15s',
                '-n',runtime.pods.namespace,'exec','-i',runtime.plan.identity.name,'-c','runner','--',
                'python','-B','-m','runner.access_supply','/control/access.sock',
                '--expected-pod-uid',str(state.pod_uid)],
                input=json.dumps(envelope).encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                timeout=75,check=False)
            require(result.returncode == 0 and result.stdout.strip() == b'{"accepted":true}')
            # The DB lock cannot prevent external Kubernetes deletion/replacement.
            # Revalidate after the receiver responds, before publishing success.
            current = runtime.state(db,owner)
            require(Fence.of(current) == Fence.of(state) and current.phase == state.phase
                    and stop_for(db,current) is None)
            runtime.namespace()
            confirmed = runtime.pods._request('GET','pods',runtime.plan.identity.name)
            require(not confirmed['metadata'].get('deletionTimestamp')
                    and runtime.pods._pod(confirmed,runtime.plan).uid == state.pod_uid
                    and confirmed['status']['phase'] == 'Running')
            write_private(attempt,{**metadata,'result':'accepted'})
            return 'supplied'


def diagnostic_command(args):
    from sqlalchemy import create_engine
    from app.settings import secret
    from app.runner_pool import Pool
    from ops.runner_one_shot import private_file
    from ops.runner_supply_diagnostics import ReadOnlyEngine, diagnose_observer
    from ops.runner_stop_observer import Observer
    from ops.runner_termination import HostTermination
    engine = None
    results = []
    try:
        engine = create_engine(secret('OBSERVER_DATABASE_URL'),hide_parameters=True)
        readonly = ReadOnlyEngine(engine)
        for stage, path in [('host-supply-diagnose',args.inputs),('termination-diagnose',args.observer_inputs)]:
            if path is None: continue
            code = 'input_invalid'
            try:
                config = json.loads(private_file(Path(path)))
                if stage == 'host-supply-diagnose':
                    require(set(config) == {'pool','account','sourceUid','kubeconfig','kubectl','attemptRoot'})
                    worker = Supply(engine,Pool(readonly,config['pool']),account=config['account'],source_uid=config['sourceUid'],
                                    kubeconfig=config['kubeconfig'],kubectl=config['kubectl'],attempt_root=config['attemptRoot'])
                    code = worker.diagnose(args.owner)
                else:
                    require(os.geteuid() == 0 and set(config) == {'pool','host','storageRoot'})
                    observer = Observer(engine,Pool(readonly,config['pool']),HostTermination(**config['host']),config['storageRoot'])
                    code = diagnose_observer(observer,args.owner)
            except Exception:
                pass
            results.append({'owner':str(args.owner),'stage':stage,'result':code})
    except Exception:
        results = [{'owner':str(args.owner),'stage':stage,'result':'input_invalid'}
                   for stage in ['host-supply-diagnose']+(['termination-diagnose'] if args.observer_inputs else [])]
    finally:
        if engine is not None: engine.dispose()
    for result in results: print(json.dumps(result))
    return 0 if all(r['result'] in {'already_available','ready_for_supply','no_stop','ready_for_observation','retained_receipt_ready','awaiting_pod_deletion'} for r in results) else 1


def main():
    import argparse
    from sqlalchemy import create_engine
    from app.settings import secret
    from app.runner_pool import Pool
    from runner.auth import read_private, write_private
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',required=True)
    parser.add_argument('--owner',type=UUID)
    parser.add_argument('--cursor-file')
    parser.add_argument('--limit',type=int,default=10)
    parser.add_argument('--diagnose',action='store_true',help='Read-only single-owner preflight; no exec or state changes')
    parser.add_argument('--observer-inputs',help='Also diagnose stop preconditions using private observer inputs')
    args = parser.parse_args()
    if args.diagnose:
        if args.owner is None or args.cursor_file is not None:
            parser.error('--diagnose requires --owner and forbids --cursor-file')
        return diagnostic_command(args)
    if args.observer_inputs is not None: parser.error('--observer-inputs requires --diagnose')
    engine = None
    try:
        require(1 <= args.limit <= 100 and (args.owner is not None or args.cursor_file is not None))
        config = read_private(Path(args.inputs))
        require(set(config) == {'pool','account','sourceUid','kubeconfig','kubectl','attemptRoot'})
        engine = create_engine(secret('OBSERVER_DATABASE_URL'),hide_parameters=True)
        worker = Supply(engine,Pool(engine,config['pool']),account=config['account'],source_uid=config['sourceUid'],
                        kubeconfig=config['kubeconfig'],kubectl=config['kubectl'],attempt_root=config['attemptRoot'])
        cursor = Path(args.cursor_file) if args.cursor_file else None
        after = UUID(read_private(cursor)['after']) if cursor is not None and cursor.exists() else None
        with engine.begin() as db:
            validate(db)
            query = text('''SELECT owner_id FROM runner_runtime.registry WHERE phase IN ('ready','runner_unreachable')
                AND pod_uid IS NOT NULL AND (CAST(:after AS uuid) IS NULL OR owner_id>CAST(:after AS uuid))
                ORDER BY owner_id LIMIT :limit''')
            owners = [args.owner] if args.owner is not None else db.execute(query,{'after':after,'limit':args.limit}).scalars().all()
            if not owners and after is not None:
                owners = db.execute(query,{'after':None,'limit':args.limit}).scalars().all()
        failed = False
        for owner in owners:
            try: outcome = worker.process(owner) if allowed(owner) else 'excluded'
            except CodexFailure as failure:
                failed = True
                outcome = 'auth_expired' if failure.reason == 'auth_expired' else 'supply_refused'
            except Exception:
                failed = True
                outcome = 'supply_refused'
            if cursor is not None: write_private(cursor,{'after':str(owner)})
            from ops.runner_supply_diagnostics import emit_failure, clear_failure
            import time
            item = {'owner':str(owner),'stage':'host-supply','result':outcome}
            if outcome in {'auth_expired','supply_refused'}:
                # A fresh preflight explains current blockers, not historical exec errors.
                reason = worker.diagnose(owner)
                if reason in {'ready_for_supply','already_available'}: reason = 'delivery_uncertain'
                item['reason'] = reason
                if cursor is not None and not emit_failure(cursor.parent,owner,outcome,reason,now=time.time()):
                    continue
            elif cursor is not None:
                clear_failure(cursor.parent,owner)
            print(json.dumps(item))
        if failed:
            parser.exit(1,'Host supply batch has refused owners; operator attention required\n')
    except Exception:
        parser.exit(1,'Host supply refused; no refresh or automatic dispatch\n')
    finally:
        if engine is not None: engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
