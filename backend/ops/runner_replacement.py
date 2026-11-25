"""#101 rolling runner replacement: one pass of the in-cluster runner controller.

The target is this controller's own env (app.runner_image_move.target_from_env): the runner image and
the rollout flags that render into new Pods. Each pass, per owner with a published binding:

- an owner without a Pod moves its binding to the target image (runner_rollout.move_image, decision D3);
- an owner whose live Pod runs another fingerprint is replaced between turns (decision D1): after its
  runner reports idle for TURN_GRACE_SECONDS a stop intent is recorded, and the existing stop path
  (Service.step: drain, graceful UID DELETE, 404 proof, release) runs unchanged. Past the turn deadline
  the stop is recorded anyway and the running turn is interrupted with the runner's ordinary interrupt
  call (the #90 Stop). After release the binding moves and, while the owner is still logged in, the
  owner re-enters at once; otherwise its next start uses the new image;
- a Pod on the target fingerprint is watched in its first canarySeconds; a dead runner, an image pull
  failure or an unavailable runner (not an account/credential reason) halts the rollout.

Stages: `canary` (one owner, and the canary Pod must stay healthy for canarySeconds after its first
healthy check, PM condition 1) -> `rolling` (at most maxParallel replacements in flight) -> `complete`;
`halted` stops new stops and moves. This module never deletes a Pod, writes a receipt or releases.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from uuid import UUID, uuid4, uuid5

from sqlalchemy import text

from runner.lifecycle import Phase, Refused, require

SLOT_PHASES = {'cutting', 'draining', 'restarting'}
# Health reasons that describe the account, credentials or provider, not the image.
INCONCLUSIVE = {'not_configured', 'not_enabled_for_account', 'auth_expired', 'auth_revoked',
                'budget_exhausted', 'provider_rate_limited', 'unavailable'}
PULL_FAILURES = {'ErrImagePull', 'ImagePullBackOff', 'InvalidImageName', 'ErrImageNeverPull'}
TURN_GRACE_SECONDS = 60   # The runner has been idle this long before a between-turns stop.
SETTLE_SECONDS = 120      # After an interrupt, the drain receipt is expected within this.
PULL_SECONDS = 120        # A target Pod still failing to pull its image after this halts.
RESTART_KEYS = UUID('5f0d7a52-6a3e-4a55-9a0b-101000000001')  # uuid5 namespace for re-entry keys
# Outcomes that leave an owner with nothing to do for this target. A starting target Pod or one whose
# health could not be read is not settled: it is still watched for pull failures and health (review blocker 4).
SETTLED = {'converged', 'verified', 'moved', 'blocked', 'failed', 'inconclusive', 'canary_passed'}
EXTERNAL = 'external_stop'  # A followed stop someone else opened: it holds no replacement slot.


def occupies(phase, reason):
    """A replacement in flight that counts against maxParallel."""
    return phase in SLOT_PHASES and reason != EXTERNAL


class TargetChanged(Refused):
    """The target's state or canary changed under the owner's stop or move (an operator command)."""


class Halt(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def bounded(name, default, low, high):
    value = os.environ.get(name, '')
    if value == '':
        return default
    require(value.isdigit() and low <= int(value) <= high)
    return int(value)


@dataclass(frozen=True)
class Settings:
    fingerprint: dict | None
    max_parallel: int = 1
    turn_deadline: int = 1800
    canary_seconds: int = 600
    canary_owners: tuple = ()

    def __post_init__(self):
        require(1 <= self.max_parallel <= 3 and 60 <= self.turn_deadline and 60 <= self.canary_seconds)
        require(all(isinstance(owner, UUID) for owner in self.canary_owners))


def load_settings():
    """Invalid values refuse controller startup, like the stream flags."""
    from app.runner_image_move import target_from_env
    owners = tuple(UUID(value) for value in os.environ.get('ROLLOUT_CANARY_OWNERS', '').split(',') if value)
    return Settings(target_from_env(), bounded('ROLLOUT_MAX_PARALLEL', 1, 1, 3),
                    bounded('ROLLOUT_TURN_DEADLINE_SECONDS', 1800, 60, 86400),
                    bounded('ROLLOUT_CANARY_SECONDS', 600, 60, 86400), owners)


@dataclass(frozen=True)
class Target:
    target_id: UUID
    fingerprint: dict
    state: str
    reason: str | None = None
    reason_owner: UUID | None = None


@dataclass(frozen=True)
class Row:
    phase: str
    since: datetime
    canary: bool = False
    stop_id: UUID | None = None
    reason: str | None = None


@dataclass(frozen=True)
class View:
    """One owner with a published binding, read without the owner lock; every write rechecks under it."""
    owner: UUID
    state_id: UUID
    phase: Phase
    pod_uid: UUID | None
    image: str
    stop_id: UUID | None = None        # open stop of the current generation
    stop_receipt: bool = False
    row: Row | None = None
    proved: bool = True                 # a completed stop proof exists for the current generation


def health_class(health):
    if type(health) is not dict:
        return 'bad'
    if health.get('available') is True and health.get('reason') is None:
        return 'ok'
    if health.get('reason') in INCONCLUSIVE and not health.get('failure'):
        return 'inconclusive'
    return 'bad'  # Includes a runner whose native never started (#61 R1 `failure`).


def created_at(obj):
    value = (obj.get('metadata') or {}).get('creationTimestamp')
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else None


def pull_failed(obj):
    for status in (obj.get('status') or {}).get('containerStatuses') or []:
        waiting = (status.get('state') or {}).get('waiting') or {}
        if status.get('name') == 'runner' and waiting.get('reason') in PULL_FAILURES:
            return True
    return False


class Rollout:
    def __init__(self, store, cluster, settings, now):
        self.store, self.cluster, self.settings, self.now = store, cluster, settings, now
        self.ensure_failures = {}

    # Pass -----------------------------------------------------------------
    def run(self):
        """One pass. Returns (owner, outcome, reason) lines; reason is a fixed code or None."""
        if self.settings.fingerprint is None:
            return []
        self.target = self.store.install(self.settings.fingerprint)
        views = self.store.owners(self.target.target_id)
        self.slots = sum(1 for view in views if view.row is not None and occupies(view.row.phase, view.row.reason))
        canary = self.choose_canary(views) if self.target.state == 'canary' else None
        lines, pending = [], False
        for view in sorted(views, key=lambda v: (v.owner != canary, v.row is None or not occupies(v.row.phase, v.row.reason), v.owner)):
            try:
                outcome = self.advance(view, canary)
            except Halt as halt:
                if self.target.state in {'canary', 'rolling'}:
                    self.target = self.store.set_state(self.target, 'halted', halt.reason, view.owner)
                    lines.append((view.owner, 'rollout_halted', halt.reason))
                pending = True
                continue
            except TargetChanged:
                outcome = 'held'  # An operator command (halt) committed in between; the next pass rereads.
            except Exception as failure:
                lines.append((view.owner, 'rollout', 'stuck:'+(getattr(failure, 'reason', None) or type(failure).__name__)))
                pending = True
                continue
            pending = pending or outcome not in SETTLED
            lines.append((view.owner, 'rollout', outcome))
        self.finish(pending, canary)
        return lines

    def finish(self, pending, canary):
        state = self.target.state
        if state == 'canary' and canary is None and not pending:
            self.target = self.store.set_state(self.target, 'rolling')  # Nothing left to try a canary on.
            state = 'rolling'
        if state == 'rolling' and not pending and self.slots == 0:
            self.target = self.store.set_state(self.target, 'complete')
        elif state == 'complete' and pending:
            self.target = self.store.set_state(self.target, 'rolling')

    def choose_canary(self, views):
        T = self.settings.fingerprint
        current = [v.owner for v in views if v.row is not None and v.row.canary and v.row.phase not in {'blocked', 'failed'}]
        if current:
            return current[0]
        def eligible(view):
            # Never an owner already blocked or failed for this target, nor a stopped owner whose binding
            # can never move (no stop proof): the canary would wait forever (review major 3).
            return (view.row is None or view.row.phase not in {'blocked', 'failed'}) and not (
                view.pod_uid is None and view.phase == Phase.STOPPED and not view.proved)
        def needs(view):
            return view.image != T['image'] or view.pod_uid is not None
        by_owner = {view.owner: view for view in views}
        configured = [owner for owner in self.settings.canary_owners
                      if owner in by_owner and needs(by_owner[owner]) and eligible(by_owner[owner])]
        running = [v.owner for v in views if v.pod_uid is not None and v.phase in {Phase.READY, Phase.UNREACHABLE}
                   and v.stop_id is None and eligible(v)]
        stopped = [v.owner for v in views if v.pod_uid is None and v.image != T['image'] and v.phase == Phase.STOPPED
                   and eligible(v)]
        for owner in configured + running + stopped:
            return owner
        return None

    def allowed(self, view, canary):
        state = self.target.state
        return state in {'rolling', 'complete'} or (state == 'canary' and view.owner == canary)

    def set(self, view, phase, *, canary, stop_id=None, reason=None):
        old = view.row
        self.store.set_row(self.target.target_id, view.owner, phase, canary=canary, stop_id=stop_id, reason=reason)
        self.slots += occupies(phase, reason) - (old is not None and occupies(old.phase, old.reason))
        return phase

    def elapsed(self, since):
        return (self.now() - since).total_seconds()

    # One owner ------------------------------------------------------------
    def advance(self, view, canary):
        T = self.settings.fingerprint
        row, is_canary = view.row, view.owner == canary
        if row is not None and row.phase == 'blocked' and row.stop_id is not None and view.stop_id == row.stop_id:
            return 'blocked'  # Review major 2: the same unsettled stop stays blocked, and holds no slot.
        obj = self.cluster.pod(view) if view.pod_uid is not None else None
        if obj is not None and obj['metadata'].get('uid') != str(view.pod_uid):
            return 'pod_uid_mismatch'  # Never act on a replaced object; Service.step reports it.
        from app.runner_image_move import pod_fingerprint
        live = pod_fingerprint(obj) if obj is not None else None
        if live == T:
            return self.observe(view, obj, is_canary)
        if row is not None and row.phase in {'cutting', 'draining'}:
            return self.after_stop(view, obj, canary, is_canary)
        if row is not None and row.phase == 'restarting' and obj is None:
            if self.elapsed(row.since) > 2 * self.settings.canary_seconds:
                return self.set(view, 'blocked', canary=is_canary, reason='restart_timeout')
            return 'restarting'
        if obj is None:
            return self.without_pod(view, canary, is_canary)
        return self.replace(view, obj, canary, is_canary)

    def without_pod(self, view, canary, is_canary):
        T = self.settings.fingerprint
        if view.pod_uid is not None:
            return 'pod_missing'  # Service.step takes the dead-runner path.
        if view.image == T['image']:
            return 'converged'
        if view.phase == Phase.FAILED or (view.phase == Phase.STOPPED and not view.proved):
            # Permanent until an operator acts: a failed runner, or a legacy stop without the proof the
            # binding move requires (review major 3). Recorded once; never a canary.
            reason = 'runner_failed' if view.phase == Phase.FAILED else 'no_stop_proof'
            if view.row is None or (view.row.phase, view.row.reason) != ('blocked', reason):
                self.set(view, 'blocked', canary=False, reason=reason)
            return 'blocked'
        if not self.allowed(view, canary) or view.phase not in {Phase.STOPPED, Phase.PREPARING, Phase.WAITING}:
            return 'held'
        try:
            moved = self.store.move(view.owner, T['image'], target_id=self.target.target_id, canary=is_canary)
        except TargetChanged:
            raise
        except Exception:
            return 'not_movable'  # A create permission is out, or a stop is still open: next pass.
        if moved:
            return self.set(view, 'moved', canary=is_canary)
        return 'converged'

    def replace(self, view, obj, canary, is_canary):
        row = view.row
        if not self.allowed(view, canary):
            return 'held'
        if view.stop_id is not None:
            # A stop opened by someone else (idle stop, logout, operator): follow it to release, without a
            # replacement slot (review major 2).
            return self.set(view, 'draining', canary=is_canary, stop_id=view.stop_id, reason=EXTERNAL)
        if row is None or row.phase != 'waiting_turn':
            self.set(view, 'waiting_turn', canary=is_canary)
            row = Row('waiting_turn', self.now(), is_canary)
        if self.slots >= self.settings.max_parallel:
            return 'waiting_slot'
        if (obj.get('status') or {}).get('phase') != 'Running':
            return 'waiting_turn'
        try:
            idle = self.cluster.idle(view, obj, TURN_GRACE_SECONDS)
        except Exception:
            # Review major 1: no idle evidence (timeout, refusal). This Pod's UID was just matched, so the
            # deadline below still applies; before it, wait.
            idle = None
        if idle:
            stop_id = self.store.stop_between_turns(self.target.target_id, view.owner, canary=is_canary)
            if stop_id is not None:
                return self.set(view, 'draining', canary=is_canary, stop_id=stop_id)
            # The database still holds a running conversation the runner no longer has: wait, but only
            # until the deadline below (PM 11-25: the deadline applies whether or not the runner is idle;
            # the drain receipt's close_dead_work closes such a conversation, #114).
        if self.elapsed(row.since) < self.settings.turn_deadline:
            return 'waiting_turn' if idle is not None else 'idle_unknown'
        # D1 deadline: the stop and the `cutting` row commit together (review blocker 2), so a controller
        # restarted right after still knows this stop is a cut and resends the interrupts (after_stop).
        stop_id, turns = self.store.stop_for_cut(self.target.target_id, view.owner, canary=is_canary)
        self.set(view, 'cutting', canary=is_canary, stop_id=stop_id)
        self.interrupt(view, obj, turns)
        return 'cutting'

    def interrupt(self, view, obj, turns):
        for session, request in turns:
            try:
                self.cluster.interrupt(view, obj, session, request)
            except Exception:
                pass  # The settle timeout in after_stop reports a turn that does not end.

    def after_stop(self, view, obj, canary, is_canary):
        T, row = self.settings.fingerprint, view.row
        if view.phase == Phase.STOPPING or (view.stop_id is not None and view.stop_id == row.stop_id):
            # Our stop is still open: Service.step drains, deletes and releases.
            if row.phase == 'cutting' and obj is not None and not view.stop_receipt:
                if self.elapsed(row.since) > SETTLE_SECONDS:
                    return self.set(view, 'blocked', canary=is_canary, stop_id=row.stop_id, reason='turn_not_settled')
                # Within the settle window, resend the interrupts each pass (review blocker 2: also after a
                # controller restart). Interrupting a turn that already ends is a no-op for the runner.
                self.interrupt(view, obj, self.store.running_turns(view.owner))
            return row.phase
        if view.pod_uid is not None:
            # Released and already running again, not on the target (a target Pod went to observe()).
            return self.replace(view, obj, canary, is_canary) if obj is not None else 'pod_missing'
        halted = self.target.state == 'halted'
        # Review blocker 1: a stop carried over from an earlier target finishes, but the move to this target
        # obeys this target's stage like any other move (only the canary during the canary).
        may_move = not halted and self.allowed(view, canary)
        if may_move and view.image != T['image']:
            try:
                self.store.move(view.owner, T['image'], target_id=self.target.target_id, canary=is_canary)
            except TargetChanged:
                raise
            except Exception:
                return self.set(view, 'blocked', canary=is_canary, reason='not_movable')
        if view.phase in {Phase.PREPARING, Phase.WAITING}:
            restarted = True  # The owner's own queued entry re-entered at release.
        else:
            key = uuid5(RESTART_KEYS, '%s:%s:%s' % (self.target.target_id, view.owner, row.stop_id))
            restarted = self.store.restart(view.owner, key)
        if halted:
            return self.set(view, 'failed', canary=is_canary, reason='halted')
        if not may_move:
            # Restarted on its unchanged binding; it is replaced once this target is rolling.
            return self.set(view, 'blocked', canary=False, reason='awaiting_canary')
        return self.set(view, 'restarting' if restarted else 'moved', canary=is_canary)

    def observe(self, view, obj, is_canary):
        """A Pod already on the target fingerprint."""
        row, watching = view.row, self.target.state in {'canary', 'rolling'}
        canary_window = is_canary and self.target.state == 'canary'
        if watching and self.cluster.terminal(obj) is not None:
            raise Halt('runner_terminated')
        born = created_at(obj)
        age = self.elapsed(born) if born is not None else 0
        if watching and pull_failed(obj) and age >= PULL_SECONDS:
            raise Halt('image_pull')
        if (obj.get('status') or {}).get('phase') != 'Running':
            return self.unconfirmed(view, is_canary, canary_window, 'starting')
        if not (canary_window or (row is not None and row.phase == 'restarting') or age < self.settings.canary_seconds):
            return 'converged'
        try:
            health = self.cluster.health(view, obj)
        except Exception:
            return self.unconfirmed(view, is_canary, canary_window, 'health_unreachable')
        kind = health_class(health)
        if kind == 'bad':
            if watching:
                raise Halt('runner_unavailable')
            return 'converged'
        if kind == 'inconclusive':
            return self.unconfirmed(view, is_canary, canary_window, 'inconclusive')
        if canary_window and row is not None and row.phase == 'verified' and born is not None and born > row.since:
            # A newer canary Pod than the healthy streak started on: the streak belongs to another Pod UID.
            return self.unconfirmed(view, is_canary, canary_window, 'canary_pod_changed')
        if row is None or row.phase != 'verified':
            self.set(view, 'verified', canary=is_canary)
            row = Row('verified', self.now(), is_canary)
        if canary_window and self.elapsed(row.since) >= self.settings.canary_seconds:
            self.target = self.store.set_state(self.target, 'rolling')
            return 'canary_passed'
        return 'verified'

    def unconfirmed(self, view, is_canary, canary_window, outcome):
        """PM 11-25 (blocker 3): the canary passes only on canarySeconds of consecutive healthy checks of the
        same Pod. Any other observation breaks the streak: neither pass nor halt; the next healthy check
        starts it again. `rollout resume` is the operator's explicit pass."""
        row = view.row
        if canary_window and row is not None and row.phase == 'verified':
            self.set(view, 'restarting', canary=is_canary, reason='canary_unconfirmed')
        return outcome

    # Controller hooks -------------------------------------------------------
    def before_create(self, owner):
        """Service.ensure calls this before it resolves the binding: an owner waiting for its first Pod
        of a generation moves to the target first (also covers new owners, PM 11-18 observation).
        Halted rollouts do not move; during the canary only the canary owner moves (PM 11-25), the others
        start on their current binding and are replaced once rolling."""
        if self.settings.fingerprint is None:
            return False
        try:
            target = self.store.current()
            if target is None or target.state not in {'canary', 'rolling', 'complete'}:
                return False
            canary = target.state == 'canary'
            if canary and self.store.canary_owner(target.target_id) != owner:
                return False
            return self.store.move(owner, self.settings.fingerprint['image'], target_id=target.target_id, canary=canary)
        except Exception:
            return False  # Not movable (a create permission is out) or unreadable: create as bound.

    def ensure_result(self, owner, ok, image_is_target):
        """Three ensure failures in a row for an owner bound to the target image halt the rollout."""
        if ok or not image_is_target:
            self.ensure_failures.pop(owner, None)
            return None
        count = self.ensure_failures[owner] = self.ensure_failures.get(owner, 0) + 1
        target = self.store.current()
        if count >= 3 and target is not None and target.state in {'canary', 'rolling'}:
            self.store.set_state(target, 'halted', 'ensure_failed', owner)
            self.ensure_failures.pop(owner, None)
            return 'ensure_failed'
        return None


def clock():
    return datetime.now(timezone.utc)


class SqlStore:
    """The observer login's view of rollout state (101_runner_rollout_grants.sql)."""
    def __init__(self, engine, now=clock):
        self.engine, self.now = engine, now

    def validate(self):
        from app.runner_rollout_schema import validate
        with self.engine.begin() as db:
            validate(db)

    @staticmethod
    def _target(row):
        return Target(row['target_id'], row['fingerprint'], row['state'], row['reason'], row['reason_owner'])

    def current(self):
        with self.engine.begin() as db:
            row = db.execute(text("SELECT * FROM runner_rollout.targets WHERE state<>'superseded'")).mappings().one_or_none()
            return None if row is None else self._target(row)

    def install(self, fingerprint):
        """The current target for this fingerprint; a new one supersedes the old and carries its in-flight
        replacements. A fingerprint that once completed starts rolling (a rollback skips the canary)."""
        from app.runner_image_move import fingerprint_sha
        sha = fingerprint_sha(fingerprint)
        with self.engine.begin() as db:
            db.execute(text('SELECT pg_advisory_xact_lock(44102)'))
            current = db.execute(text("SELECT * FROM runner_rollout.targets WHERE state<>'superseded'")).mappings().one_or_none()
            if current is not None and current['fingerprint_sha'] == sha:
                return self._target(current)
            known = db.execute(text('''SELECT 1 FROM runner_rollout.targets
                WHERE fingerprint_sha=:sha AND completed_at IS NOT NULL'''), {'sha': sha}).first() is not None
            if current is not None:
                db.execute(text('''UPDATE runner_rollout.targets SET state='superseded', reason=NULL, reason_owner=NULL,
                    updated_at=now() WHERE target_id=:target'''), {'target': current['target_id']})
            target = uuid4()
            db.execute(text('''INSERT INTO runner_rollout.targets(target_id,fingerprint,fingerprint_sha,state)
                VALUES(:target,CAST(:fingerprint AS jsonb),:sha,:state)'''),
                {'target': target, 'fingerprint': json.dumps(fingerprint), 'sha': sha,
                 'state': 'rolling' if known else 'canary'})
            if current is not None:
                db.execute(text('''INSERT INTO runner_rollout.owners(target_id,owner_id,phase,canary,stop_id,since,reason)
                    SELECT :target,owner_id,phase,false,stop_id,since,reason FROM runner_rollout.owners
                    WHERE target_id=:old AND phase IN ('cutting','draining','restarting')'''),
                    {'target': target, 'old': current['target_id']})
            row = db.execute(text('SELECT * FROM runner_rollout.targets WHERE target_id=:target'),
                             {'target': target}).mappings().one()
            return self._target(row)

    PAGE = 500

    def owners(self, target_id):
        """Every owner with a published binding, read in owner_id pages (review major 4: a fixed LIMIT
        left owners unevaluated while the target completed)."""
        views, after = [], None
        while True:
            with self.engine.begin() as db:
                rows = db.execute(text('''SELECT r.owner_id, r.state_id, r.phase, r.pod_uid, b.plan->>'image' AS image,
                        s.stop_id, s.receipt IS NOT NULL AS receipt,
                        EXISTS (SELECT 1 FROM runner_termination.stops p WHERE p.owner_id=r.owner_id
                            AND p.state_id=r.state_id AND p.generation=r.generation AND p.completed_at IS NOT NULL
                            AND jsonb_typeof(p.proof)='object' AND p.proof<>'{}'::jsonb) AS proved,
                        o.phase AS row_phase, o.since, o.canary, o.stop_id AS row_stop, o.reason
                    FROM runner_bindings.bindings b
                    JOIN runner_runtime.registry r ON r.owner_id=b.owner_id AND r.state_id=b.state_id
                    LEFT JOIN runner_termination.stops s ON s.owner_id=r.owner_id AND s.state_id=r.state_id
                        AND s.generation=r.generation AND s.completed_at IS NULL
                    LEFT JOIN runner_rollout.owners o ON o.target_id=:target AND o.owner_id=r.owner_id
                    WHERE CAST(:after AS uuid) IS NULL OR r.owner_id > CAST(:after AS uuid)
                    ORDER BY r.owner_id LIMIT :limit'''), {'target': target_id, 'after': after, 'limit': self.PAGE}).mappings().all()
            views += [View(row['owner_id'], row['state_id'], Phase(row['phase']), row['pod_uid'], row['image'],
                           row['stop_id'], bool(row['receipt']),
                           None if row['row_phase'] is None else Row(row['row_phase'], row['since'], row['canary'],
                                                                     row['row_stop'], row['reason']),
                           bool(row['proved']))
                      for row in rows]
            if len(rows) < self.PAGE:
                return views
            after = rows[-1]['owner_id']

    def canary_owner(self, target_id):
        """The target's current canary, as Rollout.choose_canary keeps it (not blocked/failed), or None."""
        with self.engine.begin() as db:
            return db.execute(text('''SELECT owner_id FROM runner_rollout.owners WHERE target_id=:target AND canary
                AND phase NOT IN ('blocked','failed') ORDER BY owner_id LIMIT 1'''), {'target': target_id}).scalar_one_or_none()

    def counts(self, target_id):
        """Owners of this target by phase, and the reasons of blocked/failed ones (operator status)."""
        with self.engine.begin() as db:
            rows = db.execute(text('''SELECT phase, coalesce(reason, '') AS reason, count(*) AS n FROM runner_rollout.owners
                WHERE target_id=:target GROUP BY phase, reason ORDER BY phase, reason'''), {'target': target_id}).all()
        return {phase+(':'+reason if reason else ''): n for phase, reason, n in rows}

    @staticmethod
    def _set_row(db, target_id, owner, phase, *, canary, stop_id=None, reason=None):
        db.execute(text('''INSERT INTO runner_rollout.owners(target_id,owner_id,phase,canary,stop_id,since,reason)
            VALUES(:target,:owner,:phase,:canary,:stop,now(),:reason)
            ON CONFLICT(target_id,owner_id) DO UPDATE SET phase=EXCLUDED.phase,
                canary=runner_rollout.owners.canary OR EXCLUDED.canary,
                stop_id=coalesce(EXCLUDED.stop_id, runner_rollout.owners.stop_id), reason=EXCLUDED.reason,
                since=CASE WHEN runner_rollout.owners.phase=EXCLUDED.phase THEN runner_rollout.owners.since
                           ELSE EXCLUDED.since END'''),
            {'target': target_id, 'owner': owner, 'phase': phase, 'canary': canary, 'stop': stop_id, 'reason': reason})

    def set_row(self, target_id, owner, phase, *, canary, stop_id=None, reason=None):
        with self.engine.begin() as db:
            self._set_row(db, target_id, owner, phase, canary=canary, stop_id=stop_id, reason=reason)

    @staticmethod
    def _gate(db, target_id, owner, canary):
        """Review blocker 5: inside the stop or move transaction, lock the target row (set_state, i.e. an
        operator halt/resume, updates it, so the two serialize) and recheck that this target is still the
        current, unhalted one and that, during the canary, this owner is the only canary."""
        state = db.execute(text('SELECT state FROM runner_rollout.targets WHERE target_id=:target FOR UPDATE'),
                           {'target': target_id}).scalar_one_or_none()
        if state not in {'canary', 'rolling', 'complete'}:
            raise TargetChanged('rollout_target_changed')
        if state == 'canary':
            other = db.execute(text('''SELECT 1 FROM runner_rollout.owners WHERE target_id=:target AND canary
                AND phase NOT IN ('blocked','failed') AND owner_id<>:owner'''), {'target': target_id, 'owner': owner}).first()
            if not canary or other is not None:
                raise TargetChanged('rollout_target_changed')

    def set_state(self, target, state, reason=None, owner=None):
        """Compare-and-swap on the current state; a concurrent operator command wins."""
        with self.engine.begin() as db:
            row = db.execute(text('''UPDATE runner_rollout.targets SET state=:state, reason=:reason, reason_owner=:owner,
                    updated_at=now(), completed_at=CASE WHEN :state='complete' THEN now() ELSE completed_at END
                WHERE target_id=:target AND state=:old RETURNING *'''),
                {'state': state, 'reason': reason, 'owner': owner, 'target': target.target_id,
                 'old': target.state}).mappings().one_or_none()
            if row is None:
                row = db.execute(text('SELECT * FROM runner_rollout.targets WHERE target_id=:target'),
                                 {'target': target.target_id}).mappings().one()
            return self._target(row)

    def move(self, owner, image, *, target_id, canary):
        from app.runner_image_move import move
        with self.engine.begin() as db:
            self._gate(db, target_id, owner, canary)  # Target row before the owner lock.
            return move(db, owner, image)

    def stop_between_turns(self, target_id, owner, *, canary):
        """Record the stop, and this owner's `draining` row with it, only while the database holds no running
        conversation (rows before the owner lock, #114). Returns the stop id, or None while one is running."""
        from app.runner_termination_control import request_stop, no_pending_work
        from app.runner_registry import Registry
        from ops.runner_controller_service import lock_running, running_conversations
        with self.engine.begin() as db:
            lock_running(db, owner)
            if running_conversations(db, owner):
                return None
            self._gate(db, target_id, owner, canary)
            state = Registry(db)._current(owner)
            require(state.pod_uid is not None)
            no_pending_work(db, state)
            stop = request_stop(db, owner)['stop_id']
            self._set_row(db, target_id, owner, 'draining', canary=canary, stop_id=stop)
            return stop

    def stop_for_cut(self, target_id, owner, *, canary):
        """D1 deadline: the stop fences new turns and commits together with this owner's `cutting` row
        (review blocker 2); the running turns are returned to be interrupted."""
        from app.runner_termination_control import request_stop
        from ops.runner_controller_service import lock_running
        with self.engine.begin() as db:
            lock_running(db, owner)
            self._gate(db, target_id, owner, canary)
            stop = request_stop(db, owner)['stop_id']
            self._set_row(db, target_id, owner, 'cutting', canary=canary, stop_id=stop)
            return stop, self._running_turns(db, owner)

    @staticmethod
    def _running_turns(db, owner):
        rows = db.execute(text("""SELECT id, request_id FROM research.conversations
            WHERE owner_id=:owner AND state='running' AND request_id IS NOT NULL ORDER BY id"""), {'owner': owner}).all()
        return [(row[0], row[1]) for row in rows]

    def running_turns(self, owner):
        with self.engine.begin() as db:
            return self._running_turns(db, owner)

    def restart(self, owner, key):
        """Re-enter a replaced owner who still has a live login session (decision D1)."""
        from app.runner_registry import Registry
        from ops.runner_provision_worker import allowed
        if not allowed(owner):
            return False
        with self.engine.begin() as db:
            login = db.execute(text('''SELECT 1 FROM auth.sessions WHERE account_id=:owner
                AND expires_at>clock_timestamp() LIMIT 1'''), {'owner': owner}).first()
            if login is None:
                return False
            try:
                Registry(db).enter(owner, key, lambda: self.now())
            except Refused:
                return False
            return True
