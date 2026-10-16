"""Explicit stopped-binding image maintenance on disposable PostgreSQL."""
from dataclasses import replace
import os
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from app.runner_bindings import Bindings
from app.runner_termination_control import release
from ops.runner_binding_image import upgrade, plan_digest
from runner.lifecycle import Fence, Refused
from config_tests.test_runner_registry import schema, admin, owner, t0, read, preservation_snapshot
from config_tests.test_runner_bindings import binding_schema, plan_value
from config_tests.test_user_runner_http import research_schema
from config_tests.test_runner_termination_control import drained

NEW_IMAGE = 'registry.invalid/runner@sha256:'+'b'*64


@pytest.fixture
def stopped_binding(admin,owner,t0):
    state,stop,proof = drained(admin,owner,t0)
    value = plan_value(state)
    with admin.begin() as db:
        Bindings(db).publish(value,'isolated',uuid4(),{'native':uuid4(),'control':uuid4()})
        release(db,owner,stop['stop_id'],proof,allow_entry=False)
        db.execute(text('UPDATE runner_runtime.outbox SET settled=true WHERE owner_id=:owner'),{'owner':owner})
    return Fence.of(read(admin,owner)),value


def test_report_and_apply_change_only_image_with_no_automatic_repeat(admin,stopped_binding):
    fence,value = stopped_binding
    before = preservation_snapshot(admin)
    with admin.begin() as db:
        resources = db.execute(text('SELECT * FROM runner_bindings.resources')).all()
        row = dict(db.execute(text('SELECT * FROM runner_bindings.bindings')).mappings().one())
        report = upgrade(db,fence,plan_digest(value),NEW_IMAGE)
        assert dict(db.execute(text('SELECT * FROM runner_bindings.bindings')).mappings().one()) == row
        assert upgrade(db,fence,plan_digest(value),NEW_IMAGE,apply=True) == report
        updated = dict(db.execute(text('SELECT * FROM runner_bindings.bindings')).mappings().one())
        assert updated == {**row,'plan':{**value,'image':NEW_IMAGE}}
        assert db.execute(text('SELECT * FROM runner_bindings.resources')).all() == resources
    assert preservation_snapshot(admin) == before
    with pytest.raises(Refused):
        with admin.begin() as db: upgrade(db,fence,plan_digest(value),NEW_IMAGE,apply=True)
    # A separately reviewed reverse change uses the new full-plan digest and
    # the same still-stopped fence, never a live Pod patch or registry rewind.
    with admin.begin() as db:
        upgrade(db,fence,plan_digest({**value,'image':NEW_IMAGE}),value['image'],apply=True)
        assert db.execute(text('SELECT plan FROM runner_bindings.bindings')).scalar_one() == value
    assert preservation_snapshot(admin) == before


@pytest.mark.parametrize('damage',['stale','hash','image','phase','missing-proof','outbox','conversation','pending-stop'])
def test_image_change_refuses_unsafe_state(admin,stopped_binding,damage):
    fence,value = stopped_binding
    supplied_fence = replace(fence,revision=fence.revision+1) if damage == 'stale' else fence
    with admin.begin() as db:
        if damage == 'phase': db.execute(text("UPDATE runner_runtime.registry SET phase='runner_failed'"))
        if damage == 'missing-proof': db.execute(text('DELETE FROM runner_termination.stops'))
        if damage == 'outbox': db.execute(text('UPDATE runner_runtime.outbox SET settled=false'))
        if damage == 'conversation': db.execute(text("INSERT INTO research.conversations(owner_id,title,state) VALUES(:owner,'busy','running')"),{'owner':fence.owner})
        if damage == 'pending-stop': db.execute(text('UPDATE runner_termination.stops SET completed_at=NULL'))
    with pytest.raises(Refused):
        with admin.begin() as db:
            upgrade(db,supplied_fence,'wrong' if damage == 'hash' else plan_digest(value),
                    'mutable:latest' if damage == 'image' else NEW_IMAGE,apply=True)
    with admin.begin() as db:
        assert db.execute(text('SELECT plan FROM runner_bindings.bindings')).scalar_one() == value


def test_api_role_cannot_use_maintenance_write(admin,stopped_binding):
    fence,value = stopped_binding
    api = create_engine(os.environ['DATABASE_URL'])
    try:
        with pytest.raises(DBAPIError):
            with api.begin() as db: upgrade(db,fence,plan_digest(value),NEW_IMAGE,apply=True)
    finally: api.dispose()
