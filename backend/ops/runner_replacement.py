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
# Outcomes that leave an owner with nothing to do for this target. A starting target Pod, one whose health
# could not be read, or one never confirmed healthy (an account reason) is not settled: it is still watched
# (review blocker 4; PM 11-25 allowance B: when unsure, hold; completion may wait).
# `stopping` (PM 11-25 decision B): a target Pod that its own normal stop drained, or that is gone after it; the
# stop path finishes it, and a fresh Pod is watched again.
SETTLED = {'converged', 'verified', 'moved', 'blocked', 'failed', 'canary_passed', 'stopping'}
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
    # The Pod UID of the open stop when its receipt is a normal drain (version 1) of that same Pod; None otherwise
    # (no receipt yet, a terminal/unavailable/operator receipt, or another Pod).
    drained_pod: UUID | None = None


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
        # PM 11-25 allowance B, observation continuity. Process memory only, so a controller restart starts
        # every count again (more waiting, never an early pass):
        self.streak = {}        # owner -> (Pod UID, start of its consecutive healthy checks)
        self.confirmed = set()  # Pod UIDs whose /health was read healthy at least once
        self.canary = None

    # Pass -----------------------------------------------------------------
    def run(self):
        """One pass. Returns (owner, outcome, reason) lines; reason is a fixed code or None."""
        if self.settings.fingerprint is None:
            return []
        try:
            return self._run()
        except Exception:
            # 3rd re-review blocker 2: a pass that could not read its target or owners (Service.reconcile
            # logs it and reuses this Rollout) observed nobody, so every canary streak ends here.
            self.streak.clear()
            raise

    def _run(self):
        self.target = self.store.install(self.settings.fingerprint)
        views = self.store.owners(self.target.target_id)
        self.rows_now = {}  # owner -> the row as last written in this pass (slot accounting, review major)
        self.slots = sum(1 for view in views if view.row is not None and occupies(view.row.phase, view.row.reason))
        self.protected = set()  # owners whose binding a superseded target's pending restart still protects
        lines = self.finish_previous({view.owner: view for view in views})
        # Allowance A: while a superseded target's own stop is open, this target starts nothing.
        self.previous_open = any(outcome in {'cutting', 'draining', 'restarting'} for _, kind, outcome in lines if kind == 'rollout_previous')
        canary = self.choose_canary(views) if self.target.state == 'canary' and not self.previous_open else None
        self.canary = canary  # This pass's selected canary (the only owner written with canary=True).
        pending = self.previous_open
        for view in sorted(views, key=lambda v: (v.owner != canary, v.row is None or not occupies(v.row.phase, v.row.reason), v.owner)):
            try:
                outcome = self.advance(view, canary)
            except Halt as halt:
                self.streak.pop(view.owner, None)
                if self.target.state in {'canary', 'rolling', 'complete'}:  # complete: observe()'s bad read (PM A)
                    self.target = self.store.set_state(self.target, 'halted', halt.reason, view.owner)
                    lines.append((view.owner, 'rollout_halted', halt.reason))
                pending = True
                continue
            except TargetChanged:
                outcome = 'held'  # An operator command (halt) committed in between; the next pass rereads.
            except Exception as failure:
                self.streak.pop(view.owner, None)  # An unreadable pass breaks the healthy streak.
                lines.append((view.owner, 'rollout', 'stuck:'+(getattr(failure, 'reason', None) or type(failure).__name__)))
                pending = True
                continue
            if outcome not in {'verified', 'canary_passed'} or view.owner != canary:
                self.streak.pop(view.owner, None)  # Pod missing, UID changed, not healthy: the streak ends.
            pending = pending or outcome not in SETTLED
            lines.append((view.owner, 'rollout', outcome))
        self.finish(pending, canary)
        return lines

    def finish_previous(self, views):
        """Allowance A: stops, cuts and restarts that a superseded target opened finish under that target
        first. A cut keeps receiving its interrupts within the settle window; after release the owner
        restarts on its unchanged binding (no move for any target), and that restart is itself held under
        the old target (`restarting`) until the owner has a Pod again, stopped, or the restart timed out
        (review 2nd re-review blocker 1). Only then is the old row closed (`failed:superseded`). A cut that
        does not settle is `blocked:turn_not_settled` under the old target (operator R2) and no longer holds
        the new target back; its open stop is then followed as an `external_stop`."""
        lines = []
        for old_target, owner, row in self.store.carried(self.target.target_id):
            view = views.get(owner)
            try:
                if view is None:
                    self.store.set_row(old_target, owner, 'failed', canary=False, reason='superseded')
                    continue
                if row.phase in {'restarting', 'blocked'}:
                    if view.pod_uid is not None or view.phase in {Phase.STOPPED, Phase.FAILED}:
                        # The restart produced its Pod (on the old binding), or there is nothing left to wait for.
                        self.store.set_row(old_target, owner, 'failed', canary=False, reason='superseded')
                        lines.append((owner, 'rollout_previous', 'previous_finished'))
                    elif row.phase == 'restarting' and self.elapsed(row.since) <= 2 * self.settings.canary_seconds:
                        lines.append((owner, 'rollout_previous', 'restarting'))
                    else:
                        # 3rd re-review blocker 1: a restart that takes too long no longer holds the other owners
                        # back, but its generation is still pending on the old binding: that one owner's binding
                        # stays protected (no move, before_create included) until it has a Pod or stops.
                        if row.phase == 'restarting':
                            self.store.set_row(old_target, owner, 'blocked', canary=False, reason='restart_timeout')
                        self.protected.add(owner)
                        lines.append((owner, 'rollout_previous', 'protected'))
                    continue
                if view.phase == Phase.STOPPING or (view.stop_id is not None and view.stop_id == row.stop_id):
                    if row.phase == 'cutting' and not view.stop_receipt:
                        if self.elapsed(row.since) > SETTLE_SECONDS:
                            self.store.set_row(old_target, owner, 'blocked', canary=False, stop_id=row.stop_id,
                                               reason='turn_not_settled')
                            lines.append((owner, 'rollout_previous', 'previous_blocked'))
                            continue
                        obj = self.cluster.pod(view) if view.pod_uid is not None else None
                        if obj is not None and obj['metadata'].get('uid') == str(view.pod_uid):
                            self.interrupt(view, obj, self.store.running_turns(owner))
                    lines.append((owner, 'rollout_previous', row.phase))
                    continue
                restarting = view.pod_uid is None and view.phase in {Phase.PREPARING, Phase.WAITING}  # Own entry.
                if view.phase == Phase.STOPPED and view.pod_uid is None:
                    key = uuid5(RESTART_KEYS, '%s:%s:%s' % (old_target, owner, row.stop_id))
                    restarting = self.store.restart(owner, key)
                if restarting:
                    # Hold the new target until this restart has its Pod (on the unchanged binding).
                    self.store.set_row(old_target, owner, 'restarting', canary=False)
                    lines.append((owner, 'rollout_previous', 'restarting'))
                    continue
                self.store.set_row(old_target, owner, 'failed', canary=False, reason='superseded')
                lines.append((owner, 'rollout_previous', 'previous_finished'))
            except Exception as failure:
                lines.append((owner, 'rollout_previous', 'stuck:'+(getattr(failure, 'reason', None) or type(failure).__name__)))
                lines.append((owner, 'rollout_previous', 'cutting'))  # Unknown: keep holding the new target back.
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
        for view in views:
            if (view.owner in self.protected and view.row is not None and view.row.canary
                    and view.row.phase not in {'blocked', 'failed'}):
                # A canary whose binding a superseded target's restart now protects cannot proceed: it stops
                # being the canary (blocked rows are skipped by canary_owner and the transaction gate).
                self.set(view, 'blocked', canary=False, reason='awaiting_previous')
        current = [v.owner for v in views if v.row is not None and v.row.canary and v.owner not in self.protected
                   and (self.rows_now.get(v.owner) or v.row).phase not in {'blocked', 'failed'}]
        if current:
            return current[0]
        def eligible(view):
            # Never an owner already blocked or failed for this target, nor a stopped owner whose binding
            # can never move (no stop proof): the canary would wait forever (review major 3); nor an owner
            # whose binding a superseded target's restart still protects (4th re-review): the protection
            # holds that owner only, the canary moves on to the next candidate.
            return (view.row is None or view.row.phase not in {'blocked', 'failed'}) and not (
                view.pod_uid is None and view.phase == Phase.STOPPED and not view.proved) and (
                view.owner not in self.protected)
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
        if self.previous_open:
            return False  # Allowance A: a superseded target's stop is still open.
        if view.owner in self.protected:
            return False  # A superseded target's restart of this owner is still pending.
        state = self.target.state
        return state in {'rolling', 'complete'} or (state == 'canary' and view.owner == canary)

    def set(self, view, phase, *, canary, stop_id=None, reason=None):
        # The row as last written in this pass: an owner can change twice in one pass (review major).
        old = self.rows_now.get(view.owner, view.row)
        self.store.set_row(self.target.target_id, view.owner, phase, canary=canary, stop_id=stop_id, reason=reason)
        self.slots += occupies(phase, reason) - (old is not None and occupies(old.phase, old.reason))
        self.rows_now[view.owner] = Row(phase, self.now(), canary,
                                        stop_id or (old.stop_id if old else None), reason)
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
        if row is not None and row.phase in {'cutting', 'draining'} and (
                view.phase == Phase.STOPPING or (view.stop_id is not None and view.stop_id == row.stop_id)):
            return self.after_stop(view, obj, canary, is_canary)  # Our open stop first, whatever the Pod runs.
        if self.normally_stopped(view, obj, live):
            return 'stopping'
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

    def normally_stopped(self, view, obj, live):
        """PM 11-25 decision B: an idle stop, logout or any other normal stop drains the runner (receipt version 1)
        and only then deletes its Pod, so the runner's exit and the Pod's disappearance are that stop's own
        effects, not a failure: such a target Pod is neither watched nor a halt reason. The evidence is the open
        stop of the current generation holding a drain receipt for this very Pod UID. A crash keeps its terminal
        receipt (version 2), so a dead Pod is still watched and halts, also once it is being deleted (review). Only
        a target Pod counts: one with the target fingerprint, or, once gone, an owner already bound to the target
        image (as without_pod() would count it after the release). Stops the rollout opened itself went to
        after_stop() before this."""
        if view.pod_uid is None or view.drained_pod != view.pod_uid:
            return False
        if obj is not None:
            return live == self.settings.fingerprint
        return view.image == self.settings.fingerprint['image']

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
        uid = obj['metadata']['uid']
        # Allowance B (review blocker 2): age alone never settles a Pod whose health was never read healthy.
        if not (canary_window or (row is not None and row.phase == 'restarting') or age < self.settings.canary_seconds
                or uid not in self.confirmed):
            return 'converged'
        try:
            health = self.cluster.health(view, obj)
        except Exception:
            return self.unconfirmed(view, is_canary, canary_window, 'health_unreachable')
        kind = health_class(health)
        if kind == 'bad':
            # PM 11-25 (A, dev VM G4) and review 8366d0c: a bad read of an UNCONFIRMED target Pod after `complete`
            # halts in this same pass, like in canary/rolling, so one bad read halts whatever the first pass saw.
            # Review ff121fe: a UID this controller already read healthy is out of that rule after `complete`.
            if watching or (self.target.state == 'complete' and uid not in self.confirmed):
                raise Halt('runner_unavailable')
            return 'converged'  # A halted target, or a confirmed Pod after complete: nothing to stop.
        if kind == 'inconclusive':
            return self.unconfirmed(view, is_canary, canary_window, 'inconclusive')
        self.confirmed.add(uid)
        if row is None or row.phase != 'verified' or (is_canary and not row.canary):
            # Shown in `rollout status`; the pass uses the streak. An owner already `verified` before it was
            # selected still gets its canary flag written (canary_owner/before_create read it).
            self.set(view, 'verified', canary=is_canary)
        if not canary_window:
            return 'verified'  # Healthy reads before this owner is the selected canary never count (PM 11-25).
        # The streak belongs to the observed Pod UID and to passes where this owner is the selected canary;
        # any other pass outcome for this owner (no Pod, lookup error, another UID, not healthy, not the
        # canary) has already ended it in run().
        streak = self.streak.get(view.owner)
        if streak is None or streak[0] != uid:
            streak = self.streak[view.owner] = (uid, self.now())
        if self.elapsed(streak[1]) >= self.settings.canary_seconds:
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
            carried = self.store.carried(target.target_id)
            if any(row.phase in {'cutting', 'draining', 'restarting'} or carried_owner == owner
                   for _, carried_owner, row in carried):
                return False  # Allowance A: a superseded target's work is open, or still protects this owner.
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
        """The current target for this fingerprint; a new one supersedes the old. Nothing is copied: the old
        target's own open stops finish under it first (Rollout.finish_previous, PM 11-25 allowance A).
        A fingerprint that once completed starts rolling (a rollback skips the canary)."""
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
                        CASE WHEN s.receipt->>'version'='1' AND s.receipt->>'podUid'=s.pod_uid::text
                             THEN s.pod_uid END AS drained_pod,
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
                           bool(row['proved']), row['drained_pod'])
                      for row in rows]
            if len(rows) < self.PAGE:
                return views
            after = rows[-1]['owner_id']

    def carried(self, target_id):
        """Stops, cuts and restarts that superseded targets opened themselves and have not finished
        (allowance A, PM 11-25: restarts included). Followed `external_stop`s are not theirs."""
        with self.engine.begin() as db:
            rows = db.execute(text('''SELECT o.target_id, o.owner_id, o.phase, o.since, o.canary, o.stop_id, o.reason
                FROM runner_rollout.owners o JOIN runner_rollout.targets t ON t.target_id=o.target_id
                WHERE t.state='superseded' AND t.target_id<>:target
                AND (o.phase IN ('cutting','draining','restarting') OR (o.phase='blocked' AND o.reason='restart_timeout'))
                AND coalesce(o.reason,'')<>:external ORDER BY o.owner_id, o.target_id'''),
                {'target': target_id, 'external': EXTERNAL}).mappings().all()
        return [(row['target_id'], row['owner_id'], Row(row['phase'], row['since'], row['canary'], row['stop_id'], row['reason']))
                for row in rows]

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
                canary=EXCLUDED.canary,  -- The canary as of the pass that wrote it (5th re-review: never sticky).
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
