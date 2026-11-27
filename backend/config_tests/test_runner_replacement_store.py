"""#101 SqlStore on disposable PostgreSQL (review's DB suite), connected as the observer role so the
101/016/061 grants are exercised: target install/supersede, owner views, binding moves, between-turns
and deadline stops, and re-entry of a logged-in owner."""
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text

from app.runner_image_move import fingerprint, fingerprint_sha
from app.runner_registry import Registry
from app.runner_termination_control import stop_for
from ops.runner_replacement import SqlStore
from runner.lifecycle import Phase, Refused
from config_tests.test_runner_registry import schema, admin, owner, t0, enter, read, ready
from config_tests.test_runner_bindings import binding_schema, plan_value
from config_tests.test_user_runner_http import research_schema
from config_tests.test_runner_termination_control import login
from config_tests.test_runner_rollout_schema import rollout_schema, publish, stopped, plan, NEW_IMAGE

DB = Path('/source/db')
T = fingerprint(NEW_IMAGE, [])


def rolling(observer):
    """The current target for T, in `rolling` (store writes are gated on an active target, review blocker 5)."""
    target = observer.install(T)
    if target.state == 'canary':
        target = observer.set_state(target, 'rolling')
    return target.target_id


@pytest.fixture
def observer(admin):
    with admin.begin() as db:
        db.execute(text('TRUNCATE runner_rollout.owners, runner_rollout.targets'))
    engine = create_engine(admin.url, hide_parameters=True)

    @event.listens_for(engine, 'connect')
    def as_observer(connection, record):
        with connection.cursor() as cursor:
            cursor.execute('SET ROLE anxious_runner_observer')
        connection.commit()
    yield SqlStore(engine)
    engine.dispose()


def test_install_supersedes_without_copying_and_skips_canary_for_a_completed_fingerprint(admin, owner, t0, observer):
    enter(admin, owner, uuid4(), t0)  # owners.owner_id references the registry.
    first = observer.install(T)
    assert first.state == 'canary' and observer.install(T) == first
    stop, external = uuid4(), uuid4()
    observer.set_row(first.target_id, owner, 'draining', canary=True, stop_id=stop)
    observer.set_state(first, 'rolling')
    other = fingerprint(NEW_IMAGE, ['RUNNER_RESEARCH_API_ENABLE'])
    second = observer.install(other)
    assert second.state == 'canary' and second.target_id != first.target_id
    with admin.begin() as db:
        states = dict(db.execute(text('SELECT target_id, state FROM runner_rollout.targets')).all())
        copied = db.execute(text('SELECT count(*) FROM runner_rollout.owners WHERE target_id=:t'),
                            {'t': second.target_id}).scalar_one()
    assert states == {first.target_id: 'superseded', second.target_id: 'canary'}
    # PM 11-25 allowance A: nothing is copied; the old target's own open stop is reported by carried().
    assert copied == 0
    (old_target, carried_owner, row), = observer.carried(second.target_id)
    assert (old_target, carried_owner, row.phase, row.stop_id) == (first.target_id, owner, 'draining', stop)
    observer.set_row(first.target_id, owner, 'draining', canary=False, stop_id=external, reason='external_stop')
    assert observer.carried(second.target_id) == []  # A followed stop is not the old target's own.
    observer.set_row(first.target_id, owner, 'restarting', canary=False)  # PM 11-25: restarts are carried too.
    (_, _, row), = observer.carried(second.target_id)
    assert row.phase == 'restarting'
    # A timed-out restart still protects its owner's binding until the generation ends (3rd re-review).
    observer.set_row(first.target_id, owner, 'blocked', canary=False, reason='restart_timeout')
    (_, _, row), = observer.carried(second.target_id)
    assert (row.phase, row.reason) == ('blocked', 'restart_timeout')
    observer.set_row(first.target_id, owner, 'blocked', canary=False, reason='turn_not_settled')
    assert observer.carried(second.target_id) == []  # Other blocked rows are not carried.
    observer.set_row(first.target_id, owner, 'failed', canary=False, reason='superseded')
    assert observer.carried(second.target_id) == []
    observer.set_state(second, 'rolling')
    done = observer.set_state(observer.current(), 'complete')
    assert done.state == 'complete'
    assert observer.install(T).state == 'canary'  # T never completed.
    assert observer.install(other).state == 'rolling'  # A rollback to a completed fingerprint.
    with admin.begin() as db:
        assert db.execute(text("SELECT count(*) FROM runner_rollout.targets WHERE state<>'superseded'")).scalar_one() == 1
        assert db.execute(text('SELECT fingerprint_sha FROM runner_rollout.targets WHERE target_id=:t'),
                          {'t': second.target_id}).scalar_one() == fingerprint_sha(other)


def test_state_changes_are_compare_and_swap(observer):
    target = observer.install(T)
    halted = observer.set_state(target, 'halted', 'operator')
    assert halted.state == 'halted' and halted.reason == 'operator'
    assert observer.set_state(target, 'rolling').state == 'halted'  # Stale `canary` view loses.


def test_owner_views_and_row_since(admin, owner, t0, observer):
    value = stopped(admin, owner, t0)
    target = observer.install(T)
    view, = observer.owners(target.target_id)
    assert (view.owner, view.phase, view.pod_uid, view.image, view.stop_id, view.row) == (owner, Phase.STOPPED, None, value['image'], None, None)
    observer.set_row(target.target_id, owner, 'waiting_turn', canary=True)
    first = observer.owners(target.target_id)[0].row
    observer.set_row(target.target_id, owner, 'waiting_turn', canary=False)
    again = observer.owners(target.target_id)[0].row
    # Same phase keeps since; the canary flag is the one written (5th re-review: never sticky).
    assert again.since == first.since and first.canary and not again.canary
    observer.set_row(target.target_id, owner, 'blocked', canary=False, reason='runner_failed')
    assert observer.owners(target.target_id)[0].row.phase == 'blocked'


def test_observer_moves_stopped_and_waiting_owners_only(admin, owner, t0, observer):
    value = stopped(admin, owner, t0)
    assert observer.move(owner, NEW_IMAGE, target_id=rolling(observer), canary=False) is True and plan(admin)['image'] == NEW_IMAGE
    assert observer.move(owner, NEW_IMAGE, target_id=rolling(observer), canary=False) is False
    with admin.begin() as db:  # An open entry intent is not a stopped-owner state for the shared predicate.
        db.execute(text("INSERT INTO runner_termination.entries(owner_id,key,state_id,generation,session_hash) VALUES(:o,:k,:s,1,:h)"),
                   {'o': owner, 'k': uuid4(), 's': value['state'], 'h': login(admin, owner)})
    with pytest.raises(Refused):
        observer.move(owner, value['image'], target_id=rolling(observer), canary=False)
    assert plan(admin)['image'] == NEW_IMAGE


def test_ready_owner_is_never_moved(admin, owner, t0, observer):
    state = ready(admin, owner, t0)
    value = publish(admin, state)
    with pytest.raises(Exception):
        observer.move(owner, NEW_IMAGE, target_id=rolling(observer), canary=False)
    assert plan(admin) == value


def test_between_turns_stop_waits_for_running_conversations(admin, owner, t0, observer):
    state = ready(admin, owner, t0)
    publish(admin, state)
    with admin.begin() as db:
        db.execute(text("""INSERT INTO research.conversations(owner_id,title,state,request_id)
            VALUES(:o,'busy','running',gen_random_uuid())"""), {'o': owner})
    assert observer.stop_between_turns(rolling(observer), owner, canary=False) is None
    with admin.begin() as db:
        assert stop_for(db, Registry(db).read(owner)) is None
        db.execute(text("UPDATE research.conversations SET state='idle'"))
    stop = observer.stop_between_turns(rolling(observer), owner, canary=False)
    with admin.begin() as db:
        assert stop_for(db, Registry(db).read(owner))['stop_id'] == stop


def test_deadline_stop_returns_the_running_turns(admin, owner, t0, observer):
    state = ready(admin, owner, t0)
    publish(admin, state)
    with admin.begin() as db:
        conversation = db.execute(text("""INSERT INTO research.conversations(owner_id,title,state,request_id)
            VALUES(:o,'busy','running',gen_random_uuid()) RETURNING id, request_id"""), {'o': owner}).one()
    stop, turns = observer.stop_for_cut(rolling(observer), owner, canary=False)
    assert turns == [tuple(conversation)]
    with admin.begin() as db:
        assert stop_for(db, Registry(db).read(owner))['stop_id'] == stop


@pytest.mark.parametrize('session', [True, False])
def test_restart_re_enters_only_a_logged_in_owner(admin, owner, t0, observer, monkeypatch, session):
    monkeypatch.setenv('CODEX_USER_RUNNERS_ENABLE', 'true')
    monkeypatch.delenv('CODEX_USER_RUNNER_ALLOWLIST', raising=False)
    monkeypatch.delenv('CODEX_USER_RUNNER_ACCOUNT_ID', raising=False)
    stopped(admin, owner, t0)
    if session:
        login(admin, owner)
    key = uuid4()
    assert observer.restart(owner, key) is session
    assert observer.restart(owner, key) is session  # The same key replays, never a second generation.
    state = read(admin, owner)
    assert (state.phase == Phase.PREPARING) is session and state.generation == (2 if session else 1)


def test_restart_respects_the_runner_allowlist(admin, owner, t0, observer, monkeypatch):
    monkeypatch.setenv('CODEX_USER_RUNNERS_ENABLE', 'true')
    monkeypatch.setenv('CODEX_USER_RUNNER_ALLOWLIST', str(uuid4()))
    monkeypatch.delenv('CODEX_USER_RUNNER_ACCOUNT_ID', raising=False)
    stopped(admin, owner, t0)
    login(admin, owner)
    assert observer.restart(owner, uuid4()) is False and read(admin, owner).phase == Phase.STOPPED


def test_canary_owner_is_the_current_unblocked_canary(admin, owner, t0, observer):
    enter(admin, owner, uuid4(), t0)
    target = observer.install(T)
    assert observer.canary_owner(target.target_id) is None
    observer.set_row(target.target_id, owner, 'waiting_turn', canary=True)
    assert observer.canary_owner(target.target_id) == owner
    observer.set_row(target.target_id, owner, 'blocked', canary=True, reason='turn_not_settled')
    assert observer.canary_owner(target.target_id) is None  # Rollout.choose_canary picks another.


def test_stop_rows_commit_with_their_stops(admin, owner, t0, observer):
    state = ready(admin, owner, t0)
    publish(admin, state)
    target = rolling(observer)
    stop = observer.stop_between_turns(target, owner, canary=False)
    view, = observer.owners(target)
    assert (view.row.phase, view.row.stop_id, view.stop_id) == ('draining', stop, stop)


def test_cut_row_commits_with_its_stop(admin, owner, t0, observer):
    state = ready(admin, owner, t0)
    publish(admin, state)
    target = rolling(observer)
    stop, turns = observer.stop_for_cut(target, owner, canary=False)
    view, = observer.owners(target)
    assert (view.row.phase, view.row.stop_id) == ('cutting', stop) and turns == [] == observer.running_turns(owner)


@pytest.mark.parametrize('case', ['halted', 'superseded', 'canary-not-canary', 'canary-other-canary'])
def test_stops_and_moves_recheck_the_target_in_their_transaction(admin, owner, t0, observer, case):
    """Review blocker 5: an operator command (or a newer target) committed after the pass read its target."""
    from ops.runner_replacement import TargetChanged
    state = ready(admin, owner, t0)
    publish(admin, state)
    target = observer.install(T)
    if case == 'halted':
        observer.set_state(target, 'halted', 'operator')
    elif case == 'superseded':
        observer.install(fingerprint(NEW_IMAGE, ['RUNNER_RESEARCH_API_ENABLE']))
    elif case == 'canary-other-canary':
        other = uuid4()
        with admin.begin() as db:
            db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:id,'777','other','editor',true)"), {'id': other})
        enter(admin, other, uuid4(), t0)
        observer.set_row(target.target_id, other, 'waiting_turn', canary=True)
    canary = case == 'canary-other-canary'
    for call in (lambda: observer.stop_between_turns(target.target_id, owner, canary=canary),
                 lambda: observer.stop_for_cut(target.target_id, owner, canary=canary),
                 lambda: observer.move(owner, NEW_IMAGE, target_id=target.target_id, canary=canary)):
        with pytest.raises(TargetChanged):
            call()
    with admin.begin() as db:
        assert stop_for(db, Registry(db).read(owner)) is None


def test_owners_reads_every_page_and_the_stop_proof(admin, owner, t0, observer):
    """Review major 4: no owner is left unevaluated by a page limit."""
    owners = [owner]
    for number in range(2):
        other = uuid4()
        with admin.begin() as db:
            db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:id,:gh,'o','editor',true)"),
                       {'id': other, 'gh': str(800 + number)})
        owners.append(other)
    for each in owners:
        stopped(admin, each, t0)
    observer.PAGE = 2
    target = rolling(observer)
    views = observer.owners(target)
    assert sorted(view.owner for view in views) == sorted(owners) and all(view.proved for view in views)
    with admin.begin() as db:
        db.execute(text('DELETE FROM runner_termination.stops WHERE owner_id=:o'), {'o': owner})
    assert {view.owner: view.proved for view in observer.owners(target)}[owner] is False


def test_the_database_refuses_a_second_active_canary(admin, owner, t0, observer):
    """PM 11-25 (5th re-review): owners_one_active_canary. A stepped-aside (blocked) canary does not count."""
    from sqlalchemy.exc import IntegrityError
    other = uuid4()
    with admin.begin() as db:
        db.execute(text("INSERT INTO auth.accounts(id,github_id,login,role,is_approved) VALUES(:id,'999','o','editor',true)"), {'id': other})
    enter(admin, owner, uuid4(), t0)
    enter(admin, other, uuid4(), t0)
    target = observer.install(T)
    observer.set_row(target.target_id, owner, 'waiting_turn', canary=True)
    with pytest.raises(IntegrityError):
        observer.set_row(target.target_id, other, 'waiting_turn', canary=True)
    observer.set_row(target.target_id, owner, 'blocked', canary=False, reason='awaiting_previous')
    observer.set_row(target.target_id, other, 'waiting_turn', canary=True)
    assert observer.canary_owner(target.target_id) == other
    observer.set_row(target.target_id, owner, 'verified', canary=False)  # A later observation of the old one.
    assert observer.canary_owner(target.target_id) == other
