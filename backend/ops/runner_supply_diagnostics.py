"""Read-only supply preflight, never a permit to exec or clear uncertainty."""
from contextlib import contextmanager
import ipaddress
import json
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from app.runner_registry import Registry
from app.runner_termination_control import stop_for
from app.runner_termination_schema import validate
from ops.runner_provision_worker import allowed
from ops.runner_one_shot import private_file
from app.runner_controller import Identity
from runner.lifecycle import Phase
from app.codex_policy import CodexFailure

CODES = frozenset('excluded owner_ineligible registry_missing db_validate_failed state_ineligible stop_pending active_requests pending_admissions running_conversation binding_unavailable binding_mismatch namespace_unavailable pod_unavailable pod_uid_mismatch pod_terminating pod_invalid pod_not_running pod_address_invalid health_unavailable health_identity_mismatch health_refused already_available source_unreadable source_expired source_account_mismatch attempt_unreadable attempt_mismatch attempt_unknown attempt_accepted ready_for_supply input_invalid diagnostic_failed delivery_uncertain'.split())
OBSERVER_CODES = CODES | frozenset('no_stop stop_identity_mismatch stale_stop node_unavailable node_mismatch receipt_unavailable receipt_mismatch ready_for_observation retained_receipt_ready awaiting_pod_deletion'.split())


def diagnose_observer(observer, owner):
    """Read-only stop preflight. Drain and host death proof remain untested."""
    from datetime import datetime, timezone
    from app.runner_termination_control import drain_body
    stage = 'input_invalid'
    try:
        if not isinstance(owner, UUID): return stage
        stage = 'db_validate_failed'
        with ReadOnlyEngine(observer.engine).begin() as db:
            validate(db)
            state = Registry(db).read(owner)
            if state is None: return 'registry_missing'
            stop = stop_for(db,state)
            other = db.execute(text('SELECT 1 FROM runner_termination.stops WHERE owner_id=:owner AND completed_at IS NULL AND (state_id<>:state OR generation<>:generation)'),
                               {'owner':owner,'state':state.state_id,'generation':state.generation}).first()
            if other: return 'stale_stop'
            if stop is not None and stop['pod_uid'] != state.pod_uid: return 'stop_identity_mismatch'
            if state.active_requests: return 'active_requests'
            if Registry(db)._pending(owner): return 'pending_admissions'
            if db.execute(text("SELECT 1 FROM research.conversations WHERE owner_id=:owner AND state='running' LIMIT 1"),{'owner':owner}).first(): return 'running_conversation'
            if stop is None: return 'no_stop'
            if state.phase not in {Phase.READY,Phase.UNREACHABLE,Phase.STOPPING} or state.pod_uid is None: return 'state_ineligible'
            stage = 'binding_unavailable'
            with observer.pool.bound(owner) as runtime:
                if runtime.plan.identity != Identity.of(state): return 'binding_mismatch'
                stage = 'namespace_unavailable'
                runtime.namespace()
                stage = 'node_unavailable'
                node = observer.node(runtime)
                stage = 'node_mismatch'
                observer.host.node_matches(node,datetime.now(timezone.utc))
                if stop['node_uid'] is not None and (stop['node_name'],stop['node_uid'],stop['boot_id']) != (observer.host.node_name,observer.host.node_uid,observer.host.boot_id): return stage
                stage = 'pod_unavailable'
                obj = observer.pod(runtime)
                if stop['node_uid'] is None and (obj is None or obj['spec'].get('nodeName') != observer.host.node_name): return 'node_mismatch'
                expected = {**drain_body(stop),'version':1,'clean':True}
                if stop['receipt'] is not None and stop['receipt'] != expected: return 'receipt_mismatch'
                if obj is None:
                    stage = 'receipt_unavailable'
                    receipt = observer.retained_receipt(db,runtime,state)
                    return 'retained_receipt_ready' if receipt == expected else 'receipt_mismatch'
                if obj['metadata']['uid'] != str(state.pod_uid): return 'pod_uid_mismatch'
                if obj['metadata'].get('deletionTimestamp'):
                    return 'awaiting_pod_deletion' if stop['receipt'] is not None else 'receipt_unavailable'
                stage = 'pod_invalid'
                runtime.pods._pod(obj,runtime.plan)
                stage = 'pod_address_invalid'
                address = ipaddress.ip_address(obj['status']['podIP'])
                if address not in runtime.network or address.is_loopback or address.is_link_local: return stage
                return 'ready_for_observation'
    except Exception:
        return stage


class ReadOnlyEngine:
    def __init__(self, engine): self.engine = engine

    @contextmanager
    def begin(self):
        with self.engine.begin() as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            db.execute(text("SET LOCAL statement_timeout='10s'"))
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            yield db


def diagnose(worker, owner):
    """First failing precondition only; no receipt or provider execution is tested."""
    stage = 'input_invalid'
    try:
        if not isinstance(owner, UUID): return stage
        if not allowed(owner): return 'excluded'
        stage = 'db_validate_failed'
        with ReadOnlyEngine(worker.engine).begin() as db:
            validate(db)
            membership = db.execute(text('SELECT role,is_approved FROM auth.accounts WHERE id=:owner'), {'owner':owner}).one_or_none()
            if not membership or not membership.is_approved or membership.role not in {'editor','admin'}: return 'owner_ineligible'
            state = Registry(db).read(owner)
            if state is None: return 'registry_missing'
            if state.phase not in {Phase.READY,Phase.UNREACHABLE} or state.pod_uid is None: return 'state_ineligible'
            if stop_for(db,state) is not None: return 'stop_pending'
            if state.active_requests: return 'active_requests'
            if Registry(db)._pending(owner): return 'pending_admissions'
            if db.execute(text("SELECT 1 FROM research.conversations WHERE owner_id=:owner AND state='running' LIMIT 1"),{'owner':owner}).first(): return 'running_conversation'
            stage = 'binding_unavailable'
            with worker.pool.bound(owner) as runtime:
                if runtime.plan.identity != Identity.of(state): return 'binding_mismatch'
                stage = 'namespace_unavailable'
                runtime.namespace()
                stage = 'pod_unavailable'
                obj = runtime.pods._request('GET','pods',runtime.plan.identity.name,missing=True)
                if obj is None: return stage
                if obj['metadata']['uid'] != str(state.pod_uid): return 'pod_uid_mismatch'
                if obj['metadata'].get('deletionTimestamp'): return 'pod_terminating'
                stage = 'pod_invalid'
                runtime.pods._pod(obj,runtime.plan)
                if obj['status']['phase'] != 'Running': return 'pod_not_running'
                stage = 'pod_address_invalid'
                address = ipaddress.ip_address(obj['status']['podIP'])
                if address not in runtime.network or address.is_loopback or address.is_link_local: return stage
                host = '['+str(address)+']' if address.version == 6 else str(address)
                stage = 'health_unavailable'
                health = worker.runner('http://'+host+':8080',runtime.token,owner,str(state.state_id)).health()
                if (health.get('ownerId'),health.get('stateId'),health.get('podUid')) != (str(owner),str(state.state_id),str(state.pod_uid)): return 'health_identity_mismatch'
                if health.get('available') is True and health.get('reason') is None: return 'already_available'
                if health.get('available') is not False or health.get('reason') not in {'not_configured','auth_expired','auth_revoked'}: return 'health_refused'
                stage = 'source_unreadable'
                from ops.runner_host_supply import SOURCE, snapshot_id
                try: source = worker.source_reader(SOURCE,expected_uid=worker.source_uid)
                except CodexFailure as exc:
                    return 'source_expired' if exc.reason == 'auth_expired' else stage
                if source[1] != worker.account: return 'source_account_mismatch'
                snapshot = snapshot_id(owner,state,source)
                attempt = worker.attempt_root/(str(snapshot)+'.json')
                stage = 'attempt_unreadable'
                # lstat distinguishes a missing permit from a dangling symlink.
                try: attempt.lstat()
                except FileNotFoundError: return 'ready_for_supply'
                value = json.loads(private_file(attempt))
                expected = {'owner':str(owner),'state':str(state.state_id),'generation':state.generation,
                            'podUid':str(state.pod_uid),'snapshotId':str(snapshot)}
                if type(value) is not dict or set(value) != {*expected,'result'} or any(value[k] != v for k,v in expected.items()) or type(value['generation']) is not int: return 'attempt_mismatch'
                if value['result'] not in {'unknown','accepted'}: return 'attempt_mismatch'
                return 'attempt_'+value['result']
    except Exception:
        return stage  # Never interpolate exception text, SQL, URLs or credentials.


def emit_failure(directory, owner, outcome, reason, *, now):
    """Best-effort 15min JSON log dedup; never skip work or hide exit status."""
    from runner.auth import write_private
    path = Path(directory)/('supply-log-'+str(owner)+'.json')
    if reason not in CODES: reason = 'diagnostic_failed'
    try:
        try:
            old = json.loads(private_file(path))
        except FileNotFoundError: old = {}
        if old.get('outcome') == outcome and old.get('reason') == reason and type(old.get('at')) in (int,float) and 0 <= now-old['at'] < 900:
            return False
        write_private(path, {'outcome':outcome,'reason':reason,'at':now})
    except Exception:
        pass  # Broken log metadata must fail open for logging, never for supply.
    return True


def clear_failure(directory, owner):
    # Only observability metadata; never touch the supply attempt permits.
    try:
        path = Path(directory)/('supply-log-'+str(owner)+'.json')
        private_file(path)
        path.unlink()
    except Exception:
        pass
