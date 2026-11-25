"""#101 rollout schema on disposable PostgreSQL (review's DB suite): decision D3, a binding plan changes
only in `image` and only through runner_rollout.move_image; the database refuses every other write."""
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.runner_bindings import Bindings, validate as bindings_validate
from app.runner_rollout_schema import validate
from app.runner_termination_control import release
from ops.runner_rollout_migrate import migrate
from runner.lifecycle import Refused
from config_tests.test_runner_registry import schema, admin, owner, t0, enter, ready
from config_tests.test_runner_bindings import binding_schema, plan_value
from config_tests.test_user_runner_http import research_schema
from config_tests.test_runner_termination_control import drained

NEW_IMAGE = 'registry.invalid/runner@sha256:'+'b'*64
DB = Path('/source/db')


@pytest.fixture(autouse=True)
def rollout_schema(binding_schema, research_schema, admin):
    migrate()
    with admin.begin() as db:
        if not db.execute(text("SELECT 1 FROM pg_roles WHERE rolname='anxious_runner_observer'")).first():
            db.connection.driver_connection.execute((DB/'016_runner_observer_grants.sql').read_text())
        db.connection.driver_connection.execute((DB/'101_runner_rollout_grants.sql').read_text())


def publish(admin, state):
    value = plan_value(state)
    with admin.begin() as db:
        Bindings(db).publish(value, 'isolated', uuid4(), {'native': uuid4(), 'control': uuid4()})
    return value


def stopped(admin, owner, t0):
    """idle_stopped with a completed stop: the state a running owner reaches after its drain and release."""
    state, stop, proof = drained(admin, owner, t0)
    value = publish(admin, state)
    with admin.begin() as db:
        release(db, owner, stop['stop_id'], proof, allow_entry=False)
    return value


def plan(admin):
    with admin.begin() as db:
        return db.execute(text('SELECT plan FROM runner_bindings.bindings')).scalar_one()


def move(admin, owner, value, image=NEW_IMAGE, role='anxious_runner_observer', state=None):
    with admin.begin() as db:
        db.execute(text('SET LOCAL ROLE '+role))
        return db.execute(text('SELECT runner_rollout.move_image(:owner,:state,CAST(:old AS jsonb),:image)'),
                          {'owner': owner, 'state': state or value['state'], 'old': json.dumps(value),
                           'image': image}).scalar_one()


def test_migration_is_idempotent_and_leaves_the_binding_contract_valid(admin):
    migrate()
    with admin.begin() as db:
        validate(db)
        bindings_validate(db)


def test_observer_moves_only_the_image_of_a_stopped_binding(admin, owner, t0):
    value = stopped(admin, owner, t0)
    with admin.begin() as db:
        resources = db.execute(text('SELECT * FROM runner_bindings.resources ORDER BY kind')).all()
        row = dict(db.execute(text('SELECT owner_id,state_id,namespace,namespace_uid,published_at FROM runner_bindings.bindings')).mappings().one())
    assert move(admin, owner, value) == {**value, 'image': NEW_IMAGE}
    assert plan(admin) == {**value, 'image': NEW_IMAGE}
    with admin.begin() as db:
        assert db.execute(text('SELECT * FROM runner_bindings.resources ORDER BY kind')).all() == resources
        assert dict(db.execute(text('SELECT owner_id,state_id,namespace,namespace_uid,published_at FROM runner_bindings.bindings')).mappings().one()) == row
    # The same compare-and-swap with the old plan is now stale.
    with pytest.raises(DBAPIError):
        move(admin, owner, value)


@pytest.mark.parametrize('field', ['image', 'callback', 'serviceAccount', 'native', 'owner'])
def test_database_refuses_a_direct_plan_update_by_the_observer(admin, owner, t0, field):
    """D3 at the database: the observer holds no UPDATE on bindings, so neither the image nor any
    other plan field can be written except through move_image (which only takes an image)."""
    value = stopped(admin, owner, t0)
    changed = {'image': NEW_IMAGE, 'callback': 'https://api.invalid/other', 'serviceAccount': 'other',
               'native': {**value['native'], 'name': 'native-other'}, 'owner': str(uuid4())}[field]
    with pytest.raises(DBAPIError):
        with admin.begin() as db:
            db.execute(text('SET LOCAL ROLE anxious_runner_observer'))
            db.execute(text('UPDATE runner_bindings.bindings SET plan=jsonb_set(plan,CAST(:path AS text[]),CAST(:value AS jsonb))'),
                       {'path': '{'+field+'}', 'value': json.dumps(changed)})
    assert plan(admin) == value


@pytest.mark.parametrize('column', ['namespace', 'state_id', 'published_at'])
def test_database_refuses_other_binding_columns_even_for_the_writer(admin, owner, t0, column):
    value = stopped(admin, owner, t0)
    new = {'namespace': "'other'", 'state_id': 'gen_random_uuid()', 'published_at': 'now()'}[column]
    with pytest.raises(DBAPIError):
        with admin.begin() as db:
            db.execute(text('SET LOCAL ROLE anxious_runner_binding_writer'))
            db.execute(text('UPDATE runner_bindings.bindings SET '+column+'='+new))
    assert plan(admin) == value


@pytest.mark.parametrize('damage', ['mutable-image', 'same-image', 'stale-plan', 'other-state', 'running-pod',
                                    'settled-ensure', 'runner-failed'])
def test_move_refuses_unsafe_input_or_state(admin, owner, t0, damage):
    if damage in {'running-pod', 'settled-ensure'}:
        state = ready(admin, owner, t0) if damage == 'running-pod' else enter(admin, owner, uuid4(), t0)[0]
        value = publish(admin, state)
        if damage == 'settled-ensure':
            with admin.begin() as db:
                db.execute(text("UPDATE runner_runtime.outbox SET settled=true WHERE owner_id=:owner"), {'owner': owner})
    else:
        value = stopped(admin, owner, t0)
    if damage == 'runner-failed':
        with admin.begin() as db:
            db.execute(text("UPDATE runner_runtime.registry SET phase='runner_failed'"))
    image = {'mutable-image': 'registry.invalid/runner:latest', 'same-image': value['image']}.get(damage, NEW_IMAGE)
    old = {**value, 'callback': 'https://api.invalid/other'} if damage == 'stale-plan' else value
    with pytest.raises(DBAPIError):
        move(admin, owner, old, image=image, state=str(uuid4()) if damage == 'other-state' else None)
    assert plan(admin) == value


def test_move_takes_the_registry_owner_lock(admin, owner, t0):
    """The function's lock key equals Registry._lock's, so a transaction holding the owner lock (entry,
    reserve, bind, stop, release) blocks the move until it commits."""
    from app.runner_registry import Registry
    import hashlib
    expected = int.from_bytes(hashlib.sha256(('runner-registry-v1:'+str(owner)).encode()).digest()[:8], 'big', signed=True)
    with admin.begin() as db:
        computed = db.execute(text("""SELECT ('x' || substr(encode(sha256(convert_to('runner-registry-v1:' || CAST(:owner AS uuid)::text,
            'UTF8')), 'hex'), 1, 16))::bit(64)::bigint"""), {'owner': owner}).scalar_one()
    assert computed == expected
    value = stopped(admin, owner, t0)
    holder = admin.connect()
    transaction = holder.begin()
    try:
        Registry(holder)._current(owner)  # Holds the owner lock until rollback.
        with pytest.raises(DBAPIError):
            with admin.begin() as db:
                db.execute(text("SET LOCAL lock_timeout='1s'"))
                db.execute(text('SET LOCAL ROLE anxious_runner_observer'))
                db.execute(text('SELECT runner_rollout.move_image(:owner,:state,CAST(:old AS jsonb),:image)'),
                           {'owner': owner, 'state': value['state'], 'old': json.dumps(value), 'image': NEW_IMAGE})
    finally:
        transaction.rollback()
        holder.close()
    assert plan(admin) == value
    assert move(admin, owner, value)['image'] == NEW_IMAGE  # Free again once the holder ends.


def test_waiting_owner_moves_before_its_create_permission(admin, owner, t0):
    state, _ = enter(admin, owner, uuid4(), t0)
    value = publish(admin, state)
    assert move(admin, owner, value)['image'] == NEW_IMAGE


def test_api_cannot_call_the_move_or_read_rollout_state(admin, owner, t0):
    value = stopped(admin, owner, t0)
    for sql in ('SELECT runner_rollout.move_image(:owner,:state,CAST(:old AS jsonb),:image)',
                'SELECT * FROM runner_rollout.targets'):
        with pytest.raises(DBAPIError):
            with admin.begin() as db:
                db.execute(text('SET LOCAL ROLE anxious_api'))
                db.execute(text(sql), {'owner': owner, 'state': value['state'], 'old': json.dumps(value), 'image': NEW_IMAGE})
    assert plan(admin) == value


@pytest.mark.parametrize('drift', [
    'GRANT UPDATE(namespace) ON runner_bindings.bindings TO anxious_runner_binding_writer',
    'GRANT UPDATE(plan) ON runner_bindings.bindings TO anxious_runner_observer',
    'GRANT EXECUTE ON FUNCTION runner_rollout.move_image(uuid,uuid,jsonb,text) TO PUBLIC',
    'GRANT USAGE ON SCHEMA runner_rollout TO anxious_api',
    'ALTER FUNCTION runner_rollout.move_image(uuid,uuid,jsonb,text) SECURITY INVOKER',
    'ALTER FUNCTION runner_rollout.move_image(uuid,uuid,jsonb,text) RESET search_path',
    'GRANT anxious_runner_binding_writer TO anxious_runner_observer',
    """CREATE OR REPLACE FUNCTION runner_rollout.move_image(p_owner uuid, p_state uuid, p_old jsonb, p_image text)
       RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
       BEGIN UPDATE runner_bindings.bindings SET plan = p_old WHERE owner_id = p_owner; RETURN p_old; END $$""",
    'CREATE FUNCTION runner_rollout.extra() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$',
])
def test_validation_refuses_any_other_write_path(admin, drift):
    with admin.connect() as db:
        transaction = db.begin()
        try:
            db.connection.driver_connection.execute(drift)
            with pytest.raises(Refused):
                validate(db)
        finally:
            transaction.rollback()
    with admin.begin() as db:
        validate(db)
