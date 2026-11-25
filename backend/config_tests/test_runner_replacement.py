"""#101 rolling replacement decisions with an in-memory store and cluster (no database, no Kubernetes)."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid5

import pytest

from app.runner_image_move import fingerprint, fingerprint_sha, pod_fingerprint, target_from_env
from ops.runner_replacement import (Rollout, Settings, Target, Row, View, Halt, TargetChanged, health_class, load_settings,
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
        self.before_stop, self.crash_after_cut, self.turns = None, False, [(uuid4(), uuid4())]
        self.old_rows, self.completed = {}, set()

    def install(self, value):
        """SqlStore.install: a new fingerprint supersedes the target; nothing is copied (allowance A)."""
        if value != self.target.fingerprint:
            for owner, row in self.rows.items():
                self.old_rows[(self.target.target_id, owner)] = row
            self.rows = {}
            known = fingerprint_sha(value) in self.completed
            self.target = Target(uuid4(), value, 'rolling' if known else 'canary')
        return self.target

    def carried(self, target_id):
        return [(t, o, row) for (t, o), row in sorted(self.old_rows.items(), key=lambda item: (str(item[0][1]), str(item[0][0])))
                if t != target_id and row.phase in {'cutting', 'draining', 'restarting'} and row.reason != 'external_stop']

    def current(self):
        return self.target

    def canary_owner(self, target_id):
        current = sorted(owner for owner, row in self.rows.items() if row.canary and row.phase not in {'blocked', 'failed'})
        return current[0] if current else None

    def owners(self, target_id):
        assert target_id == self.target.target_id
        return [replace(view, row=self.rows.get(owner)) for owner, view in sorted(self.views.items())]

    def set_row(self, target_id, owner, phase, *, canary, stop_id=None, reason=None):
        if target_id != self.target.target_id:
            old = self.old_rows.get((target_id, owner))
            self.old_rows[(target_id, owner)] = Row(phase, self.clock(), bool(old and old.canary) or canary,
                                                     stop_id or (old.stop_id if old else None), reason)
            return
        old = self.rows.get(owner)
        since = old.since if old is not None and old.phase == phase else self.clock()
        self.rows[owner] = Row(phase, since, bool(old and old.canary) or canary,
                               stop_id or (old.stop_id if old else None), reason)

    def set_state(self, target, state, reason=None, owner=None):
        if target.target_id != self.target.target_id or target.state != self.target.state:
            return self.target  # Compare-and-swap, as SqlStore.set_state.
        self.target = Target(target.target_id, target.fingerprint, state, reason, owner)
        if state == 'complete':
            self.completed.add(fingerprint_sha(target.fingerprint))
        return self.target

    def gate(self, target_id, owner, canary):
        """SqlStore._gate: the current target is still active and, in the canary, this owner is the only canary."""
        state = self.target.state if target_id == self.target.target_id else 'superseded'
        others = [o for o, row in self.rows.items() if row.canary and row.phase not in {'blocked', 'failed'} and o != owner]
        if state not in {'canary', 'rolling', 'complete'} or (state == 'canary' and (not canary or others)):
            self.calls.append(('gate_refused', owner))
            raise TargetChanged('rollout_target_changed')

    def move(self, owner, image, *, target_id, canary):
        self.calls.append(('move', owner))
        self.gate(target_id, owner, canary)
        if owner in self.unmovable:
            raise Refused('not movable')
        view = self.views[owner]
        self.views[owner] = replace(view, image=image)
        return view.image != image

    def stop_between_turns(self, target_id, owner, *, canary):
        self.calls.append(('stop', owner))
        if self.before_stop is not None:
            self.before_stop()  # A concurrent operator command committing in between.
        if owner in self.running_db:
            return None
        self.gate(target_id, owner, canary)
        stop = uuid4()
        self.views[owner] = replace(self.views[owner], stop_id=stop)
        self.set_row(target_id, owner, 'draining', canary=canary, stop_id=stop)  # Same transaction as the stop.
        return stop

    def stop_for_cut(self, target_id, owner, *, canary):
        self.calls.append(('cut', owner))
        self.gate(target_id, owner, canary)
        stop = uuid4()
        self.views[owner] = replace(self.views[owner], stop_id=stop)
        self.set_row(target_id, owner, 'cutting', canary=canary, stop_id=stop)  # Same transaction as the stop.
        if self.crash_after_cut:
            raise RuntimeError('controller stopped right after the commit')
        return stop, list(self.turns)

    def running_turns(self, owner):
        return list(self.turns)

    def restart(self, owner, key):
        """SqlStore.restart: a logged-in stopped owner re-enters (Registry.enter -> provisioning)."""
        self.calls.append(('restart', owner, key))
        if owner not in self.sessions:
            return False
        if self.views[owner].phase == Phase.STOPPED:
            self.views[owner] = replace(self.views[owner], phase=Phase.PREPARING)
        return True


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
    # b's Pod is old, but this controller never read its health: one healthy read first (allowance B).
    assert outcomes(rollout.run()) == {a: 'converged', b: 'verified'}
    assert store.target.state == 'complete'
    assert outcomes(rollout.run()) == {a: 'converged', b: 'converged'}
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


# Full review of d8616be (request-changes): each reproduced case ------------------------------------------------

def test_b1_a_carried_stop_of_a_non_canary_owner_does_not_move_during_the_canary():
    canary, other = sorted(uuid4() for _ in range(2))
    clock, store, cluster, rollout = setup([running(canary), stopped(other)], 'canary', canary_owners=(canary,))
    store.rows[other] = Row('draining', clock(), False, uuid4())  # Carried over from the previous target.
    store.sessions.add(other)
    lines = outcomes(rollout.run())
    assert lines[other] == 'blocked' and store.rows[other].reason == 'awaiting_canary'
    assert store.views[other].image == OLD and ('move', other) not in store.calls
    assert [c[:2] for c in store.calls if c[0] == 'restart'] == [('restart', other)]  # On its unchanged binding.
    assert store.rows[canary].canary and store.rows[canary].phase == 'waiting_turn'


def test_b2_a_cut_survives_a_controller_restart_and_resends_the_interrupts():
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a)], 'rolling', turn_deadline=60)
    rollout.run(); clock.advance(60)
    store.crash_after_cut = True
    lines = rollout.run()
    assert any(kind == 'rollout' and str(reason).startswith('stuck:') for _, kind, reason in lines)
    assert store.rows[a].phase == 'cutting' and store.rows[a].stop_id == store.views[a].stop_id  # Committed together.
    assert cluster.interrupts == []
    store.crash_after_cut = False
    restarted = Rollout(store, cluster, Settings(T, turn_deadline=60), clock)  # A new controller process.
    assert outcomes(restarted.run())[a] == 'cutting' and len(cluster.interrupts) == 1
    clock.advance(30)
    assert outcomes(restarted.run())[a] == 'cutting' and len(cluster.interrupts) == 2  # Bounded retries...
    clock.advance(SETTLE_SECONDS)
    assert outcomes(restarted.run())[a] == 'blocked' and len(cluster.interrupts) == 2  # ...until the settle window.


@pytest.mark.parametrize('break_with', ['inconclusive', 'unreachable'])
def test_b3_the_canary_window_needs_consecutive_healthy_checks_of_the_same_pod(break_with):
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a, NEW)], 'canary', canary_seconds=600)
    cluster.pods[a] = pod(store.views[a].pod_uid, NEW, created=clock())
    clock.advance(1)
    assert outcomes(rollout.run())[a] == 'verified'
    clock.advance(300)
    if break_with == 'inconclusive':
        cluster.health_of[a] = {'available': False, 'reason': 'auth_expired'}
    elif break_with == 'unreachable':
        cluster.health = lambda view, obj: (_ for _ in ()).throw(OSError('timeout'))
    broken = outcomes(rollout.run())[a]
    assert broken in {'inconclusive', 'health_unreachable'} and store.target.state == 'canary'
    assert store.rows[a].phase == 'restarting' and store.rows[a].reason == 'canary_unconfirmed'
    cluster.health_of.pop(a, None)
    cluster.health = Cluster.health.__get__(cluster)
    clock.advance(1)
    assert outcomes(rollout.run())[a] == 'verified'  # The streak starts again here.
    clock.advance(599)
    assert outcomes(rollout.run())[a] == 'verified' and store.target.state == 'canary'
    clock.advance(1)
    # Passed; the only owner is on the target, so the target also completes in this pass.
    assert outcomes(rollout.run())[a] == 'canary_passed' and store.target.state == 'complete'


def test_b4_a_starting_target_pod_keeps_the_rollout_watching_until_its_pull_failure_halts():
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a, NEW)], 'rolling')
    cluster.pods[a] = pod(store.views[a].pod_uid, NEW, phase='Pending', waiting='ImagePullBackOff', created=clock())
    assert outcomes(rollout.run())[a] == 'starting' and store.target.state == 'rolling'  # Not complete.
    clock.advance(PULL_SECONDS)
    assert (a, 'rollout_halted', 'image_pull') in rollout.run() and store.target.state == 'halted'


def test_b5_an_operator_halt_during_the_idle_check_records_no_stop():
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a)], 'rolling')
    cluster.idle_owners.add(a)
    store.before_stop = lambda: store.set_state(store.target, 'halted', 'operator')
    assert outcomes(rollout.run())[a] == 'held'
    assert store.views[a].stop_id is None and ('gate_refused', a) in store.calls


def test_b5_a_move_after_an_operator_halt_is_refused_in_its_transaction():
    a = uuid4()
    clock, store, cluster, rollout = setup([stopped(a)], 'rolling')
    original = store.owners
    def owners_then_halt(target_id):
        views = original(target_id)
        store.target = Target(store.target.target_id, T, 'halted', 'operator')  # Committed after the read.
        return views
    store.owners = owners_then_halt
    assert outcomes(rollout.run())[a] == 'held' and store.views[a].image == OLD


def test_m1_no_idle_evidence_still_reaches_the_deadline():
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a)], 'rolling', turn_deadline=60)
    cluster.idle = lambda view, obj, seconds: (_ for _ in ()).throw(OSError('timeout'))
    assert outcomes(rollout.run())[a] == 'idle_unknown'
    clock.advance(60)
    assert outcomes(rollout.run())[a] == 'cutting' and ('cut', a) in store.calls


def test_m2_followed_and_blocked_stops_hold_no_slot():
    a, b, c = sorted(uuid4() for _ in range(3))
    clock, store, cluster, rollout = setup([replace(running(a), stop_id=uuid4()), replace(running(b), stop_id=uuid4()),
                                            running(c)], 'rolling', max_parallel=1)
    cluster.idle_owners.add(c)
    lines = outcomes(rollout.run())
    assert lines[a] == lines[b] == 'draining' and store.rows[a].reason == store.rows[b].reason == 'external_stop'
    assert lines[c] == 'draining' and rollout.slots == 1  # The one replacement slot is c's.
    # A blocked unsettled stop keeps its block on the next pass instead of draining again.
    stop = uuid4()
    store.views[a] = replace(store.views[a], stop_id=stop)
    store.rows[a] = Row('blocked', clock(), False, stop, 'turn_not_settled')
    assert outcomes(rollout.run())[a] == 'blocked' and store.rows[a].phase == 'blocked'


def test_m3_a_stopped_owner_without_proof_is_blocked_and_never_the_canary():
    legacy, other = sorted(uuid4() for _ in range(2))
    clock, store, cluster, rollout = setup([replace(stopped(legacy), proved=False), stopped(other)], 'canary')
    lines = outcomes(rollout.run())
    assert lines[legacy] == 'blocked' and store.rows[legacy].reason == 'no_stop_proof' and not store.rows[legacy].canary
    assert ('move', legacy) not in store.calls
    assert lines[other] == 'moved' and store.rows[other].canary


# Full re-review of 57ebf2e (request-changes) and PM 11-25 allowances A/B ----------------------------------------

@pytest.mark.parametrize('gap', ['pod_missing', 'lookup_error', 'other_uid_same_creation'])
def test_rb1_the_canary_streak_belongs_to_one_observed_pod_uid(gap):
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a, NEW)], 'canary', canary_seconds=600)
    first = store.views[a].pod_uid
    cluster.pods[a] = pod(first, NEW, created=clock())
    assert outcomes(rollout.run())[a] == 'verified'
    clock.advance(400)
    if gap == 'pod_missing':
        saved = cluster.pods.pop(a)
        assert outcomes(rollout.run())[a] == 'pod_missing'
        cluster.pods[a] = saved
    elif gap == 'lookup_error':
        original = cluster.pod
        cluster.pod = lambda view: (_ for _ in ()).throw(OSError('api'))
        assert any(str(reason).startswith('stuck:') for _, _, reason in rollout.run())
        cluster.pod = original
    else:  # A different Pod UID created at the very same second.
        other = uuid4()
        store.views[a] = replace(store.views[a], pod_uid=other)
        cluster.pods[a] = pod(other, NEW, created=T0)
    clock.advance(300)  # 700 s since the first healthy check, but the streak was broken.
    assert outcomes(rollout.run())[a] == 'verified' and store.target.state == 'canary'
    clock.advance(599)
    assert outcomes(rollout.run())[a] == 'verified' and store.target.state == 'canary'
    clock.advance(1)
    assert outcomes(rollout.run())[a] == 'canary_passed'


def test_rb2_a_target_pod_never_read_healthy_is_never_converged_by_age():
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a, NEW)], 'rolling', canary_seconds=600)
    cluster.pods[a] = pod(store.views[a].pod_uid, NEW, created=clock())
    cluster.health = lambda view, obj: (_ for _ in ()).throw(OSError('timeout'))
    for _ in range(4):
        clock.advance(300)
        assert outcomes(rollout.run())[a] == 'health_unreachable' and store.target.state == 'rolling'
    cluster.health = Cluster.health.__get__(cluster)
    assert outcomes(rollout.run())[a] == 'verified' and store.target.state == 'complete'


def test_rb3_a_rollback_finishes_the_previous_targets_open_cut_first():
    """Allowance A: the open cut of the superseded target keeps getting its interrupts and the new target
    (here a rollback to the image that Pod runs) starts nothing until that stop is released."""
    a, b = sorted(uuid4() for _ in range(2))
    clock, store, cluster, rollout = setup([running(a), running(b)], 'rolling', turn_deadline=60)
    rollout.run(); clock.advance(60)
    assert outcomes(rollout.run())[a] == 'cutting' and len(cluster.interrupts) == 1
    back = fingerprint(OLD, [])  # Roll back to the image a and b run.
    store.completed.add(fingerprint_sha(back))
    rolled = Rollout(store, cluster, Settings(back, turn_deadline=60), clock)
    cluster.idle_owners.add(b)
    lines = rolled.run()
    assert (a, 'rollout_previous', 'cutting') in lines and len(cluster.interrupts) == 2
    assert store.target.fingerprint == back and ('stop', b) not in store.calls and ('cut', b) not in store.calls
    release(store, cluster, a)
    store.sessions.add(a)
    lines = rolled.run()
    # The restart of the old target holds the rollback until it has its Pod (PM 11-25: A includes restarts).
    assert (a, 'rollout_previous', 'restarting') in lines
    assert [c[:2] for c in store.calls if c[0] == 'restart'] == [('restart', a)] and store.views[a].image == OLD
    uid = uuid4()
    store.views[a] = replace(store.views[a], phase=Phase.UNREACHABLE, pod_uid=uid)
    cluster.pods[a] = pod(uid, OLD, created=clock())
    assert (a, 'rollout_previous', 'previous_finished') in rolled.run()


def test_r_major_an_owner_changing_twice_in_one_pass_keeps_the_slot_count():
    a, b = sorted(uuid4() for _ in range(2))
    clock, store, cluster, rollout = setup([running(a), running(b)], 'rolling', max_parallel=1)
    store.rows[a] = Row('restarting', clock(), False)  # Its restart came up on another fingerprint.
    cluster.idle_owners |= {a, b}
    lines = outcomes(rollout.run())
    assert lines[a] == 'draining' and lines[b] == 'waiting_slot'
    assert [c for c in store.calls if c[0] == 'stop'] == [('stop', a)] and rollout.slots == 1




def test_scenario_mixed_passes_keep_the_invariants():
    """PM 11-25: canary with a health flap, rolling with competing idle owners, a deadline cut still open when
    a rollback arrives, controller restarts, a halt. After every pass:
      I1 at most maxParallel rollout-opened stops are open (all targets together);
      I2 every open, unreceipted cut within its settle window got an interrupt in that pass;
      I3 while a superseded target's cut is open, the current target records no stop or move;
      I4 the canary passes only after canarySeconds of healthy checks of one Pod UID with no gap.
    Coverage asserts at the end make sure each situation actually happened."""
    a, b, c, d = owners = sorted(uuid4() for _ in range(4))
    clock, store, cluster, _ = setup([running(owner) for owner in owners], 'canary')
    store.sessions |= set(owners)
    store.turns = [(uuid4(), uuid4())]  # c's turn: running in the database until c is released.
    cluster.idle_owners |= {a, b, d}  # c is always busy: it reaches the deadline.
    settings = dict(turn_deadline=240, canary_seconds=180, max_parallel=1)
    opened = {}  # owner -> stop id opened by the rollout and not released
    seen = {'waiting_slot': 0, 'cut': 0, 'gated': 0, 'canary_passed': None, 'previous_finished': 0}
    health_log = []  # (time, owner, uid, healthy) for every health read of the canary candidate
    selected = {}  # owner -> time of the pass in which it became this target's canary

    original_stop, original_cut = store.stop_between_turns, store.stop_for_cut
    def stop_between_turns(target_id, owner, *, canary):
        stop = original_stop(target_id, owner, canary=canary)
        if stop is not None:
            opened[owner] = stop
        return stop
    def stop_for_cut(target_id, owner, *, canary):
        stop, turns = original_cut(target_id, owner, canary=canary)
        opened[owner] = stop
        seen['cut'] += 1
        return stop, [(uuid4(), uuid4())]
    store.stop_between_turns, store.stop_for_cut = stop_between_turns, stop_for_cut
    def restart(owner, key):
        store.calls.append(('restart', owner, key))
        if store.views[owner].phase == Phase.STOPPED:
            store.views[owner] = replace(store.views[owner], phase=Phase.PREPARING)
        return True
    store.restart = restart
    flapping = []
    def health(view, obj):
        if flapping:
            health_log.append((clock(), view.owner, obj['metadata']['uid'], False))
            raise OSError('timeout')
        health_log.append((clock(), view.owner, obj['metadata']['uid'], True))
        return {'available': True, 'reason': None}
    cluster.health = health

    def release_ready(only=None):
        """Service.step: a drained stop (idle owner, or an interrupted cut) is released. c's cut only on
        `release_c`, so that it is still open when the rollback arrives."""
        for owner in owners if only is None else [only]:
            if owner == c and only is None:
                continue
            view = store.views[owner]
            interrupted = any(o == owner for o, _, _ in cluster.interrupts)
            if view.stop_id is not None and (owner in cluster.idle_owners or interrupted):
                release(store, cluster, owner)
                opened.pop(owner, None)
                if owner == c:
                    store.turns = []
                cluster.interrupts[:] = [i for i in cluster.interrupts if i[0] != owner]

    def spawn():
        """Service.ensure: an owner waiting for a Pod gets one from its binding."""
        for owner in owners:
            view = store.views[owner]
            if view.phase == Phase.PREPARING and view.pod_uid is None:
                uid = uuid4()
                store.views[owner] = replace(view, phase=Phase.UNREACHABLE, pod_uid=uid)
                cluster.pods[owner] = pod(uid, view.image, created=clock())

    def check(lines, interrupts_before, gated):
        # I1: a cut that did not settle is `blocked:turn_not_settled` and frees its slot by design (operator
        # R2), so a blocked stop is not counted.
        blocked = {o for o, row in store.rows.items() if row.phase == 'blocked'}
        blocked |= {o for (_, o), row in store.old_rows.items() if row.phase == 'blocked'}
        assert len(set(opened) - blocked) <= settings['max_parallel']                            # I1
        sent = {o for o, _, _ in cluster.interrupts[interrupts_before:]}
        cuts = [(o, row) for o, row in store.rows.items() if row.phase == 'cutting']
        cuts += [(o, row) for (_, o), row in store.old_rows.items() if row.phase == 'cutting']
        for owner, row in cuts:
            if store.views[owner].stop_id == row.stop_id and (clock() - row.since).total_seconds() <= SETTLE_SECONDS:
                assert owner in sent                                                               # I2
        if gated:
            seen['gated'] += 1
            assert not [line for line in lines if line[1] == 'rollout' and line[2] in {'draining', 'cutting', 'moved'}]  # I3
        for owner, kind, outcome in lines:
            if outcome == 'waiting_slot':
                seen['waiting_slot'] += 1
            if outcome == 'previous_finished':
                seen['previous_finished'] += 1
            if outcome == 'canary_passed':                                                         # I4
                uid = str(store.views[owner].pod_uid)  # The cluster serves metadata.uid as a string.
                # Only reads after this owner became the selected canary count (PM 11-25 on allowance B).
                reads = [entry for entry in health_log if entry[1] == owner and entry[0] >= selected[owner]]
                streak = []
                for entry in reads:
                    streak = streak + [entry] if entry[3] and entry[2] == uid else []
                assert streak and (streak[-1][0] - streak[0][0]).total_seconds() >= settings['canary_seconds']
                seen['canary_passed'] = clock()

    rollout = Rollout(store, cluster, Settings(T, **settings), clock)
    plan = (['run', 'release', 'run', 'spawn', 'run', 'flap', 'run', 'restart', 'run', 'run', 'run', 'run', 'run']
            + ['run', 'release', 'run', 'spawn', 'run'] * 3
            + ['run'] * 8)
    # The rollback arrives right after c's deadline cut: a gated pass inside the settle window (with a
    # controller restart before it), c released within the window, then halt/resume and the remaining owners.
    after_cut = ['restart', 'run', 'release_c', 'run', 'spawn', 'run', 'halt', 'run', 'resume', 'run', 'release',
                 'run', 'spawn', 'run']
    rolled_back = False
    while plan:
        action = plan.pop(0)
        if action != 'restart':
            clock.advance(61 if action == 'run' else 1)
        if action == 'release':
            release_ready()
        elif action == 'release_c':
            release_ready(c)
        elif action == 'spawn':
            spawn()
        elif action == 'restart':
            rollout = Rollout(store, cluster, rollout.settings, clock)
        elif action == 'flap':
            flapping.append(True)
        elif action == 'halt':
            store.set_state(store.target, 'halted', 'operator')
        elif action == 'resume':
            if store.target.state == 'halted':
                store.set_state(store.target, 'rolling')
        else:
            before = len(cluster.interrupts)
            target = store.target.target_id
            lines = rollout.run()
            flapping.clear()
            for owner, row in store.rows.items():
                if row.canary and (target, owner) not in selected:
                    selected[(target, owner)] = clock()
                    selected[owner] = clock()
            gated = any(kind == 'rollout_previous' and outcome in {'cutting', 'draining', 'restarting'}
                        for _, kind, outcome in lines)
            check(lines, before, gated)
            if not rolled_back and store.rows.get(c) is not None and store.rows[c].phase == 'cutting':
                back = fingerprint(OLD, [])  # Roll back to the image c (and the others not yet replaced) run.
                store.completed.add(fingerprint_sha(back))
                rollout = Rollout(store, cluster, Settings(back, **settings), clock)
                rolled_back, plan = True, list(after_cut)
    assert seen['canary_passed'] is not None and seen['waiting_slot'] and seen['cut'] and seen['gated'], seen
    assert seen['previous_finished'] and rolled_back


def test_rb3_while_a_previous_cut_is_open_the_new_target_neither_moves_nor_moves_before_create():
    a, c = sorted(uuid4() for _ in range(2))
    clock, store, cluster, rollout = setup([running(a), stopped(c, NEW)], 'rolling', turn_deadline=60)
    rollout.run(); clock.advance(60)
    assert outcomes(rollout.run())[a] == 'cutting'
    back = fingerprint(OLD, [])
    store.completed.add(fingerprint_sha(back))
    rolled = Rollout(store, cluster, Settings(back, turn_deadline=60), clock)
    lines = rolled.run()
    assert (a, 'rollout_previous', 'cutting') in lines and outcomes(lines)[c] == 'held'
    assert store.views[c].image == NEW and rolled.before_create(c) is False
    release(store, cluster, a)
    lines = rolled.run()  # The previous stop finishes first in this pass, then the rollback moves c.
    assert (a, 'rollout_previous', 'previous_finished') in lines
    assert outcomes(lines)[c] == 'moved' and store.views[c].image == OLD


def test_our_open_stop_is_handled_before_the_pods_fingerprint():
    """Review re-review blocker 3: an open stop of this target is never overwritten by observe()."""
    a = uuid4()
    clock, store, cluster, rollout = setup([running(a, NEW)], 'rolling')
    stop = uuid4()
    store.views[a] = replace(store.views[a], stop_id=stop)
    store.rows[a] = Row('cutting', clock(), False, stop)
    cluster.pods[a] = pod(store.views[a].pod_uid, NEW, created=clock())  # Already on the target.
    assert outcomes(rollout.run())[a] == 'cutting' and len(cluster.interrupts) == 1
    assert store.rows[a].phase == 'cutting'


def test_rr1_the_previous_targets_restart_holds_the_binding_and_the_new_target():
    """2nd re-review blocker 1 / PM 11-25: allowance A includes restarts. Until the old target's restart has
    its Pod, the new target neither moves that owner's binding nor opens a stop elsewhere."""
    a, b = sorted(uuid4() for _ in range(2))
    clock, store, cluster, rollout = setup([running(a), running(b)], 'rolling', turn_deadline=60)
    store.sessions.add(a)
    cluster.idle_owners.add(a)
    assert outcomes(rollout.run())[a] == 'draining'
    release(store, cluster, a)
    other = fingerprint(OLD, ['RUNNER_RESEARCH_API_ENABLE'])  # A new target: neither a's nor b's fingerprint.
    rolled = Rollout(store, cluster, Settings(other, turn_deadline=60), clock)
    cluster.idle_owners.add(b)
    lines = rolled.run()
    assert (a, 'rollout_previous', 'restarting') in lines and store.views[a].phase == Phase.PREPARING
    assert rolled.before_create(a) is False and store.views[a].image == OLD
    assert ('stop', b) not in store.calls
    clock.advance(30)
    lines = rolled.run()
    assert (a, 'rollout_previous', 'restarting') in lines and ('stop', b) not in store.calls
    uid = uuid4()  # Service.ensure: the restart gets its Pod on the unchanged binding.
    store.views[a] = replace(store.views[a], phase=Phase.UNREACHABLE, pod_uid=uid)
    cluster.pods[a] = pod(uid, OLD, created=clock())
    lines = rolled.run()
    assert (a, 'rollout_previous', 'previous_finished') in lines
    assert store.target.state == 'canary' and any(c[0] == 'stop' for c in store.calls[-3:])  # Now it starts.


def test_rr2_canary_time_counts_only_from_selection():
    """2nd re-review blocker 2 / PM 11-25: healthy reads of an owner before it is the selected canary (here
    while a previous target's cut holds the new target back) never count toward canarySeconds."""
    a, b = sorted(uuid4() for _ in range(2))
    NEW2 = 'registry.invalid/runner@sha256:'+'c'*64
    clock, store, cluster, rollout = setup([running(a), running(b, NEW2)], 'rolling', turn_deadline=60)
    rollout.run(); clock.advance(60)
    assert outcomes(rollout.run())[a] == 'cutting'
    second = fingerprint(NEW2, [])  # b already runs it.
    rolled = Rollout(store, cluster, Settings(second, turn_deadline=60, canary_seconds=200, canary_owners=(b,)), clock)
    cluster.pods[b] = pod(store.views[b].pod_uid, NEW2, created=clock())  # Young: health-read every pass.
    for _ in range(2):  # 110 s of healthy reads of b, inside a's settle window.
        clock.advance(55)
        lines = rolled.run()
        assert (a, 'rollout_previous', 'cutting') in lines and outcomes(lines)[b] == 'verified'
        assert store.target.state == 'canary' and not (store.rows.get(b) and store.rows[b].canary)
    release(store, cluster, a)  # Not logged in: no restart; the old target's stop is over.
    clock.advance(1)
    lines = rolled.run()
    assert (a, 'rollout_previous', 'previous_finished') in lines
    assert store.rows[b].canary and outcomes(lines)[b] == 'verified' and store.target.state == 'canary'  # Not passed.
    clock.advance(199)  # 310 s after the first pre-selection read, 199 s after selection.
    assert outcomes(rolled.run())[b] == 'verified' and store.target.state == 'canary'
    clock.advance(1)
    assert outcomes(rolled.run())[b] == 'canary_passed'
