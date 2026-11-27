"""#101 runner image targets: fingerprints and the binding-image move (decision D3).

A fingerprint is what a runner Pod runs: its image and the rollout flags rendered into its env
(runner_kube.STREAM_FLAGS targets, research included). The controller's target comes from its own env
(RUNNER_TARGET_IMAGE plus the same stream_env() that renders new Pods), so target and render agree.

A binding moves only while its owner has no Pod and no create permission (runner_rollout.move_image
enforces that in the database); the Python predicates below add the stopped-owner checks of the
reviewed manual tool (ops.runner_binding_image), which shares stopped_movable().
"""
import hashlib
import json
import os
import re

from sqlalchemy import text

from .runner_bindings import decode
from .runner_kube import STREAM_FLAGS, stream_env
from .runner_registry import Registry
from .runner_termination_control import no_pending_work
from runner.lifecycle import Phase, require

FLAG_TARGETS = tuple(target for _, target in STREAM_FLAGS)
IMAGE = re.compile(r'[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}')


def fingerprint(image, flags):
    require(type(image) is str and IMAGE.fullmatch(image) is not None)
    flags = sorted(flags)
    require(len(set(flags)) == len(flags) and set(flags) <= set(FLAG_TARGETS))
    return {'image': image, 'runnerFlags': flags}


def fingerprint_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def target_from_env():
    """This controller's target, or None while RUNNER_TARGET_IMAGE is unset (no rolling replacement)."""
    image = os.environ.get('RUNNER_TARGET_IMAGE', '')
    if not image:
        return None
    return fingerprint(image, stream_env())


def pod_fingerprint(obj):
    """What a live runner Pod runs: the runner container's image and its "true" rollout flags."""
    containers = [c for c in ((obj or {}).get('spec') or {}).get('containers') or [] if c.get('name') == 'runner']
    require(len(containers) == 1)
    env = {item.get('name'): item.get('value') for item in containers[0].get('env') or []}
    return fingerprint(containers[0].get('image'), [name for name in FLAG_TARGETS if env.get(name) == 'true'])


def stopped_movable(db, state):
    """(a) A stopped owner: no Pod, nothing pending, its last stop proved. The reviewed preconditions of
    ops.runner_binding_image (minus its operator fence and membership checks)."""
    require(state.phase == Phase.STOPPED and state.pod_uid is None and not state.ambiguous
            and state.active_requests == 0)
    no_pending_work(db, state)
    # Old settled ensure receipts are history; no new creator may remain queued.
    require(not db.execute(text('SELECT 1 FROM runner_runtime.outbox WHERE owner_id=:owner AND NOT settled'),
                           {'owner': state.owner}).first())
    require(not db.execute(text('SELECT 1 FROM runner_termination.stops WHERE owner_id=:owner AND completed_at IS NULL'),
                           {'owner': state.owner}).first())
    require(not db.execute(text('SELECT 1 FROM runner_termination.entries WHERE owner_id=:owner AND consumed_at IS NULL'),
                           {'owner': state.owner}).first())
    proof = db.execute(text('''SELECT proof FROM runner_termination.stops WHERE owner_id=:owner
        AND state_id=:state AND generation=:generation AND completed_at IS NOT NULL'''),
        {'owner': state.owner, 'state': state.state_id, 'generation': state.generation}).scalar_one_or_none()
    require(type(proof) is dict and bool(proof))


def waiting_movable(db, state):
    """(b) An owner waiting for its first Pod of this generation, before the create permission."""
    require(state.phase in {Phase.PREPARING, Phase.WAITING} and state.pod_uid is None)
    require(not db.execute(text('''SELECT 1 FROM runner_runtime.outbox
        WHERE owner_id=:owner AND state_id=:state AND generation=:generation AND kind='ensure' AND settled'''),
        {'owner': state.owner, 'state': state.state_id, 'generation': state.generation}).first())


def move(db, owner, image):
    """Move this owner's binding to `image`. True when moved, False when it already is there.
    Refused (nothing changed) when no binding exists for the current state or the owner is not movable."""
    require(type(image) is str and IMAGE.fullmatch(image) is not None)
    state = Registry(db)._current(owner)  # Owner lock first; move_image takes the same lock again.
    row = db.execute(text('SELECT state_id, plan FROM runner_bindings.bindings WHERE owner_id=:owner'),
                     {'owner': owner}).mappings().one_or_none()
    require(row is not None and row['state_id'] == state.state_id)
    old = row['plan']
    if old['image'] == image:
        return False
    if state.phase == Phase.STOPPED:
        stopped_movable(db, state)
    else:
        waiting_movable(db, state)
    moved = db.execute(text('SELECT runner_rollout.move_image(:owner,:state,CAST(:old AS jsonb),:image)'),
                       {'owner': owner, 'state': state.state_id, 'old': json.dumps(old), 'image': image}).scalar_one()
    require(moved == {**old, 'image': image})
    decode(moved)
    return True
