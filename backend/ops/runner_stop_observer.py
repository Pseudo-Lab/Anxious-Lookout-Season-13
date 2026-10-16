"""Finite stop observer. Private controller inputs, no permanent watcher.

API records a stop before native drain; this worker anchors the actual scheduled
Node before any destructive call, then drains, deletes exactly that Pod UID, and
only releases capacity after retained receipt plus trusted host proof.
"""
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from app.runner_termination_control import stop_for, accept_drain, release, drain_body, no_pending_work
from app.runner_termination_schema import validate
from app.conversations import Runner
from ops.runner_one_shot import request
from ops.runner_binding_import import retained_file
from ops.runner_provision_worker import allowed
from runner.lifecycle import require


class Observer:
    def __init__(self, engine, pool, host, storage_root, *, read=retained_file, runner=Runner):
        self.engine, self.pool, self.host = engine, pool, host
        self.storage_root, self.read, self.runner = Path(storage_root), read, runner
        require(self.storage_root.is_absolute() and self.storage_root == self.storage_root.resolve())

    def current(self, db, runtime, owner):
        db.execute(text('SELECT id FROM auth.accounts WHERE id=:owner FOR SHARE'),{'owner':owner}).one()
        validate(db)
        return runtime.state(db,owner)

    def node(self, runtime):
        return request(runtime.pods.client,'GET','/api/v1/nodes/'+self.host.node_name)

    def pod(self, runtime):
        return runtime.pods._request('GET','pods',runtime.plan.identity.name,missing=True)

    def anchor(self, runtime, owner):
        with self.engine.begin() as db:
            state = self.current(db,runtime,owner)
            stop = stop_for(db,state)
            if stop is None: return None
            require(stop['pod_uid'] == state.pod_uid)
            if stop['node_uid'] is None:
                obj = self.pod(runtime)
                require(obj is not None and runtime.pods._pod(obj,runtime.plan).uid == state.pod_uid
                        and obj['spec']['nodeName'] == self.host.node_name)
                self.host.node_matches(self.node(runtime),datetime.now(timezone.utc))
                db.execute(text('''UPDATE runner_termination.stops SET node_name=:name,node_uid=:uid,boot_id=:boot
                    WHERE stop_id=:stop'''),{'name':self.host.node_name,'uid':self.host.node_uid,
                                           'boot':self.host.boot_id,'stop':stop['stop_id']})
            else:
                require((stop['node_name'],stop['node_uid'],stop['boot_id']) ==
                        (self.host.node_name,self.host.node_uid,self.host.boot_id))
            return stop['stop_id']

    def retained_receipt(self, db, runtime, state):
        plan = runtime.plan
        pv = request(runtime.pods.client,'GET','/api/v1/persistentvolumes/'+plan.native.volume)
        uid = db.execute(text("SELECT uid FROM runner_bindings.resources WHERE owner_id=:owner AND kind='pv' AND name=:name"),
                         {'owner':state.owner,'name':plan.native.volume}).scalar_one()
        require(pv['metadata']['uid'] == str(uid) and not pv['metadata'].get('deletionTimestamp')
                and pv['spec']['claimRef']['uid'] == str(plan.native.uid)
                and pv['spec']['claimRef']['name'] == plan.native.name
                and pv['spec']['claimRef']['namespace'] == runtime.pods.namespace)
        root = Path(pv['spec']['local']['path'])
        base = self.storage_root/state.state_id.hex
        require(root in {base/'native',base/'native/data'})
        if root == base/'native/data':
            from ops.runner_storage import verify
            verify(base)
        require(self.read(root/'account-id').decode().strip() == str(state.owner))
        require(json.loads(self.read(root/'state-binding.json')) ==
                {'version':1,'ownerId':str(state.owner),'stateId':str(state.state_id)})
        return json.loads(self.read(root/('termination-'+str(state.pod_uid)+'.json')))

    def process(self, owner):
        with self.pool.bound(owner) as runtime:
            runtime.namespace()
            stop_id = self.anchor(runtime,owner)  # Commit Node attribution BEFORE drain/delete.
            if stop_id is None: return None
            with self.engine.begin() as db:
                state = self.current(db,runtime,owner)
                stop = stop_for(db,state)
                require(stop is not None and stop['stop_id'] == stop_id)
                obj = self.pod(runtime)
                no_pending_work(db,state)
                if obj is not None:
                    require(obj['metadata']['uid'] == str(state.pod_uid))
                    if obj['metadata'].get('deletionTimestamp'):
                        require(stop['receipt'] is not None)
                        return state  # Preserve slot until kubelet/CRI/proc prove death.
                    require(runtime.pods._pod(obj,runtime.plan).uid == state.pod_uid)
                    address = ipaddress.ip_address(obj['status']['podIP'])
                    require(address in runtime.network and not address.is_loopback and not address.is_link_local)
                    host = '['+str(address)+']' if address.version == 6 else str(address)
                    receipt = self.runner('http://'+host+':8080',runtime.token,owner,str(state.state_id)).call(
                        'POST','/lifecycle/drain',drain_body(stop))
                    result = accept_drain(db,owner,stop_id,receipt)
                else:
                    receipt = self.retained_receipt(db,runtime,state)
                    result = accept_drain(db,owner,stop_id,receipt)
                    proof = self.host.verify(state.pod_uid,get_node=lambda:self.node(runtime),get_pod=lambda:self.pod(runtime))
                    return release(db,owner,stop_id,proof,allow_entry=allowed(owner))
            # Drain receipt + dispatch fence committed before ordinary DELETE.
            runtime.pods._request('DELETE','pods',runtime.plan.identity.name,
                body={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':str(result.pod_uid)}})
            return result


def main():
    import argparse
    import os
    from sqlalchemy import create_engine
    from app.settings import secret
    from app.runner_pool import Pool
    from runner.auth import read_private, write_private
    from ops.runner_termination import HostTermination
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',required=True)
    parser.add_argument('--owner',type=UUID)
    parser.add_argument('--cursor-file')
    parser.add_argument('--limit',type=int,default=10)
    args = parser.parse_args()
    engine = None
    try:
        require(os.geteuid() == 0 and 1 <= args.limit <= 100)
        require(args.owner is not None or args.cursor_file is not None)
        config = read_private(Path(args.inputs))
        require(set(config) == {'pool','host','storageRoot'})
        engine = create_engine(secret('OBSERVER_DATABASE_URL'),hide_parameters=True)
        observer = Observer(engine,Pool(engine,config['pool']),HostTermination(**config['host']),config['storageRoot'])
        cursor = Path(args.cursor_file) if args.cursor_file else None
        after = UUID(read_private(cursor)['after']) if cursor is not None and cursor.exists() else None
        with engine.begin() as db:
            validate(db)
            if args.owner is not None:
                owners = [args.owner]
            else:
                statement = text('''SELECT DISTINCT owner_id FROM runner_termination.stops
                    WHERE completed_at IS NULL AND (CAST(:after AS uuid) IS NULL OR owner_id>CAST(:after AS uuid))
                    ORDER BY owner_id LIMIT :limit''')
                owners = db.execute(statement,{'after':after,'limit':args.limit}).scalars().all()
                if not owners and after is not None:
                    owners = db.execute(statement,{'after':None,'limit':args.limit}).scalars().all()
        for owner in owners:
            try:
                observer.process(owner)
                outcome = 'observed'
            except Exception:
                outcome = 'reconciliation_required'
            if cursor is not None:
                write_private(cursor,{'after':str(owner)})
            print(json.dumps({'owner':str(owner),'stage':'termination','result':outcome}))
    except Exception:
        parser.exit(1,'Observer refused; preserve inputs, receipts and retained state\n')
    finally:
        if engine is not None: engine.dispose()


if __name__ == '__main__':
    main()
