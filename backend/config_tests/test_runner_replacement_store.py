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


@pytest.fixture
def observer(admin):
    with admin.begin() as db:
        db.connection.driver_connection.execute((DB/'061_runner_controller_grants.sql').read_text())
        db.execute(text('TRUNCATE runner_rollout.owners, runner_rollout.targets'))
    engine = create_engine(admin.url, hide_parameters=True)

    @event.listens_for(engine, 'connect')
    def as_observer(connection, record):
        with connection.cursor() as cursor:
            cursor.execute('SET ROLE anxious_runner_observer')
        connection.commit()
    yield SqlStore(engine)
    engine.dispose()


def test_install_supersedes_carries_in_flight_rows_and_skips_canary_for_a_completed_fingerprint(admin, owner, t0, observer):
    enter(admin, owner, uuid4(), t0)  # owners.owner_id references the registry.
    first = observer.install(T)
    assert first.state == 'canary' and observer.install(T) == first
    stop = uuid4()
    observer.set_row(first.target_id, owner, 'draining', canary=True, stop_id=stop)
    observer.set_state(first, 'rolling')
    other = fingerprint(NEW_IMAGE, ['RUNNER_RESEARCH_API_ENABLE'])
    second = observer.install(other)
    assert second.state == 'canary' and second.target_id != first.target_id
    with admin.begin() as db:
        states = dict(db.execute(text('SELECT target_id, state FROM runner_rollout.targets')).all())
        carried = db.execute(text('SELECT phase, canary, stop_id FROM runner_rollout.owners WHERE target_id=:t'),
                             {'t': second.target_id}).one()
    assert states == {first.target_id: 'superseded', second.target_id: 'canary'}
    assert tuple(carried) == ('draining', False, stop)  # In flight continues; the canary is chosen again.
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
    assert again.since == first.since and again.canary  # Same phase keeps since; canary sticks.
    observer.set_row(target.target_id, owner, 'blocked', canary=False, reason='runner_failed')
    assert observer.owners(target.target_id)[0].row.phase == 'blocked'


def test_observer_moves_stopped_and_waiting_owners_only(admin, owner, t0, observer):
    value = stopped(admin, owner, t0)
    assert observer.move(owner, NEW_IMAGE) is True and plan(admin)['image'] == NEW_IMAGE
    assert observer.move(owner, NEW_IMAGE) is False
    with admin.begin() as db:  # An open entry intent is not a stopped-owner state for the shared predicate.
        db.execute(text("INSERT INTO runner_termination.entries(owner_id,key,state_id,generation,session_hash) VALUES(:o,:k,:s,1,:h)"),
                   {'o': owner, 'k': uuid4(), 's': value['state'], 'h': login(admin, owner)})
    with pytest.raises(Refused):
        observer.move(owner, value['image'])
    assert plan(admin)['image'] == NEW_IMAGE


def test_ready_owner_is_never_moved(admin, owner, t0, observer):
    state = ready(admin, owner, t0)
    value = publish(admin, state)
    with pytest.raises(Exception):
        observer.move(owner, NEW_IMAGE)
    assert plan(admin) == value


def test_between_turns_stop_waits_for_running_conversations(admin, owner, t0, observer):
    state = ready(admin, owner, t0)
    publish(admin, state)
    with admin.begin() as db:
        db.execute(text("""INSERT INTO research.conversations(owner_id,title,state,request_id)
            VALUES(:o,'busy','running',gen_random_uuid())"""), {'o': owner})
    assert observer.stop_between_turns(owner) is None
    with admin.begin() as db:
        assert stop_for(db, Registry(db).read(owner)) is None
        db.execute(text("UPDATE research.conversations SET state='idle'"))
    stop = observer.stop_between_turns(owner)
    with admin.begin() as db:
        assert stop_for(db, Registry(db).read(owner))['stop_id'] == stop


def test_deadline_stop_returns_the_running_turns(admin, owner, t0, observer):
    state = ready(admin, owner, t0)
    publish(admin, state)
    with admin.begin() as db:
        conversation = db.execute(text("""INSERT INTO research.conversations(owner_id,title,state,request_id)
            VALUES(:o,'busy','running',gen_random_uuid()) RETURNING id, request_id"""), {'o': owner}).one()
    stop, turns = observer.stop_for_cut(owner)
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
