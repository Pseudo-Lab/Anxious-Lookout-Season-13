"""#101 rolling replacement decisions with an in-memory store and cluster (no database, no Kubernetes)."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid5

import pytest

from app.runner_image_move import fingerprint, fingerprint_sha, pod_fingerprint, target_from_env
from ops.runner_replacement import (Rollout, Settings, Target, Row, View, Halt, health_class, load_settings,
                                    RESTART_KEYS, SETTLE_SECONDS, PULL_SECONDS)
from runner.lifecycle import Phase, Refused

OLD = 'registry.invalid/runner@sha256:'+'a'*64
NEW = 'registry.invalid/runner@sha256:'+'b'*64
T = fingerprint(NEW, [])
T0 = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.value = T0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def pod(uid, image=OLD, flags=(), phase='Running', created=T0, waiting=None, dead=False):
    env = [{'name': 'APP_ENV', 'value': 'operator-service'}] + [{'name': flag, 'value': 'true'} for flag in flags]
    status = {'phase': phase, 'containerStatuses': [{'name': 'runner', 'state': {'waiting': {'reason': waiting}} if waiting else {'running': {}}}]}
    return {'metadata': {'uid': str(uid), 'creationTimestamp': created.isoformat().replace('+00:00', 'Z')},
            'spec': {'containers': [{'name': 'runner', 'image': image, 'env': env}]}, 'status': status, 'dead': dead}


class Store:
    def __init__(self, clock, views, state='canary'):
        self.clock, self.views = clock, {view.owner: view for view in views}
        self.target = Target(uuid4(), T, state)
        self.rows, self.calls = {}, []
        self.unmovable, self.running_db, self.sessions = set(), set(), set()

    def install(self, value):
        assert value == T
        return self.target

    def current(self):
        return self.target

    def canary_owner(self, target_id):
        current = sorted(owner for owner, row in self.rows.items() if row.canary and row.phase not in {'blocked', 'failed'})
        return current[0] if current else None

    def owners(self, target_id):
        assert target_id == self.target.target_id
        return [replace(view, row=self.rows.get(owner)) for owner, view in sorted(self.views.items())]

    def set_row(self, target_id, owner, phase, *, canary, stop_id=None, reason=None):
        old = self.rows.get(owner)
        since = old.since if old is not None and old.phase == phase else self.clock()
        self.rows[owner] = Row(phase, since, bool(old and old.canary) or canary,
                               stop_id or (old.stop_id if old else None), reason)

    def set_state(self, target, state, reason=None, owner=None):
        self.target = Target(target.target_id, target.fingerprint, state, reason, owner)
        return self.target

    def move(self, owner, image):
        self.calls.append(('move', owner))
        if owner in self.unmovable:
            raise Refused('not movable')
        view = self.views[owner]
        self.views[owner] = replace(view, image=image)
        return view.image != image

    def stop_between_turns(self, owner):
        self.calls.append(('stop', owner))
        if owner in self.running_db:
            return None
        stop = uuid4()
        self.views[owner] = replace(self.views[owner], stop_id=stop)
        return stop

    def stop_for_cut(self, owner):
        self.calls.append(('cut', owner))
        stop = uuid4()
        self.views[owner] = replace(self.views[owner], stop_id=stop)
        return stop, [(uuid4(), uuid4())]

    def restart(self, owner, key):
        self.calls.append(('restart', owner, key))
        return owner in self.sessions


class Cluster:
    def __init__(self):
        self.pods, self.health_of, self.idle_owners, self.interrupts = {}, {}, set(), []

    def pod(self, view):
        return self.pods.get(view.owner)

    def terminal(self, obj):
        return {'podPhase': 'Failed'} if obj.get('dead') else None

    def health(self, view, obj):
        return self.health_of.get(view.owner, {'available': True, 'reason': None})

    def idle(self, view, obj, seconds):
        return view.owner in self.idle_owners

    def interrupt(self, view, obj, session, request):
        self.interrupts.append((view.owner, session, request))


def running(owner, image=OLD):
    return View(owner, uuid4(), Phase.UNREACHABLE, uuid4(), image)


def stopped(owner, image=OLD):
    return View(owner, uuid4(), Phase.STOPPED, None, image)


def setup(views, state='canary', **settings):
    clock = Clock()
    store, cluster = Store(clock, views, state), Cluster()
    for view in views:
        if view.pod_uid is not None:
            cluster.pods[view.owner] = pod(view.pod_uid, view.image)
    return clock, store, cluster, Rollout(store, cluster, Settings(T, **settings), clock)


def outcomes(lines):
    return {owner: outcome for owner, kind, outcome in lines if kind == 'rollout'}


def release(store, cluster, owner, phase=Phase.STOPPED):
    """What Service.step does once the drain receipt, DELETE and 404 proof are through."""
    store.views[owner] = replace(store.views[owner], phase=phase, pod_uid=None, stop_id=None, stop_receipt=False)
    cluster.pods.pop(owner, None)


def test_no_target_means_no_rollout():
    store = Store(Clock(), [], 'canary')
    assert Rollout(store, Cluster(), Settings(None), Clock()).run() == []


def test_canary_stage_replaces_one_running_owner_between_turns_and_holds_the_rest():
    a, b, c = sorted(uuid4() for _ in range(3))
    clock, store, cluster, rollout = setup([running(a), running(b), stopped(c)])
    lines = outcomes(rollout.run())
    assert lines == {a: 'waiting_turn', b: 'held', c: 'held'}  # Busy canary: nothing is stopped yet.
    assert store.rows[a].canary and ('move', c) not in store.calls
    cluster.idle_owners.add(a)
    assert outcomes(rollout.run())[a] == 'draining'
    assert store.calls.count(('stop', a)) == 1 and store.rows[a].stop_id == store.views[a].stop_id


def test_canary_waits_the_whole_window_after_its_first_healthy_check():
    a, b = sorted(uuid4() for _ in range(2))
    clock, store, cluster, rollout = setup([running(a), stopped(b)], canary_seconds=600)
    store.sessions.add(a)
    cluster.idle_owners.add(a)
    rollout.run()
    release(store, cluster, a)
    assert outcomes(rollout.run())[a] == 'restarting'
    assert store.views[a].image == NEW
    assert store.calls[-1] == ('restart', a, uuid5(RESTART_KEYS, '%s:%s:%s' % (store.target.target_id, a, store.rows[a].stop_id)))
    new = uuid4()
    store.views[a] = replace(store.views[a], phase=Phase.UNREACHABLE, pod_uid=new)
    cluster.pods[a] = pod(new, NEW, created=clock())
    assert outcomes(rollout.run())[a] == 'verified'  # Healthy at once, still canary (PM condition 1).
    clock.advance(599)
    assert outcomes(rollout.run()) == {a: 'verified', b: 'held'} and store.target.state == 'canary'
    clock.advance(1)
    # The canary is handled first in a pass; once it passes, the stopped owner moves in the same pass and
    # nothing is left, so the target completes.
    assert outcomes(rollout.run()) == {a: 'canary_passed', b: 'moved'} and store.target.state == 'complete'
    assert store.views[b].image == NEW


def test_deadline_cut_interrupts_the_running_turn_and_reports_an_unsettled_one():
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a)], 'rolling', turn_deadline=1800)
    assert outcomes(rollout.run())[a] == 'waiting_turn'
    clock.advance(1799)
    assert outcomes(rollout.run())[a] == 'waiting_turn' and ('cut', a) not in store.calls
    clock.advance(1)
    assert outcomes(rollout.run())[a] == 'cutting'
    assert len(cluster.interrupts) == 1 and store.rows[a].phase == 'cutting'
    clock.advance(SETTLE_SECONDS)
    assert outcomes(rollout.run())[a] == 'cutting'
    clock.advance(1)
    assert outcomes(rollout.run())[a] == 'blocked' and store.rows[a].reason == 'turn_not_settled'
    assert rollout.slots == 0


def test_cut_turn_that_settles_follows_the_normal_drain():
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a)], 'rolling', turn_deadline=60)
    rollout.run(); clock.advance(60); rollout.run()
    store.views[a] = replace(store.views[a], stop_receipt=True, phase=Phase.STOPPING)
    clock.advance(SETTLE_SECONDS + 10)
    assert outcomes(rollout.run())[a] == 'cutting'  # Receipt accepted; Service.step deletes and releases.
    release(store, cluster, a)
    assert outcomes(rollout.run())[a] == 'moved' and store.views[a].image == NEW  # Logged out: next start.


def test_running_conversation_in_the_database_defers_the_stop():
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a)], 'rolling')
    cluster.idle_owners.add(a)
    store.running_db.add(a)
    assert outcomes(rollout.run())[a] == 'waiting_turn' and store.rows[a].phase == 'waiting_turn'


def test_parallel_limit_counts_in_flight_replacements():
    a, b = sorted(uuid4() for _ in range(2))
    clock, store, cluster, rollout = setup([running(a), running(b)], 'rolling', max_parallel=1)
    cluster.idle_owners |= {a, b}
    assert outcomes(rollout.run()) == {a: 'draining', b: 'waiting_slot'}
    assert ('stop', b) not in store.calls


def test_an_open_stop_is_followed_not_duplicated():
    a = uuid4()
    view = replace(running(a), stop_id=uuid4())
    clock, store, cluster, rollout = setup([view], 'rolling')
    assert outcomes(rollout.run())[a] == 'draining'
    assert store.rows[a].stop_id == view.stop_id and ('stop', a) not in store.calls


def test_flag_only_change_replaces_without_moving():
    a = uuid4()
    flagged = fingerprint(NEW, ['RUNNER_RESEARCH_API_ENABLE'])
    clock = Clock()
    store, cluster = Store(clock, [running(a, NEW)], 'rolling'), Cluster()
    store.target = Target(store.target.target_id, flagged, 'rolling')
    store.install = lambda value: store.target
    cluster.pods[a] = pod(store.views[a].pod_uid, NEW)
    cluster.idle_owners.add(a)
    store.sessions.add(a)
    rollout = Rollout(store, cluster, Settings(flagged), clock)
    assert outcomes(rollout.run())[a] == 'draining'
    release(store, cluster, a)
    assert outcomes(rollout.run())[a] == 'restarting' and ('move', a) not in store.calls


@pytest.mark.parametrize('signal', ['dead', 'pull', 'unavailable'])
def test_target_pod_failure_halts(signal):
    a, b = sorted(uuid4() for _ in range(2))
    clock, store, cluster, rollout = setup([running(a, NEW), running(b)], 'rolling')
    if signal == 'dead':
        cluster.pods[a] = pod(store.views[a].pod_uid, NEW, dead=True)
    elif signal == 'pull':
        cluster.pods[a] = pod(store.views[a].pod_uid, NEW, phase='Pending', waiting='ImagePullBackOff', created=clock())
        assert outcomes(rollout.run())[a] == 'starting' and store.target.state == 'rolling'
        clock.advance(PULL_SECONDS)
    else:
        cluster.pods[a] = pod(store.views[a].pod_uid, NEW, created=clock())
        cluster.health_of[a] = {'available': False, 'reason': 'policy_refused'}
    lines = rollout.run()
    reason = {'dead': 'runner_terminated', 'pull': 'image_pull', 'unavailable': 'runner_unavailable'}[signal]
    assert (a, 'rollout_halted', reason) in lines
    assert store.target.state == 'halted' and store.target.reason == reason and store.target.reason_owner == a
    cluster.idle_owners.add(b)
    assert outcomes(rollout.run())[b] == 'held' and ('stop', b) not in store.calls


@pytest.mark.parametrize('reason', ['auth_revoked', 'auth_expired', 'not_configured'])
def test_account_reasons_are_inconclusive_not_a_halt(reason):
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a, NEW)], 'canary')
    cluster.pods[a] = pod(store.views[a].pod_uid, NEW, created=clock())
    cluster.health_of[a] = {'available': False, 'reason': reason}
    assert outcomes(rollout.run())[a] == 'inconclusive' and store.target.state == 'canary'


def test_halted_rollout_finishes_in_flight_stops_on_the_old_image():
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a)], 'rolling')
    cluster.idle_owners.add(a)
    store.sessions.add(a)
    rollout.run()
    store.set_state(store.target, 'halted', 'operator')
    release(store, cluster, a)
    assert outcomes(rollout.run())[a] == 'failed'
    assert store.rows[a].reason == 'halted' and store.views[a].image == OLD
    assert store.calls[-1][:2] == ('restart', a) and ('move', a) not in store.calls


def test_user_entry_at_release_counts_as_restart():
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a)], 'rolling')
    cluster.idle_owners.add(a)
    rollout.run()
    release(store, cluster, a, phase=Phase.PREPARING)
    assert outcomes(rollout.run())[a] == 'restarting'
    assert not any(call[0] == 'restart' for call in store.calls)


def test_released_owner_running_again_off_target_is_replaced_again():
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a)], 'rolling')
    cluster.idle_owners.add(a)
    rollout.run()
    store.unmovable.add(a)
    release(store, cluster, a)
    assert outcomes(rollout.run())[a] == 'blocked' and store.rows[a].reason == 'not_movable'
    store.unmovable.discard(a)
    again = uuid4()
    store.views[a] = replace(store.views[a], phase=Phase.UNREACHABLE, pod_uid=again)
    cluster.pods[a] = pod(again, OLD)
    assert outcomes(rollout.run())[a] == 'draining'


def test_complete_and_back_to_rolling():
    a, b = sorted(uuid4() for _ in range(2))
    clock, store, cluster, rollout = setup([stopped(a, NEW), running(b, NEW)], 'rolling')
    cluster.pods[b] = pod(store.views[b].pod_uid, NEW, created=clock() - timedelta(hours=1))
    assert outcomes(rollout.run()) == {a: 'converged', b: 'converged'}
    assert store.target.state == 'complete'
    store.views[a] = replace(store.views[a], image=OLD)
    store.unmovable.add(a)
    assert outcomes(rollout.run())[a] == 'not_movable' and store.target.state == 'rolling'


def test_runner_failed_owner_is_blocked_not_moved():
    a = uuid4()
    view = replace(stopped(a), phase=Phase.FAILED)
    clock, store, cluster, rollout = setup([view], 'rolling')
    assert outcomes(rollout.run())[a] == 'blocked' and store.rows[a].reason == 'runner_failed'
    assert ('move', a) not in store.calls


def test_configured_canary_owner_goes_first():
    a, b = sorted(uuid4() for _ in range(2))
    clock, store, cluster, rollout = setup([running(a), running(b)], 'canary', canary_owners=(b,))
    assert outcomes(rollout.run()) == {a: 'held', b: 'waiting_turn'} and store.rows[b].canary


def test_before_create_moves_only_when_not_halted():
    a = uuid4()
    clock, store, cluster, rollout = setup([replace(stopped(a), phase=Phase.PREPARING)], 'rolling')  # canary: see test_before_create_moves_only_the_canary_owner_during_the_canary
    assert rollout.before_create(a) is True and store.views[a].image == NEW
    store.views[a] = replace(store.views[a], image=OLD)
    store.set_state(store.target, 'halted', 'operator')
    assert rollout.before_create(a) is False and store.views[a].image == OLD


def test_three_ensure_failures_on_the_target_image_halt():
    a = uuid4()
    clock, store, cluster, rollout = setup([], 'rolling')
    assert rollout.ensure_result(a, False, True) is None
    assert rollout.ensure_result(a, False, False) is None  # Not the target image: counter resets.
    assert [rollout.ensure_result(a, False, True) for _ in range(3)] == [None, None, 'ensure_failed']
    assert store.target.state == 'halted' and store.target.reason == 'ensure_failed'


def test_fingerprints_and_health_classes(monkeypatch):
    with pytest.raises(Refused):
        fingerprint('registry.invalid/runner:latest', [])
    with pytest.raises(Refused):
        fingerprint(NEW, ['NOT_A_FLAG'])
    assert fingerprint(NEW, ['STREAM_TRACE', 'RUNNER_STREAM_TEXT'])['runnerFlags'] == ['RUNNER_STREAM_TEXT', 'STREAM_TRACE']
    assert fingerprint_sha(fingerprint(NEW, ['STREAM_TRACE'])) != fingerprint_sha(T)
    live = pod(uuid4(), NEW, flags=('RUNNER_RESEARCH_API_ENABLE',))
    live['spec']['containers'][0]['env'].append({'name': 'STREAM_TRACE', 'value': 'false'})
    assert pod_fingerprint(live) == fingerprint(NEW, ['RUNNER_RESEARCH_API_ENABLE'])
    assert health_class({'available': True, 'reason': None}) == 'ok'
    assert health_class({'available': False, 'reason': 'auth_revoked'}) == 'inconclusive'
    assert health_class({'available': False, 'reason': 'auth_revoked', 'failure': 'auth_revoked'}) == 'bad'
    assert health_class({'available': False, 'reason': 'model_unavailable'}) == 'bad'
    monkeypatch.delenv('RUNNER_TARGET_IMAGE', raising=False)
    assert target_from_env() is None
    monkeypatch.setenv('RUNNER_TARGET_IMAGE', NEW)
    monkeypatch.setenv('CODEX_RUNNER_RESEARCH_API_ENABLE', 'true')
    assert target_from_env() == fingerprint(NEW, ['RUNNER_RESEARCH_API_ENABLE'])


@pytest.mark.parametrize('name,value', [('ROLLOUT_MAX_PARALLEL', '4'), ('ROLLOUT_MAX_PARALLEL', '0'),
                                        ('ROLLOUT_TURN_DEADLINE_SECONDS', '59'), ('ROLLOUT_CANARY_SECONDS', 'x'),
                                        ('ROLLOUT_CANARY_OWNERS', 'not-a-uuid')])
def test_invalid_rollout_settings_refuse_startup(monkeypatch, name, value):
    monkeypatch.setenv('RUNNER_TARGET_IMAGE', NEW)
    monkeypatch.setenv(name, value)
    with pytest.raises((Refused, ValueError)):
        load_settings()


def test_default_settings(monkeypatch):
    for name in ('ROLLOUT_MAX_PARALLEL', 'ROLLOUT_TURN_DEADLINE_SECONDS', 'ROLLOUT_CANARY_SECONDS', 'ROLLOUT_CANARY_OWNERS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('RUNNER_TARGET_IMAGE', NEW)
    settings = load_settings()
    assert (settings.max_parallel, settings.turn_deadline, settings.canary_seconds, settings.canary_owners) == (1, 1800, 600, ())


def test_deadline_applies_while_the_runner_is_idle_but_a_conversation_stays_running():
    """PM 11-25: a runner that reports idle while the database still holds a `running` conversation is not
    waited on forever; past the deadline the cut path records the stop (the drain closes the row, #114)."""
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a)], 'rolling', turn_deadline=1800)
    cluster.idle_owners.add(a)
    store.running_db.add(a)
    assert outcomes(rollout.run())[a] == 'waiting_turn'
    clock.advance(1799)
    assert outcomes(rollout.run())[a] == 'waiting_turn' and ('cut', a) not in store.calls
    clock.advance(1)
    assert outcomes(rollout.run())[a] == 'cutting' and ('cut', a) in store.calls
    assert store.rows[a].stop_id == store.views[a].stop_id


def test_before_create_moves_only_the_canary_owner_during_the_canary():
    """PM 11-25: in the canary stage another waiting owner starts on its current binding."""
    canary, other = sorted(uuid4() for _ in range(2))
    waiting = lambda owner: replace(stopped(owner), phase=Phase.PREPARING)
    clock, store, cluster, rollout = setup([waiting(canary), waiting(other)], 'canary')
    assert rollout.before_create(other) is False and store.views[other].image == OLD  # No canary chosen yet.
    store.set_row(store.target.target_id, canary, 'waiting_turn', canary=True)
    assert rollout.before_create(other) is False and store.views[other].image == OLD
    assert rollout.before_create(canary) is True and store.views[canary].image == NEW
    store.set_state(store.target, 'rolling')
    assert rollout.before_create(other) is True and store.views[other].image == NEW
